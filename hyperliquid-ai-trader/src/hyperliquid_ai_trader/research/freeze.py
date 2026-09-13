"""Immutable experiment freeze records."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from .artifacts import canonical_config_sha256, sha256_path


class FreezeError(ValueError):
    """Raised when a frozen experiment would drift."""


def build_freeze_manifest(
    *,
    config: Mapping[str, Any],
    code_commit_sha: str,
    artifact_paths: Mapping[str, Path | str] | None = None,
    prompts: Mapping[str, Path | str] | None = None,
    dates: Mapping[str, Any] | None = None,
    budget_usd: str | None = None,
    rules: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    artifacts = {name: sha256_path(Path(path)) for name, path in (artifact_paths or {}).items()}
    prompt_hashes = {name: sha256_path(Path(path)) for name, path in (prompts or {}).items()}
    return {
        "schema_version": 1,
        "experiment_id": config.get("experiment_id"),
        "config_sha256": canonical_config_sha256(config),
        "code_commit_sha": code_commit_sha,
        "artifact_sha256": artifacts,
        "prompt_sha256": prompt_hashes,
        "dates": deepcopy(dict(dates or {})),
        "budget_usd": budget_usd,
        "rules": deepcopy(dict(rules or {})),
    }


def freeze_fingerprint(manifest: Mapping[str, Any]) -> str:
    return canonical_config_sha256(manifest)


def verify_freeze(frozen: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    """Reject any response-affecting freeze drift, including code or dates."""

    keys = ("experiment_id", "config_sha256", "code_commit_sha", "artifact_sha256", "prompt_sha256", "dates", "budget_usd", "rules")
    for key in keys:
        if frozen.get(key) != current.get(key):
            raise FreezeError(f"freeze drift detected in {key}")


def write_freeze_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(manifest), ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
