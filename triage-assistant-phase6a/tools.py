"""Read-only, agent-allowlisted tools; this module has no database write interface."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agents.document import VitalsLabAnalyst
from agents.triage import QueueScheduler, TriageRule
from rag import ClinicalKnowledgeAgent
from schemas import RawLabRow


class ReadOnlyToolRegistry:
    """Expose only the plan's read-only rule, lab-range, and guideline tools."""

    ALLOWLISTS = {
        "triage_agent": {"rules_engine", "lab_range_lookup", "guideline_search"},
        "clinical_knowledge_agent": {"guideline_search"},
        "verifier_agent": {"rules_engine", "lab_range_lookup", "guideline_search"},
        "lab_analyst": {"lab_range_lookup"},
        "queue_scheduler": {"queue_scoring_config"},
    }

    def __init__(self, rule_pack_path: str | Path,
                 knowledge_agent: ClinicalKnowledgeAgent | None = None):
        self.rule_pack_path = Path(rule_pack_path)
        self.knowledge_agent = knowledge_agent or ClinicalKnowledgeAgent()
        self._tools = {
            "rules_engine": self._rules_engine,
            "lab_range_lookup": self._lab_range_lookup,
            "guideline_search": self._guideline_search,
            "queue_scoring_config": self._queue_scoring_config,
        }

    def call(self, *, agent: str, tool: str, **arguments: Any) -> Any:
        if tool not in self.ALLOWLISTS.get(agent, set()):
            raise PermissionError("tool is not allowed for this agent")
        handler = self._tools.get(tool)
        if handler is None:
            raise PermissionError("tool is not available")
        # Handlers return schema objects/data only; no handler has store/DB access.
        return handler(**arguments)

    def _scenario_profile(self, scenario: str) -> dict[str, Any]:
        try:
            root = yaml.safe_load(self.rule_pack_path.read_text(encoding="utf-8")) or {}
            scenarios = root.get("scenarios", {}) if isinstance(root, dict) else {}
            profile = scenarios.get(scenario, {}) if isinstance(scenarios, dict) else {}
            return profile if isinstance(profile, dict) else {}
        except (OSError, yaml.YAMLError, AttributeError):
            return {}

    def _rules_engine(self, *, scenario: str) -> list[TriageRule]:
        entries = self._scenario_profile(scenario).get("rules", [])
        if not isinstance(entries, list):
            return []
        result: list[TriageRule] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            terms = entry.get("trigger_terms", [])
            if not isinstance(terms, list) or any(not isinstance(term, str) for term in terms):
                continue
            try:
                rule = TriageRule(
                    rule_id=str(entry["rule_id"]), category=str(entry["category"]),
                    trigger_terms=tuple(terms), source=str(entry["source"]),
                    section=str(entry.get("section", "")),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if rule.rule_id and rule.category in {"red", "amber", "green"} and rule.source:
                result.append(rule)
        return result

    def _lab_range_lookup(self, *, scenario: str, rows: list[RawLabRow],
                          age: float | None = None, sex: str | None = None,
                          pregnancy: bool | None = None):
        try:
            analyst = VitalsLabAnalyst.from_rule_pack(str(self.rule_pack_path), scenario)
        except Exception:
            analyst = VitalsLabAnalyst()
        return analyst.analyze(rows, age=age, sex=sex, pregnancy=pregnancy)

    def _guideline_search(self, *, query: str, top_k: int = 3):
        return self.knowledge_agent.search(query, top_k=top_k)

    def _queue_scoring_config(self, *, scenario: str) -> QueueScheduler:
        return QueueScheduler.from_rule_pack(str(self.rule_pack_path), scenario)
