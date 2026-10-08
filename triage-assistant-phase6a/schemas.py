"""Strict, whitelisted data contracts for the educational triage prototype."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


FactField = Literal[
    "complaint", "onset", "duration", "severity", "age", "sex",
    "pregnancy", "condition", "medicine", "timeline",
]


class QuoteFact(StrictModel):
    field: FactField
    value: str = Field(min_length=1, max_length=500)
    quote: str = Field(min_length=1, max_length=1000)

    @field_validator("value", "quote")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class SymptomRecord(StrictModel):
    facts: list[QuoteFact] = Field(default_factory=list, max_length=100)
    status: Literal["structured", "insufficient_information"]
    scenario: str = Field(default="general_outpatient", min_length=1, max_length=80)
    language: str = Field(default="en", min_length=2, max_length=20)
    source_text_sha256: str = Field(min_length=64, max_length=64)


class RawLabRow(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=100)
    unit: str | None = Field(default=None, max_length=60)
    reference_range: str | None = Field(default=None, max_length=100)
    source_quote: str = Field(min_length=1, max_length=1000)


class DocumentResult(StrictModel):
    status: Literal["read", "unreadable_needs_manual_entry"]
    text: str = Field(default="", max_length=50000)
    rows: list[RawLabRow] = Field(default_factory=list, max_length=1000)
    page_count: int = Field(ge=0, le=100)
    engines_attempted: list[str] = Field(default_factory=list, max_length=5)
    note: str = ""


RiskCategory = Literal["red", "amber", "green", "insufficient_information"]
UserRole = Literal["health_worker", "nurse", "doctor", "medical_officer", "admin", "service"]


class RuleHit(StrictModel):
    rule_id: str = Field(min_length=1, max_length=100)
    category: Literal["red", "amber", "green"]
    source: str = Field(min_length=1, max_length=300)
    section: str = Field(default="", max_length=200)
    evidence: str = Field(min_length=1, max_length=500)


class RiskAssessment(StrictModel):
    category: RiskCategory
    triggered_rules: list[RuleHit] = Field(default_factory=list, max_length=100)
    reasoning_summary: str = Field(min_length=1, max_length=2000)
    requires_human_review: bool = True
    rationale_source: Literal["template", "model"] = "template"
    flags: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def conservative_category(self) -> "RiskAssessment":
        if not self.triggered_rules and self.category != "insufficient_information":
            raise ValueError("a non-insufficient category requires at least one configured rule hit")
        if self.triggered_rules:
            rank = {"green": 0, "amber": 1, "red": 2}
            if rank[self.category] < max(rank[h.category] for h in self.triggered_rules):
                raise ValueError("category cannot be lower than a triggered rule")
        return self


class HealthResponse(StrictModel):
    status: Literal["ok"]
    service: str
    mode: Literal["prototype"]
    disclaimer: str
    background_monitoring: Literal["online", "offline", "disabled"] = "disabled"
    background_banner: str | None = Field(default=None, max_length=240)


class AuthRequest(StrictModel):
    user_id: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=20, max_length=8192)


class LogoutRequest(StrictModel):
    refresh_token: str | None = Field(default=None, max_length=8192)


class TokenPair(StrictModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(gt=0)


class ConsentRequest(StrictModel):
    purpose: str = Field(min_length=1, max_length=300)
    consented: bool
    language: str = Field(default="en", min_length=2, max_length=20)


class ConsentResponse(StrictModel):
    consent_id: str
    status: Literal["recorded"]


class CreateCaseRequest(StrictModel):
    consent_id: str = Field(min_length=1, max_length=128)
    language: str = Field(default="en", min_length=2, max_length=20)
    scenario: Literal[
        "outpatient_queue", "occupational_screening", "campus_fever",
        "maternal_follow_up", "chronic_check_in", "camp_screening",
    ] = "outpatient_queue"


class CaseCreatedResponse(StrictModel):
    case_id: str
    pseudonymous_id: str
    state: Literal["consented"]
    created_at: str
    idempotent_replay: bool = False


class LanguageResult(StrictModel):
    status: Literal["complete", "needs_interpreter", "quarantined"]
    detected_language: str = Field(min_length=2, max_length=20)
    english_text: str | None = Field(default=None, max_length=20000)
    encrypted_original_text: str | None = Field(default=None, max_length=50000)
    needs_interpreter: bool
    flags: list[str] = Field(default_factory=list, max_length=50)


class CaseDetails(StrictModel):
    pseudonymous_id: str = Field(min_length=1, max_length=100)
    facility: str = Field(min_length=1, max_length=150)
    scenario: str = Field(min_length=1, max_length=80)
    age_band: str | None = Field(default=None, max_length=40)
    sex: str | None = Field(default=None, max_length=40)
    pregnancy_status: str | None = Field(default=None, max_length=80)
    created_at: str = Field(min_length=1, max_length=80)


class GuidelineCitation(StrictModel):
    source: str = Field(min_length=1, max_length=200)
    section: str = Field(default="", max_length=200)
    excerpt: str = Field(default="", max_length=1000)


class ReminderDraft(StrictModel):
    text: str = Field(min_length=1, max_length=1000)
    language: str = Field(default="en", max_length=20)
    status: Literal["pending_approval", "approved"] = "pending_approval"


class ProcessingDetails(StrictModel):
    models: list[str] = Field(default_factory=list, max_length=20)
    rule_pack_version: str = Field(default="unconfigured", max_length=100)
    fallbacks: list[str] = Field(default_factory=list, max_length=50)
    language: str = Field(default="en", max_length=20)
    translation_status: Literal["not_needed", "translated", "needs_interpreter"] = "not_needed"


class ReviewerActionPanel(StrictModel):
    status: Literal["awaiting_review", "reviewed", "escalated"] = "awaiting_review"
    reviewer_role: UserRole | None = None
    decision: Literal["approve", "edit", "escalate", "reject"] | None = None
    reason: str | None = Field(default=None, max_length=1000)
    action_time: str | None = Field(default=None, max_length=80)


class TriageNote(StrictModel):
    case_details: CaseDetails
    chief_complaint_original: str = Field(default="", max_length=2000)
    chief_complaint_english: str = Field(default="", max_length=2000)
    symptom_timeline: list[QuoteFact] = Field(default_factory=list, max_length=100)
    vitals_and_lab_findings: list[RawLabRow] = Field(default_factory=list, max_length=1000)
    risk_category: RiskCategory
    triggered_rules: list[RuleHit] = Field(default_factory=list, max_length=100)
    guideline_citations: list[GuidelineCitation] = Field(default_factory=list, max_length=100)
    reasoning_summary: str = Field(min_length=1, max_length=2000)
    missing_information: list[str] = Field(default_factory=list, max_length=100)
    suggested_questions: list[str] = Field(default_factory=list, max_length=5)
    queue_priority_score: float | None = None
    queue_position: int | None = Field(default=None, ge=1)
    verifier_result: Literal["passed", "raised", "unverified"]
    verifier_flags: list[str] = Field(default_factory=list, max_length=100)
    referral_summary_draft: str = Field(min_length=1, max_length=3000)
    reviewer_summary: str = Field(min_length=1, max_length=3000)
    reminder_drafts: list[ReminderDraft] = Field(default_factory=list, max_length=20)
    processing_details: ProcessingDetails
    reviewer_action_panel: ReviewerActionPanel = Field(default_factory=ReviewerActionPanel)
    status: Literal["awaiting_review", "reviewed", "escalated"] = "awaiting_review"
    patient_language: str = Field(default="en", max_length=20)
    disclaimer: str = Field(min_length=1, max_length=1000)


class LabFinding(StrictModel):
    test_name: str = Field(min_length=1, max_length=200)
    observed_value: str = Field(min_length=1, max_length=100)
    normalized_value: float | None = None
    unit: str | None = Field(default=None, max_length=60)
    reference_range: str | None = Field(default=None, max_length=120)
    status: Literal["normal", "low", "high", "critical_low", "critical_high", "unrecognized", "review"]
    source_quote: str = Field(min_length=1, max_length=1000)
    reference_source: str | None = Field(default=None, max_length=200)
    reference_section: str | None = Field(default=None, max_length=200)
    note: str = Field(default="", max_length=500)


class KnowledgePassage(StrictModel):
    source: str = Field(min_length=1, max_length=200)
    section: str = Field(default="", max_length=200)
    excerpt: str = Field(min_length=1, max_length=3000)
    citation_url: str = Field(default="", max_length=1000)
    score: float = Field(ge=0, le=1)
    document_id: str = Field(min_length=1, max_length=100)
    passage_id: str = Field(min_length=1, max_length=100)


class KnowledgeSearchResult(StrictModel):
    status: Literal["matched", "no_guideline_match"]
    query: str = Field(max_length=2000)
    passages: list[KnowledgePassage] = Field(default_factory=list, max_length=5)
    message: str = ""


class FollowUpQuestion(StrictModel):
    field: FactField
    question: str = Field(min_length=1, max_length=500)
    priority: int = Field(ge=1, le=100)
    source: Literal["template", "model"] = "template"


class FollowUpPlan(StrictModel):
    status: Literal["ready", "needs_configuration"]
    missing_information: list[str] = Field(default_factory=list, max_length=100)
    suggested_questions: list[FollowUpQuestion] = Field(default_factory=list, max_length=5)
    priority_source: str = Field(default="scenario_rulepack_order", max_length=100)
    flags: list[str] = Field(default_factory=list, max_length=50)


class QueueCaseInput(StrictModel):
    case_id: str = Field(min_length=1, max_length=100)
    risk_category: RiskCategory
    vital_abnormality: bool | None = None
    age_extreme: bool | None = None
    pregnancy: bool | None = None
    condition_count: int | None = Field(default=None, ge=0, le=100)
    waiting_minutes: float = Field(ge=0, allow_inf_nan=False)
    arrival_time: datetime

    @field_validator("case_id")
    @classmethod
    def safe_case_identifier(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", value):
            raise ValueError("case_id must be a safe pseudonymous identifier")
        return value

    @field_validator("arrival_time")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("arrival_time must include a timezone")
        return value.astimezone(timezone.utc)


class QueueScoringConfig(StrictModel):
    category_points: dict[str, float]
    vital_abnormality_weight: float = Field(ge=0, allow_inf_nan=False)
    age_extreme_weight: float = Field(ge=0, allow_inf_nan=False)
    pregnancy_weight: float = Field(ge=0, allow_inf_nan=False)
    condition_weight: float = Field(ge=0, allow_inf_nan=False)
    wait_time_weight: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_scoring(self) -> "QueueScoringConfig":
        ranks = ("red", "amber", "green", "insufficient_information")
        if set(self.category_points) != set(ranks):
            raise ValueError("category_points must define exactly the four risk categories")
        values = [self.category_points[key] for key in ranks]
        if (any(not isfinite(value) or value < 0 for value in values)
                or values != sorted(values, reverse=True)):
            raise ValueError("category points must be nonnegative and descending from red")
        return self


class QueueItem(StrictModel):
    case_id: str = Field(min_length=1, max_length=100)
    risk_category: RiskCategory
    queue_position: int = Field(ge=1)
    priority_score: float | None = Field(default=None, allow_inf_nan=False)
    arrival_time: datetime
    immediate_red_alert: bool = False


class QueueAlert(StrictModel):
    case_id: str = Field(min_length=1, max_length=100)
    event_type: Literal["immediate_red_alert"]
    idempotency_key: str = Field(min_length=1, max_length=200)


class QueueSchedule(StrictModel):
    strategy: Literal["weighted_score", "category_then_arrival"]
    items: list[QueueItem] = Field(default_factory=list, max_length=10000)
    alerts: list[QueueAlert] = Field(default_factory=list, max_length=10000)
    flags: list[str] = Field(default_factory=list, max_length=50)


class VerifierResult(StrictModel):
    status: Literal["passed", "raised", "unverified"]
    risk_category: RiskCategory
    verification_source: Literal["groq", "deterministic"]
    fact_count: int = Field(ge=0, le=1000)
    supported_fact_count: int = Field(ge=0, le=1000)
    flags: list[str] = Field(default_factory=list, max_length=100)
    human_review_required: bool = True

    @model_validator(mode="after")
    def supported_facts_do_not_exceed_total(self) -> "VerifierResult":
        if self.supported_fact_count > self.fact_count:
            raise ValueError("supported_fact_count cannot exceed fact_count")
        return self


class ReminderPlan(StrictModel):
    status: Literal["drafted", "not_applicable"]
    drafts: list[ReminderDraft] = Field(default_factory=list, max_length=20)
    requires_reviewer_approval: bool = True
    flags: list[str] = Field(default_factory=list, max_length=50)


CaseState = Literal[
    "created", "consented", "processing", "awaiting_review", "reviewed",
    "escalated", "closed", "quarantined", "purged",
]


class IntakeRequest(StrictModel):
    text: str | None = Field(default=None, max_length=20000)
    audio_base64: str | None = Field(default=None, max_length=2_000_000)
    language: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def exactly_one_input(self) -> "IntakeRequest":
        if (self.text is None) == (self.audio_base64 is None):
            raise ValueError("provide exactly one of text or audio_base64")
        if self.text is not None and not self.text.strip():
            raise ValueError("text must not be blank")
        return self


class IntakeAcknowledgement(StrictModel):
    case_id: str
    status: Literal["accepted", "needs_interpreter", "quarantined"]
    encrypted_at_rest: bool
    flags: list[str] = Field(default_factory=list, max_length=50)


class DocumentUploadAcknowledgement(StrictModel):
    case_id: str
    status: Literal["read", "unreadable_needs_manual_entry", "rejected"]
    sanitized: bool
    page_count: int = Field(ge=0, le=100)
    manual_entry_required: bool
    flags: list[str] = Field(default_factory=list, max_length=50)


class ReviewEdits(StrictModel):
    reviewer_summary: str | None = Field(default=None, max_length=3000)
    referral_summary_draft: str | None = Field(default=None, max_length=3000)


class ReviewRequest(StrictModel):
    decision: Literal["approve", "edit", "escalate"]
    reason: str = Field(min_length=1, max_length=1000)
    edits: ReviewEdits | None = None

    @model_validator(mode="after")
    def edits_match_decision(self) -> "ReviewRequest":
        if self.decision == "edit" and (self.edits is None or not self.edits.model_dump(exclude_none=True)):
            raise ValueError("edit decision requires at least one permitted edit")
        if self.decision != "edit" and self.edits is not None:
            raise ValueError("edits are permitted only for the edit decision")
        return self


class ReviewResponse(StrictModel):
    request_id: str
    case_id: str
    status: CaseState
    audit_reference: str
    queued_outbox_count: int = Field(ge=0)
    disclaimer: str


class ReminderApprovalRequest(StrictModel):
    draft_indices: list[int] = Field(min_length=1, max_length=20)

    @field_validator("draft_indices")
    @classmethod
    def indices_unique_and_nonnegative(cls, values: list[int]) -> list[int]:
        if any(value < 0 for value in values) or len(values) != len(set(values)):
            raise ValueError("draft indices must be unique and nonnegative")
        return values


class ReminderApprovalResponse(StrictModel):
    request_id: str
    case_id: str
    queued_count: int = Field(ge=0)
    status: Literal["queued_for_delivery"]
    delivery_status: Literal["pending_outbox"] = "pending_outbox"
    disclaimer: str


class CaseEnvelope(StrictModel):
    request_id: str
    case_id: str
    status: CaseState
    degraded_mode: bool = False
    triage_note: TriageNote | None = None
    disclaimer: str


class QueueResponse(StrictModel):
    request_id: str
    items: list[QueueItem] = Field(default_factory=list, max_length=10000)
    alerts: list[QueueAlert] = Field(default_factory=list, max_length=10000)
    strategy: Literal["weighted_score", "category_then_arrival"]
    flags: list[str] = Field(default_factory=list, max_length=50)
    disclaimer: str


class AuditEntry(StrictModel):
    audit_reference: str
    actor_id: str
    actor_role: UserRole
    facility_id: str
    action: str
    case_reference_hash: str | None = None
    occurred_at: str
    details: dict[str, str] = Field(default_factory=dict)


class AuditResponse(StrictModel):
    request_id: str
    entries: list[AuditEntry] = Field(default_factory=list, max_length=10000)


class BackgroundAgentHealth(StrictModel):
    agent_name: str = Field(min_length=1, max_length=80)
    last_seen: str | None = Field(default=None, max_length=80)
    status: Literal["ok", "degraded", "failed", "offline", "unconfigured"]
    last_event_sequence: int = Field(default=0, ge=0)
    last_error_code: str | None = Field(default=None, max_length=80)


class BackgroundAlert(StrictModel):
    alert_id: str = Field(min_length=1, max_length=80)
    pseudonymous_id: str = Field(min_length=1, max_length=100)
    facility_id: str = Field(min_length=1, max_length=150)
    revision: int = Field(ge=0)
    level: str = Field(min_length=1, max_length=80)
    reason_code: str = Field(min_length=1, max_length=80)
    waiting_minutes: float = Field(ge=0, allow_inf_nan=False)
    current_category: RiskCategory
    score: float | None = Field(default=None, allow_inf_nan=False)
    next_role: Literal["nurse", "doctor", "medical_officer"]
    acknowledged_by: str | None = Field(default=None, max_length=128)
    acknowledged_at: str | None = Field(default=None, max_length=80)
    status: Literal["open", "acknowledged", "closed"]
    created_at: str
    updated_at: str


class BackgroundAlertResponse(StrictModel):
    request_id: str
    alerts: list[BackgroundAlert] = Field(default_factory=list, max_length=1000)
    monitoring_status: Literal["online", "offline", "unconfigured"]
    banner: str | None = Field(default=None, max_length=240)


class DeleteCaseResponse(StrictModel):
    request_id: str
    case_id: str
    status: Literal["purged"]
    deleted: bool = True
    audit_reference: str
    disclaimer: str
