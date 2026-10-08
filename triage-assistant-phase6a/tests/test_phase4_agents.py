from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml
from pydantic import ValidationError

from agents.referral import ReferralAgent, ReminderOutreachAgent
from agents.safety import SafetyAgent, VerifierAgent
from agents.triage import QueueScheduler, TriageRule
from schemas import QueueCaseInput, QueueScoringConfig, QuoteFact, RiskAssessment, RuleHit


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_case(case_id, category, *, arrival_offset=0, waiting=0, vital=None,
              age_extreme=None, pregnancy=None, condition_count=None):
    return QueueCaseInput(
        case_id=case_id,
        risk_category=category,
        vital_abnormality=vital,
        age_extreme=age_extreme,
        pregnancy=pregnancy,
        condition_count=condition_count,
        waiting_minutes=waiting,
        arrival_time=NOW + timedelta(minutes=arrival_offset),
    )


class QueueSchedulerTests(unittest.TestCase):
    def test_fallback_sorts_by_category_then_arrival_and_emits_all_red_alerts(self):
        scheduler = QueueScheduler()
        result = scheduler.schedule([
            make_case("green-1", "green", arrival_offset=-20),
            make_case("red-2", "red", arrival_offset=-5),
            make_case("red-1", "red", arrival_offset=-10),
            make_case("amber-1", "amber", arrival_offset=-15),
        ])
        self.assertEqual(result.strategy, "category_then_arrival")
        self.assertEqual([item.case_id for item in result.items],
                         ["red-1", "red-2", "amber-1", "green-1"])
        self.assertEqual({alert.case_id for alert in result.alerts}, {"red-1", "red-2"})
        self.assertTrue(all(item.immediate_red_alert for item in result.items[:2]))
        self.assertEqual({alert.idempotency_key for alert in result.alerts},
                         {"immediate-red:red-1", "immediate-red:red-2"})
        self.assertIn("queue_weights_not_configured_category_then_arrival_fallback", result.flags)

    def test_configured_score_uses_all_factors_and_waiting_time(self):
        config = QueueScoringConfig(
            category_points={"red": 100, "amber": 50, "green": 10, "insufficient_information": 0},
            vital_abnormality_weight=8,
            age_extreme_weight=7,
            pregnancy_weight=6,
            condition_weight=5,
            wait_time_weight=0.5,
        )
        scheduler = QueueScheduler(config)
        result = scheduler.schedule([
            make_case("amber-new", "amber", waiting=0, vital=False, age_extreme=False,
                      pregnancy=False, condition_count=0),
            make_case("amber-old", "amber", waiting=120, vital=True, age_extreme=True,
                      pregnancy=True, condition_count=2),
            make_case("red", "red", waiting=0, vital=False, age_extreme=False,
                      pregnancy=False, condition_count=0),
        ])
        self.assertEqual(result.strategy, "weighted_score")
        self.assertEqual([item.case_id for item in result.items], ["amber-old", "red", "amber-new"])
        self.assertEqual(result.items[0].priority_score, 50 + 8 + 7 + 6 + 10 + 60)
        self.assertIn("red", {alert.case_id for alert in result.alerts})

    def test_empty_yaml_weights_select_fallback(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "scenarios.yaml"
            path.write_text(yaml.safe_dump({"scenarios": {"outpatient_queue": {"queue_weights": {}}}}),
                            encoding="utf-8")
            scheduler = QueueScheduler.from_rule_pack(path, "outpatient_queue")
            self.assertIsNone(scheduler.scoring)
            self.assertEqual(scheduler.schedule([]).strategy, "category_then_arrival")

    def test_missing_weighted_factors_are_not_assumed_false(self):
        config = QueueScoringConfig(
            category_points={"red": 100, "amber": 50, "green": 10, "insufficient_information": 0},
            vital_abnormality_weight=8, age_extreme_weight=7, pregnancy_weight=6,
            condition_weight=5, wait_time_weight=0.5,
        )
        result = QueueScheduler(config).schedule([
            make_case("red-known", "red", vital=False, age_extreme=False, pregnancy=False, condition_count=0),
            make_case("amber-unknown", "amber", vital=False, age_extreme=None,
                      pregnancy=None, condition_count=None),
        ])
        self.assertEqual(result.strategy, "category_then_arrival")
        self.assertTrue(all(item.priority_score is None for item in result.items))
        self.assertIn("weighted_inputs_incomplete_category_then_arrival_fallback", result.flags)

    def test_invalid_weight_order_duplicate_cases_and_naive_time_are_rejected(self):
        with self.assertRaises(ValidationError):
            QueueScoringConfig(
                category_points={"red": 1, "amber": 2, "green": 3, "insufficient_information": 0},
                vital_abnormality_weight=0, age_extreme_weight=0, pregnancy_weight=0,
                condition_weight=0, wait_time_weight=0.1,
            )
        with self.assertRaises(ValueError):
            QueueScheduler().schedule([make_case("same", "red"), make_case("same", "green")])
        with self.assertRaises(ValidationError):
            QueueCaseInput(case_id="bad-time", risk_category="red", waiting_minutes=0,
                           arrival_time=datetime(2026, 1, 1))
        with self.assertRaises(ValidationError):
            make_case("case-id with whitespace", "red")

    def test_queue_returns_alert_records_but_has_no_sender_or_external_side_effect(self):
        scheduler = QueueScheduler()
        result = scheduler.schedule([make_case("red-synthetic", "red")])
        self.assertEqual(len(result.alerts), 1)
        self.assertFalse(hasattr(scheduler, "send_alert"))


class VerifierAgentTests(unittest.TestCase):
    def setUp(self):
        self.source = "Patient reports synthetic marker during the sample check."
        self.fact = QuoteFact(field="complaint", value="synthetic marker", quote=self.source)
        self.rule = TriageRule("synthetic-rule", "amber", ("synthetic marker",),
                               "synthetic approved source", "synthetic section")
        self.hit = RuleHit(rule_id="synthetic-rule", category="amber",
                           source="synthetic approved source", section="synthetic section",
                           evidence=self.source)
        self.assessment = RiskAssessment(
            category="amber", triggered_rules=[self.hit],
            reasoning_summary="Rule synthetic-rule matched; human review required.",
        )

    def test_deterministic_verification_requires_ai_status_even_when_checks_pass(self):
        result = VerifierAgent().verify(source_text=self.source, facts=[self.fact],
                                        risk_assessment=self.assessment, rules=[self.rule])
        self.assertEqual(result.status, "unverified")
        self.assertEqual(result.verification_source, "deterministic")
        self.assertEqual(result.supported_fact_count, 1)
        self.assertIn("unverified_by_ai", result.flags)
        self.assertTrue(result.human_review_required)

    def test_exact_quote_failure_and_diagnostic_language_are_flagged(self):
        unsupported = QuoteFact(field="complaint", value="synthetic", quote="not present in source")
        bad_reason = RiskAssessment(category="amber", triggered_rules=[self.hit],
                                    reasoning_summary="Diagnosis is established.")
        result = VerifierAgent().verify(source_text=self.source, facts=[unsupported],
                                        risk_assessment=bad_reason, rules=[self.rule])
        self.assertIn("unsupported_fact_quote", result.flags)
        self.assertIn("prohibited_diagnostic_or_treatment_language", result.flags)
        self.assertEqual(result.supported_fact_count, 0)
        self.assertEqual(result.status, "unverified")

    def test_matching_rule_can_raise_but_never_lower_reported_category(self):
        raw = {
            "category": "green",
            "triggered_rules": [],
            "reasoning_summary": "A synthetic rule should have been included.",
        }
        result = VerifierAgent().verify(source_text=self.source, facts=[self.fact],
                                        risk_assessment=raw, rules=[self.rule])
        self.assertEqual(result.risk_category, "amber")
        self.assertEqual(result.status, "raised")
        self.assertIn("risk_category_raised_to_matching_rule", result.flags)
        self.assertIn("matching_rule_omitted_from_assessment", result.flags)

    def test_reported_red_without_matching_rule_is_not_reduced(self):
        raw = {"category": "red", "triggered_rules": [],
               "reasoning_summary": "No matching synthetic rule was recorded."}
        result = VerifierAgent().verify(source_text=self.source, facts=[self.fact],
                                        risk_assessment=raw, rules=[])
        self.assertEqual(result.risk_category, "red")
        self.assertIn("risk_category_has_no_matching_rule", result.flags)

    def test_groq_callback_is_skipped_when_provider_gate_is_disabled(self):
        calls = []
        result = VerifierAgent(lambda payload: calls.append(payload) or {}).verify(
            source_text=self.source, facts=[self.fact], risk_assessment=self.assessment, rules=[self.rule])
        self.assertEqual(calls, [])
        self.assertEqual(result.verification_source, "deterministic")

    def test_mocked_groq_lower_risk_proposal_is_ignored(self):
        calls = []
        def fake_groq(payload):
            calls.append(payload)
            return {"verified": True, "risk_category": "green", "flags": []}
        agent = VerifierAgent(fake_groq, safety_agent=SafetyAgent(("SyntheticPlace",)))
        with patch("config.Settings.provider_enabled", return_value=True):
            result = agent.verify(source_text=self.source, facts=[self.fact],
                                  risk_assessment=self.assessment, rules=[self.rule])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.risk_category, "amber")
        self.assertEqual(result.status, "unverified")
        self.assertIn("lower_groq_risk_proposal_ignored", result.flags)
        sent_text = str(calls[0])
        self.assertNotIn("SyntheticPlace", sent_text)


class ReminderOutreachAgentTests(unittest.TestCase):
    def test_maternal_and_chronic_reminders_remain_pending_and_unsent(self):
        agent = ReminderOutreachAgent()
        maternal = agent.draft(scenario="maternal_follow_up", facility="facility demo", patient_language="en")
        chronic = agent.draft(scenario="chronic_check_in", facility="facility demo", patient_language="en")
        self.assertEqual(maternal.status, "drafted")
        self.assertEqual(chronic.drafts[0].status, "pending_approval")
        self.assertTrue(maternal.requires_reviewer_approval)
        self.assertIn("not sent automatically", maternal.drafts[0].text)
        self.assertIn("maternal follow-up", maternal.drafts[0].text)
        self.assertIn("chronic-care follow-up", chronic.drafts[0].text)
        self.assertFalse(hasattr(agent, "send"))

    def test_local_translation_is_used_and_unsafe_translation_falls_back_to_english(self):
        seen = []
        translated = ReminderOutreachAgent(
            local_translator=lambda text, language: seen.append((text, language)) or "स्थानीय अनुस्मारक।"
        ).draft(scenario="maternal_follow_up", facility="facility demo", patient_language="hi")
        self.assertEqual(translated.drafts[0].language, "hi")
        self.assertEqual(seen[0][1], "hi")

        unsafe = ReminderOutreachAgent(
            local_translator=lambda _text, _language: "Take the medicine now."
        ).draft(scenario="chronic_check_in", facility="facility demo", patient_language="hi")
        self.assertEqual(unsafe.drafts[0].language, "en")
        self.assertIn("local_translation_unavailable_english_template", unsafe.flags)

    def test_other_scenarios_produce_no_reminder(self):
        result = ReminderOutreachAgent().draft(scenario="camp_screening", facility="facility demo")
        self.assertEqual(result.status, "not_applicable")
        self.assertEqual(result.drafts, [])


class Phase4IntegrationTests(unittest.TestCase):
    def test_verifier_queue_reminder_and_referral_handoff_share_strict_contracts(self):
        source = "Patient reports synthetic marker and stable indicator during the sample check."
        rule = TriageRule("synthetic-rule", "amber", ("synthetic marker",),
                          "synthetic approved source", "synthetic section")
        green_rule = TriageRule("synthetic-green-rule", "green", ("stable indicator",),
                                "synthetic green source", "synthetic green section")
        green_hit = RuleHit(rule_id="synthetic-green-rule", category="green",
                            source="synthetic green source", section="synthetic green section", evidence=source)
        fact = QuoteFact(field="complaint", value="synthetic marker", quote=source)
        risk = RiskAssessment(category="green", triggered_rules=[green_hit],
                              reasoning_summary="Rule synthetic-green-rule matched; reviewer attention required.")
        verified = VerifierAgent().verify(source_text=source, facts=[fact],
                                           risk_assessment=risk, rules=[rule, green_rule])
        queue_case = make_case("case_synthetic_001", verified.risk_category)
        queued = QueueScheduler().schedule([queue_case])
        reminders = ReminderOutreachAgent().draft(
            scenario="maternal_follow_up", facility="facility demo", patient_language="en")

        referral_input = {
            "verifier_result": verified,
            "human_review_required": True,
            "case_details": {
                "pseudonymous_id": queue_case.case_id,
                "facility": "facility demo",
                "scenario": "maternal_follow_up",
                "created_at": NOW.isoformat(),
            },
            "chief_complaint_original": source,
            "chief_complaint_english": source,
            "symptom_record": {"facts": [fact.model_dump()]},
            "vitals_and_lab_findings": [],
            "risk_assessment": risk.model_dump(),
            "guideline_citations": [],
            "processing_details": {"models": [], "rule_pack_version": "synthetic-test", "fallbacks": []},
            "patient_language": "en",
            "queue_priority_score": queued.items[0].priority_score,
            "queue_position": queued.items[0].queue_position,
            "reminder_drafts": reminders,
        }
        note = ReferralAgent().build_draft(referral_input)
        self.assertEqual(verified.risk_category, "amber")
        self.assertEqual(note.risk_category, verified.risk_category)
        self.assertEqual(note.verifier_result, "raised")
        self.assertEqual(note.queue_position, 1)
        self.assertEqual(note.reminder_drafts[0].status, "pending_approval")
        self.assertEqual(note.reviewer_action_panel.status, "awaiting_review")


if __name__ == "__main__":
    unittest.main()
