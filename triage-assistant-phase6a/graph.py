"""Phase 5 workflow graph: local-only agent composition with an enforced review pause.

All clinical rule data is read from the supplied rule pack. The graph never creates
clinical thresholds, sends reminders, contacts a provider, or bypasses human review.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from agents.document import LabFinding
from agents.intake import FollowUpPlanner, IntakeAgent
from agents.referral import ReferralAgent, ReminderOutreachAgent
from agents.safety import SafetyAgent, VerifierAgent
from agents.triage import QueueScheduler, ScenarioProfile, ScenarioRouter, TriageAgent, TriageRule
from rag import ClinicalKnowledgeAgent
from tools import ReadOnlyToolRegistry
from llm_gateway import LLMGateway
from schemas import (
    FollowUpPlan, KnowledgeSearchResult, QueueCaseInput, QueueSchedule, ReminderPlan,
    QueueScoringConfig, RiskAssessment, SymptomRecord, TriageNote, VerifierResult,
)
from store import Actor, WorkflowStore


@dataclass(frozen=True)
class WorkflowResult:
    state: str
    triage_note: TriageNote | None
    degraded_mode: bool


class WorkflowRunner:
    """Execute the configured local steps and stop at ``awaiting_review``."""

    def __init__(self, store: WorkflowStore, *, rule_pack_path: str | Path | None = None,
                 safety_agent: SafetyAgent | None = None,
                 llm_adapters: Mapping[str, Callable[[dict[str, Any]], Any]] | None = None):
        self.store = store
        self.rule_pack_path = Path(rule_pack_path or Path(__file__).parent / "rulepacks" / "scenarios.yaml")
        self.safety = safety_agent or SafetyAgent()
        self.llm_gateway = LLMGateway(adapters=llm_adapters, safety_agent=self.safety)
        self._active_context: tuple[str, Actor] | None = None
        self._gateway_usage: list[str] = []
        self.router = ScenarioRouter(
            self.rule_pack_path,
            conflict_resolver=self._gateway_callback("scenario_conflict", "gemini", "text"),
        )
        self.intake = IntakeAgent(
            model_call=self._gateway_callback("intake_extraction", "gemini"),
            safety_agent=self.safety,
        )
        self.triage = TriageAgent(
            rationale_call=self._gateway_callback("triage_rationale", "gemini", "text"),
            safety_agent=self.safety,
        )
        self.knowledge = ClinicalKnowledgeAgent(safety_agent=self.safety)
        self.tools = ReadOnlyToolRegistry(self.rule_pack_path, self.knowledge)
        self.follow_up = FollowUpPlanner(
            model_call=self._gateway_callback("follow_up_questions", "gemini"),
            safety_agent=self.safety,
        )
        self.verifier = VerifierAgent(
            groq_call=self._gateway_callback("verification", "groq"),
            safety_agent=self.safety,
        )
        local_translate_raw = self._gateway_callback("reminder_translation", "local", "text",
                                                     allowed_providers=("local",))
        def local_translate(text: str, target_language: str) -> str | None:
            return local_translate_raw({"text": text, "source_language": "en",
                                        "target_language": target_language})
        self.referral = ReferralAgent(
            draft_call=self._gateway_callback("referral_summary", "gemini"),
            local_translator=local_translate, safety_agent=self.safety,
        )
        self.reminders = ReminderOutreachAgent(local_translator=local_translate,
                                               safety_agent=self.safety)

    def _gateway_callback(self, task: str, provider: str, expected: str | None = None,
                          allowed_providers: tuple[str, ...] = ("gemini", "groq", "local")):
        def callback(payload: dict[str, Any]) -> Any:
            active = self._active_context
            case_id, actor = active if active else (None, None)
            result = self.llm_gateway.call(
                task=task, payload=payload, primary_provider=provider,
                allowed_providers=allowed_providers, case_id=case_id,
            )
            if result.status == "completed" and result.provider:
                used = f"{result.provider}:{result.model or 'configured'}"
                if used not in self._gateway_usage:
                    self._gateway_usage.append(used)
            if actor is not None and case_id is not None:
                fallback_digest = hashlib.sha256(
                    json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
                self.store.append_audit(
                    actor, f"llm_gateway:{provider}:{result.status}", case_id,
                    {"input_sha256": result.payload_fingerprint or fallback_digest,
                     "result": result.status},
                )
            output = result.output
            if expected == "text":
                if isinstance(output, str):
                    return output
                if isinstance(output, dict):
                    return output.get("text") or output.get("summary")
                return None
            return output
        return callback

    def _tool_call(self, case_id: str, actor: Actor, agent: str, tool: str, **arguments: Any) -> Any:
        canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), default=str)
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.store.append_audit(actor, f"tool_called:{tool}", case_id,
                                {"input_sha256": fingerprint})
        return self.tools.call(agent=agent, tool=tool, **arguments)

    @staticmethod
    def _dump(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "__dataclass_fields__"):
            return WorkflowRunner._dump(asdict(value))
        if isinstance(value, dict):
            return {key: WorkflowRunner._dump(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [WorkflowRunner._dump(item) for item in value]
        return value

    def _step(self, case_id: str, actor: Actor, name: str, inputs: Any,
              function: Callable[[], Any], validator: Callable[[Any], Any] | None = None) -> Any:
        canonical = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        step_key, _state = self.store.begin_step(case_id, name, fingerprint)
        cached = self.store.cached_step(step_key)
        if cached is not None:
            cached = cached["__committed_step_value__"]
            self.store.append_audit(actor, f"agent_step_reused:{name}", case_id,
                                    {"input_sha256": fingerprint})
            return validator(cached) if validator else cached
        try:
            value = function()
            serialized = self._dump(value)
            self.store.commit_step(step_key, serialized)
            self.store.append_audit(actor, f"agent_step_committed:{name}", case_id,
                                    {"input_sha256": fingerprint})
            return validator(serialized) if validator else value
        except Exception as exc:
            self.store.fail_step(step_key, type(exc).__name__)
            raise

    @staticmethod
    def _timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def run(self, case_id: str, actor: Actor) -> WorkflowResult:
        metadata = self.store.case_metadata(case_id)
        if metadata is None:
            raise ValueError("case unavailable")
        if metadata["workflow_state"] in {"quarantined", "purged", "closed"}:
            return WorkflowResult(metadata["workflow_state"], None, True)
        try:
            existing_note = self.store.get_note(case_id)
        except Exception:
            self.store.quarantine(case_id)
            self.store.append_audit(actor, "workflow_quarantined", case_id,
                                    {"result": "manual_review_required"})
            return WorkflowResult("quarantined", None, True)
        if existing_note is not None and metadata["workflow_state"] in {"awaiting_review", "reviewed", "escalated"}:
            try:
                note = TriageNote.model_validate(existing_note)
                return WorkflowResult(metadata["workflow_state"], note, bool(metadata["degraded_mode"]))
            except Exception:
                self.store.quarantine(case_id)
                self.store.append_audit(actor, "workflow_quarantined", case_id,
                                        {"result": "manual_review_required"})
                return WorkflowResult("quarantined", None, True)

        try:
            self.store.set_processing(case_id)
            self._active_context = (case_id, actor)
            self._gateway_usage = []
            payload = self.store.case_payload(case_id)
            original_redacted = str(payload.get("redacted_original", ""))[:2000]
            english = str(payload.get("redacted_english", ""))[:20000]
            patient_language = str(payload.get("language") or metadata.get("language") or "en")[:20]
            intake_status = str(metadata.get("intake_status", "not_received"))
            source_for_rules = english if english else ""

            route_value = self._step(
                case_id, actor, "scenario_router",
                {"scenario": metadata["scenario"], "source_sha256": hashlib.sha256(english.encode()).hexdigest()},
                lambda: self.router.route(metadata["scenario"], original_redacted),
                lambda raw: ScenarioProfile(
                    name=raw["name"], rule_pack=raw["rule_pack"], prompt_profile=raw["prompt_profile"],
                    required_fields=tuple(raw.get("required_fields", ())), routing_note=raw.get("routing_note", ""),
                ),
            )
            scenario = route_value.name
            rules = self._step(
                case_id, actor, "rules_engine_tool", {"scenario": scenario},
                lambda: self._tool_call(case_id, actor, "triage_agent", "rules_engine", scenario=scenario),
                lambda raw: [TriageRule(
                    rule_id=item["rule_id"], category=item["category"],
                    trigger_terms=tuple(item["trigger_terms"]), source=item["source"],
                    section=item.get("section", ""),
                ) for item in raw],
            )
            fallbacks: list[str] = []
            if not rules:
                fallbacks.append("approved_clinical_rules_not_configured")
            if intake_status == "needs_interpreter":
                fallbacks.append("translation_or_transcription_unavailable_manual_interpreter_required")
            if intake_status == "not_received":
                fallbacks.append("intake_not_received")

            intake_record = self._step(
                case_id, actor, "intake_agent",
                {"source_sha256": hashlib.sha256(source_for_rules.encode()).hexdigest(), "scenario": scenario},
                lambda: self.intake.run(source_for_rules, scenario),
                lambda raw: SymptomRecord.model_validate(raw),
            )

            document_records = payload.get("documents", []) if isinstance(payload.get("documents", []), list) else []
            raw_rows = []
            document_texts = []
            for document in document_records:
                if not isinstance(document, dict):
                    continue
                document_texts.append(str(document.get("text", ""))[:50000])
                raw_rows.extend(document.get("rows", []) if isinstance(document.get("rows", []), list) else [])
            from schemas import RawLabRow
            rows = [RawLabRow.model_validate(row) for row in raw_rows[:1000]]
            document_text = "\n".join(text for text in document_texts if text)
            lab_findings = self._step(
                case_id, actor, "vitals_lab_analyst",
                {"rows": [row.model_dump(mode="json") for row in rows], "scenario": scenario},
                lambda: self._tool_call(case_id, actor, "lab_analyst", "lab_range_lookup",
                                        scenario=scenario, rows=rows),
                lambda raw: [LabFinding.model_validate(item) for item in raw],
            )
            if rows and any(item.status in {"unrecognized", "review"} for item in lab_findings):
                fallbacks.append("lab_value_or_reference_range_needs_reviewer_check")
            if not rows:
                fallbacks.append("no_document_lab_rows_available")

            risk = self._step(
                case_id, actor, "triage_agent",
                {"source_sha256": hashlib.sha256(source_for_rules.encode()).hexdigest(),
                 "document_sha256": hashlib.sha256(document_text.encode()).hexdigest(),
                 "rules": [rule.rule_id for rule in rules]},
                lambda: self.triage.assess(source_for_rules, document_text, rules),
                lambda raw: RiskAssessment.model_validate(raw),
            )
            knowledge = self._step(
                case_id, actor, "clinical_knowledge_agent",
                {"source_sha256": hashlib.sha256(source_for_rules.encode()).hexdigest()},
                lambda: self._tool_call(case_id, actor, "clinical_knowledge_agent", "guideline_search",
                                        query=source_for_rules),
                lambda raw: KnowledgeSearchResult.model_validate(raw),
            )
            if knowledge.status == "no_guideline_match":
                fallbacks.append("no_verified_public_guideline_match")
            follow_up = self._step(
                case_id, actor, "follow_up_planner",
                {"facts": [item.model_dump(mode="json") for item in intake_record.facts],
                 "required_fields": list(route_value.required_fields), "scenario": scenario},
                lambda: self.follow_up.plan(intake_record, route_value.required_fields,
                                            scenario=scenario, triage_category=risk.category),
                lambda raw: FollowUpPlan.model_validate(raw),
            )
            verifier = self._step(
                case_id, actor, "verifier_agent",
                {"source_sha256": hashlib.sha256(source_for_rules.encode()).hexdigest(),
                 "facts": [item.model_dump(mode="json") for item in intake_record.facts],
                 "risk": risk.model_dump(mode="json"), "rules": [rule.rule_id for rule in rules]},
                lambda: self.verifier.verify(source_text=source_for_rules, facts=intake_record.facts,
                                             risk_assessment=risk, rules=rules),
                lambda raw: VerifierResult.model_validate(raw),
            )

            scheduler = self._step(
                case_id, actor, "queue_scoring_config_tool", {"scenario": scenario},
                lambda: (lambda scheduler: scheduler.scoring.model_dump(mode="json")
                         if scheduler.scoring is not None else None)(
                             self._tool_call(case_id, actor, "queue_scheduler", "queue_scoring_config",
                                             scenario=scenario)),
                lambda raw: QueueScheduler(QueueScoringConfig.model_validate(raw) if raw else None),
            )
            if scheduler.scoring is None:
                fallbacks.append("queue_weights_not_configured_category_arrival_fallback")
            now = datetime.now(timezone.utc)
            existing_cases = self.store.list_queue_cases(metadata["facility_id"])
            queue_inputs = []
            for item in existing_cases:
                if item["case_id"] == case_id:
                    continue
                existing_created = self._timestamp(item["created_at"])
                queue_inputs.append(QueueCaseInput(
                    case_id=item["pseudonymous_id"], risk_category=item["risk_category"],
                    waiting_minutes=max(0.0, (now - existing_created).total_seconds() / 60.0),
                    arrival_time=existing_created,
                ))
            created_at = self._timestamp(metadata["created_at"])
            queue_inputs.append(QueueCaseInput(
                case_id=metadata["pseudonymous_id"], risk_category=verifier.risk_category,
                waiting_minutes=max(0.0, (now - created_at).total_seconds() / 60.0),
                arrival_time=created_at,
            ))
            queue = self._step(
                case_id, actor, "queue_scheduler",
                {"cases": [item.model_dump(mode="json") for item in queue_inputs], "scenario": scenario},
                lambda: scheduler.schedule(queue_inputs),
                lambda raw: QueueSchedule.model_validate(raw),
            )
            current_item = next((item for item in queue.items if item.case_id == metadata["pseudonymous_id"]), None)
            if current_item is None:
                raise ValueError("current case was not present in its queue result")
            for alert in queue.alerts:
                if alert.case_id == metadata["pseudonymous_id"]:
                    self.store.insert_outbox(case_id, alert.idempotency_key, "red_alert",
                                             {"case_reference": metadata["pseudonymous_id"],
                                              "event_type": alert.event_type})

            reminder_plan = self._step(
                case_id, actor, "reminder_outreach_agent",
                {"scenario": scenario, "language": patient_language,
                 "facility_sha256": hashlib.sha256(metadata["facility_id"].encode()).hexdigest()},
                lambda: self.reminders.draft(scenario=scenario, facility=metadata["facility_id"],
                                             patient_language=patient_language),
                lambda raw: ReminderPlan.model_validate(raw),
            )
            age_value = next((fact.value for fact in intake_record.facts if fact.field == "age"), None)
            sex_value = next((fact.value for fact in intake_record.facts if fact.field == "sex"), None)
            pregnancy_value = next((fact.value for fact in intake_record.facts if fact.field == "pregnancy"), None)
            case_details = {
                "pseudonymous_id": metadata["pseudonymous_id"], "facility": metadata["facility_id"],
                "scenario": scenario, "age_band": age_value, "sex": sex_value,
                "pregnancy_status": pregnancy_value, "created_at": metadata["created_at"],
            }
            fallbacks.extend(follow_up.flags)
            if not self.safety.gazetteer:
                fallbacks.append("name_place_gazetteer_unconfigured_external_model_egress_disabled")
            if not english:
                fallbacks.append("english_text_unavailable_no_model_egress")
            if intake_record.status == "insufficient_information":
                fallbacks.append("intake_insufficient_information")

            referral_input = {
                "verifier_result": verifier, "human_review_required": True,
                "case_details": case_details,
                "chief_complaint_original": original_redacted,
                "chief_complaint_english": english,
                "symptom_record": intake_record.model_dump(mode="json"),
                "vitals_and_lab_findings": [row.model_dump(mode="json") for row in rows],
                "risk_assessment": risk.model_dump(mode="json"),
                "guideline_citations": [item.model_dump(mode="json") for item in knowledge.passages],
                "processing_details": {
                    "models": list(self._gateway_usage), "rule_pack_version": "unconfigured",
                    "fallbacks": list(dict.fromkeys(fallbacks)), "language": patient_language,
                },
                "patient_language": patient_language,
                "missing_information": follow_up.missing_information,
                "suggested_questions": [item.question for item in follow_up.suggested_questions],
                "queue_priority_score": current_item.priority_score,
                "queue_position": current_item.queue_position,
                "reminder_drafts": reminder_plan,
            }
            referral_step_inputs = {
                "risk": risk.model_dump(mode="json"), "queue_position": current_item.queue_position,
                "fact_count": len(intake_record.facts), "scenario": scenario,
            }
            note = self._step(
                case_id, actor, "referral_handoff_agent", referral_step_inputs,
                lambda: self.referral.build_draft(referral_input),
                lambda raw: TriageNote.model_validate(raw),
            )
            if note.processing_details.models != self._gateway_usage:
                note.processing_details.models = list(self._gateway_usage)
                step_fingerprint = hashlib.sha256(json.dumps(
                    referral_step_inputs, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), default=str,
                ).encode("utf-8")).hexdigest()
                step_key, _ = self.store.begin_step(case_id, "referral_handoff_agent", step_fingerprint)
                self.store.commit_step(step_key, note.model_dump(mode="json"))
                self.store.append_audit(actor, "agent_step_finalized:referral_handoff_agent", case_id,
                                        {"input_sha256": step_fingerprint})
            if note.status != "awaiting_review":
                raise ValueError("workflow did not reach the required human review pause")
            degraded = bool(fallbacks or verifier.status == "unverified" or not rules)
            self.store.save_triage_note(case_id, note.model_dump(mode="json"), degraded_mode=degraded)
            for alert in queue.alerts:
                if alert.case_id == metadata["pseudonymous_id"]:
                    self.store.append_audit(actor, "red_alert_queued", case_id,
                                            {"result": "pending_outbox"})
            self._active_context = None
            return WorkflowResult("awaiting_review", note, degraded)
        except Exception:
            # Fail closed: no exception text or case content is put in the audit record.
            try:
                self.store.quarantine(case_id)
                self.store.append_audit(actor, "workflow_quarantined", case_id,
                                        {"result": "manual_review_required"})
            except Exception:
                pass
            self._active_context = None
            return WorkflowResult("quarantined", None, True)
