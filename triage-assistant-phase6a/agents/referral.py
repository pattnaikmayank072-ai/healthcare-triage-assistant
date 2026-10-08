"""Referral/Handoff and Reminder/Outreach agents; drafts require human review."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import ValidationError

from agents.safety import DISCLAIMER, SafetyAgent
from schemas import (
    CaseDetails, GuidelineCitation, ProcessingDetails, QuoteFact, RawLabRow,
    ReminderDraft, ReminderPlan, ReviewerActionPanel, RiskAssessment, TriageNote,
    VerifierResult,
)


class ReferralAgent:
    """Build the plan's whitelisted Triage Note from already-verified case sections.

    ``draft_call`` is an optional callback that must route through the central LLM
    Gateway and receive only redacted, verified fields. With no callback, a fixed
    template is used. Translation is optional and must be supplied by a local translator.
    """

    def __init__(self, draft_call: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
                 local_translator: Callable[[str, str], str] | None = None,
                 safety_agent: SafetyAgent | None = None):
        self.draft_call = draft_call
        self.local_translator = local_translator
        self.safety = safety_agent or SafetyAgent()

    def _redact_gateway_payload(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.safety.prepare_external_text(value) if value.strip() else value
        if isinstance(value, Mapping):
            return {key: self._redact_gateway_payload(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_gateway_payload(item) for item in value]
        if isinstance(value, tuple):
            return [self._redact_gateway_payload(item) for item in value]
        return value

    @staticmethod
    def _string_fields(value: Any):
        if isinstance(value, str):
            yield value
        elif isinstance(value, Mapping):
            for item in value.values():
                yield from ReferralAgent._string_fields(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from ReferralAgent._string_fields(item)

    def build_draft(self, verified_case: Mapping[str, Any]) -> TriageNote:
        verifier_value = verified_case.get("verifier_result")
        verifier_result: VerifierResult | None = None
        if isinstance(verifier_value, VerifierResult):
            verifier_result = verifier_value
        elif isinstance(verifier_value, Mapping):
            verifier_result = VerifierResult.model_validate(verifier_value)
        verification = verifier_result.status if verifier_result is not None else verifier_value
        if verification not in {"passed", "raised", "unverified"}:
            raise ValueError("referral draft requires a verifier result")
        if verified_case.get("human_review_required", True) is not True:
            raise ValueError("case must remain subject to human review")

        details = CaseDetails.model_validate(verified_case.get("case_details", {}))
        symptom = verified_case.get("symptom_record", {})
        facts_raw = symptom.get("facts", []) if isinstance(symptom, Mapping) else []
        facts = [QuoteFact.model_validate(item) for item in facts_raw]
        source_text = str(verified_case.get("chief_complaint_original", ""))[:2000]
        english_text = str(verified_case.get("chief_complaint_english", ""))[:2000]
        labs = [RawLabRow.model_validate(item) for item in verified_case.get("vitals_and_lab_findings", [])]
        risk = RiskAssessment.model_validate(verified_case.get("risk_assessment", {}))
        risk_rank = {"insufficient_information": 0, "green": 1, "amber": 2, "red": 3}
        verifier_category = verifier_result.risk_category if verifier_result is not None else risk.category
        final_risk_category = max((risk.category, verifier_category), key=lambda category: risk_rank[category])
        reasoning_summary = risk.reasoning_summary
        if risk_rank[final_risk_category] > risk_rank[risk.category]:
            reasoning_summary = (
                f"{reasoning_summary} Independent verification raised the risk category to "
                f"{final_risk_category}; qualified human review is required."
            )
        citations = [GuidelineCitation.model_validate(item) for item in verified_case.get("guideline_citations", [])]
        processing = ProcessingDetails.model_validate(verified_case.get("processing_details", {}))

        rules = ", ".join(hit.rule_id for hit in risk.triggered_rules) or "none configured"
        reviewer_summary = (
            f"Reviewer draft for {details.pseudonymous_id}: category {final_risk_category}; "
            f"triggered rules: {rules}. Verification status: {verification}. Human review required."
        )
        referral_summary = (
            f"Referral summary draft for {details.pseudonymous_id}: scenario {details.scenario}; "
            f"risk category {final_risk_category}; available symptom and report details are listed "
            "in the attached structured sections. This draft requires qualified review."
        )
        if self.draft_call is not None and self.safety.gazetteer:
            try:
                gateway_payload = self._redact_gateway_payload({
                    "pseudonymous_id": details.pseudonymous_id,
                    "scenario": details.scenario,
                    "risk_assessment": risk.model_dump(),
                    "symptom_facts": [fact.model_dump() for fact in facts],
                    "lab_findings": [lab.model_dump() for lab in labs],
                    "guideline_citations": [citation.model_dump() for citation in citations],
                    "verifier_result": verification,
                })
                candidate = self.draft_call(gateway_payload)
                if isinstance(candidate, Mapping):
                    ref = candidate.get("referral_summary_draft")
                    rev = candidate.get("reviewer_summary")
                    if isinstance(ref, str) and ref.strip() and len(ref) <= 3000:
                        referral_summary = ref.strip()
                    if isinstance(rev, str) and rev.strip() and len(rev) <= 3000:
                        reviewer_summary = rev.strip()
            except Exception:
                # Fixed templates remain the required fallback; do not log case content.
                pass

        guarded_referral = self.safety.guard_output(referral_summary)
        guarded_reviewer = self.safety.guard_output(reviewer_summary)
        if guarded_referral.status != "cleared" or guarded_reviewer.status != "cleared":
            raise ValueError("draft quarantined by output safety guard")

        language = str(verified_case.get("patient_language", "en"))
        translation_status = "not_needed" if language.lower().startswith("en") else "needs_interpreter"
        # Do not label a draft translated unless a configured local translator succeeds.
        if translation_status == "needs_interpreter" and self.local_translator is not None:
            try:
                translated = self.local_translator(guarded_referral.text, language)
                if isinstance(translated, str) and translated.strip():
                    guarded_referral = self.safety.guard_output(translated.strip())
                    if guarded_referral.status != "cleared":
                        raise ValueError("translated draft quarantined")
                    translation_status = "translated"
            except Exception:
                translation_status = "needs_interpreter"

        processing_data = processing.model_dump()
        processing_data["translation_status"] = translation_status
        processing = ProcessingDetails.model_validate(processing_data)
        reminder_values = verified_case.get("reminder_drafts", [])
        if isinstance(reminder_values, ReminderPlan):
            reminder_values = reminder_values.drafts
        elif isinstance(reminder_values, Mapping):
            reminder_values = reminder_values.get("drafts", [])
        verifier_flags = list(verified_case.get("verifier_flags", []))
        if verifier_result is not None:
            verifier_flags.extend(verifier_result.flags)
        note = TriageNote(
            case_details=details,
            chief_complaint_original=source_text,
            chief_complaint_english=english_text,
            symptom_timeline=facts,
            vitals_and_lab_findings=labs,
            risk_category=final_risk_category,
            triggered_rules=risk.triggered_rules,
            guideline_citations=citations,
            reasoning_summary=reasoning_summary,
            missing_information=list(verified_case.get("missing_information", [])),
            suggested_questions=list(verified_case.get("suggested_questions", []))[:5],
            queue_priority_score=verified_case.get("queue_priority_score"),
            queue_position=verified_case.get("queue_position"),
            verifier_result=verification,
            verifier_flags=list(dict.fromkeys(verifier_flags))[:100],
            referral_summary_draft=guarded_referral.text,
            reviewer_summary=guarded_reviewer.text,
            reminder_drafts=[ReminderDraft.model_validate(x) for x in reminder_values],
            processing_details=processing,
            reviewer_action_panel=ReviewerActionPanel(status="awaiting_review"),
            patient_language=language,
            disclaimer=DISCLAIMER,
        )
        for text in self._string_fields(note.model_dump()):
            content = text.replace(DISCLAIMER, "").strip()
            if content and self.safety.guard_output(content).status != "cleared":
                raise ValueError("triage note quarantined by output safety guard")
        return note


class ReminderOutreachAgent:
    """Create pending maternal/chronic follow-up drafts; never persist or send them."""

    _templates = {
        "maternal_follow_up": (
            "Reminder: Please contact {facility} about your planned maternal follow-up."
        ),
        "chronic_check_in": (
            "Reminder: Please contact {facility} to arrange your planned chronic-care follow-up."
        ),
    }

    def __init__(self, local_translator: Callable[[str, str], str] | None = None,
                 safety_agent: SafetyAgent | None = None):
        self.local_translator = local_translator
        self.safety = safety_agent or SafetyAgent()

    def draft(self, *, scenario: str, facility: str,
              patient_language: str = "en") -> ReminderPlan:
        if scenario not in self._templates:
            return ReminderPlan(
                status="not_applicable", drafts=[], requires_reviewer_approval=True,
                flags=["scenario_not_eligible_for_reminders"],
            )
        if not isinstance(facility, str) or not facility.strip() or len(facility) > 150:
            raise ValueError("a valid facility label is required for a reminder draft")
        facility = " ".join(facility.split())
        if not self.safety.scan_input(facility).safe:
            raise ValueError("facility label quarantined by the Injection Sentinel")

        english = self._templates[scenario].format(facility=facility)
        target_language = patient_language.strip() if isinstance(patient_language, str) else "en"
        if not 2 <= len(target_language) <= 20:
            target_language = "en"
        text = english
        language = "en"
        flags: list[str] = []

        if not target_language.casefold().startswith("en"):
            translated = None
            if self.local_translator is not None:
                try:
                    candidate = self.local_translator(english, target_language)
                    if (isinstance(candidate, str) and candidate.strip()
                            and len(candidate) <= 900
                            and self.safety.scan_input(candidate).safe
                            and self.safety.guard_output(candidate).status == "cleared"):
                        translated = candidate.strip()
                except Exception:
                    translated = None
            if translated is not None:
                text = translated
                language = target_language
            else:
                flags.append("local_translation_unavailable_english_template")

        text = f"{text}\n\nDRAFT ONLY: pending reviewer approval; not sent automatically."
        if self.safety.guard_output(text).status != "cleared":
            raise ValueError("reminder draft quarantined by output safety guard")
        return ReminderPlan(
            status="drafted",
            drafts=[ReminderDraft(text=text, language=language, status="pending_approval")],
            requires_reviewer_approval=True,
            flags=flags,
        )
