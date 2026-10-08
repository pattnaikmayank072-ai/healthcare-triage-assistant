from __future__ import annotations

import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import main
from agents.safety import DISCLAIMER
from schemas import CaseEnvelope, TriageNote
from store import Actor


class Phase5WorkflowApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.environment = patch.dict(os.environ, {
            "JWT_SIGNING_SECRET": "phase5-test-signing-secret-" + "x" * 48,
            "DATA_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "TRIAGE_DATABASE_PATH": os.path.join(self.temp.name, "phase5.sqlite3"),
            "RETENTION_DAYS": "",
        }, clear=False)
        self.environment.start()
        main._configured_workflow_store.cache_clear()
        main._configured_gatekeeper.cache_clear()
        self.client = TestClient(main.app)
        self.gatekeeper = main._configured_gatekeeper()
        self.gatekeeper.provision_user("worker-A", "synthetic-password-A", "health_worker", "facility-A")
        self.gatekeeper.provision_user("nurse-A", "synthetic-password-N", "nurse", "facility-A")
        self.gatekeeper.provision_user("doctor-A", "synthetic-password-D", "doctor", "facility-A")
        self.gatekeeper.provision_user("nurse-B", "synthetic-password-B", "nurse", "facility-B")
        self.gatekeeper.provision_user("admin-A", "synthetic-password-X", "admin", "facility-A")
        self.gatekeeper.provision_user("admin-B", "synthetic-password-Y", "admin", "facility-B")

    def tearDown(self):
        main._configured_workflow_store.cache_clear()
        main._configured_gatekeeper.cache_clear()
        self.environment.stop()
        self.temp.cleanup()

    def token(self, user_id: str, password: str, key: str):
        response = self.client.post("/auth/login", headers={"Idempotency-Key": key},
                                    json={"user_id": user_id, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["access_token"]

    @staticmethod
    def bearer(token: str, idem: str | None = None):
        headers = {"Authorization": f"Bearer {token}"}
        if idem is not None:
            headers["Idempotency-Key"] = idem
        return headers

    def create_case(self, token: str, *, scenario="maternal_follow_up"):
        consent_response = self.client.post(
            "/consent", headers=self.bearer(token, "consent-key-001"),
            json={"purpose": "synthetic triage support", "consented": True, "language": "en"},
        )
        self.assertEqual(consent_response.status_code, 201, consent_response.text)
        consent_id = consent_response.json()["consent_id"]
        response = self.client.post(
            "/cases", headers=self.bearer(token, "case-key-001"),
            json={"consent_id": consent_id, "scenario": scenario, "language": "en"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["case_id"]

    def test_full_synthetic_workflow_review_reminder_queue_audit_and_delete(self):
        worker = self.token("worker-A", "synthetic-password-A", "login-worker")
        nurse = self.token("nurse-A", "synthetic-password-N", "login-nurse")
        doctor = self.token("doctor-A", "synthetic-password-D", "login-doctor")
        admin = self.token("admin-A", "synthetic-password-X", "login-admin")
        case_id = self.create_case(worker)

        intake = self.client.post(
            f"/cases/{case_id}/intake", headers=self.bearer(worker, "intake-key-001"),
            json={"text": "I have a cough for 2 days.", "language": "en"},
        )
        self.assertEqual(intake.status_code, 202, intake.text)
        self.assertTrue(intake.json()["encrypted_at_rest"])

        # The secure no-network OCR sandbox is unavailable: file bytes are not retained or parsed.
        upload = self.client.post(
            f"/cases/{case_id}/documents", headers=self.bearer(worker, "document-key-001"),
            files={"file": ("synthetic.png", b"\x89PNG\r\n\x1a\nsynthetic", "image/png")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        self.assertFalse(upload.json()["sanitized"])
        self.assertTrue(upload.json()["manual_entry_required"])
        self.assertIn("upload_not_retained", upload.json()["flags"])

        submitted = self.client.post(
            f"/cases/{case_id}/submit", headers=self.bearer(worker, "submit-key-001"),
        )
        self.assertEqual(submitted.status_code, 202, submitted.text)
        envelope = CaseEnvelope.model_validate(submitted.json())
        self.assertEqual(envelope.status, "awaiting_review")
        self.assertIsNotNone(envelope.triage_note)
        note = envelope.triage_note
        self.assertEqual(note.risk_category, "insufficient_information")
        self.assertEqual(note.reviewer_action_panel.status, "awaiting_review")
        self.assertIn(DISCLAIMER, note.disclaimer)
        self.assertTrue(envelope.degraded_mode)

        workflow_store = main._configured_workflow_store()
        with workflow_store._lock:
            encrypted_case = workflow_store.db.execute(
                "SELECT payload_ciphertext,triage_note_ciphertext FROM case_workflows WHERE case_id=?",
                (case_id,),
            ).fetchone()
            encrypted_steps = workflow_store.db.execute(
                "SELECT output_ciphertext FROM agent_steps WHERE case_id=?", (case_id,)
            ).fetchall()
            encrypted_replay = workflow_store.db.execute(
                "SELECT response_ciphertext FROM idempotency_records WHERE endpoint=?",
                (f"/cases/{case_id}/submit",),
            ).fetchone()
        for value in (encrypted_case["payload_ciphertext"], encrypted_case["triage_note_ciphertext"],
                      *[row["output_ciphertext"] for row in encrypted_steps],
                      encrypted_replay["response_ciphertext"]):
            self.assertNotIn("cough", value)

        # Repeating the same state-changing request replays the stored result.
        repeated = self.client.post(
            f"/cases/{case_id}/submit", headers=self.bearer(worker, "submit-key-001"),
        )
        self.assertEqual(repeated.status_code, 202)
        self.assertEqual(repeated.json(), submitted.json())

        # A second health worker/nurse cannot access another user's case, even in the same facility.
        other_worker = self.gatekeeper.login("nurse-A", "synthetic-password-N")["access_token"]
        # Case was created by worker-A; nurse-A is not the owner.
        denied = self.client.get(f"/cases/{case_id}", headers=self.bearer(other_worker))
        self.assertEqual(denied.status_code, 403)
        other_facility = self.gatekeeper.login("nurse-B", "synthetic-password-B")["access_token"]
        denied_facility = self.client.get(f"/cases/{case_id}", headers=self.bearer(other_facility))
        self.assertEqual(denied_facility.status_code, 403)

        visible_to_reviewer = self.client.get(f"/cases/{case_id}", headers=self.bearer(doctor))
        self.assertEqual(visible_to_reviewer.status_code, 200, visible_to_reviewer.text)
        self.assertEqual(visible_to_reviewer.json()["triage_note"]["chief_complaint_english"],
                         "I have a cough for 2 days.")

        queue = self.client.get("/queue", headers=self.bearer(nurse))
        self.assertEqual(queue.status_code, 200, queue.text)
        self.assertEqual(len(queue.json()["items"]), 1)
        self.assertEqual(queue.json()["items"][0]["risk_category"], "insufficient_information")

        edit_review = self.client.post(
            f"/cases/{case_id}/review", headers=self.bearer(doctor, "review-edit-key-001"),
            json={"decision": "edit", "reason": "Add a synthetic reviewer summary.",
                  "edits": {"reviewer_summary": "Reviewer checked this synthetic record."}},
        )
        self.assertEqual(edit_review.status_code, 200, edit_review.text)
        self.assertEqual(edit_review.json()["status"], "awaiting_review")
        edited_note = self.client.get(f"/cases/{case_id}", headers=self.bearer(doctor)).json()["triage_note"]
        self.assertEqual(edited_note["reviewer_action_panel"]["status"], "awaiting_review")
        self.assertIn(DISCLAIMER, edited_note["reviewer_summary"])

        review = self.client.post(
            f"/cases/{case_id}/review", headers=self.bearer(doctor, "review-key-001"),
            json={"decision": "approve", "reason": "Synthetic review complete; retain manual clinical review."},
        )
        self.assertEqual(review.status_code, 200, review.text)
        self.assertEqual(review.json()["status"], "reviewed")
        self.assertEqual(review.json()["queued_outbox_count"], 1)

        reminder = self.client.post(
            f"/cases/{case_id}/reminders", headers=self.bearer(doctor, "reminder-key-001"),
            json={"draft_indices": [0]},
        )
        self.assertEqual(reminder.status_code, 200, reminder.text)
        self.assertEqual(reminder.json()["queued_count"], 1)
        self.assertEqual(reminder.json()["delivery_status"], "pending_outbox")
        outbox = main._configured_workflow_store().outbox_entries(case_id)
        self.assertEqual({entry["action_type"] for entry in outbox}, {"referral_packet", "reminder"})
        self.assertTrue(all(entry["status"] == "pending" for entry in outbox))

        # Administrator sees audit metadata only; case content and review reason are not returned.
        admin_other_facility = self.token("admin-B", "synthetic-password-Y", "login-admin-B")
        denied_admin_delete = self.client.delete(
            f"/cases/{case_id}", headers=self.bearer(admin_other_facility, "cross-facility-delete")
        )
        self.assertEqual(denied_admin_delete.status_code, 403)
        audit = self.client.get("/audit", headers=self.bearer(admin))
        self.assertEqual(audit.status_code, 200, audit.text)
        audit_text = audit.text
        self.assertNotIn("I have a cough", audit_text)
        self.assertNotIn("Synthetic review complete", audit_text)
        self.assertTrue(all(entry["facility_id"] == "facility-A" for entry in audit.json()["entries"]))
        self.assertTrue(main._configured_workflow_store().verify_audit_chain())

        deleted = self.client.delete(f"/cases/{case_id}",
                                     headers=self.bearer(worker, "delete-key-001"))
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["status"], "purged")
        replay_delete = self.client.delete(f"/cases/{case_id}",
                                           headers=self.bearer(worker, "delete-key-001"))
        self.assertEqual(replay_delete.status_code, 200, replay_delete.text)
        self.assertEqual(replay_delete.json(), deleted.json())
        self.assertEqual(self.client.get(f"/cases/{case_id}", headers=self.bearer(doctor)).status_code, 403)

    def test_endpoint_registry_matches_every_planned_route_and_methods(self):
        actual = {(method, route.path) for route in main.app.routes
                  for method in getattr(route, "methods", set())}
        expected = {
            ("POST", "/auth/login"), ("POST", "/auth/refresh"), ("POST", "/auth/logout"),
            ("POST", "/consent"), ("POST", "/cases"), ("POST", "/cases/{case_id}/intake"),
            ("POST", "/cases/{case_id}/documents"), ("POST", "/cases/{case_id}/submit"),
            ("GET", "/cases/{case_id}"), ("GET", "/queue"), ("POST", "/cases/{case_id}/review"),
            ("POST", "/cases/{case_id}/reminders"), ("DELETE", "/cases/{case_id}"),
            ("GET", "/audit"), ("GET", "/health"),
        }
        self.assertTrue(expected.issubset(actual), sorted(expected - actual))

    def test_health_is_service_only_and_validation_errors_do_not_echo_input(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(set(health.json()), {
            "status", "service", "mode", "disclaimer", "background_monitoring", "background_banner",
        })
        self.assertIn(health.json()["background_monitoring"], {"online", "offline", "disabled"})
        self.assertTrue(health.headers.get("X-Request-ID"))
        self.assertEqual(health.headers.get("Cache-Control"), "no-store")
        bad = self.client.post("/auth/login", json={"user_id": "synthetic-secret-user", "password": ""})
        self.assertEqual(bad.status_code, 422)
        self.assertNotIn("synthetic-secret-user", bad.text)
        self.assertNotIn("password", bad.text)

    def test_mutations_fail_before_writing_when_data_key_is_missing(self):
        token = self.gatekeeper.login("worker-A", "synthetic-password-A")["access_token"]
        with patch.dict(os.environ, {"DATA_ENCRYPTION_KEY": ""}):
            response = self.client.post(
                "/consent", headers=self.bearer(token, "no-key-consent"),
                json={"purpose": "synthetic triage", "consented": True, "language": "en"},
            )
        self.assertEqual(response.status_code, 503)
        count = self.gatekeeper.db.execute("SELECT count(*) FROM consents").fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
