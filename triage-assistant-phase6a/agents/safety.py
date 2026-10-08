"""Safety/Privacy, Injection Sentinel, and fail-closed Verifier agents."""
from __future__ import annotations

import base64
import html
import re
import unicodedata
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pydantic import ValidationError

from config import settings
from schemas import QuoteFact, RiskAssessment, RiskCategory, RuleHit, VerifierResult

DISCLAIMER = (
    "Educational prototype for triage-support purposes only. Not a medical device; "
    "does not diagnose, prescribe, or replace a qualified health professional. "
    "A qualified human must review all outputs."
)

# Pattern-only identifiers. Name/place recognition is supplied via an approved gazetteer.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AADHAAR", re.compile(r"(?<![\d+])(?:\d[ -]?){11}\d(?!\d)")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.I)),
    ("EMAIL", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("PHONE", re.compile(r"(?<!\w)(?:\+?91[\s-]?)?[6-9](?:[\s-]?\d){9}(?!\w)")),
)

# High-confidence hostile instruction patterns.
_INJECTION_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
    r"disregard\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
    r"override\s+(?:the\s+)?(?:system|developer|safety)\s+(?:prompt|instructions|rules)",
    r"reveal\s+(?:the\s+)?(?:system\s+prompt|hidden instructions|secrets)",
    r"\b(?:tool call|run this command|execute this code)\b",
    r"(?:पिछले|पहले)\s+(?:निर्देशों|आदेशों)\s+को\s+अनदेखा\s+करो",
    r"(?:सिस्टम|सुरक्षा)\s+निर्देशों\s+को\s+बदल(?:ो|ें)",
    r"act\s+as\s+(?:an?\s+)?(?:unrestricted|uncensored|different)\s+(?:ai|assistant|model)",
    r"(?:do\s+not|don't)\s+follow\s+(?:your\s+)?(?:safety|system)\s+(?:rules|instructions)",
    r"(?:diagnose|prescribe)\s+(?:the\s+)?patient\s+despite\s+(?:the\s+)?(?:rules|warnings)",
    r"(?:प्रणाली|डेवलपर)\s+निर्देशों\s+को\s+नज़रअंदाज़",
))

# Ambiguous role/tool instructions: call an enabled judge only if configured; otherwise quarantine.
_BORDERLINE_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"\b(?:system|developer|assistant)\s*:\s*",
    r"\b(?:new instructions|new task|change your role|act as|pretend to be)\b",
    r"\b(?:call|use)\s+(?:the\s+)?(?:tool|function|browser|shell)\b",
    r"\b(?:ignore|bypass|disable)\s+(?:the\s+)?(?:safety|policy|filter|guard)\b",
    r"(?:निर्देश|भूमिका|सिस्टम)\s*[:：]",
    r"\b(?:reveal|print|show)\s+(?:hidden|internal)\s+(?:prompt|instructions)\b",
))

_PROHIBITED_OUTPUT_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"\b(?:diagnosed with|diagnosis is|the diagnosis|diagnosis\s*:)\b",
    r"\b(?:take|start|stop|increase|decrease)\s+(?:the\s+)?(?:medicine|medication|drug|tablet|dose)\b",
    r"\b(?:prescribe|prescription|treatment plan|dosage|\d+\s*mg)\b",
    r"\b(?:treat the patient|treatment should|you should take|must take)\b",
))


@dataclass(frozen=True)
class SentinelResult:
    safe: bool
    text: str
    reasons: tuple[str, ...] = ()
    confidence: str = "low"


@dataclass(frozen=True)
class SafetyResult:
    status: str
    text: str
    flags: tuple[str, ...] = ()


class SafetyAgent:
    def __init__(self, name_place_gazetteer: tuple[str, ...] = (),
                 borderline_judge: Callable[[str], bool] | None = None):
        self.gazetteer = tuple(sorted({x.strip() for x in name_place_gazetteer if x.strip()},
                                      key=len, reverse=True))
        self.borderline_judge = borderline_judge

    @staticmethod
    def _normalize(text: str) -> str:
        clean = unicodedata.normalize("NFKC", text)
        clean = "".join(ch for ch in clean if unicodedata.category(ch) != "Cf")
        clean = html.unescape(urllib.parse.unquote(clean))
        # Decode only short, plausible Base64 tokens, and scan both original and decoded forms.
        for token in re.findall(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{24,256}={0,2}(?![A-Za-z0-9+/])", clean):
            try:
                decoded = base64.b64decode(token, validate=True).decode("utf-8")
                clean += "\n" + decoded
            except Exception:
                continue
        return clean

    def scan_input(self, text: str) -> SentinelResult:
        normalized = self._normalize(text)
        hard = tuple(f"hard_pattern_{i + 1}" for i, pattern in enumerate(_INJECTION_PATTERNS)
                     if pattern.search(normalized))
        if hard:
            return SentinelResult(False, "[QUARANTINED_SPAN]", hard, "high")
        borderline = tuple(f"borderline_pattern_{i + 1}" for i, pattern in enumerate(_BORDERLINE_PATTERNS)
                           if pattern.search(normalized))
        if borderline:
            if (self.borderline_judge is not None and settings.provider_enabled("groq")
                    and self.gazetteer and normalized.isascii()):
                try:
                    judge_text = self.redact(normalized, pass_number=1)
                    judge_text = self.redact(judge_text, pass_number=2)
                    if self.borderline_judge(judge_text):
                        return SentinelResult(False, "[QUARANTINED_SPAN]", borderline, "high")
                    return SentinelResult(True, text, borderline, "medium")
                except Exception:
                    pass
            # Missing/failed judge or an unresolved score: quarantine rather than guess.
            return SentinelResult(False, "[QUARANTINED_SPAN]", borderline, "medium")
        return SentinelResult(True, text, (), "low")

    def redact(self, text: str, pass_number: int = 1) -> str:
        result = text
        if pass_number not in (1, 2):
            raise ValueError("pass_number must be 1 or 2")
        for label, pattern in _PATTERNS:
            result = pattern.sub(f"[REDACTED_{label}]", result)
        if pass_number == 2:
            for value in self.gazetteer:
                result = re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", "[REDACTED_NAME_OR_PLACE]",
                                result, flags=re.I)
        return result

    def prepare_external_text(self, text: str) -> str:
        """Fail closed: check, redact identifiers, and re-check derived text."""
        if not self.gazetteer:
            raise ValueError("name/place gazetteer unavailable; external egress is blocked")
        first = self.scan_input(text)
        if not first.safe:
            raise ValueError("input quarantined by safety sentinel")
        redacted = self.redact(text, pass_number=1)
        redacted = self.redact(redacted, pass_number=2)
        second = self.scan_input(redacted)
        if not second.safe:
            raise ValueError("derived text quarantined by safety sentinel")
        return redacted

    def guard_output(self, text: str) -> SafetyResult:
        if not isinstance(text, str) or not text.strip():
            return SafetyResult("quarantined", "", ("empty_output",))
        if any(pattern.search(text) for pattern in _PROHIBITED_OUTPUT_PATTERNS):
            return SafetyResult("quarantined", "", ("prohibited_medical_advice_language",))
        if DISCLAIMER not in text:
            text = f"{text.rstrip()}\n\n{DISCLAIMER}"
        return SafetyResult("cleared", text, ("human_review_required",))


class VerifierAgent:
    """Second-opinion verifier; deterministic checks always run and risk never falls."""

    _rank = {"insufficient_information": 0, "green": 1, "amber": 2, "red": 3}

    def __init__(self, groq_call: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
                 safety_agent: SafetyAgent | None = None):
        self.groq_call = groq_call
        self.safety = safety_agent or SafetyAgent()

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, Mapping):
            return item.get(key, default)
        return getattr(item, key, default)

    def _redact_payload(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.safety.prepare_external_text(value) if value.strip() else value
        if isinstance(value, Mapping):
            return {key: self._redact_payload(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact_payload(item) for item in value]
        return value

    def verify(self, *, source_text: str, facts: Sequence[QuoteFact | Mapping[str, Any]],
               risk_assessment: RiskAssessment | Mapping[str, Any],
               rules: Sequence[Any] = ()) -> VerifierResult:
        flags: list[str] = []
        issues: list[str] = []
        source = source_text if isinstance(source_text, str) else ""
        if len(source) > 20000:
            source = source[:20000]
            flags.append("source_text_truncated")
            issues.append("source_text_truncated")

        supported_count = 0
        valid_facts: list[QuoteFact] = []
        for raw_fact in list(facts)[:1000]:
            try:
                fact = raw_fact if isinstance(raw_fact, QuoteFact) else QuoteFact.model_validate(raw_fact)
            except (ValidationError, TypeError, ValueError):
                flags.append("invalid_fact_schema")
                issues.append("invalid_fact_schema")
                continue
            valid_facts.append(fact)
            if fact.quote in source:
                supported_count += 1
            else:
                flags.append("unsupported_fact_quote")
                issues.append("unsupported_fact_quote")
        if len(facts) > 1000:
            flags.append("fact_limit_exceeded")
            issues.append("fact_limit_exceeded")

        raw_risk = risk_assessment.model_dump() if isinstance(risk_assessment, RiskAssessment) else risk_assessment
        if not isinstance(raw_risk, Mapping):
            raw_risk = {}
            flags.append("invalid_risk_assessment_schema")
            issues.append("invalid_risk_assessment_schema")
        category = raw_risk.get("category", "insufficient_information")
        if category not in self._rank:
            category = "insufficient_information"
            flags.append("invalid_risk_category")
            issues.append("invalid_risk_category")
        reasoning = raw_risk.get("reasoning_summary", "")
        if not isinstance(reasoning, str) or not reasoning.strip():
            reasoning = ""
            flags.append("missing_risk_reasoning")
            issues.append("missing_risk_reasoning")

        hits: list[RuleHit] = []
        raw_hits = raw_risk.get("triggered_rules", [])
        if not isinstance(raw_hits, list):
            raw_hits = []
            flags.append("invalid_triggered_rules")
            issues.append("invalid_triggered_rules")
        for raw_hit in raw_hits[:100]:
            try:
                hits.append(raw_hit if isinstance(raw_hit, RuleHit) else RuleHit.model_validate(raw_hit))
            except (ValidationError, TypeError, ValueError):
                flags.append("invalid_rule_hit_schema")
                issues.append("invalid_rule_hit_schema")

        matching_rules: dict[str, tuple[str, tuple[str, ...], str, str]] = {}
        source_folded = source.casefold()
        for rule in rules:
            rule_id = self._value(rule, "rule_id")
            rule_category = self._value(rule, "category")
            terms = self._value(rule, "trigger_terms", ())
            if not isinstance(rule_id, str) or rule_category not in self._rank:
                continue
            if not isinstance(terms, (tuple, list)):
                continue
            terms = tuple(term for term in terms if isinstance(term, str) and term.strip())
            if any(term.casefold() in source_folded for term in terms):
                matching_rules[rule_id] = (
                    rule_category,
                    terms,
                    str(self._value(rule, "source", "")),
                    str(self._value(rule, "section", "")),
                )

        hit_ids: set[str] = set()
        for hit in hits:
            hit_ids.add(hit.rule_id)
            approved = matching_rules.get(hit.rule_id)
            if approved is None:
                flags.append("unapproved_or_unmatched_rule_hit")
                issues.append("unapproved_or_unmatched_rule_hit")
                continue
            approved_category, terms, approved_source, approved_section = approved
            if hit.category != approved_category or hit.source != approved_source or hit.section != approved_section:
                flags.append("rule_hit_metadata_mismatch")
                issues.append("rule_hit_metadata_mismatch")
            if hit.evidence not in source:
                flags.append("unsupported_rule_evidence")
                issues.append("unsupported_rule_evidence")
            if not any(term.casefold() in hit.evidence.casefold() for term in terms):
                flags.append("rule_evidence_does_not_match_trigger")
                issues.append("rule_evidence_does_not_match_trigger")
            if hit.rule_id not in reasoning:
                flags.append("rule_missing_from_reasoning_summary")
                issues.append("rule_missing_from_reasoning_summary")

        for rule_id in matching_rules:
            if rule_id not in hit_ids:
                flags.append("matching_rule_omitted_from_assessment")
                issues.append("matching_rule_omitted_from_assessment")

        expected_category = max(
            (details[0] for details in matching_rules.values()),
            key=lambda item: self._rank[item],
            default="insufficient_information",
        )
        raised = False
        if self._rank[expected_category] > self._rank[category]:
            category = expected_category
            raised = True
            flags.append("risk_category_raised_to_matching_rule")
        elif category != "insufficient_information" and not matching_rules:
            flags.append("risk_category_has_no_matching_rule")
            issues.append("risk_category_has_no_matching_rule")
        elif self._rank[category] > self._rank[expected_category]:
            # Conservative over-triage is retained, but the inconsistency is surfaced.
            flags.append("risk_category_exceeds_matching_rules")
            issues.append("risk_category_exceeds_matching_rules")

        if reasoning:
            if not self.safety.scan_input(reasoning).safe:
                flags.append("risk_reasoning_quarantined")
                issues.append("risk_reasoning_quarantined")
            if self.safety.guard_output(reasoning).status != "cleared":
                flags.append("prohibited_diagnostic_or_treatment_language")
                issues.append("prohibited_diagnostic_or_treatment_language")

        groq_verified = False
        verification_source = "deterministic"
        if (not issues and self.groq_call is not None
                and settings.provider_enabled("groq")):
            if not self.safety.gazetteer:
                flags.append("groq_egress_blocked_gazetteer_unavailable")
            else:
                try:
                    payload = self._redact_payload({
                        "required_model_family": "groq",
                        "source_text": source,
                        "facts": [fact.model_dump() for fact in valid_facts],
                        "risk_assessment": {
                            "category": category,
                            "triggered_rules": [hit.model_dump() for hit in hits],
                            "reasoning_summary": reasoning,
                        },
                        "approved_matching_rule_ids": sorted(matching_rules),
                    })
                    model_result = self.groq_call(payload)
                    if not isinstance(model_result, Mapping):
                        raise ValueError("Groq verifier returned a non-object response")
                    proposed_category = model_result.get("risk_category")
                    if proposed_category in self._rank:
                        if self._rank[proposed_category] > self._rank[category]:
                            category = proposed_category
                            raised = True
                            flags.append("risk_category_raised_by_groq")
                        elif self._rank[proposed_category] < self._rank[category]:
                            flags.append("lower_groq_risk_proposal_ignored")
                            issues.append("lower_groq_risk_proposal_ignored")
                    else:
                        flags.append("invalid_groq_risk_category")
                        issues.append("invalid_groq_risk_category")
                    model_flags = model_result.get("flags", [])
                    if not isinstance(model_flags, list):
                        flags.append("invalid_groq_flags")
                        issues.append("invalid_groq_flags")
                    elif model_flags:
                        # Keep model-authored details out of the case record; expose a fixed flag only.
                        flags.append("groq_flagged_case")
                        issues.append("groq_flagged_case")
                    groq_verified = model_result.get("verified") is True
                    if not groq_verified:
                        flags.append("groq_verification_not_clear")
                        issues.append("groq_verification_not_clear")
                    verification_source = "groq"
                except Exception:
                    flags.append("groq_verification_unavailable")
                    issues.append("groq_verification_unavailable")

        if verification_source == "deterministic":
            flags.append("unverified_by_ai")
        status = "raised" if raised else (
            "passed" if verification_source == "groq" and groq_verified and not issues else "unverified"
        )
        return VerifierResult(
            status=status,
            risk_category=category,
            verification_source=verification_source,
            fact_count=min(len(facts), 1000),
            supported_fact_count=supported_count,
            flags=list(dict.fromkeys(flags))[:100],
            human_review_required=True,
        )
