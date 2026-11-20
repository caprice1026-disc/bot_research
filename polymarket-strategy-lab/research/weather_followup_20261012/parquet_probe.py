"""Inspect only a public Parquet footer using bounded verified range reads."""
import io
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from weather_followup import requests, certifi, read_json, DATA
import pyarrow.parquet as pq


class Remote(io.RawIOBase):
    def __init__(self, url):
        self.url, self.position = url, 0
        self.session = requests.Session()
        r = self.get(0, 7)
        self.length = int(r.headers["Content-Range"].split("/")[1])

    def get(self, start, end):
        r = self.session.get(self.url, headers={"User-Agent": "Mozilla/5.0", "Range": f"bytes={start}-{end}"}, timeout=45, verify=certifi.where())
        r.raise_for_status()
        if r.status_code != 206:
            raise ValueError("Server did not honor range request")
        return r

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.position
    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.length + offset
        return self.position
    def read(self, size=-1):
        size = self.length - self.position if size < 0 else min(size, self.length - self.position)
        if size <= 0: return b""
        raw = self.get(self.position, self.position + size - 1).content
        if len(raw) != size: raise ValueError("Truncated HTTP range")
        self.position += len(raw)
        return raw


if __name__ == "__main__":
    event = read_json(DATA / "chicago/events/2026-10-04.json")["body"][0]
    conditions = [bytes.fromhex(m["conditionId"][2:]) for m in event["markets"]]
    url = "https://dl.pendulumflow.com/v3/2026-10-04/14/2026-10-04T14.parquet"
    source = Remote(url)
    metadata = pq.ParquetFile(source).metadata
    market_index = next(i for i in range(metadata.num_columns) if metadata.schema.column(i).path == "market")
    matched = []
    for i in range(metadata.num_row_groups):
        group = metadata.row_group(i)
        stats = group.column(market_index).statistics
        if stats is None or any(stats.min <= c <= stats.max for c in conditions):
            matched.append(i)
    print({"size": source.length, "rows": metadata.num_rows, "groups": metadata.num_row_groups, "candidate_groups": len(matched),
           "candidate_compressed_bytes": sum(metadata.row_group(i).column(j).total_compressed_size for i in matched for j in range(metadata.num_columns))})
    print(metadata.schema)
