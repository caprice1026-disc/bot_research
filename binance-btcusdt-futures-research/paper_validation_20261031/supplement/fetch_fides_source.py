"""Fetch and inventory public arXiv source without executing its contents."""
from pathlib import Path
import sys
import tarfile

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from data_inputs import DATA,OUT,download,write_json,sha256_file

path=DATA/"fides-2610.36476v1.tar.gz"
url="https://arxiv.org/src/2610.36476v1"
if not path.exists():path.write_bytes(download(url))
target=DATA/"fides_source"
target.mkdir(exist_ok=True)
with tarfile.open(path,"r:*") as archive:
    members=archive.getmembers()
    for member in members:
        resolved=(target/member.name).resolve()
        if target.resolve() not in resolved.parents or member.issym() or member.islnk():
            raise ValueError("unsafe archive entry")
    archive.extractall(target)
    names=[m.name for m in members]
write_json(OUT/"fides_source_manifest.json",dict(url=url,sha256=sha256_file(path),members=names))
print("\n".join(names))
