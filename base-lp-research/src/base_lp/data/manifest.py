from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from base_lp.schemas import DatasetManifest, PoolMetadata


def build_manifest(
    status: str,
    source: str,
    pool: PoolMetadata,
    block_start: int,
    block_end: int,
    row_counts: dict[str, int],
    checksums: dict[str, str],
    errors: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> DatasetManifest:
    return DatasetManifest(
        status=status,
        source=source,
        chain_id=pool.chain_id,
        pool=pool,
        block_start=block_start,
        block_end=block_end,
        row_counts=row_counts,
        checksums=checksums,
        errors=errors or [],
        metadata=metadata or {},
    )


def write_manifest(path: Path, manifest: DatasetManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
