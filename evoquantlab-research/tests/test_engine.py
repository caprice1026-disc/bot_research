from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from engine import (Panel, HOUR, make_features, Evaluator, validate_ast, fit_normalizer, normalize, make_weights,
                    fold_indices, train_mask, simulate, forward_labels, temporal_ic, block_permutation,
                    OfflineProposer, LLMProposer, PolicyPatch, validate_patch, search, file_hash, ms,
                    TERMINALS, UNARY, BINARY, digest)


def settings():
    return json.loads(Path(__file__).resolve().parents[1].joinpath("settings.json").read_text(encoding="utf-8"))


def synthetic(hours=1800):
    rng = np.random.default_rng(112)
    returns = rng.normal(0.00008, 0.003, (hours, 2))
    o = 100 * np.exp(np.cumsum(returns, axis=0))
    c = o * np.exp(rng.normal(0, 0.002, (hours, 2)))
    panel = Panel(ms("2024-06-01T00:00:00Z") + np.arange(hours, dtype=np.int64) * HOUR, ["BTCUSDT", "ETHUSDT"],
                  {"open": o, "close": c, "high": np.maximum(o, c) * 1.005,
                   "low": np.minimum(o, c) * .995, "volume": rng.lognormal(7, 0.3, (hours, 2))})
    return panel


def small_cfg():
    cfg = settings()
    cfg.update({"train_start": "2024-06-10T00:00:00Z", "train_end": "2024-07-01T00:00:00Z",
                "validation_boundaries": ["2024-07-01T00:00:00Z", "2024-07-10T00:00:00Z", "2024-07-20T00:00:00Z", "2024-08-01T00:00:00Z"],
                "test_start": "2024-08-01T00:00:00Z", "test_end": "2024-08-10T00:00:00Z",
                "cycles": 3, "evaluations_per_cycle": 12, "seeds": [11]})
    return cfg


class CausalTests(unittest.TestCase):
    def test_feature_and_ast_prefix_invariance_all_operators(self):
        panel = synthetic()
        full, prefix = make_features(panel), make_features(panel.prefix(1200))
        for key in full:
            np.testing.assert_allclose(full[key][:1200], prefix[key], rtol=0, atol=1e-12, equal_nan=True)
        cfg = settings()
        e1, e2 = Evaluator(full, cfg), Evaluator(prefix, cfg)
        trees = [{"terminal": t} for t in TERMINALS]
        trees += [{"op": op, "args": [{"terminal": "mom_24"}]} for op in UNARY]
        trees += [{"op": op, "args": [{"terminal": "mom_24"}, {"terminal": "vol_24"}]} for op in BINARY]
        for tree in trees:
            np.testing.assert_allclose(e1(tree)[:1200], e2(tree), rtol=0, atol=1e-10, equal_nan=True)

    def test_normalizer_train_only_under_future_shock(self):
        p = synthetic()
        cfg = small_cfg()
        features = make_features(p)
        raw = features["mom_24"]
        mask = train_mask(p, cfg)
        params = fit_normalizer(raw, mask)
        shocked = raw.copy()
        shocked[~mask] = 1e9
        self.assertEqual(params, fit_normalizer(shocked, mask))
        self.assertLess(p.time[mask][-1] + cfg["purge_hours"] * HOUR, ms(cfg["train_end"]))

    def test_execution_is_one_hour_after_close(self):
        cfg = settings()
        cfg["signal_smoothing_hours"] = 1
        score = np.zeros((20, 2))
        score[5:, 0] = 1
        features = {"vol_168": np.full_like(score, 0.001)}
        w = make_weights(score, features, cfg)
        self.assertEqual(w[5, 0], 0)
        self.assertEqual(w[6, 0], 0)
        self.assertGreater(w[7, 0], 0)
        w2 = make_weights(score, features, cfg, delay=2)
        self.assertEqual(w2[7, 0], 0)
        self.assertGreater(w2[8, 0], 0)

    def test_future_shock_cannot_change_prior_weights(self):
        p = synthetic()
        cfg = small_cfg()
        f = make_features(p)
        raw = f["mom_24"]
        params = fit_normalizer(raw, train_mask(p, cfg))
        original = make_weights(normalize(raw, params), f, cfg)
        shocked = copy.deepcopy(p)
        for key in ("open", "high", "low", "close"):
            shocked.values[key][1400:] *= 3
        fs = make_features(shocked)
        changed = make_weights(normalize(fs["mom_24"], params), fs, cfg)
        np.testing.assert_array_equal(original[:1402], changed[:1402])

    def test_label_purge_and_embargo(self):
        p, cfg = synthetic(), small_cfg()
        labels = forward_labels(p, 24, 1)
        expected = p.values["open"][126] / p.values["open"][102] - 1
        np.testing.assert_allclose(labels[100], expected)
        start, end = cfg["validation_boundaries"][:2]
        idx = fold_indices(p, start, end, cfg)
        self.assertGreaterEqual(p.time[idx[0]], ms(start) + 26 * HOUR)
        self.assertLess(p.time[idx[-1]] + 26 * HOUR, ms(end))
        self.assertLess(p.time[idx[-1] + 1], ms(end))

    def test_search_rejects_holdout_input(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "holdout"):
                search(synthetic(1800), small_cfg(), "fixed_gp", 11, Path(temp) / "bad")

    def test_two_asset_temporal_ic_is_not_cross_sectional(self):
        x = np.column_stack([np.arange(50), np.arange(50) ** 2])
        y = x.copy()
        out = temporal_ic(x, y, np.ones(50, bool))
        self.assertAlmostEqual(out["0"], 1)
        self.assertAlmostEqual(out["1"], 1)


class SafetyTests(unittest.TestCase):
    def test_search_process_file_guard(self):
        import run
        with tempfile.TemporaryDirectory() as temp:
            sealed = Path(temp) / "sealed.npz"
            sealed.write_bytes(b"heldout")
            run.install_holdout_guard(sealed)
            with self.assertRaises(PermissionError):
                sealed.read_bytes()

    def test_unsafe_dsl_rejected(self):
        bad = ["__import__('os').system('x')", {"terminal": "__class__"},
               {"op": "exec", "args": []}, {"op": "lag", "args": [{"terminal": "ret_1"}], "periods": -1},
               {"op": "add", "args": [{"terminal": "ret_1"}]}, {"terminal": "ret_1", "code": "x"}]
        for tree in bad:
            with self.assertRaises((ValueError, TypeError)):
                validate_ast(tree)

    def test_depth_and_complexity_bounded(self):
        tree = {"terminal": "ret_1"}
        for _ in range(4):
            tree = {"op": "neg", "args": [tree]}
        with self.assertRaises(ValueError):
            validate_ast(tree)

    def test_patch_cannot_change_contract_or_remove_family(self):
        with self.assertRaises(ValueError):
            validate_patch(PolicyPatch({"fee_bps": 0}, "unsafe"))
        with self.assertRaises(ValueError):
            validate_patch(PolicyPatch({"momentum": 0}, "unsafe"))
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            LLMProposer().propose({}, {})

    def test_deterministic_proposer_has_bounded_support(self):
        evidence = {"family_mean_fitness": {"momentum": 2, "activity": -2}}
        a = validate_patch(OfflineProposer().propose(evidence, {}))
        b = OfflineProposer().propose(evidence, {})
        self.assertEqual(a, b)
        self.assertGreater(a.priorities["momentum"], a.priorities["activity"])
        self.assertTrue(all(0.5 <= x <= 2 for x in a.priorities.values()))


class AccountingTests(unittest.TestCase):
    def test_hand_computed_buy_liquidate_with_fees(self):
        initial, fee = 10000, 0.0005
        result = simulate([[1.0]], [[0.1]], 5, initial)
        expected = initial / (1 + fee) * 1.1 * (1 - fee)
        self.assertAlmostEqual(result["metrics"]["final_equity"], expected, places=8)
        self.assertEqual(result["metrics"]["trades"], 2)
        self.assertAlmostEqual(result["metrics"]["accounting_residual"], 0, places=8)

    def test_flat_market_roundtrip_cost_and_initial_drawdown(self):
        result = simulate(np.ones((20, 1)), np.zeros((20, 1)), 10)
        expected = 10000 / 1.001 * .999
        self.assertAlmostEqual(result["metrics"]["final_equity"], expected, places=8)
        self.assertLess(result["metrics"]["max_drawdown"], 0)
        self.assertEqual(result["metrics"]["trades"], 2)

    def test_cash_is_zero_pnl_no_fake_success(self):
        result = simulate(np.zeros((100, 2)), np.full((100, 2), 0.01), 5)
        self.assertEqual(result["metrics"]["net_profit"], 0)
        self.assertEqual(result["metrics"]["trades"], 0)
        self.assertEqual(result["metrics"]["cost"], 0)

    def test_long_only_constraints_and_fee_monotonicity(self):
        rng = np.random.default_rng(111)
        w = rng.uniform(0, .5, (200, 2))
        r = rng.normal(0.001, .01, (200, 2))
        result = [simulate(w, r, b)["metrics"] for b in (0, 5, 10, 20)]
        self.assertTrue(all(a["net_return"] >= b["net_return"] for a, b in zip(result[:-1], result[1:])))
        with self.assertRaises(ValueError):
            simulate([[-.1, .5]], [[.01, .01]], 5)
        with self.assertRaises(ValueError):
            simulate([[.8, .8]], [[.01, .01]], 5)

    def test_independent_sequential_cash_share_replay(self):
        rng = np.random.default_rng(43)
        p = 100 * np.cumprod(1 + rng.normal(0, .01, (101, 2)), axis=0)
        w = rng.uniform(0, .5, (100, 2))
        r = p[1:] / p[:-1] - 1
        result = simulate(w, r, 5)
        cash, shares = 10000.0, np.zeros(2)
        for i in range(100):
            before = cash + (shares * p[i]).sum()
            self.assertAlmostEqual(before, result["before"][i], places=7)
            shares += result["notionals"][i] / p[i]
            cash -= result["notionals"][i].sum() + result["fees"][i].sum()
            self.assertGreaterEqual(cash, -1e-8)
            self.assertGreaterEqual(shares.min(), -1e-8)
            self.assertAlmostEqual(cash + (shares * p[i + 1]).sum(), result["equity"][i] if i < 99 else result["equity"][i] + result["liquidation_fees"].sum(), places=7)
        cash += result["liquidation_notional"].sum() - result["liquidation_fees"].sum()
        shares -= result["liquidation_notional"] / p[-1]
        self.assertAlmostEqual(cash, result["metrics"]["final_equity"], places=7)
        np.testing.assert_allclose(shares, 0, atol=1e-10)

    def test_buy_hold_has_no_hidden_hourly_rebalancing(self):
        p = synthetic(200).values["open"]
        relative = p[:-1] / p[0]
        weights = relative / relative.sum(axis=1, keepdims=True)
        result = simulate(weights, p[1:] / p[:-1] - 1, 5)
        self.assertEqual(result["metrics"]["trades"], 4)


class ReproducibilityTests(unittest.TestCase):
    def test_block_null_preserves_joint_rows_and_changes_alignment(self):
        r = np.column_stack([np.arange(80), -np.arange(80)])
        a = block_permutation(r, np.random.default_rng(11), 8)
        b = block_permutation(r, np.random.default_rng(11), 8)
        np.testing.assert_array_equal(a, b)
        self.assertFalse(np.array_equal(a, r))
        np.testing.assert_array_equal(a[:, 0], -a[:, 1])
        np.testing.assert_array_equal(np.sort(a[:, 0]), np.sort(r[:, 0]))

    def test_seeded_search_and_trial_history_identical(self):
        p, cfg = synthetic(1464), small_cfg()  # ends strictly before held-out August
        with tempfile.TemporaryDirectory() as temp:
            a = search(p, cfg, "dual_gp", 11, Path(temp) / "a")
            b = search(p, cfg, "dual_gp", 11, Path(temp) / "b")
            self.assertEqual(a, b)
            self.assertEqual(a["evaluations"], 36)
            self.assertEqual(a["test_observations_seen"], 0)
            self.assertEqual(a["llm_calls"], 0)
            for name in ("trials.jsonl", "attempts.jsonl", "selection.json"):
                self.assertEqual(file_hash(Path(temp) / "a" / name), file_hash(Path(temp) / "b" / name))


class PipelineTests(unittest.TestCase):
    def test_submaterial_movements_are_kept_in_audit_ledger(self):
        import run
        import pandas as pd
        panel = synthetic(2200)
        indices = np.arange(2156)
        rng = np.random.default_rng(5)
        weights = rng.uniform(0, 1e-9, (2156, 2))
        returns = panel.values["open"][indices + 1] / panel.values["open"][indices] - 1
        result = simulate(weights, returns, 5)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "audit"
            run.save_account(directory, result, panel, indices, settings(), 1, 5, "tiny")
            audit = json.loads((directory / "ledger_audit.json").read_text())
            self.assertTrue(audit["passed"])
            self.assertGreater(audit["full_movement_rows"], audit["material_trade_rows"])
            self.assertLess(abs(audit["equity_residual"]), 1e-6)
            movements = pd.read_csv(directory / "account_movements.csv")
            self.assertAlmostEqual(movements.fee.sum(), result["metrics"]["cost"], places=10)

    def test_synthetic_freeze_holdout_grid_ledger_and_reopen_guard(self):
        from argparse import Namespace
        import run
        from engine import save_panel, write_json
        cfg = small_cfg()
        cfg.update({"cycles": 1, "evaluations_per_cycle": 8})
        panel = synthetic(1800)
        split = int((ms(cfg["test_start"]) - panel.time[0]) // HOUR)
        development = panel.prefix(split)
        heldout = Panel(panel.time[split:], panel.symbols, {k: v[split:] for k, v in panel.values.items()})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save_panel(root / "development.npz", development)
            save_panel(root / "sealed_test.npz", heldout)
            write_json(root / "settings.json", cfg)
            write_json(root / "pre_registration.json", {"protocol_hash": digest(cfg), "code_hashes": run.code_hashes()})
            write_json(root / "data_manifest.json", {"development_sha256": file_hash(root / "development.npz"),
                       "sealed_test_sha256": file_hash(root / "sealed_test.npz")})
            for method in cfg["methods"]:
                search(development, cfg, method, 11, root / "search" / f"{method}_seed11")
            run.evaluate(Namespace(output=str(root)), cfg, {"synthetic": True})
            result = json.loads((root / "verification.json").read_text())
            self.assertTrue(result["passed"])
            self.assertEqual(result["metrics_rows"], 52)
            self.assertEqual(len(result["accounts"]), 5)
            with self.assertRaises(FileExistsError):
                run.evaluate(Namespace(output=str(root)), cfg, {"synthetic": True})
            with self.assertRaisesRegex(ValueError, "forbidden"):
                run.do_search(Namespace(output=str(root), method="dual_gp", seed=11), cfg, {})


if __name__ == "__main__":
    unittest.main()
