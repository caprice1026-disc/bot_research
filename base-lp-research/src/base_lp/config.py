from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ResearchConfig:
    chain_id: int
    chain_name: str
    rpc_env: str
    token_pair: str
    factory: str
    token0: str
    token1: str
    fee_tier: int
    counterfactual_mode: str
    fee_precision: str
    execution_latency_blocks: int
    strategy_name: str
    half_width_pct: float
    trigger_ratio: float
    cooldown_seconds: int
    safety_margin_usd: float
    capital_usd: float
    pilot_start_utc: str
    pilot_end_utc: str
    cost_model: dict[str, Any]
    validation: dict[str, Any]
    source_path: str
    collection_chunk_size: int = 500
    request_interval_seconds: float = 0.25
    rpc_timeout_seconds: float = 20.0
    rpc_max_retries: int = 2
    max_runtime_seconds: float = 90.0
    collection_source: str = "json-rpc"
    dune_api_env: str = "DUNE_API_KEY"
    dune_poll_interval_seconds: float = 5.0


def load_config(path: Path) -> ResearchConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        return ResearchConfig(
            chain_id=int(raw["chain"]["chain_id"]),
            chain_name=str(raw["chain"]["name"]),
            rpc_env=str(raw["chain"]["rpc_env"]),
            token_pair=str(raw["pool"]["token_pair"]),
            factory=str(raw["pool"]["factory"]),
            token0=str(raw["pool"]["token0"]),
            token1=str(raw["pool"]["token1"]),
            fee_tier=int(raw["pool"]["fee_tier"]),
            counterfactual_mode=str(raw["backtest"]["counterfactual_mode"]),
            fee_precision=str(raw["backtest"]["fee_precision"]),
            execution_latency_blocks=int(raw["backtest"]["execution_latency_blocks"]),
            strategy_name=str(raw["strategy"]["name"]),
            half_width_pct=float(raw["strategy"]["half_width_pct"]),
            trigger_ratio=float(raw["strategy"]["trigger_ratio"]),
            cooldown_seconds=int(raw["strategy"]["cooldown_seconds"]),
            safety_margin_usd=float(raw["strategy"]["safety_margin_usd"]),
            capital_usd=float(raw["capital_usd"]),
            pilot_start_utc=str(raw["pilot"]["start_utc"]),
            pilot_end_utc=str(raw["pilot"]["end_utc"]),
            cost_model=dict(raw["cost_model"]),
            validation=dict(raw["validation"]),
            source_path=str(path),
            collection_chunk_size=int(raw.get("collection", {}).get("chunk_size", 500)),
            request_interval_seconds=float(raw.get("collection", {}).get("request_interval_seconds", 0.25)),
            rpc_timeout_seconds=float(raw.get("collection", {}).get("rpc_timeout_seconds", 20.0)),
            rpc_max_retries=int(raw.get("collection", {}).get("rpc_max_retries", 2)),
            max_runtime_seconds=float(raw.get("collection", {}).get("max_runtime_seconds", 90.0)),
            collection_source=str(raw.get("collection", {}).get("source", "json-rpc")),
            dune_api_env=str(raw.get("collection", {}).get("dune_api_env", "DUNE_API_KEY")),
            dune_poll_interval_seconds=float(raw.get("collection", {}).get("dune_poll_interval_seconds", 5.0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid research config: {path}") from exc
