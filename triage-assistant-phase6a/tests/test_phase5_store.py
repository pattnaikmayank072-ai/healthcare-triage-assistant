from __future__ import annotations

import os
import sqlite3
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from store import Actor, AuthorizationError, Gatekeeper, WorkflowStore


class Phase5StoreIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DATA_ENCRYPTION_KEY": Fernet.generate_key().decode()}, clear=False)
        self.environment.start()
        self.gatekeeper = Gatekeeper(":memory:", jwt_secret="phase5-store-test-secret-" + "k" * 40)
        self.gatekeeper.provision_user("worker", "synthetic-password", "health_worker", "facility")
        token = self.gatekeeper.login("worker", "synthetic-password")["access_token"]
        self.actor = self.gatekeeper.verify_access_token(token)
        consent_id = self.gatekeeper.record_consent(self.actor, "synthetic purpose", True)
        self.case = self.gatekeeper.start_case(self.actor, consent_id, "outpatient_queue", "case-key")
        self.workflow = WorkflowStore(self.gatekeeper, retention_days=1)
        self.workflow.ensure_case(self.case.case_id)

    def tearDown(self):
        self.gatekeeper.close()
        self.environment.stop()

    def test_retention_purge_removes_encrypted_case_data_and_keeps_hash_chain(self):
        self.workflow.save_intake(self.case.case_id, {
            "redacted_original": "synthetic-only", "redacted_english": "synthetic-only",
            "original_ciphertext": "already-encrypted-synthetic-placeholder",
        }, "accepted")
        with self.gatekeeper._lock:
            self.gatekeeper.db.execute(
                "UPDATE case_workflows SET retention_expires_at='2000-01-01T00:00:00Z' WHERE case_id=?",
                (self.case.case_id,),
            )
        removed = self.workflow.purge_expired("2001-01-01T00:00:00Z")
        self.assertEqual(removed, 1)
        self.assertIsNone(self.workflow.case_metadata(self.case.case_id))
        with self.assertRaises(AuthorizationError):
            self.gatekeeper.authorize_case_access(self.actor, self.case.case_id)
        self.assertTrue(self.workflow.verify_audit_chain())
        self.assertEqual(self.workflow.audit_entries()[-1]["action"], "retention_purge")

    def test_audit_rows_are_database_append_only(self):
        self.workflow.append_audit(self.actor, "synthetic_test_event", self.case.case_id,
                                   {"result": "synthetic"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.gatekeeper.db.execute("UPDATE audit_events SET action='tampered' WHERE sequence=1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.gatekeeper.db.execute("DELETE FROM audit_events WHERE sequence=1")
        self.assertTrue(self.workflow.verify_audit_chain())


if __name__ == "__main__":
    unittest.main()
