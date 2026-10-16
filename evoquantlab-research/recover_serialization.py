"""Complete a frozen batch after a ledger-export failure; never rerun search.

Only the account serializer in run.py changed. Original source, freeze, results,
and access log remain untouched. The exact same model and stress grid are replayed.
"""
from pathlib import Path
from datetime import datetime, timezone
from argparse import ArgumentParser, Namespace
import json
import shutil

from limits import apply_limits


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.output).resolve()
    cfg = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    resources = apply_limits(cfg["memory_limit_mb"], cfg["cpu_cores"])
    import run
    from engine import digest, file_hash, write_json
    old = json.loads((root / "pre_registration.json").read_text(encoding="utf-8"))
    freeze = json.loads((root / "freeze.json").read_text(encoding="utf-8"))
    current = run.code_hashes()
    if digest(cfg) != old["protocol_hash"] or current["engine.py"] != old["code_hashes"]["engine.py"] or current["limits.py"] != old["code_hashes"]["limits.py"]:
        raise ValueError("Strategy/config/resource implementation changed; refuse technical replay")
    for name, expected in old["code_hashes"].items():
        if file_hash(root / "frozen_source" / name) != expected:
            raise ValueError("Original source snapshot mismatch")
    for relative, expected in freeze["search_artifact_hashes"].items():
        if file_hash(root / relative) != expected:
            raise ValueError("Frozen search artifact changed")
    # Permit only edits confined to the existing serialization function.
    before = (root / "frozen_source" / "run.py").read_text(encoding="utf-8")
    after = Path(run.__file__).read_text(encoding="utf-8")
    def outside_serializer(source):
        a, remainder = source.split("def save_account(", 1)
        _, b = remainder.split("\ndef evaluate(", 1)
        return a, b
    if outside_serializer(before) != outside_serializer(after):
        raise ValueError("Changes outside ledger serialization are prohibited in recovery")
    destination = root / "completed_batch"
    destination.mkdir(exist_ok=False)
    metadata = {"status": "starting", "reason": "Original serializer omitted movements <=1e-10 of equity; 0.000007943 USDT residual triggered strict export audit",
                "started_at_utc": datetime.now(timezone.utc).isoformat(), "original_admission_hash": freeze["admission_hash"],
                "original_code_hashes": old["code_hashes"], "corrected_code_hashes": current,
                "changed_scope": "save_account serialization only; include all nonzero movements in account_movements.csv for independent replay",
                "strategy_engine_identical": True, "configuration_identical": True, "selection_artifacts_identical": True,
                "search_reexecuted": False, "model_retuned": False, "holdout_batch_attempts_total": 2,
                "note": "Technical deterministic replay of the same frozen test grid; not a fresh independent OOS test"}
    write_json(root / "recovery.json", metadata)
    for name in ("settings.json", "development.npz", "sealed_test.npz", "data_manifest.json", "test_results.txt"):
        shutil.copy2(root / name, destination / name)
    shutil.copytree(root / "search", destination / "search")
    corrected_registration = dict(old, code_hashes=current, original_pre_registration_sha256=file_hash(root / "pre_registration.json"),
                                  code_hash_updated_at_utc=datetime.now(timezone.utc).isoformat(), correction=metadata)
    write_json(destination / "pre_registration.json", corrected_registration)
    run.evaluate(Namespace(output=str(destination)), cfg, resources)
    matched = {}
    for arm in sorted((root / "backtests").iterdir()):
        for name in ("metrics.json", "equity.csv", "trades.csv"):
            matched[f"{arm.name}/{name}"] = file_hash(arm / name) == file_hash(destination / "backtests" / arm.name / name)
    if not all(matched.values()):
        raise ValueError("A previously computed outcome changed during serialization-only replay")
    metadata.update(status="completed", completed_at_utc=datetime.now(timezone.utc).isoformat(), previous_outcome_hashes_identical=matched,
                    completed_admission_hash=json.loads((destination / "freeze.json").read_text())["admission_hash"])
    write_json(root / "recovery.json", metadata)
    write_json(destination / "recovery.json", metadata)
    note = ("\n## 出力障害と同一条件の技術的再計算\n\n"
            "最初のtest batchは、極小約定をtrades.csvから省いたため台帳の差が約0.000008 USDTとなり、5番目の口座出力で停止した。"
            "内部会計の保存則は成功していた。元のソース・freeze・開封記録・途中結果を親ディレクトリに保存し、"
            "save_accountの出力処理だけを修正してaccount_movements.csvへ全ての非ゼロ数量移動を記録した。"
            "engine・設定・全候補・train normalizer・stress gridは同一hashのまま、同じ凍結バッチを技術的に再計算した。"
            "test batchの試行は計2回であり、1回だけで完走したとは報告しない。"
            "先に出力された5口座のmetrics/equity/tradesは再計算後もファイルhashが全て一致した。"
            "探索や調整は再実行していない。recovery.jsonに差分の範囲、2つのadmission hash、照合結果を保存した。"
            "これは新たな独立OOS実験ではない。\n")
    with (destination / "report_ja.md").open("a", encoding="utf-8") as handle:
        handle.write(note)
    print(json.dumps({"completed": str(destination), "technical_replay": True, "previous_outcomes_identical": True}))


if __name__ == "__main__":
    main()
