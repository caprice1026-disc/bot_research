"""Install the pinned archive reader locally, without changing existing venvs.

Windows x64 / CPython: python setup_runtime.py
Downloads only the official PyPI wheel and DuckDB-signed httpfs extension.
"""
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import urllib.request
import zipfile


VERSION = "1.5.6"
ROOT = Path(__file__).resolve().parent / ".probe-deps"


def download(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as response:
        return response.read()


def main():
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("This local setup targets Windows x64; install duckdb==1.5.6 and its httpfs extension for your platform")
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    metadata = json.loads(download(f"https://pypi.org/pypi/duckdb/{VERSION}/json"))
    wheels = [item for item in metadata["urls"] if item["filename"].endswith(f"-{tag}-{tag}-win_amd64.whl")]
    if len(wheels) != 1:
        raise RuntimeError(f"No unique official Windows x64 wheel for {tag}")
    wheel = wheels[0]
    ROOT.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    try:
        import duckdb
        installed = duckdb.__version__ == VERSION
    except ImportError:
        installed = False
    if not installed:
        raw = download(wheel["url"])
        if hashlib.sha256(raw).hexdigest() != wheel["digests"]["sha256"]:
            raise RuntimeError("PyPI wheel SHA256 mismatch")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for member in archive.namelist():
                (ROOT / member).resolve().relative_to(ROOT.resolve())
            archive.extractall(ROOT)
        import duckdb
        if duckdb.__version__ != VERSION:
            raise RuntimeError("Another DuckDB version is loaded; run setup in a fresh Python process")
    extension_url = f"https://extensions.duckdb.org/v{VERSION}/windows_amd64/httpfs.duckdb_extension.gz"
    extension = ROOT / "extensions" / f"v{VERSION}" / "windows_amd64/httpfs.duckdb_extension"
    if not extension.exists():
        extension.parent.mkdir(parents=True, exist_ok=True)
        extension.write_bytes(gzip.decompress(download(extension_url)))
    con = duckdb.connect(config={"extension_directory": (ROOT / "extensions").as_posix()})
    # DuckDB verifies the official extension signature; unsigned loading is off.
    con.execute("LOAD httpfs")
    con.close()
    manifest = {"version": VERSION, "python": tag, "verified_at": datetime.now(timezone.utc).isoformat(),
                "wheel_url": wheel["url"], "wheel_sha256": wheel["digests"]["sha256"],
                "extension_url": extension_url, "extension_sha256": hashlib.sha256(extension.read_bytes()).hexdigest(),
                "extension_load_verified": True}
    (ROOT / "setup_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"DuckDB {VERSION} and signed httpfs ready in {ROOT}")


if __name__ == "__main__":
    main()
