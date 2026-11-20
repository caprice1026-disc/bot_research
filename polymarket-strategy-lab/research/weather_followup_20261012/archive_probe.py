"""Bounded metadata/read diagnostics for the public range archive."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from weather_followup import deep


if __name__ == "__main__":
    con = deep.connect()
    print(con.execute("SELECT name,value FROM duckdb_settings() WHERE name LIKE '%http%' OR name LIKE '%prefetch%' OR name LIKE '%user_agent%'").fetchall(), flush=True)
    con.execute("SET threads=1")
    con.execute("SET disable_parquet_prefetching=true")
    con.execute("SET http_retries=0")
    print(con.execute("SELECT event_type FROM read_parquet(?) LIMIT 1", ["https://dl.pendulumflow.com/v3/2026-10-10/15/2026-10-10T15.parquet"]).fetchone(), flush=True)
