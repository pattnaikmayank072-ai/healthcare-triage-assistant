from __future__ import annotations

import unittest
from unittest.mock import patch

from agents.safety import SafetyAgent
from llm_gateway import LLMGateway
from tools import ReadOnlyToolRegistry


class LLMGatewayTests(unittest.TestCase):
    def test_disabled_external_provider_and_missing_gazetteer_never_call_adapter(self):
        calls = []
        gateway = LLMGateway({"gemini": lambda payload: calls.append(payload) or {"ok": True}})
        result = gateway.call(task="summary", payload={"text": "synthetic text"},
                              primary_provider="gemini")
        self.assertEqual(result.status, "fallback")
        self.assertEqual(calls, [])

        with patch("config.Settings.provider_enabled", return_value=True):
            result = gateway.call(task="summary", payload={"text": "synthetic text"},
                                  primary_provider="gemini")
        self.assertEqual(result.status, "fallback")
        self.assertIn("payload_egress_blocked_or_over_limit", result.flags)
        self.assertEqual(calls, [])

    def test_external_text_is_redacted_before_adapter_and_cached_by_fingerprint(self):
        calls = []
        def adapter(request):
            calls.append(request)
            return {"summary": "synthetic template result"}
        gateway = LLMGateway({"gemini": adapter}, safety_agent=SafetyAgent(("Asha", "SyntheticPlace")))
        with patch("config.Settings.provider_enabled", return_value=True):
            first = gateway.call(task="summary", payload={
                "text": "Asha in SyntheticPlace has synthetic symptoms; phone +91 9876543210"
            }, primary_provider="gemini")
            second = gateway.call(task="summary", payload={
                "text": "Asha in SyntheticPlace has synthetic symptoms; phone +91 9876543210"
            }, primary_provider="gemini")
        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "completed")
        self.assertEqual(len(calls), 1)
        sent = str(calls[0]["payload"])
        self.assertNotIn("Asha", sent)
        self.assertNotIn("SyntheticPlace", sent)
        self.assertNotIn("9876543210", sent)
        self.assertIn("prompt_fingerprint_cache_hit", second.flags)

    def test_retry_policy_and_three_failure_circuit_breaker(self):
        class TemporaryUnavailable(Exception):
            status_code = 503

        calls = []
        def failed(_request):
            calls.append(1)
            raise TemporaryUnavailable("synthetic failure")
        gateway = LLMGateway(
            {"gemini": failed}, safety_agent=SafetyAgent(("SyntheticPlace",)),
            sleep=lambda _seconds: None, jitter=lambda _low, _high: 0.0,
        )
        with patch("config.Settings.provider_enabled", return_value=True):
            first = gateway.call(task="summary", payload={"text": "SyntheticPlace synthetic text"},
                                 primary_provider="gemini")
            count_after_trip = len(calls)
            second = gateway.call(task="summary", payload={"text": "SyntheticPlace synthetic text"},
                                  primary_provider="gemini")
        self.assertEqual(first.status, "fallback")
        self.assertEqual(count_after_trip, 3)
        self.assertEqual(len(calls), 3)
        self.assertIn("gemini_unavailable_or_circuit_open", second.flags)

    def test_read_only_tool_registry_rejects_unlisted_or_write_like_tools(self):
        registry = ReadOnlyToolRegistry("/path/that/does/not/exist.yaml")
        with self.assertRaises(PermissionError):
            registry.call(agent="clinical_knowledge_agent", tool="rules_engine", scenario="outpatient_queue")
        with self.assertRaises(PermissionError):
            registry.call(agent="triage_agent", tool="write_database", query="anything")
        self.assertEqual(registry.call(agent="triage_agent", tool="rules_engine",
                                       scenario="outpatient_queue"), [])


if __name__ == "__main__":
    unittest.main()
