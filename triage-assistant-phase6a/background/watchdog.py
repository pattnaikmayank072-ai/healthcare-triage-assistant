"""Re-triage Watchdog: event-driven structured monitoring with fail-closed escalation.

The default escalation rule pack deliberately contains no clinical thresholds. This
agent never sends messages and never reads encrypted case narratives or triage notes.
"""
from __future__ import annotations

import os
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from store import BackgroundStore


AGENT_NAME = "re_triage_watchdog"


@dataclass(frozen=True)
class WatchdogRunResult:
    status: str
    processed_events: int
    alerts_created: int
    last_event_sequence: int
    flags: tuple[str, ...] = ()


class ReTriageWatchdog:
    """Consume committed events and create only configured, auditable alerts.

    ``retriage`` is an application-owned callback supplied by the workflow layer.
    It receives a structured event and a restricted case projection, never raw text.
    The callback is optional so the watchdog can still report liveness while the
    re-triage lane is not yet configured.
    """

    def __init__(self, service: BackgroundStore, *,
                 rulepack_path: str | Path | None = None,
                 retriage: Callable[[Mapping[str, Any], Mapping[str, Any]], Any] | None = None,
                 owner_id: str | None = None):
        self.service = service
        self.rulepack_path = Path(rulepack_path or Path(__file__).parents[1] / "rulepacks" / "escalation.yaml")
        self.retriage = retriage
        self.owner_id = owner_id or f"watchdog:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def _load_rules(self) -> tuple[str, dict[str, list[dict[str, Any]]], str | None]:
        try:
            data = yaml.safe_load(self.rulepack_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return "unconfigured", {}, "escalation_rulepack_invalid"
            version = data.get("version")
            ladder = data.get("escalation_ladder")
            if not isinstance(version, str) or not version.strip() or not isinstance(ladder, dict):
                return "unconfigured", {}, "escalation_rulepack_invalid"
            validated: dict[str, list[dict[str, Any]]] = {}
            for category, steps in ladder.items():
                if category not in {"red", "amber", "green", "insufficient_information"}:
                    return version, {}, "escalation_rulepack_invalid"
                if not isinstance(steps, list) or len(steps) > 10:
                    return version, {}, "escalation_rulepack_invalid"
                category_steps = []
                for step in steps:
                    if not isinstance(step, dict):
                        return version, {}, "escalation_rulepack_invalid"
                    level = step.get("level")
                    reason = step.get("reason_code")
                    role = step.get("next_role")
                    delay = step.get("after_minutes")
                    if (not isinstance(level, str) or not level or len(level) > 80
                            or not isinstance(reason, str) or not reason or len(reason) > 80
                            or role not in {"nurse", "doctor", "medical_officer"}
                            or isinstance(delay, bool) or not isinstance(delay, (int, float))
                            or not math.isfinite(float(delay)) or float(delay) < 0):
                        return version, {}, "escalation_rulepack_invalid"
                    category_steps.append({"level": level, "reason_code": reason,
                                           "next_role": role, "after_minutes": float(delay)})
                validated[category] = category_steps
            return version, validated, None
        except FileNotFoundError:
            return "unconfigured", {}, "escalation_rulepack_missing"
        except Exception:
            return "unconfigured", {}, "escalation_rulepack_invalid"

    @staticmethod
    def _elapsed_minutes(created_at: str, now: datetime) -> float | None:
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 60.0)
        except (ValueError, TypeError):
            return None

    def _scan_escalations(self, version: str, ladder: dict[str, list[dict[str, Any]]],
                          now: datetime) -> tuple[int, list[str]]:
        if not ladder or not any(ladder.values()):
            return 0, ["escalation_thresholds_unconfigured"]
        created = 0
        flags: list[str] = []
        for case in self.service.reader.waiting_cases():
            if case.get("workflow_state") not in {"awaiting_review", "escalated"}:
                continue
            if case.get("acknowledged_at"):
                continue
            category = case.get("risk_category")
            steps = ladder.get(category, [])
            waiting = self._elapsed_minutes(str(case.get("created_at", "")), now)
            if waiting is None:
                flags.append("case_timestamp_unavailable")
                continue
            for step in steps:
                if waiting < step["after_minutes"]:
                    continue
                case_id = str(case["case_id"])
                idempotency_key = f"watchdog:{case_id}:r{case['revision']}:{step['level']}"
                try:
                    created += int(self.service.create_alert(
                        case_id=case_id, revision=int(case["revision"]), level=step["level"],
                        reason_code=step["reason_code"], waiting_minutes=waiting,
                        current_category=str(category), score=case.get("priority_score"),
                        next_role=step["next_role"], rule_version=version,
                        idempotency_key=idempotency_key,
                    ))
                except Exception:
                    flags.append("alert_creation_failed")
        return created, flags

    def run_once(self, *, now: datetime | None = None, event_limit: int = 500) -> WatchdogRunResult:
        timestamp = now or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise ValueError("watchdog time must be timezone-aware")
        version, ladder, rule_error = self._load_rules()
        run_id = self.service.acquire_lease("re_triage_watchdog", self.owner_id, lease_seconds=55)
        if run_id is None:
            return WatchdogRunResult("lease_held", 0, 0, self.service.cursor(AGENT_NAME), ("lease_not_acquired",))

        cursor = self.service.cursor(AGENT_NAME)
        processed = 0
        alerts_created = 0
        flags: list[str] = []
        status = "ok"
        last_sequence = cursor
        try:
            if rule_error:
                flags.append(rule_error)
            for event in self.service.reader.events_after(cursor, limit=event_limit):
                sequence = int(event["sequence"])
                run_key = f"event:{sequence}"
                if not self.service.begin_run(AGENT_NAME, run_key):
                    self.service.advance_cursor(AGENT_NAME, sequence)
                    last_sequence = sequence
                    continue
                try:
                    if event["event_type"] == "vitals_updated":
                        case = self.service.reader.case(str(event.get("case_id") or ""))
                        if case is None:
                            # Expired/deleted cases are safe to acknowledge in the cursor.
                            flags.append("event_case_unavailable")
                        elif self.retriage is None:
                            raise RuntimeError("retriage_handler_not_configured")
                        else:
                            self.retriage(event, case)
                    self.service.finish_run(AGENT_NAME, run_key, sequence)
                    last_sequence = sequence
                    processed += 1
                except Exception as exc:
                    code = "retriage_handler_not_configured" if str(exc) == "retriage_handler_not_configured" else "event_processing_failed"
                    self.service.fail_run(AGENT_NAME, run_key, code)
                    flags.append(code)
                    status = "degraded"
                    break

            new_alerts, scan_flags = self._scan_escalations(version, ladder, timestamp.astimezone(timezone.utc))
            alerts_created += new_alerts
            flags.extend(scan_flags)
            if flags:
                status = "degraded"
            self.service.heartbeat(AGENT_NAME, status, last_sequence,
                                   (rule_error or (flags[0] if flags else None)))
            return WatchdogRunResult(status, processed, alerts_created, last_sequence,
                                     tuple(dict.fromkeys(flags)))
        except Exception:
            self.service.heartbeat(AGENT_NAME, "failed", last_sequence, "watchdog_run_failed")
            raise
        finally:
            self.service.release_lease("re_triage_watchdog", self.owner_id, run_id)
