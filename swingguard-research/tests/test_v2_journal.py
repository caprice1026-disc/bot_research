import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from decimal import Decimal as D

import test_research as h
import swingguard.providers as providers
from swingguard.providers import GeminiProvider


class DurableJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = h.config()
        self.response = types.SimpleNamespace(
            text=json.dumps({"weights": {"BTCUSDT": "0.5", "ETHUSDT": "0"}, "rationale": "test"}),
            model_version="gemini-3.6-flash",
            usage_metadata=types.SimpleNamespace(prompt_token_count=100, candidates_token_count=10,
                thoughts_token_count=20, total_token_count=130, tool_use_prompt_token_count=0,
                cached_content_token_count=0))
        self.call = Mock(return_value=self.response)
        self.client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=self.call))
        self.fake = patch.dict("sys.modules", h.GeminiJournalTests().fake_types())
        self.fake.start()
        self.addCleanup(self.fake.stop)

    def provider(self, budget="10"):
        return GeminiProvider(self.config, self.root, D(budget), client=self.client)

    def entry(self):
        return next(iter(json.loads((self.root / "budget.json").read_text()).values()))

    def fault(self, phase, *, after=False):
        original, fired = providers.write_json, False
        def writer(path, value):
            nonlocal fired
            stage = next(iter(value.values())).get("status") if Path(path).name == "budget.json" else "cache"
            if stage == phase and not fired:
                fired = True
                if after:
                    original(path, value)
                raise OSError("simulated publication interruption")
            return original(path, value)
        return patch.object(providers, "write_json", side_effect=writer)

    def test_cache_publish_failure_recovers_without_second_send(self):
        with self.fault("cache"):
            with self.assertRaises(OSError):
                self.provider().propose(h.DAY_MS, {})
        self.assertEqual(self.entry()["status"], "settled")
        self.assertIn("proposal", self.entry())
        restored = self.provider()
        result = restored.propose(h.DAY_MS, {})
        self.assertEqual(result.status, "ok")
        self.assertEqual(self.call.call_count, 1)
        self.assertEqual(restored.sent_count, 0)
        self.assertTrue(restored.audit[0]["restored_from_journal"])

    def test_received_record_survives_crash_before_settlement(self):
        with self.fault("settled"):
            with self.assertRaises(OSError):
                self.provider().propose(h.DAY_MS, {})
        self.assertEqual(self.entry()["status"], "response_received")
        result = self.provider().propose(h.DAY_MS, {})
        self.assertEqual(result.status, "ok")
        self.assertEqual(self.entry()["status"], "settled")
        self.assertEqual(self.call.call_count, 1)

    def test_crash_after_received_commit_is_recoverable(self):
        with self.fault("response_received", after=True):
            with self.assertRaises(OSError):
                self.provider().propose(h.DAY_MS, {})
        self.assertEqual(self.provider().propose(h.DAY_MS, {}).status, "ok")
        self.assertEqual(self.call.call_count, 1)

    def test_crash_after_settlement_commit_is_recoverable(self):
        with self.fault("settled", after=True):
            with self.assertRaises(OSError):
                self.provider().propose(h.DAY_MS, {})
        self.assertEqual(self.provider().propose(h.DAY_MS, {}).status, "ok")
        self.assertEqual(self.call.call_count, 1)

    def test_response_not_durably_saved_never_resends(self):
        with self.fault("response_received"):
            with self.assertRaises(OSError):
                self.provider().propose(h.DAY_MS, {})
        self.assertEqual(self.entry()["status"], "reserved_or_unknown")
        for as_of in (h.DAY_MS, 2 * h.DAY_MS):
            with self.assertRaises(RuntimeError):
                self.provider().propose(as_of, {})
        self.assertEqual(self.call.call_count, 1)

    def test_failed_reservation_save_sends_nothing(self):
        with self.fault("reserved_or_unknown"):
            with self.assertRaises(OSError):
                self.provider().propose(h.DAY_MS, {})
        self.call.assert_not_called()
        self.assertEqual(self.provider().propose(h.DAY_MS, {}).status, "ok")
        self.assertEqual(self.call.call_count, 1)

    def test_invalid_or_partial_derived_cache_repairs_from_journal(self):
        result = self.provider().propose(h.DAY_MS, {})
        (self.root / (result.input_hash + ".json")).write_text("{partial")
        self.assertEqual(self.provider().propose(h.DAY_MS, {}), result)
        self.assertEqual(self.call.call_count, 1)

    def test_orphan_cache_without_journal_cannot_resend(self):
        self.provider().propose(h.DAY_MS, {})
        (self.root / "budget.json").unlink()
        with self.assertRaises(RuntimeError):
            self.provider().propose(h.DAY_MS, {})
        self.assertEqual(self.call.call_count, 1)

    def test_missing_thinking_with_inconsistent_total_retains_and_blocks(self):
        self.response.usage_metadata.thoughts_token_count = None
        self.response.usage_metadata.total_token_count = 612
        for as_of in (h.DAY_MS, 2 * h.DAY_MS):
            with self.assertRaises(RuntimeError):
                self.provider().propose(as_of, {})
        self.assertEqual(self.call.call_count, 1)
        self.assertEqual(self.entry()["status"], "usage_uncertain")
        self.assertGreaterEqual(D(self.entry()["cost_or_reservation_usd"]), D(".05"))

    def test_omitted_thinking_can_only_be_inferred_zero_from_exact_total(self):
        self.response.usage_metadata.thoughts_token_count = None
        self.response.usage_metadata.total_token_count = 110
        p = self.provider().propose(h.DAY_MS, {})
        self.assertEqual(D(p.cost_usd), D(".0001125"))
        self.assertTrue(self.entry()["normalized_usage"]["thinking_inferred_zero"])

    def test_missing_total_is_not_settled_even_with_other_counts(self):
        self.response.usage_metadata.total_token_count = None
        with self.assertRaises(RuntimeError):
            self.provider().propose(h.DAY_MS, {})
        self.assertEqual(self.entry()["status"], "usage_uncertain")

    def test_tool_usage_and_invalid_cached_subset_are_rejected(self):
        self.response.usage_metadata.tool_use_prompt_token_count = 1
        self.response.usage_metadata.total_token_count = 131
        with self.assertRaises(RuntimeError):
            self.provider().propose(h.DAY_MS, {})
        self.assertEqual(self.entry()["status"], "usage_uncertain")

    def test_cached_subset_cannot_exceed_prompt(self):
        self.response.usage_metadata.cached_content_token_count = 101
        with self.assertRaises(RuntimeError):
            self.provider().propose(h.DAY_MS, {})
        self.assertEqual(self.entry()["status"], "usage_uncertain")

    def test_excess_usage_keeps_larger_observed_liability_and_globally_stops(self):
        self.response.usage_metadata.prompt_token_count = 4_000_000
        self.response.usage_metadata.candidates_token_count = 0
        self.response.usage_metadata.thoughts_token_count = 0
        self.response.usage_metadata.total_token_count = 4_000_000
        with self.assertRaises(RuntimeError):
            self.provider().propose(h.DAY_MS, {})
        self.assertGreaterEqual(D(self.entry()["cost_or_reservation_usd"]), D(15))
        with self.assertRaises(RuntimeError):
            self.provider().propose(2 * h.DAY_MS, {})
        self.assertEqual(self.call.call_count, 1)

    def test_output_bound_and_unpriced_returned_model_fail_closed(self):
        self.response.model_version = "different-unpriced-model"
        with self.assertRaises(RuntimeError):
            self.provider().propose(h.DAY_MS, {})
        self.assertEqual(self.entry()["status"], "usage_uncertain")

    def test_unverified_config_price_rejected_before_sending(self):
        self.config["gemini"]["assumed_input_usd_per_million"] = ".01"
        with self.assertRaises(ValueError):
            self.provider().propose(h.DAY_MS, {})
        self.call.assert_not_called()

    def test_unknown_request_model_or_suffix_rejected_before_client_or_reservation(self):
        for name in ("unverified-model", "gemini-3.6-flash-unverified-variant"):
            with self.subTest(model=name):
                self.config["gemini"]["model"] = name
                provider = self.provider()
                with patch.object(provider, "_client_for_send") as create_client:
                    with self.assertRaises(ValueError):
                        provider.propose(h.DAY_MS, {})
                    create_client.assert_not_called()
                self.assertFalse((self.root / "budget.json").exists())
        self.call.assert_not_called()

    def test_unverified_returned_suffix_retains_liability_and_blocks_all_resends(self):
        self.response.model_version = "gemini-3.6-flash-unverified-variant"
        for as_of in (h.DAY_MS, h.DAY_MS, 2 * h.DAY_MS):
            with self.assertRaises(RuntimeError):
                self.provider().propose(as_of, {})
        self.assertEqual(self.call.call_count, 1)
        entry = self.entry()
        self.assertEqual(entry["status"], "usage_uncertain")
        self.assertGreaterEqual(D(entry["cost_or_reservation_usd"]), D(".05"))
        self.assertNotIn("proposal", entry)
        self.assertEqual(set(self.root.glob("*.json")), {self.root / "budget.json"})

    def test_exact_allowlisted_model_uses_only_its_verified_prices(self):
        proposal = self.provider().propose(h.DAY_MS, {})
        self.assertEqual(proposal.model, "gemini-3.6-flash")
        self.assertEqual(D(proposal.cost_usd), D(".0001875"))
        self.assertEqual(self.entry()["status"], "settled")

    def test_tampered_primary_record_does_not_use_or_replace_with_new_call(self):
        result = self.provider().propose(h.DAY_MS, {})
        ledger = json.loads((self.root / "budget.json").read_text())
        ledger[result.input_hash]["response"]["text"] = "tampered"
        (self.root / "budget.json").write_text(json.dumps(ledger))
        with self.assertRaises(RuntimeError):
            self.provider().propose(h.DAY_MS, {})
        self.assertEqual(self.call.call_count, 1)

    def test_usage_exceeds_output_limit_even_if_total_reconciles(self):
        self.response.usage_metadata.thoughts_token_count = 503
        self.response.usage_metadata.total_token_count = 613
        with self.assertRaises(RuntimeError):
            self.provider().propose(h.DAY_MS, {})
        self.assertEqual(self.entry()["status"], "usage_uncertain")


if __name__ == "__main__":
    unittest.main()
