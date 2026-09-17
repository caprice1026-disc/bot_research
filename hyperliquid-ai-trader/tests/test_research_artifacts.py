import json
from pathlib import Path

import pytest

from hyperliquid_ai_trader.research import cli
from hyperliquid_ai_trader.research.artifacts import (
    ArtifactError,
    canonical_config_sha256,
    load_artifact_manifest,
    manifest_path_for,
    sha256_path,
    verify_artifact,
)


def test_artifact_manifest_hash_is_verified(tmp_path: Path) -> None:
    artifact = tmp_path / "points.jsonl"
    artifact.write_text("{}\n", encoding="utf-8")
    manifest = manifest_path_for(artifact)
    manifest.write_text(
        json.dumps({"schema_version": 1, "mode": "points", "content_sha256": sha256_path(artifact)}) + "\n",
        encoding="utf-8",
    )
    assert verify_artifact(artifact, expected_type="points")["mode"] == "points"
    artifact.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(ArtifactError, match="content SHA-256"):
        verify_artifact(artifact, expected_type="points")


def test_config_hash_changes_with_content(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"b":2,"a":1}\n', encoding="utf-8")
    first = canonical_config_sha256(config)
    config.write_text('{"b":3,"a":1}\n', encoding="utf-8")
    assert canonical_config_sha256(config) != first


def test_load_artifact_manifest_requires_existing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError, match="manifest"):
        load_artifact_manifest(tmp_path / "missing.jsonl")


def test_cli_reads_an_optional_artifact_manifest_once(tmp_path: Path) -> None:
    artifact = tmp_path / "points.jsonl"
    artifact.write_text("{}\n", encoding="utf-8")
    manifest_path_for(artifact).write_text(
        json.dumps({"schema_version": 1, "content_sha256": sha256_path(artifact)}) + "\n",
        encoding="utf-8",
    )

    assert cli._read_manifest_if_present(artifact)["content_sha256"] == sha256_path(artifact)
