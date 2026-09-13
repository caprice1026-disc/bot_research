"""Small hash-chain helpers for immutable research artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


class ArtifactError(ValueError):
    """Raised when an artifact or its lineage cannot be verified."""


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ArtifactError(f"cannot read artifact: {path}") from error
    return digest.hexdigest()


def manifest_path_for(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".manifest.json")


def load_artifact_manifest(path: Path) -> dict[str, Any]:
    manifest_path = manifest_path_for(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArtifactError(f"artifact manifest is required: {manifest_path}") from error
    if not isinstance(payload, dict):
        raise ArtifactError("artifact manifest must be an object")
    return payload


def verify_artifact(path: Path, *, expected_type: str | None = None) -> dict[str, Any]:
    manifest = load_artifact_manifest(path)
    expected_hash = manifest.get("content_sha256")
    if not isinstance(expected_hash, str) or expected_hash != sha256_path(path):
        raise ArtifactError(f"content SHA-256 mismatch: {path}")
    if expected_type is not None and expected_type not in {
        manifest.get("artifact_type"), manifest.get("mode"), manifest.get("type")
    }:
        raise ArtifactError(f"artifact type mismatch: expected {expected_type}")
    return manifest


def verify_parent_hash(manifest: Mapping[str, Any], name: str, path: Path) -> None:
    expected = manifest.get(name)
    if expected is None:
        raise ArtifactError(f"manifest is missing {name}")
    if expected != sha256_path(path):
        raise ArtifactError(f"{name} does not match {path}")


def canonical_config_sha256(config: Path | Mapping[str, Any]) -> str:
    if isinstance(config, Path):
        try:
            config = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ArtifactError("cannot read config for fingerprint") from error
    try:
        payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ArtifactError("config is not canonical JSON") from error
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_experiment_fingerprint(**values: Any) -> str:
    return canonical_config_sha256(values)
