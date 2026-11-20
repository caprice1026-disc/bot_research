"""Read public Parquet by coalesced row-group ranges, with verified local cache.

One sequential request per row group avoids hundreds of column-level requests.
Only candidate groups and footer metadata are downloaded, never a full dump.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import time
import uuid

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests
import certifi


class RangeFile(io.RawIOBase):
    def __init__(self, url, cache):
        if not url.startswith("https://dl.pendulumflow.com/v3/"):
            raise ValueError("Only the public v3 archive is accepted")
        self.url, self.position, self.segments = url, 0, []
        self.cache = Path(cache) / hashlib.sha256(url.encode()).hexdigest()[:20]
        self.cache.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        r = self.request(0, 7)
        self.length = int(r.headers["Content-Range"].split("/")[1])
        if r.content[:4] != b"PAR1":
            raise ValueError("Source is not Parquet")
        self.last_request = time.monotonic()

    def request(self, start, end):
        for attempt in range(4):
            response = self.session.get(self.url, headers={"User-Agent": "PolymarketWeatherResearch/1.0", "Range": f"bytes={start}-{end}"}, timeout=60, verify=certifi.where())
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                wait = max(5, min(60, int(response.headers.get("Retry-After", "10"))))
                print(f"Range response {response.status_code}; waiting {wait}s", flush=True)
                time.sleep(wait)
                continue
            response.raise_for_status()
            expected = f"bytes {start}-{end}/"
            if response.status_code != 206 or not response.headers.get("Content-Range", "").startswith(expected) or len(response.content) != end - start + 1:
                raise ValueError("Incomplete or mismatched HTTP range")
            return response
        raise AssertionError("unreachable")

    def fetch(self, start, size):
        for at, raw in self.segments:
            if at <= start and start + size <= at + len(raw):
                return raw[start - at:start - at + size]
        path = self.cache / f"{start}-{size}.bin"
        checksum = path.with_suffix(".sha256")
        if path.exists() and checksum.exists():
            raw = path.read_bytes()
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != checksum.read_text():
                raise ValueError("Cached HTTP range changed")
        else:
            time.sleep(max(0, .25 - (time.monotonic() - self.last_request)))
            raw = self.request(start, start + size - 1).content
            self.last_request = time.monotonic()
            temporary = path.with_suffix("." + uuid.uuid4().hex + ".part")
            temporary.write_bytes(raw)
            temporary.replace(path)
            checksum.write_text(hashlib.sha256(raw).hexdigest(), encoding="ascii")
        self.segments.append((start, raw))
        return raw

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.position

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.length + offset
        if not 0 <= self.position <= self.length:
            raise ValueError("Seek outside source")
        return self.position

    def read(self, size=-1):
        size = self.length - self.position if size < 0 else min(size, self.length - self.position)
        if size <= 0:
            return b""
        raw = self.fetch(self.position, size)
        self.position += size
        return raw

    def close(self):
        self.session.close()
        super().close()


COLUMNS = ["event_type", "timestamp_received", "timestamp", "market", "asset_id", "best_bid", "best_ask", "bids", "asks", "new_tick_size", "sequence", "price", "size", "side"]
EVENT_TYPES = ["book", "best_bid_ask", "price_change", "tick_size_change"]


def extract(url, conditions, start, end, destination, cache):
    conditions = [bytes.fromhex(c[2:]) for c in conditions]
    source = RangeFile(url, cache)
    groups_read, rows = 0, 0
    try:
        parquet = pq.ParquetFile(source)
        schema = pa.schema([parquet.schema_arrow.field(name) for name in COLUMNS])
        market_column = next(i for i in range(parquet.metadata.num_columns) if parquet.metadata.schema.column(i).path == "market")
        time_column = next(i for i in range(parquet.metadata.num_columns) if parquet.metadata.schema.column(i).path == "timestamp_received")
        event_column = next(i for i in range(parquet.metadata.num_columns) if parquet.metadata.schema.column(i).path == "event_type")
        with pq.ParquetWriter(destination, schema, compression="zstd") as writer:
            for index in range(parquet.metadata.num_row_groups):
                group = parquet.metadata.row_group(index)
                stats = group.column(market_column).statistics
                if stats is not None and stats.has_min_max and not any(stats.min <= c <= stats.max for c in conditions):
                    continue
                times = group.column(time_column).statistics
                if times is not None and times.has_min_max and times.min.tzinfo is not None and (times.min.timestamp() >= end or times.max.timestamp() < start):
                    continue
                kinds = group.column(event_column).statistics
                if kinds is not None and kinds.has_min_max and kinds.min == kinds.max and kinds.min not in EVENT_TYPES:
                    continue
                offsets = []
                for c in range(group.num_columns):
                    col = group.column(c)
                    at = min(o for o in (col.dictionary_page_offset, col.data_page_offset) if o is not None and o >= 0)
                    offsets.append((at, at + col.total_compressed_size))
                first, last = min(x[0] for x in offsets), max(x[1] for x in offsets)
                source.fetch(first, last - first)
                table = parquet.read_row_group(index, columns=COLUMNS, use_threads=False)
                mask = pc.and_(pc.is_in(table["market"], value_set=pa.array(conditions, type=pa.binary())),
                               pc.is_in(table["event_type"], value_set=pa.array(EVENT_TYPES)))
                times = pc.cast(table["timestamp_received"], pa.int64())
                mask = pc.and_(mask, pc.and_(pc.greater_equal(times, start * 1_000_000), pc.less(times, end * 1_000_000)))
                selected = table.filter(mask)
                writer.write_table(selected)
                groups_read += 1
                rows += selected.num_rows
                # Retain only footer buffers between groups; all range bytes
                # remain on disk with hashes, including across city queries.
                source.segments = [(at, raw) for at, raw in source.segments if at >= source.length - 10_000_000]
        return {"rows": rows, "row_groups_read": groups_read, "source_bytes": source.length,
                "range_cache_sha256_verified": True, "reader": "PyArrow, one coalesced request per candidate row group"}
    finally:
        source.close()
