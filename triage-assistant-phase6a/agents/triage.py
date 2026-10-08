"""Triage/Risk, Scenario Router, and Queue Scheduler agents."""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any

from agents.safety import SafetyAgent
from config import settings
from schemas import (
    QueueAlert, QueueCaseInput, QueueItem, QueueSchedule, QueueScoringConfig,
    RiskAssessment, RiskCategory, RuleHit,
)
import yaml

SCENARIOS = {
    "outpatient_queue", "occupational_screening", "campus_fever",
    "maternal_follow_up", "chronic_check_in", "camp_screening",
}


@dataclass(frozen=True)
class ScenarioProfile:
    name: str
    rule_pack: str
    prompt_profile: str
    required_fields: tuple[str, ...] = ()
    routing_note: str = ""


@dataclass(frozen=True)
class TriageRule:
    rule_id: str
    category: str
    trigger_terms: tuple[str, ...]
    source: str
    section: str = ""


class ScenarioRouter:
    """Route from declared scenario and configured keywords; only resolve conflicts with Gemini."""

    def __init__(self, rule_pack_path: str | Path | None = None,
                 conflict_resolver: Callable[[dict[str, Any]], str] | None = None):
        self.conflict_resolver = conflict_resolver
        path = Path(rule_pack_path) if rule_pack_path else Path(__file__).parents[1] / "rulepacks" / "scenarios.yaml"
        try:
            content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            profiles = content.get("scenarios", {}) if isinstance(content, dict) else {}
            self.profiles = profiles if isinstance(profiles, dict) else {}
        except (OSError, yaml.YAMLError, AttributeError):
            self.profiles = {}

    def _profile(self, name: str, note: str) -> ScenarioProfile:
        raw = self.profiles.get(name, {}) if isinstance(self.profiles, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        required = raw.get("required_fields", [])
        if not isinstance(required, list) or any(not isinstance(x, str) for x in required):
            required = []
        return ScenarioProfile(
            name=name,
            rule_pack=str(raw.get("rule_pack") or name),
            prompt_profile=str(raw.get("prompt_profile") or name),
            required_fields=tuple(required),
            routing_note=note,
        )

    def _keyword_matches(self, text: str) -> list[tuple[str, str]]:
        normalized = text.casefold()
        matches: list[tuple[str, str]] = []
        for scenario, raw in self.profiles.items():
            if scenario not in SCENARIOS or not isinstance(raw, dict):
                continue
            keywords = raw.get("keywords", [])
            if not isinstance(keywords, list):
                continue
            for keyword in keywords:
                if isinstance(keyword, str) and keyword.strip() and re.search(
                    rf"(?<!\w){re.escape(keyword.casefold())}(?!\w)", normalized
                ):
                    matches.append((scenario, keyword))
        # Multiple synonymous matches for one scenario are still a single route.
        return list(dict.fromkeys(matches))

    def route(self, declared_scenario: str | None = None, text: str = "") -> ScenarioProfile:
        declared = declared_scenario if declared_scenario in SCENARIOS else None
        keyword_matches = self._keyword_matches(text)
        keyword_scenarios = list(dict.fromkeys(name for name, _ in keyword_matches))
        keyword_scenario = keyword_scenarios[0] if len(keyword_scenarios) == 1 else None

        if declared and keyword_scenario and declared != keyword_scenario:
            if self.conflict_resolver is not None and settings.provider_enabled("gemini"):
                try:
                    resolved = self.conflict_resolver({
                        "declared_scenario": declared,
                        "keyword_scenario": keyword_scenario,
                        "matched_keywords": [word for _, word in keyword_matches],
                    })
                    if resolved in SCENARIOS:
                        return self._profile(resolved, "Scenario conflict resolved by configured Gemini callback.")
                except Exception:
                    pass
            return self._profile("outpatient_queue", "Scenario disagreement unresolved; defaulted to cautious outpatient profile.")

        if declared:
            return self._profile(declared, "Scenario selected from declared value.")
        if keyword_scenario:
            return self._profile(keyword_scenario, "Scenario selected by configured keyword rule.")
        note = "Ambiguous or absent scenario evidence; defaulted to cautious general outpatient profile."
        return self._profile("outpatient_queue", note)


class TriageAgent:
    """Rules choose the category; optional model callback may only explain it."""
    _rank = {"insufficient_information": 0, "green": 1, "amber": 2, "red": 3}

    def __init__(self, rationale_call: Callable[[dict[str, Any]], str] | None = None,
                 safety_agent: SafetyAgent | None = None):
        self.rationale_call = rationale_call
        self.safety = safety_agent or SafetyAgent()

    def _redact_gateway_value(self, value: Any) -> Any:
        if isinstance(value, str):
            if not value.strip():
                return value
            if not value.isascii():
                raise ValueError("only redacted English may leave the machine")
            return self.safety.prepare_external_text(value)
        if isinstance(value, dict):
            return {key: self._redact_gateway_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_gateway_value(item) for item in value]
        return value

    def assess(self, symptom_text: str, lab_text: str = "", rules: Iterable[TriageRule] = ()) -> RiskAssessment:
        combined = f"{symptom_text}\n{lab_text}"
        hits: list[RuleHit] = []
        for rule in rules:
            if rule.category not in {"red", "amber", "green"}:
                continue
            matches = [term for term in rule.trigger_terms if term and term.casefold() in combined.casefold()]
            if matches:
                evidence = next((line.strip() for line in combined.splitlines()
                                 if any(term.casefold() in line.casefold() for term in matches)), matches[0])
                hits.append(RuleHit(rule_id=rule.rule_id, category=rule.category,
                                    source=rule.source, section=rule.section, evidence=evidence[:500]))
        if not hits:
            return RiskAssessment(
                category="insufficient_information",
                reasoning_summary="No approved scenario rule matched, or the required evidence is not configured. Human review is required; no category is inferred.",
                flags=["no_approved_rule_match", "human_review_required"],
            )
        category: RiskCategory = max((hit.category for hit in hits), key=lambda c: self._rank[c])  # type: ignore[assignment]
        summary = "Rule-based category from: " + ", ".join(hit.rule_id for hit in hits) + ". Human review is required."
        source = "template"
        if self.rationale_call is not None and self.safety.gazetteer:
            # The callback is explanatory only. Its output cannot change the category.
            try:
                payload = self._redact_gateway_value(
                    {"category": category, "rules": [h.model_dump() for h in hits]})
                candidate = self.rationale_call(payload)
                if isinstance(candidate, str) and candidate.strip() and len(candidate) <= 1500:
                    summary = candidate.strip()
                    source = "model"
            except Exception:
                pass
        return RiskAssessment(category=category, triggered_rules=hits,
                              reasoning_summary=summary, rationale_source=source,
                              flags=["human_review_required"])


class QueueScheduler:
    """Order cases from approved weights, or use the plan's category/arrival fallback.

    This returns dashboard alert events for an idempotent outbox; it never sends alerts.
    Age-extreme and vital-abnormality booleans must be determined upstream from approved
    configuration. No age thresholds or queue weights are embedded here.
    """

    _category_rank = {
        "insufficient_information": 0,
        "green": 1,
        "amber": 2,
        "red": 3,
    }

    def __init__(self, scoring: QueueScoringConfig | None = None):
        self.scoring = scoring

    @classmethod
    def from_rule_pack(cls, path: str | Path, scenario: str) -> "QueueScheduler":
        try:
            content = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return cls()
        if not isinstance(content, dict):
            return cls()
        scenarios = content.get("scenarios", {})
        profile = scenarios.get(scenario, {}) if isinstance(scenarios, dict) else {}
        raw_weights = profile.get("queue_weights", {}) if isinstance(profile, dict) else {}
        if raw_weights in (None, {}):
            return cls()
        return cls(QueueScoringConfig.model_validate(raw_weights))

    def _score(self, case: QueueCaseInput) -> float:
        if self.scoring is None:
            raise RuntimeError("weighted score requested without a scoring configuration")
        weights = self.scoring
        value = (
            weights.category_points[case.risk_category]
            + (weights.vital_abnormality_weight if case.vital_abnormality is True else 0.0)
            + (weights.age_extreme_weight if case.age_extreme is True else 0.0)
            + (weights.pregnancy_weight if case.pregnancy is True else 0.0)
            + (case.condition_count or 0) * weights.condition_weight
            + case.waiting_minutes * weights.wait_time_weight
        )
        if not isfinite(value):
            raise ValueError("queue priority score is not finite")
        return value

    def schedule(self, cases: Sequence[QueueCaseInput | dict[str, Any]]) -> QueueSchedule:
        if len(cases) > 10000:
            raise ValueError("queue batch exceeds the configured safety limit")
        validated = [case if isinstance(case, QueueCaseInput) else QueueCaseInput.model_validate(case)
                     for case in cases]
        ids = [case.case_id for case in validated]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id must be unique within a queue batch")

        weighted_inputs_complete = True
        if self.scoring is not None:
            feature_fields = (
                ("vital_abnormality", self.scoring.vital_abnormality_weight),
                ("age_extreme", self.scoring.age_extreme_weight),
                ("pregnancy", self.scoring.pregnancy_weight),
                ("condition_count", self.scoring.condition_weight),
            )
            weighted_inputs_complete = all(
                weight == 0 or getattr(case, field) is not None
                for case in validated for field, weight in feature_fields
            )

        if self.scoring is None or not weighted_inputs_complete:
            ordered = sorted(validated, key=lambda case: (
                -self._category_rank[case.risk_category], case.arrival_time, case.case_id,
            ))
            strategy = "category_then_arrival"
            score_by_id: dict[str, float | None] = {case.case_id: None for case in ordered}
            flags = (["queue_weights_not_configured_category_then_arrival_fallback"]
                     if self.scoring is None else
                     ["weighted_inputs_incomplete_category_then_arrival_fallback"])
        else:
            score_by_id = {case.case_id: self._score(case) for case in validated}
            ordered = sorted(validated, key=lambda case: (
                -float(score_by_id[case.case_id]),
                -self._category_rank[case.risk_category],
                case.arrival_time,
                case.case_id,
            ))
            strategy = "weighted_score"
            flags = []

        items: list[QueueItem] = []
        alerts: list[QueueAlert] = []
        for position, case in enumerate(ordered, start=1):
            is_red = case.risk_category == "red"
            items.append(QueueItem(
                case_id=case.case_id,
                risk_category=case.risk_category,
                queue_position=position,
                priority_score=score_by_id[case.case_id],
                arrival_time=case.arrival_time,
                immediate_red_alert=is_red,
            ))
            if is_red:
                alerts.append(QueueAlert(
                    case_id=case.case_id,
                    event_type="immediate_red_alert",
                    idempotency_key=f"immediate-red:{case.case_id}",
                ))
        return QueueSchedule(strategy=strategy, items=items, alerts=alerts, flags=flags)
