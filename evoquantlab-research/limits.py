"""Apply process limits before importing numerical libraries; no worker processes."""
import os
import sys

_JOB = None


def apply_limits(memory_mb=1024, cores=1):
    global _JOB
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = str(cores)
    evidence = {"numerical_threads": cores, "memory_limit_mb": memory_mb, "affinity_applied": False,
                "memory_limit_applied": False}
    if sys.platform == "win32":
        import ctypes as c
        from ctypes import wintypes as w
        k = c.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = w.HANDLE
        process = k.GetCurrentProcess()
        p, system = c.c_size_t(), c.c_size_t()
        k.GetProcessAffinityMask.argtypes = [w.HANDLE, c.POINTER(c.c_size_t), c.POINTER(c.c_size_t)]
        k.GetProcessAffinityMask(process, c.byref(p), c.byref(system))
        chosen = 0
        for bit in range(64):
            if p.value & (1 << bit) and chosen.bit_count() < cores:
                chosen |= 1 << bit
        k.SetProcessAffinityMask.argtypes = [w.HANDLE, c.c_size_t]
        evidence["affinity_applied"] = bool(k.SetProcessAffinityMask(process, chosen))

        class Basic(c.Structure):
            _fields_ = [("PerProcessUserTimeLimit", c.c_longlong), ("PerJobUserTimeLimit", c.c_longlong),
                        ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", c.c_size_t),
                        ("MaximumWorkingSetSize", c.c_size_t), ("ActiveProcessLimit", w.DWORD),
                        ("Affinity", c.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
        class IO(c.Structure):
            _fields_ = [(name, c.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount",
                         "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(c.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO), ("ProcessMemoryLimit", c.c_size_t),
                        ("JobMemoryLimit", c.c_size_t), ("PeakProcessMemoryUsed", c.c_size_t), ("PeakJobMemoryUsed", c.c_size_t)]
        k.CreateJobObjectW.restype = w.HANDLE
        k.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        _JOB = k.CreateJobObjectW(None, None)
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        limits.ProcessMemoryLimit = memory_mb * 1024 * 1024
        evidence["memory_limit_applied"] = bool(k.SetInformationJobObject(_JOB, 9, c.byref(limits), c.sizeof(limits))
                                                  and k.AssignProcessToJobObject(_JOB, process))
        if not evidence["memory_limit_applied"]:
            raise RuntimeError(f"Cannot apply Windows memory limit: {c.get_last_error()}")
    else:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024**2, memory_mb * 1024**2))
        evidence["memory_limit_applied"] = True
        if hasattr(os, "sched_getaffinity"):
            os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:cores])
            evidence["affinity_applied"] = True
    return evidence
