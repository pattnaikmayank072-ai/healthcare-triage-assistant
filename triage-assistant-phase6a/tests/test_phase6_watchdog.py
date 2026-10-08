from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from cryptography.fernet import Fernet

from background.watchdog import ReTriageWatchdog
from store import Actor, AuthorizationError, BackgroundStore, Gatekeeper, WorkflowStore, _insert_background_event


class BackgroundFoundationTests(unittest.TestCase):
    def setUp(self):
        self._old_key = os.environ.get("DATA_ENCRYPTION_KEY")
        os.environ["DATA_ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "background.sqlite3")
        self.gatekeeper = Gatekeeper(self.db_path, jwt_secret="x" * 40)
        self.actor = Actor("worker-1", "nurse", "facility-a")
        self.gatekeeper.provision_user(self.actor.user_id, "synthetic-password", self.actor.role,
                                       self.actor.facility_id)
        self.workflow = WorkflowStore(self.gatekeeper)
        self.background = self.workflow.background_store()
        self.consent = self.gatekeeper.record_consent(self.actor, "synthetic test", True)
        self.case = self.gatekeeper.start_case(self.actor, self.consent, "outpatient_queue", "create-1")

    def tearDown(self):
        self.gatekeeper.close()
        self.temp_dir.cleanup()
        if self._old_key is None:
            os.environ.pop("DATA_ENCRYPTION_KEY", None)
        else:
            os.environ["DATA_ENCRYPTION_KEY"] = self._old_key

    def test_case_event_is_committed_with_case_and_contains_only_safe_fields(self):
        rows = self.background.reader.events_after(0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "case_created")
        self.assertEqual(set(rows[0]["payload"]), {"scenario", "pseudonymous_id"})
        self.assertEqual(rows[0]["case_id"], self.case.case_id)

    def test_reader_blocks_encrypted_and_narrative_columns_even_if_requested(self):
        with self.assertRaises(sqlite3.DatabaseError):
            self.background.reader._select("SELECT payload_ciphertext FROM case_workflows")
        with self.assertRaises(sqlite3.DatabaseError):
            self.background.reader._select("SELECT triage_note_ciphertext FROM case_workflows")
        with self.assertRaises(sqlite3.DatabaseError):
            self.background.reader._select("SELECT purpose FROM consents")

    def test_intake_event_is_atomic_and_does_not_copy_text(self):
        payload = {
            "language": "en",
            "redacted_original": "SYNTHETIC PRIVATE NARRATIVE",
            "redacted_english": "SYNTHETIC PRIVATE NARRATIVE",
            "original_ciphertext": None,
            "audio_persisted": False,
        }
        self.workflow.save_intake(self.case.case_id, payload, "accepted")
        rows = self.background.reader.events_after(0)
        intake = next(row for row in rows if row["event_type"] == "intake_received")
        self.assertEqual(intake["payload"], {"language": "en", "status": "accepted"})
        self.assertNotIn("SYNTHETIC PRIVATE NARRATIVE", str(intake))

        before = self.workflow.case_payload(self.case.case_id)
        with patch("store._insert_background_event", side_effect=ValueError("test rollback")):
            with self.assertRaises(ValueError):
                self.workflow.save_intake(self.case.case_id, {"language": "hi"}, "accepted")
        self.assertEqual(self.workflow.case_payload(self.case.case_id), before)

    def test_lease_excludes_concurrent_workers_and_recovers_after_expiry(self):
        first = self.background.acquire_lease("watchdog", "worker-a", lease_seconds=10, now_epoch=100)
        self.assertIsNotNone(first)
        self.assertIsNone(self.background.acquire_lease("watchdog", "worker-b", lease_seconds=10,
                                                        now_epoch=105))
        second = self.background.acquire_lease("watchdog", "worker-b", lease_seconds=10, now_epoch=111)
        self.assertIsNotNone(second)
        self.assertFalse(self.background.release_lease("watchdog", "worker-a", first))
        self.assertTrue(self.background.release_lease("watchdog", "worker-b", second))

    def test_run_ledger_and_cursor_commit_together(self):
        self.assertTrue(self.background.begin_run("test_agent", "event:1"))
        self.background.finish_run("test_agent", "event:1", 17)
        self.assertFalse(self.background.begin_run("test_agent", "event:1"))
        self.assertEqual(self.background.cursor("test_agent"), 17)
        self.assertTrue(self.background.begin_run("test_agent", "event:2"))
        self.background.fail_run("test_agent", "event:2", "synthetic_failure")
        self.assertTrue(self.background.begin_run("test_agent", "event:2"))

    def test_alert_creation_is_idempotent_facility_scoped_and_acknowledgeable(self):
        kwargs = dict(case_id=self.case.case_id, revision=0, level="nurse-stage-1",
                      reason_code="synthetic-test", waiting_minutes=1.0,
                      current_category="red", score=None, next_role="nurse",
                      rule_version="synthetic-v1", idempotency_key="test-alert-1")
        self.assertTrue(self.background.create_alert(**kwargs))
        self.assertFalse(self.background.create_alert(**kwargs))
        alerts = self.background.alerts_for_facility("facility-a", status="open")
        self.assertEqual(len(alerts), 1)
        alert_id = alerts[0]["alert_id"]
        with self.assertRaises(AuthorizationError):
            self.background.acknowledge_alert(alert_id, Actor("nurse-b", "nurse", "facility-b"))
        self.assertTrue(self.background.acknowledge_alert(alert_id, self.actor))
        self.assertFalse(self.background.acknowledge_alert(alert_id, self.actor))
        self.assertTrue(self.workflow.verify_audit_chain())

    def test_watchdog_does_not_escalate_without_approved_thresholds(self):
        self.workflow.save_triage_note(self.case.case_id, {
            "risk_category": "red", "queue_priority_score": None,
            "reviewer_action_panel": {"status": "awaiting_review"},
        })
        result = ReTriageWatchdog(self.background, owner_id="synthetic-watchdog").run_once(
            now=datetime.now(timezone.utc)
        )
        self.assertEqual(result.alerts_created, 0)
        self.assertIn("escalation_thresholds_unconfigured", result.flags)
        self.assertEqual(self.background.alerts_for_facility("facility-a"), [])
        self.assertEqual(self.background.heartbeat_status("re_triage_watchdog")["status"], "degraded")

    def test_vitals_event_without_retriage_callback_is_not_advanced_or_lost(self):
        with self.gatekeeper._lock:
            seq = _insert_background_event(
                self.gatekeeper.db, "vitals_updated", case_id=self.case.case_id,
                facility_id="facility-a", revision=1,
                payload={"revision": 1, "vitals": {"pulse": 120}, "symptom_groups": [],
                         "onset_week": None, "age_band": None, "sex": None,
                         "site_id": None, "coarse_unit": None},
            )
        result = ReTriageWatchdog(self.background, owner_id="synthetic-watchdog-no-callback").run_once(
            now=datetime.now(timezone.utc)
        )
        self.assertEqual(result.status, "degraded")
        self.assertIn("retriage_handler_not_configured", result.flags)
        self.assertLess(self.background.cursor("re_triage_watchdog"), seq)


if __name__ == "__main__":
    unittest.main()
