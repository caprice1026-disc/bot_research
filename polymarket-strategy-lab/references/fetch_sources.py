"""Download pinned public source archives for inspection; never execute them."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import urllib.request
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
REPOS = [
    ("evan-kolberg/prediction-market-backtesting", "v4.1-alpha", "c76e77af00ef53472a9da8f66dae7fdd2d3e5928"),
    ("alsk1992/CloddsBot", "main", "c930628c47a4a745f497e29035a3b64622f97cc7"),
    ("ent0n29/polybot", "main", "51e5362f026adf54162fc1256f453241d93680e6"),
    ("yangyuan-zhen/PolyWeather", "main", "43e658bbf6286e3cccdf4f09ce478ec90b3fb960"),
]


def main():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    records = []
    for repo, branch, commit in REPOS:
        name = repo.split("/")[1]
        archive = ROOT / "archives" / f"{name}-{commit}.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://codeload.github.com/{repo}/zip/{commit}"
        if not archive.exists():
            with opener.open(url, timeout=90) as response:
                archive.write_bytes(response.read())
        target = ROOT / "sources" / name
        target.mkdir(parents=True, exist_ok=True)
        files = []
        with ZipFile(archive) as source:
            for item in source.infolist():
                if item.is_dir():
                    continue
                relative = Path(*Path(item.filename).parts[1:])
                dest = (target / relative).resolve()
                if not dest.is_relative_to(target.resolve()) or ".git" in relative.parts:
                    raise ValueError(f"Unsafe archive member: {item.filename}")
                content = source.read(item)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
                files.append({"path": relative.as_posix(), "bytes": len(content), "sha256": sha256(content).hexdigest()})
        records.append({"repository": repo, "default_branch_at_inspection": branch, "commit": commit,
                        "archive_url": url, "archive_sha256": sha256(archive.read_bytes()).hexdigest(),
                        "local_source": target.relative_to(ROOT).as_posix(), "files": files})
        print(f"{repo}: {len(files)} files, commit {commit}", flush=True)
    manifest = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "executed_upstream_code": False,
                "duplicate_input_url": "https://github.com/alsk1992/CloddsBot", "repositories": records}
    (ROOT / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
