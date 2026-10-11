"""Portable plot wheels from PyPI with SHA verification; leaves existing venv intact."""
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from setup_runtime import download

TARGET = ROOT / ".plot-deps"
PACKAGES = {"matplotlib": "3.10.7", "contourpy": "1.3.3", "cycler": "0.12.1",
            "fonttools": "4.60.1", "kiwisolver": "1.4.9", "pillow": "11.3.0", "pyparsing": "3.2.5"}


def main():
    records = []
    TARGET.mkdir(exist_ok=True)
    for name, version in PACKAGES.items():
        metadata = json.loads(download(f"https://pypi.org/pypi/{name}/{version}/json"))
        tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
        wheels = [w for w in metadata["urls"] if w["filename"].endswith((f"-{tag}-{tag}-win_amd64.whl", "-py3-none-any.whl", "-py2.py3-none-any.whl"))]
        if not wheels:
            raise ValueError("No compatible wheel: " + name)
        wheel = next((w for w in wheels if "none-any" in w["filename"]), wheels[0])
        raw = download(wheel["url"])
        if hashlib.sha256(raw).hexdigest() != wheel["digests"]["sha256"]:
            raise ValueError("PyPI wheel SHA mismatch")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for item in archive.namelist():
                (TARGET / item).resolve().relative_to(TARGET.resolve())
            archive.extractall(TARGET)
        records.append({"package": name, "version": version, "wheel_url": wheel["url"], "wheel_sha256": wheel["digests"]["sha256"]})
        print(name, version, "verified", flush=True)
    (TARGET / "setup_manifest.json").write_text(json.dumps(records, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
