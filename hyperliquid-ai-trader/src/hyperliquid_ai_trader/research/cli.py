"""Small, offline-only command line surface for research configuration."""

from __future__ import annotations

import argparse
from decimal import Decimal, DecimalException
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Sequence

from .baseline import run_baseline
from .artifacts import ArtifactError, load_artifact_manifest, sha256_path, verify_artifact
from .batch import BatchError, BatchManager
from .binance import (
    BINANCE_USDM_VENUE,
    FundingDataError,
    read_binance_usdm_1m_csv,
    read_binance_usdm_funding_csv,
)
from .collector import create_hyperliquid_mainnet_public_collector
from .config import ResearchConfigError, load_research_config
from .data import (
    RESEARCH_DECISION_INTERVAL_MS,
    ResearchDataError,
    read_normalized_candles_jsonl,
    validate_candles_match_market,
    write_normalized_candles_jsonl,
)
from .evaluation import (
    ResearchEvaluationError,
    evaluate_validated_decisions,
    read_validated_decisions_jsonl,
    write_point_evaluation_jsonl,
)
from .points import (
    PointSelectionError,
    build_point_candidates,
    candidate_set_sha256,
    read_point_selection_jsonl,
    select_research_points,
    write_point_selection_jsonl,
)
from .preparation import (
    ResearchPreparationError,
    prepare_point_requests,
    read_strategy_asset,
    read_text_asset,
    write_prepared_requests_jsonl,
)
from .request_identity import ModelRequestError
from .request_identity import PreparedModelRequest
from .responses import (
    ResearchResponseError,
    read_model_responses_jsonl,
    read_prepared_requests_jsonl,
    validate_model_responses,
    write_validated_decisions_jsonl,
)
from .simulator import SimulationError
from .store import ResearchStore
from .freeze import build_freeze_manifest, freeze_fingerprint, write_freeze_manifest
from .forward import ForwardError
from .replay import run_replay
from .risk import ResearchRiskEngine
from .testnet_audit import AuditError, actual_stop_risk, max_hold_deadline


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-config", help="validate a public research JSON config")
    validate.add_argument("--config", type=Path, required=True)
    baseline = commands.add_parser("baseline", help="replay one fixed rule from normalized candles")
    baseline.add_argument("--config", type=Path, required=True)
    baseline.add_argument("--candles", type=Path, required=True)
    baseline.add_argument(
        "--baseline",
        choices=("always_abstain", "momentum", "mean_reversion"),
        required=True,
    )
    baseline.add_argument("--funding-csv", type=Path)
    import_binance = commands.add_parser(
        "import-binance-csv", help="convert a verified Binance USD-M 1m CSV to normalized JSONL"
    )
    import_binance.add_argument("--config", type=Path, required=True)
    import_binance.add_argument("--input", type=Path, required=True)
    import_binance.add_argument("--delivery-delay-ms", type=int, default=0)
    import_binance.add_argument("--output", type=Path, required=True)
    collect = commands.add_parser("collect", help="collect one public Hyperliquid 1m snapshot")
    collect.add_argument("--config", type=Path, required=True)
    collect.add_argument("--start-ms", type=int, required=True)
    collect.add_argument("--end-ms", type=int, required=True)
    collect.add_argument("--output", type=Path, required=True)
    points = commands.add_parser("points", help="select deterministic offline research points")
    points.add_argument("--config", type=Path, required=True)
    points.add_argument("--candles", type=Path, required=True)
    points.add_argument("--count", type=int, required=True)
    points.add_argument("--seed", type=int, default=42)
    points.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("prepare-requests", help="prepare offline model requests without submission")
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--points", type=Path, required=True)
    prepare.add_argument("--constitution", type=Path, default=_PROJECT_ROOT / "prompts" / "research" / "constitution.md")
    prepare.add_argument("--instruction", type=Path, default=_PROJECT_ROOT / "prompts" / "research" / "trader_v002.md")
    prepare.add_argument("--strategy", type=Path, default=_PROJECT_ROOT / "configs" / "research" / "initial_strategy.json")
    prepare.add_argument("--trial-prefix", default="trader-v001")
    prepare.add_argument("--temperature", default=Decimal("0"), type=Decimal)
    prepare.add_argument("--thinking", default="none")
    prepare.add_argument("--max-output-tokens", default=500, type=int)
    prepare.add_argument("--output", type=Path, required=True)
    responses = commands.add_parser("validate-responses", help="validate saved normalized model responses")
    responses.add_argument("--config", type=Path, required=True)
    responses.add_argument("--requests", type=Path, required=True)
    responses.add_argument("--responses", type=Path, required=True)
    responses.add_argument("--output", type=Path, required=True)
    evaluate = commands.add_parser("evaluate-decisions", help="simulate validated decisions at selected points")
    evaluate.add_argument("--config", type=Path, required=True)
    evaluate.add_argument("--candles", type=Path, required=True)
    evaluate.add_argument("--points", type=Path, required=True)
    evaluate.add_argument("--decisions", type=Path, required=True)
    evaluate.add_argument("--funding-csv", type=Path)
    evaluate.add_argument("--output", type=Path, required=True)
    replay = commands.add_parser("replay", help="run sequential account-aware research replay")
    replay.add_argument("--config", type=Path, required=True)
    replay.add_argument("--candles", type=Path, required=True)
    replay.add_argument("--decisions", type=Path, required=True)
    replay.add_argument("--funding-csv", type=Path)
    replay.add_argument("--days", type=int, default=3)
    replay.add_argument("--output", type=Path, required=True)
    freeze = commands.add_parser("freeze", help="write an immutable experiment freeze manifest")
    freeze.add_argument("--config", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--code-commit-sha")
    freeze.add_argument("--candles", type=Path)
    freeze.add_argument("--constitution", type=Path, default=_PROJECT_ROOT / "prompts" / "research" / "constitution.md")
    freeze.add_argument("--instruction", type=Path, default=_PROJECT_ROOT / "prompts" / "research" / "trader_v002.md")
    audit = commands.add_parser("audit", help="run offline Testnet execution audit calculations")
    audit.add_argument("--mode", choices=("fixture", "frozen-model"), default="fixture")
    audit.add_argument("--entry-price", type=Decimal, required=True)
    audit.add_argument("--stop-price", type=Decimal, required=True)
    audit.add_argument("--filled-at-ms", type=int, required=True)
    audit.add_argument("--max-hold-ms", type=int, required=True)
    audit.add_argument("--freeze", type=Path)
    batch = commands.add_parser("batch", help="submit or sync provider-neutral Batch ledger state")
    batch.add_argument("--config", type=Path, required=True)
    batch.add_argument("--action", choices=("submit", "sync"), required=True)
    batch.add_argument("--requests", type=Path, required=True)
    batch.add_argument("--store", type=Path, required=True)
    batch.add_argument("--results", type=Path, help="saved provider result JSON for sync")
    return parser


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _manifest_path(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".manifest.json")


def _write_artifact_manifest(*, output: Path, payload: dict[str, object]) -> Path:
    manifest_path = _manifest_path(output)
    manifest = {
        "schema_version": 1,
        "artifact_type": payload.get("artifact_type", payload.get("mode", "artifact")),
        **payload,
        "content_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    temporary = manifest_path.with_name(f"{manifest_path.name}.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return manifest_path


def _verify_optional_artifact(path: Path) -> dict[str, object] | None:
    manifest_path = _manifest_path(path)
    if not manifest_path.exists():
        return None
    return verify_artifact(path)


def _read_manifest_if_present(path: Path) -> dict[str, object] | None:
    manifest_path = _manifest_path(path)
    return load_artifact_manifest(manifest_path) if manifest_path.exists() else None


class _SavedBatchProvider:
    def __init__(self, results: dict[str, list[dict[str, object]]] | None = None) -> None:
        self.results = results or {}

    def submit(self, payloads: list[str]) -> str:
        raise BatchError("batch submit requires an explicitly configured provider adapter")

    def sync(self, provider_job_id: str) -> list[dict[str, object]]:
        return self.results.get(provider_job_id, [])


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_research_config(args.config) if hasattr(args, "config") else None
        if args.command == "validate-config":
            assert config is not None
            print(json.dumps(config.public_summary(), ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "collect":
            assert config is not None
            if config.market_venue != "hyperliquid_mainnet_public":
                raise ResearchDataError("collect currently supports hyperliquid_mainnet_public only")
            received_at_ms = _now_ms()
            collector = create_hyperliquid_mainnet_public_collector(coin=config.symbol)
            candles = collector.snapshot(
                start_ms=args.start_ms,
                end_ms=args.end_ms,
                received_at_ms=received_at_ms,
                delivery_delay_ms=0,
            )
            if not candles:
                print(
                    json.dumps(
                        {
                            "status": "insufficient_data",
                            "reason": "no_confirmed_candles",
                            "candle_count": 0,
                            "candles_path": str(args.output),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 3
            write_normalized_candles_jsonl(args.output, candles)
            manifest_path = _write_artifact_manifest(
                output=args.output,
                payload={
                    "source": {
                        "venue": config.market_venue,
                        "symbol": config.symbol,
                        "interval": "1m",
                        "requested_start_ms": args.start_ms,
                        "requested_end_ms": args.end_ms,
                        "received_at_ms": received_at_ms,
                    },
                    "candle_count": len(candles),
                },
            )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "candle_count": len(candles),
                        "candles_path": str(args.output),
                        "manifest_path": str(manifest_path),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "import-binance-csv":
            assert config is not None
            if config.market_venue != BINANCE_USDM_VENUE:
                raise ResearchDataError("import-binance-csv requires market.venue=binance_usdm_public")
            candles = read_binance_usdm_1m_csv(
                args.input, delivery_delay_ms=args.delivery_delay_ms
            )
            validate_candles_match_market(
                candles, venue=config.market_venue, symbol=config.symbol
            )
            write_normalized_candles_jsonl(args.output, candles)
            manifest_path = _write_artifact_manifest(
                output=args.output,
                payload={
                    "mode": "import_binance_usdm_1m",
                    "experiment_id": config.experiment_id,
                    "source_csv_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
                    "venue": config.market_venue,
                    "symbol": config.symbol,
                    "delivery_delay_ms": args.delivery_delay_ms,
                    "candle_count": len(candles),
                    "first_open_time_ms": candles[0].open_time_ms,
                    "end_exclusive_ms": candles[-1].close_exclusive_ms,
                },
            )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "candle_count": len(candles),
                        "candles_path": str(args.output),
                        "manifest_path": str(manifest_path),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "points":
            assert config is not None
            _verify_optional_artifact(args.candles)
            candles = read_normalized_candles_jsonl(args.candles)
            validate_candles_match_market(
                candles, venue=config.market_venue, symbol=config.symbol
            )
            candidates = build_point_candidates(candles)
            try:
                selection = select_research_points(
                    candidates,
                    count=args.count,
                    seed=args.seed,
                )
            except PointSelectionError as error:
                if str(error).startswith("only "):
                    print(
                        json.dumps(
                            {
                                "status": "insufficient_data",
                                "candidate_count": len(candidates),
                                "requested_count": args.count,
                                "reason": "insufficient_point_candidates",
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    )
                    return 3
                raise
            write_point_selection_jsonl(args.output, selection)
            manifest_path = _write_artifact_manifest(
                output=args.output,
                payload={
                    "experiment_id": config.experiment_id,
                    "feature_set": config.feature_set,
                    "seed": args.seed,
                    "decision_interval_ms": RESEARCH_DECISION_INTERVAL_MS,
                    "point_count": len(selection.points),
                    "candidate_count": len(candidates),
                    "candidate_set_sha256": candidate_set_sha256(candidates),
                    "candidate_stratum_counts": selection.candidate_stratum_counts,
                    "selected_stratum_counts": selection.stratum_counts,
                    "stratification": {
                        "return_5m": "positive_vs_non_positive",
                        "realized_vol_30m_median": selection.realized_vol_30m_median,
                        "volume_zscore": "nonnegative_vs_negative",
                    },
                    "source_candles_sha256": hashlib.sha256(args.candles.read_bytes()).hexdigest(),
                },
            )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "point_count": len(selection.points),
                        "points_path": str(args.output),
                        "manifest_path": str(manifest_path),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "prepare-requests":
            assert config is not None
            _verify_optional_artifact(args.points)
            points = read_point_selection_jsonl(args.points)
            constitution = read_text_asset(args.constitution, name="constitution")
            instruction = read_text_asset(args.instruction, name="instruction")
            strategy = read_strategy_asset(args.strategy)
            requests = prepare_point_requests(
                config=config,
                points=points,
                constitution=constitution,
                instruction=instruction,
                strategy=strategy,
                trial_prefix=args.trial_prefix,
                temperature=args.temperature,
                thinking=args.thinking,
                max_output_tokens=args.max_output_tokens,
            )
            write_prepared_requests_jsonl(args.output, requests)
            manifest_path = _write_artifact_manifest(
                output=args.output,
                payload={
                    "mode": "prepare_only",
                    "experiment_id": config.experiment_id,
                    "request_count": len(requests),
                    "requested_model": config.trader_model,
                    "temperature": format(args.temperature, "f"),
                    "thinking": args.thinking,
                    "max_output_tokens": args.max_output_tokens,
                    "source_points_sha256": hashlib.sha256(args.points.read_bytes()).hexdigest(),
                    "constitution_sha256": hashlib.sha256(constitution.encode("utf-8")).hexdigest(),
                    "instruction_sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
                    "strategy_sha256": hashlib.sha256(
                        json.dumps(strategy, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
                    ).hexdigest(),
                },
            )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "request_count": len(requests),
                        "requests_path": str(args.output),
                        "manifest_path": str(manifest_path),
                        "submission_performed": False,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "validate-responses":
            assert config is not None
            _verify_optional_artifact(args.requests)
            requests = read_prepared_requests_jsonl(args.requests)
            responses = read_model_responses_jsonl(args.responses)
            decisions = validate_model_responses(
                config=config,
                requests=requests,
                responses=responses,
            )
            write_validated_decisions_jsonl(args.output, decisions)
            manifest_path = _write_artifact_manifest(
                output=args.output,
                payload={
                    "mode": "validate_responses",
                    "experiment_id": config.experiment_id,
                    "decision_count": len(decisions),
                    "source_requests_sha256": hashlib.sha256(args.requests.read_bytes()).hexdigest(),
                    "source_responses_sha256": hashlib.sha256(args.responses.read_bytes()).hexdigest(),
                },
            )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "decision_count": len(decisions),
                        "decisions_path": str(args.output),
                        "manifest_path": str(manifest_path),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "batch":
            assert config is not None
            _verify_optional_artifact(args.requests)
            request_records = read_prepared_requests_jsonl(args.requests)
            requests = [
                PreparedModelRequest(
                    request_id=row.request_id,
                    trial_id=row.trial_id,
                    request_hash=row.request_hash,
                    requested_model=row.requested_model,
                    canonical_payload=row.canonical_payload,
                )
                for row in request_records
            ]
            with ResearchStore(args.store) as store:
                existing = store.connection.execute("SELECT 1 FROM experiments WHERE experiment_id=?", (config.experiment_id,)).fetchone()
                if existing is None:
                    store.create_experiment(config.experiment_id, {"experiment_id": config.experiment_id}, _now_ms())
                manager = BatchManager()
                if args.action == "submit":
                    result = manager.submit(config=config, requests=requests, store=store, provider=_SavedBatchProvider(), now_ms=_now_ms())
                else:
                    if args.results is None:
                        raise BatchError("batch sync requires --results")
                    saved = json.loads(args.results.read_text(encoding="utf-8"))
                    if not isinstance(saved, dict):
                        raise BatchError("batch results must map provider job IDs to result arrays")
                    provider = _SavedBatchProvider(saved)
                    result = manager.sync(store=store, provider=provider, now_ms=_now_ms())
            print(json.dumps({"status": result.status, "submitted": result.submitted, "reused": result.reused, "unknown": result.unknown}, ensure_ascii=False, sort_keys=True))
            return 0 if result.status in {"submitted", "completed", "reused"} else 3
        if args.command == "evaluate-decisions":
            assert config is not None
            _verify_optional_artifact(args.candles)
            _verify_optional_artifact(args.points)
            _verify_optional_artifact(args.decisions)
            points_manifest = _read_manifest_if_present(args.points)
            if points_manifest is not None and points_manifest.get("source_candles_sha256") != sha256_path(args.candles):
                raise ResearchEvaluationError("point artifact does not belong to the candle artifact")
            if args.funding_csv is not None and config.market_venue != BINANCE_USDM_VENUE:
                raise ResearchDataError("Binance funding CSV requires market.venue=binance_usdm_public")
            evaluation = evaluate_validated_decisions(
                candles=read_normalized_candles_jsonl(args.candles),
                points=read_point_selection_jsonl(args.points),
                decisions=read_validated_decisions_jsonl(args.decisions, config=config),
                config=config,
                verify_point_features=True,
                funding=(
                    read_binance_usdm_funding_csv(args.funding_csv)
                    if args.funding_csv is not None
                    else None
                ),
            )
            write_point_evaluation_jsonl(args.output, evaluation)
            manifest_path = _write_artifact_manifest(
                output=args.output,
                payload={
                    "mode": "evaluate_validated_decisions",
                    "experiment_id": config.experiment_id,
                    "source_candles_sha256": hashlib.sha256(args.candles.read_bytes()).hexdigest(),
                    "source_points_sha256": hashlib.sha256(args.points.read_bytes()).hexdigest(),
                    "source_decisions_sha256": hashlib.sha256(args.decisions.read_bytes()).hexdigest(),
                    **evaluation.public_summary(),
                },
            )
            print(
                json.dumps(
                    {
                        **evaluation.public_summary(),
                        "evaluation_path": str(args.output),
                        "manifest_path": str(manifest_path),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0 if evaluation.status == "ok" else 3
        if args.command == "replay":
            assert config is not None
            if args.funding_csv is not None and config.market_venue != BINANCE_USDM_VENUE:
                raise ResearchDataError("Binance funding CSV requires market.venue=binance_usdm_public")
            result = run_replay(
                candles=read_normalized_candles_jsonl(args.candles),
                decisions=read_validated_decisions_jsonl(args.decisions, config=config),
                config=config,
                risk=ResearchRiskEngine(
                    risk_per_trade_pct=config.risk_per_trade_pct,
                    max_daily_loss_pct=config.max_daily_loss_pct,
                    max_drawdown_pct=config.max_drawdown_pct,
                    max_position_notional_usd=config.max_position_notional_usd,
                    leverage=config.leverage,
                    min_notional_usd=config.min_notional_usd,
                ),
                funding=read_binance_usdm_funding_csv(args.funding_csv) if args.funding_csv else None,
                days=args.days,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in result.events),
                encoding="utf-8",
            )
            manifest_path = _write_artifact_manifest(
                output=args.output,
                payload={"mode": "sequential_replay", "experiment_id": config.experiment_id, **{
                    "episode_count": len(result.episodes), "event_count": len(result.events),
                    "source_candles_sha256": sha256_path(args.candles),
                    "source_decisions_sha256": sha256_path(args.decisions),
                    "replay_status": result.status,
                }},
            )
            print(json.dumps({"status": result.status, "episode_count": len(result.episodes), "output": str(args.output), "manifest_path": str(manifest_path)}, ensure_ascii=False, sort_keys=True))
            return 0 if result.status == "ok" else 3
        if args.command == "freeze":
            assert config is not None
            raw_config = json.loads(args.config.read_text(encoding="utf-8"))
            try:
                code_sha = args.code_commit_sha or subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=_PROJECT_ROOT, check=True, capture_output=True, text=True
                ).stdout.strip()
            except (OSError, subprocess.CalledProcessError) as error:
                raise ResearchConfigError("cannot determine code commit SHA") from error
            artifacts = {"candles": args.candles} if args.candles else {}
            manifest = build_freeze_manifest(
                config=raw_config,
                code_commit_sha=code_sha,
                artifact_paths=artifacts,
                prompts={"constitution": args.constitution, "instruction": args.instruction},
                budget_usd=format(config.budget_usd, "f"),
                rules={"feature_set": config.feature_set, "market": f"{config.market_venue}:{config.symbol}"},
            )
            write_freeze_manifest(args.output, manifest)
            print(json.dumps({"status": "ok", "freeze_path": str(args.output), "freeze_fingerprint": freeze_fingerprint(manifest)}, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "audit":
            if args.mode == "frozen-model" and args.freeze is None:
                raise AuditError("frozen-model audit requires --freeze")
            if args.mode == "frozen-model":
                try:
                    if not isinstance(json.loads(args.freeze.read_text(encoding="utf-8")), dict):
                        raise ValueError("freeze manifest must be an object")
                except (OSError, json.JSONDecodeError, ValueError) as error:
                    raise AuditError("invalid freeze manifest") from error
            print(json.dumps({"status": "ok", "mode": args.mode, "actual_stop_risk_pct": format(actual_stop_risk(side="long", entry_price=args.entry_price, stop_price=args.stop_price), "f"), "max_hold_deadline_ms": max_hold_deadline(filled_at_ms=args.filled_at_ms, max_hold_ms=args.max_hold_ms), "pnl_claim": "not_evaluated"}, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "baseline" and args.funding_csv is not None and config.market_venue != BINANCE_USDM_VENUE:
            raise ResearchDataError("Binance funding CSV requires market.venue=binance_usdm_public")
        result = run_baseline(
            candles=read_normalized_candles_jsonl(args.candles),
            baseline_name=args.baseline,
            execution_config=config.execution,
            initial_equity=config.initial_equity,
            risk=ResearchRiskEngine(
                risk_per_trade_pct=config.risk_per_trade_pct,
                max_daily_loss_pct=config.max_daily_loss_pct,
                max_drawdown_pct=config.max_drawdown_pct,
                max_position_notional_usd=config.max_position_notional_usd,
                leverage=config.leverage,
                min_notional_usd=config.min_notional_usd,
            ),
            market_venue=config.market_venue,
            symbol=config.symbol,
            funding=(
                read_binance_usdm_funding_csv(args.funding_csv)
                if args.funding_csv is not None
                else None
            ),
        )
    except (
        DecimalException,
        ModelRequestError,
        OSError,
        PointSelectionError,
        ResearchConfigError,
        ResearchDataError,
        ResearchEvaluationError,
        ResearchPreparationError,
        ResearchResponseError,
        FundingDataError,
        SimulationError,
        AuditError,
        ForwardError,
        ArtifactError,
        BatchError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result.public_summary(), ensure_ascii=False, sort_keys=True))
    return 0 if result.status == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
