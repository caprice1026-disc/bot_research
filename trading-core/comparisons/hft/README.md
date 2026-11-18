# HftBacktest maker-entry-latency sensitivity

Executed with PyPI HftBacktest 2.4.4, CPython 3.12.16 on Windows x86-64,
in a separate environment from the Nautilus comparison. `result.json` records
actual engine output, version, configuration and shared input-row hash.

One factor changes: entry latency 10 ms versus 500 ms. Response latency is zero,
queue model is risk-adverse, exchange model has no partial fills, fees are zero,
linear multiplier is 1, tick size and lot size are 1. Exchange and local feed
timestamps match (zero feed latency), in nanoseconds.

At t=0 the synthetic bid is 100 x 1 and ask 101 x 1. At t=100 ms the same strategy
submits a post-only buy 1 @ 100. At t=300 ms one seller trade of size 2 at 100
clears the queue in front of the fast order. A following depth update at
300000001 ns restores displayed bid quantity 1, without another trade. No later
executions occur. Observation ends at t=2.1 s, before the final t=3 s sentinel.

- 10 ms entry latency: arrives at t=110 ms; FILLED 1 @ 100 at t=300 ms.
- 500 ms entry latency: arrives at t=600 ms; NEW, filled quantity 0 at t=2.1 s.

The slow order is still resting, not canceled or rejected. Zero fills is the
expected latency result, not evidence of profitability or a failed data run.
Both cases were run twice with identical normalized output. Assertions inside
the script check the timing, both expected statuses and repeatability. Maker
classification follows the explicit GTX post-only instruction, not an extra
maker field exposed by the Python Order object.

## Reproduce from repository root (PowerShell)

```powershell
py -3.12 -m venv trading-core/comparisons/hft/.venv
trading-core/comparisons/hft/.venv/Scripts/python.exe -m pip install -r trading-core/comparisons/hft/requirements.lock
trading-core/comparisons/hft/.venv/Scripts/python.exe trading-core/comparisons/hft/latency_sensitivity.py --write
```

The lock pins resolved package versions, not artifact hashes. The synthetic
fixture is deliberately small and transparent; it is not historical market data
or a calibrated model. We did not validate alternate queue models, partial fills,
market impact, profitability or live execution. Recorded-market replay does not
model how our order would alter the market. No orders leave this offline process.
The Nautilus fixture is a separate accounting-parity test, not an identical
execution-sensitivity strategy. Comparing their profits would be meaningless.

Official references:
- https://hftbacktest.readthedocs.io/en/latest/reference/initialization.html
- https://hftbacktest.readthedocs.io/en/py-v2.3.0/latency_models.html
- https://pypi.org/project/hftbacktest/2.4.4/

Actual installed API was checked; this version uses constant_order_latency.
