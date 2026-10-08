"""Gatekeeper and encrypted local workflow persistence for the triage prototype."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

try:
    import bcrypt
except ImportError:  # fail closed for password operations if requirements were not installed
    bcrypt = None  # type: ignore[assignment]


class GatekeeperError(Exception):
    """Base class for safe, generic Gatekeeper failures."""


class AuthenticationError(GatekeeperError):
    pass


class AuthorizationError(GatekeeperError):
    pass


class ConsentRequired(GatekeeperError):
    pass


class IdempotencyConflict(GatekeeperError):
    pass


class ServiceNotConfigured(GatekeeperError):
    pass


@dataclass(frozen=True)
class Actor:
    user_id: str
    role: str
    facility_id: str


@dataclass(frozen=True)
class CaseCreated:
    case_id: str
    pseudonymous_id: str
    state: str
    created_at: str
    idempotent_replay: bool = False


_BACKGROUND_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "case_created": frozenset({"scenario", "pseudonymous_id"}),
    "intake_received": frozenset({"status", "language"}),
    "case_submitted": frozenset({"revision", "risk_category", "priority_score"}),
    "triage_note_created": frozenset({"revision", "risk_category", "priority_score"}),
    "review_action": frozenset({"decision", "state", "revision"}),
    "case_quarantined": frozenset({"reason_code", "revision"}),
    "alert_created": frozenset({"level", "reason_code", "next_role", "revision"}),
    "alert_acknowledged": frozenset({"level", "reason_code", "next_role", "revision"}),
    "vitals_updated": frozenset({"revision", "vitals", "symptom_groups", "onset_week",
                                  "age_band", "sex", "site_id", "coarse_unit"}),
    "reminder_approved": frozenset({"plan_id", "version"}),
    "referral_drafted": frozenset({"revision"}),
    "case_closed": frozenset({"revision"}),
    "followup_response": frozenset({"item_id", "answers", "revision"}),
}


def _insert_background_event(db: sqlite3.Connection, event_type: str, *, case_id: str | None,
                             facility_id: str, revision: int, payload: dict[str, Any],
                             idempotency_key: str | None = None,
                             occurred_at: str | None = None) -> int | None:
    """Insert only allowlisted structured values; callers include this in their state transaction."""
    allowed = _BACKGROUND_EVENT_FIELDS.get(event_type)
    if allowed is None or set(payload) - allowed:
        raise ValueError("background event fields are not allowlisted")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > 10000:
        raise ValueError("background event exceeds its size limit")
    key = idempotency_key or str(uuid.uuid4())
    cursor = db.execute(
        "INSERT OR IGNORE INTO background_events(event_id,idempotency_key,event_type,case_id,facility_id,"
        "revision,payload_json,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), key, event_type, case_id, facility_id, max(0, int(revision)),
         serialized, occurred_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
    )
    if cursor.rowcount != 1:
        return None
    row = db.execute("SELECT sequence FROM background_events WHERE idempotency_key=?", (key,)).fetchone()
    return int(row[0]) if row else None


class Gatekeeper:
    """Verify signed sessions and enforce consent, role, facility, and idempotency.

    The signing secret must be supplied by the environment or directly by a test.
    No default or embedded secret is accepted. Staff provisioning is a controlled
    local/admin operation; no public self-registration route is provided.
    """
    ALLOWED_ROLES = {"health_worker", "nurse", "doctor", "medical_officer", "admin"}
    CASE_CREATORS = {"health_worker", "nurse"}
    ACCESS_TTL_SECONDS = 900  # approximately 15 minutes, per the plan
    REFRESH_TTL_SECONDS = 604800  # configurable by constructor; operational default: 7 days
    MAX_LOGIN_FAILURES = 5
    LOCKOUT_SECONDS = 900
    ENCRYPTED_PURPOSE_PREFIX = "enc:v1:"

    def __init__(self, database_path: str = "triage_assistant.sqlite3", jwt_secret: str | None = None,
                 access_ttl_seconds: int = ACCESS_TTL_SECONDS,
                 refresh_ttl_seconds: int = REFRESH_TTL_SECONDS):
        secret = jwt_secret if jwt_secret is not None else os.getenv("JWT_SIGNING_SECRET")
        if not secret or len(secret.encode("utf-8")) < 32:
            raise ServiceNotConfigured("JWT signing secret is not configured")
        self._secret = secret.encode("utf-8")
        self.access_ttl_seconds = access_ttl_seconds
        self.refresh_ttl_seconds = refresh_ttl_seconds
        self._lock = threading.RLock()
        self.db = sqlite3.connect(database_path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self.db.executescript("""
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    facility_id TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    failed_logins INTEGER NOT NULL DEFAULT 0,
                    locked_until REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS revoked_tokens (
                    jti TEXT PRIMARY KEY,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS refresh_sessions (
                    jti TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );
                CREATE TABLE IF NOT EXISTS consents (
                    consent_id TEXT PRIMARY KEY,
                    facility_id TEXT NOT NULL,
                    captured_by TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    accepted INTEGER NOT NULL CHECK(accepted=1)
                );
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    pseudonymous_id TEXT NOT NULL UNIQUE,
                    facility_id TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    consent_id TEXT NOT NULL,
                    scenario TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state='consented'),
                    created_at TEXT NOT NULL,
                    UNIQUE(created_by, idempotency_key),
                    FOREIGN KEY(consent_id) REFERENCES consents(consent_id)
                );
                CREATE TABLE IF NOT EXISTS case_workflows (
                    case_id TEXT PRIMARY KEY,
                    workflow_state TEXT NOT NULL DEFAULT 'consented',
                    payload_ciphertext TEXT,
                    triage_note_ciphertext TEXT,
                    degraded_mode INTEGER NOT NULL DEFAULT 0,
                    intake_status TEXT NOT NULL DEFAULT 'not_received',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    retention_expires_at TEXT,
                    FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS agent_steps (
                    step_key TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('started','committed','failed')),
                    output_ciphertext TEXT,
                    error_code TEXT,
                    started_at TEXT NOT NULL,
                    committed_at TEXT,
                    UNIQUE(case_id, agent_name, input_fingerprint),
                    FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    outbox_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    action_type TEXT NOT NULL,
                    payload_ciphertext TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','failed')),
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    audit_reference TEXT NOT NULL UNIQUE,
                    actor_id TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    facility_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    case_reference_hash TEXT,
                    occurred_at TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS idempotency_records (
                    record_id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    response_ciphertext TEXT,
                    status TEXT NOT NULL CHECK(status IN ('started','completed')),
                    created_at TEXT NOT NULL,
                    UNIQUE(actor_id, endpoint, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS background_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    case_id TEXT,
                    facility_id TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS ix_background_events_facility_sequence
                    ON background_events(facility_id,sequence);
                CREATE TABLE IF NOT EXISTS background_cursors (
                    agent_name TEXT PRIMARY KEY,
                    last_sequence INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS background_leases (
                    job_name TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    lease_until REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS background_runs (
                    agent_name TEXT NOT NULL,
                    run_key TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('started','committed','failed')),
                    cursor_sequence INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(agent_name,run_key)
                );
                CREATE TABLE IF NOT EXISTS background_heartbeats (
                    agent_name TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_event_sequence INTEGER NOT NULL DEFAULT 0,
                    last_error_code TEXT
                );
                CREATE TABLE IF NOT EXISTS background_alerts (
                    alert_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    case_id TEXT NOT NULL,
                    pseudonymous_id TEXT NOT NULL,
                    facility_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    level TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    waiting_minutes REAL NOT NULL DEFAULT 0,
                    current_category TEXT NOT NULL,
                    score REAL,
                    next_role TEXT NOT NULL,
                    acknowledged_by TEXT,
                    acknowledged_at TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS case_revisions (
                    revision_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    parent_revision INTEGER,
                    reason_code TEXT NOT NULL,
                    risk_category TEXT NOT NULL,
                    priority_score REAL,
                    downgrade_suggestion TEXT,
                    note_ciphertext TEXT,
                    rule_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(case_id,revision),
                    FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE
                );
            """)
            self.db.executescript("""
                CREATE TRIGGER IF NOT EXISTS audit_events_no_update
                BEFORE UPDATE ON audit_events BEGIN
                    SELECT RAISE(ABORT, 'audit_events is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
                BEFORE DELETE ON audit_events BEGIN
                    SELECT RAISE(ABORT, 'audit_events is append-only');
                END;
            """)
            consent_columns = {row[1] for row in self.db.execute("PRAGMA table_info(consents)")}
            if "language" not in consent_columns:
                self.db.execute("ALTER TABLE consents ADD COLUMN language TEXT NOT NULL DEFAULT 'en'")
            legacy_consents = self.db.execute(
                "SELECT consent_id,purpose FROM consents WHERE purpose NOT LIKE ?",
                (self.ENCRYPTED_PURPOSE_PREFIX + "%",),
            ).fetchall()
            for consent in legacy_consents:
                encrypted_purpose = self.ENCRYPTED_PURPOSE_PREFIX + encrypt_sensitive_text(consent["purpose"])
                self.db.execute("UPDATE consents SET purpose=? WHERE consent_id=?",
                                (encrypted_purpose, consent["consent_id"]))
            case_columns = {row[1] for row in self.db.execute("PRAGMA table_info(cases)")}
            if "language" not in case_columns:
                self.db.execute("ALTER TABLE cases ADD COLUMN language TEXT NOT NULL DEFAULT 'en'")
            workflow_columns = {row[1] for row in self.db.execute("PRAGMA table_info(case_workflows)")}
            background_columns = {
                "revision": "INTEGER NOT NULL DEFAULT 0",
                "risk_category": "TEXT",
                "priority_score": "REAL",
                "acknowledged_at": "TEXT",
                "symptom_groups_json": "TEXT NOT NULL DEFAULT '[]'",
                "onset_week": "TEXT",
                "age_band": "TEXT",
                "sex": "TEXT",
                "site_id": "TEXT",
                "coarse_unit": "TEXT",
            }
            for column, declaration in background_columns.items():
                if column not in workflow_columns:
                    self.db.execute(f"ALTER TABLE case_workflows ADD COLUMN {column} {declaration}")

    def provision_user(self, user_id: str, password: str, role: str, facility_id: str) -> None:
        """Controlled bootstrap helper; do not expose directly as an unauthenticated API."""
        if role not in self.ALLOWED_ROLES or not user_id.strip() or not facility_id.strip():
            raise ValueError("invalid staff record")
        if not password or len(password.encode("utf-8")) > 72:
            raise ValueError("password length is invalid")
        if bcrypt is None:
            raise ServiceNotConfigured("bcrypt dependency is unavailable")
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
        with self._lock:
            self.db.execute(
                "INSERT INTO users(user_id, role, facility_id, password_hash) VALUES(?,?,?,?)",
                (user_id, role, facility_id, password_hash),
            )

    def login(self, user_id: str, password: str) -> dict[str, Any]:
        if bcrypt is None:
            raise ServiceNotConfigured("bcrypt dependency is unavailable")
        now = time.time()
        with self._lock:
            row = self.db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if row is None or not row["active"] or row["locked_until"] > now:
                raise AuthenticationError("credentials rejected")
            valid = False
            try:
                valid = bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("ascii"))
            except (ValueError, TypeError):
                valid = False
            if not valid:
                attempts = int(row["failed_logins"]) + 1
                locked_until = now + self.LOCKOUT_SECONDS if attempts >= self.MAX_LOGIN_FAILURES else 0
                self.db.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE user_id=?",
                                (attempts, locked_until, user_id))
                raise AuthenticationError("credentials rejected")
            self.db.execute("UPDATE users SET failed_logins=0, locked_until=0 WHERE user_id=?", (user_id,))
            return self._issue_pair(Actor(user_id, row["role"], row["facility_id"]))

    @staticmethod
    def _b64encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64decode(data: str) -> bytes:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))

    def _encode_token(self, claims: dict[str, Any]) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        signing_input = f"{self._b64encode(json.dumps(header, separators=(',', ':')).encode())}.{self._b64encode(json.dumps(claims, separators=(',', ':')).encode())}"
        signature = hmac.new(self._secret, signing_input.encode("ascii"), hashlib.sha256).digest()
        return f"{signing_input}.{self._b64encode(signature)}"

    def _decode_token(self, token: str, expected_type: str) -> dict[str, Any]:
        try:
            if len(token) > 8192:
                raise ValueError("oversized")
            head, body, signature = token.split(".")
            signing_input = f"{head}.{body}"
            expected = hmac.new(self._secret, signing_input.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, self._b64decode(signature)):
                raise ValueError("signature")
            header = json.loads(self._b64decode(head))
            claims = json.loads(self._b64decode(body))
            now = int(time.time())
            if header != {"alg": "HS256", "typ": "JWT"}:
                raise ValueError("algorithm")
            if claims.get("typ") != expected_type or int(claims.get("exp", 0)) <= now:
                raise ValueError("type or expiry")
            if int(claims.get("iat", now + 1)) > now + 60:
                raise ValueError("issued in future")
            if not all(isinstance(claims.get(k), str) and claims.get(k) for k in ("sub", "role", "facility", "jti")):
                raise ValueError("claims")
            if claims["role"] not in self.ALLOWED_ROLES:
                raise ValueError("role")
            return claims
        except Exception as exc:
            raise AuthenticationError("token rejected") from exc

    def _issue_pair(self, actor: Actor) -> dict[str, Any]:
        now = int(time.time())
        access_claims = {"sub": actor.user_id, "role": actor.role, "facility": actor.facility_id,
                         "jti": secrets.token_urlsafe(18), "iat": now,
                         "exp": now + self.access_ttl_seconds, "typ": "access"}
        refresh_claims = {"sub": actor.user_id, "role": actor.role, "facility": actor.facility_id,
                          "jti": secrets.token_urlsafe(24), "iat": now,
                          "exp": now + self.refresh_ttl_seconds, "typ": "refresh"}
        token = self._encode_token(access_claims)
        refresh = self._encode_token(refresh_claims)
        self.db.execute("INSERT INTO refresh_sessions(jti,user_id,expires_at) VALUES(?,?,?)",
                        (refresh_claims["jti"], actor.user_id, refresh_claims["exp"]))
        return {"access_token": token, "refresh_token": refresh,
                "token_type": "bearer", "expires_in": self.access_ttl_seconds}

    def verify_access_token(self, token: str) -> Actor:
        claims = self._decode_token(token, "access")
        with self._lock:
            revoked = self.db.execute("SELECT 1 FROM revoked_tokens WHERE jti=?", (claims["jti"],)).fetchone()
            row = self.db.execute("SELECT role,facility_id,active FROM users WHERE user_id=?", (claims["sub"],)).fetchone()
        if revoked or row is None or not row["active"]:
            raise AuthenticationError("token rejected")
        if row["role"] != claims["role"] or row["facility_id"] != claims["facility"]:
            raise AuthenticationError("token rejected")
        return Actor(claims["sub"], claims["role"], claims["facility"])

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        claims = self._decode_token(refresh_token, "refresh")
        now = int(time.time())
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                session = self.db.execute("SELECT * FROM refresh_sessions WHERE jti=?", (claims["jti"],)).fetchone()
                user = self.db.execute("SELECT role,facility_id,active FROM users WHERE user_id=?", (claims["sub"],)).fetchone()
                if (session is None or session["revoked"] or session["expires_at"] <= now or
                        user is None or not user["active"] or session["user_id"] != claims["sub"] or
                        user["role"] != claims["role"] or user["facility_id"] != claims["facility"]):
                    raise AuthenticationError("refresh rejected")
                self.db.execute("UPDATE refresh_sessions SET revoked=1 WHERE jti=?", (claims["jti"],))
                pair = self._issue_pair(Actor(claims["sub"], user["role"], user["facility_id"]))
                self.db.execute("COMMIT")
                return pair
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def logout(self, access_token: str, refresh_token: str | None = None) -> None:
        claims = self._decode_token(access_token, "access")
        with self._lock:
            self.db.execute("INSERT OR IGNORE INTO revoked_tokens(jti,expires_at) VALUES(?,?)",
                            (claims["jti"], claims["exp"]))
            if refresh_token:
                refresh_claims = self._decode_token(refresh_token, "refresh")
                self.db.execute("UPDATE refresh_sessions SET revoked=1 WHERE jti=? AND user_id=?",
                                (refresh_claims["jti"], claims["sub"]))

    def record_consent(self, actor: Actor, purpose: str, consented: bool,
                       language: str = "en") -> str:
        if actor.role not in self.CASE_CREATORS:
            raise AuthorizationError("role not allowed")
        if not consented:
            raise ConsentRequired("consent is required")
        if not purpose.strip() or not isinstance(language, str) or not 2 <= len(language) <= 20:
            raise ValueError("purpose is required")
        consent_id = str(uuid.uuid4())
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        encrypted_purpose = self.ENCRYPTED_PURPOSE_PREFIX + encrypt_sensitive_text(purpose.strip())
        with self._lock:
            self.db.execute(
                "INSERT INTO consents(consent_id,facility_id,captured_by,purpose,created_at,accepted,language) "
                "VALUES(?,?,?,?,?,1,?)",
                (consent_id, actor.facility_id, actor.user_id, encrypted_purpose, created_at, language),
            )
        return consent_id

    def start_case(self, actor: Actor, consent_id: str, scenario: str, idempotency_key: str,
                   language: str = "en") -> CaseCreated:
        if actor.role not in self.CASE_CREATORS:
            raise AuthorizationError("role not allowed")
        if not (1 <= len(idempotency_key) <= 128):
            raise ValueError("invalid idempotency key")
        if not isinstance(language, str) or not 2 <= len(language) <= 20:
            raise ValueError("invalid language")
        allowed = {"outpatient_queue", "occupational_screening", "campus_fever",
                   "maternal_follow_up", "chronic_check_in", "camp_screening"}
        if scenario not in allowed:
            raise ValueError("unknown scenario")
        fingerprint = hashlib.sha256(
            f"{consent_id}\0{scenario}\0{actor.facility_id}\0{language}".encode()
        ).hexdigest()
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                prior = self.db.execute(
                    "SELECT * FROM cases WHERE created_by=? AND idempotency_key=?",
                    (actor.user_id, idempotency_key),
                ).fetchone()
                if prior:
                    if prior["request_fingerprint"] != fingerprint:
                        raise IdempotencyConflict("idempotency key reused with different request")
                    self.db.execute("COMMIT")
                    return self._case_result(prior, True)
                consent = self.db.execute("SELECT * FROM consents WHERE consent_id=? AND accepted=1",
                                          (consent_id,)).fetchone()
                if consent is None or consent["facility_id"] != actor.facility_id:
                    raise ConsentRequired("valid facility consent is required")
                case_id = str(uuid.uuid4())
                pseudonymous_id = f"case_{secrets.token_hex(12)}"
                created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self.db.execute(
                    "INSERT INTO cases(case_id,pseudonymous_id,facility_id,created_by,consent_id,scenario,"
                    "idempotency_key,request_fingerprint,state,created_at,language) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (case_id, pseudonymous_id, actor.facility_id, actor.user_id, consent_id,
                     scenario, idempotency_key, fingerprint, "consented", created_at, language),
                )
                self.db.execute(
                    "INSERT INTO case_workflows(case_id,workflow_state,created_at,updated_at) "
                    "VALUES(?,'consented',?,?)", (case_id, created_at, created_at)
                )
                row = self.db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
                _insert_background_event(
                    self.db, "case_created", case_id=case_id, facility_id=actor.facility_id,
                    revision=0, payload={"scenario": scenario, "pseudonymous_id": pseudonymous_id},
                    idempotency_key=f"case-created:{case_id}", occurred_at=created_at,
                )
                self.db.execute("COMMIT")
                return self._case_result(row, False)
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    @staticmethod
    def _case_result(row: sqlite3.Row, replay: bool) -> CaseCreated:
        return CaseCreated(row["case_id"], row["pseudonymous_id"], row["state"], row["created_at"], replay)

    def authorize_case_access(self, actor: Actor, case_id: str) -> dict[str, Any]:
        """Scope case access; administrators never receive patient case content."""
        if actor.role == "admin":
            raise AuthorizationError("administrators cannot access patient content")
        with self._lock:
            row = self.db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if row is None or row["facility_id"] != actor.facility_id:
            raise AuthorizationError("case unavailable")
        if actor.role in {"health_worker", "nurse"} and row["created_by"] != actor.user_id:
            raise AuthorizationError("case unavailable")
        if actor.role not in {"health_worker", "nurse", "doctor", "medical_officer"}:
            raise AuthorizationError("case unavailable")
        # Return case metadata only; clinical content is handled by WorkflowStore separately.
        return {k: row[k] for k in ("case_id", "pseudonymous_id", "facility_id", "created_by", "scenario", "state", "created_at")}

    def close(self) -> None:
        with self._lock:
            self.db.close()


def encrypt_sensitive_text(text: str, key: str | bytes | None = None) -> str:
    """Fernet-encrypt source-language text; requires an externally managed local key."""
    secret = key if key is not None else os.getenv("DATA_ENCRYPTION_KEY")
    if not secret:
        raise ServiceNotConfigured("data encryption key is not configured")
    try:
        from cryptography.fernet import Fernet
        raw_key = secret.encode("ascii") if isinstance(secret, str) else secret
        return Fernet(raw_key).encrypt(text.encode("utf-8")).decode("ascii")
    except Exception as exc:
        raise ServiceNotConfigured("data encryption key is invalid") from exc


class WorkflowStore:
    """Workflow persistence facade sharing the Gatekeeper's SQLite connection.

    Clinical inputs, agent outputs, notes, and outbox payloads are encrypted with the
    separately configured DATA_ENCRYPTION_KEY. Audit metadata contains only actor IDs,
    action labels, hashes, timestamps, and explicitly whitelisted non-clinical details.
    """

    _ALLOWED_STATES = {
        "created", "consented", "processing", "awaiting_review", "reviewed",
        "escalated", "closed", "quarantined", "purged",
    }

    def __init__(self, gatekeeper: Gatekeeper, retention_days: int | None = None):
        self.gatekeeper = gatekeeper
        self.db = gatekeeper.db
        self._lock = gatekeeper._lock
        self.retention_days = retention_days
        now = self._now()
        with self._lock:
            self.db.execute(
                "INSERT OR IGNORE INTO case_workflows(case_id,workflow_state,created_at,updated_at) "
                "SELECT case_id,'consented',?,? FROM cases", (now, now)
            )
            if self.retention_days is not None and self.retention_days > 0:
                modifier = f"+{int(self.retention_days)} days"
                self.db.execute(
                    "UPDATE case_workflows SET retention_expires_at="
                    "strftime('%Y-%m-%dT%H:%M:%SZ',created_at,?) WHERE retention_expires_at IS NULL",
                    (modifier,),
                )

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _encrypt(self, value: Any) -> str:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return encrypt_sensitive_text(serialized)

    @staticmethod
    def _decrypt(ciphertext: str | None) -> Any:
        if not ciphertext:
            return None
        return json.loads(decrypt_sensitive_text(ciphertext))

    def ensure_case(self, case_id: str) -> None:
        now = self._now()
        retention = None
        if self.retention_days is not None and self.retention_days > 0:
            retention = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(time.time() + int(self.retention_days) * 86400),
            )
        with self._lock:
            row = self.db.execute("SELECT case_id FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if row is None:
                raise AuthorizationError("case unavailable")
            self.db.execute(
                "INSERT OR IGNORE INTO case_workflows(case_id,workflow_state,created_at,updated_at,retention_expires_at) "
                "VALUES(?,'consented',?,?,?)", (case_id, now, now, retention)
            )
            if self.retention_days is not None and self.retention_days > 0:
                self.db.execute(
                    "UPDATE case_workflows SET retention_expires_at="
                    "strftime('%Y-%m-%dT%H:%M:%SZ',created_at,?) "
                    "WHERE case_id=? AND retention_expires_at IS NULL",
                    (f"+{int(self.retention_days)} days", case_id),
                )

    def case_metadata(self, case_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.db.execute(
                "SELECT c.case_id,c.pseudonymous_id,c.facility_id,c.created_by,c.scenario,c.created_at,"
                "c.language,w.workflow_state,w.degraded_mode,w.intake_status "
                "FROM cases c JOIN case_workflows w ON w.case_id=c.case_id WHERE c.case_id=?",
                (case_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_intake(self, case_id: str, payload: dict[str, Any], intake_status: str) -> None:
        if intake_status not in {"accepted", "needs_interpreter", "quarantined"}:
            raise ValueError("invalid intake status")
        self.ensure_case(case_id)
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT w.payload_ciphertext,w.workflow_state,w.revision,c.facility_id "
                    "FROM case_workflows w JOIN cases c USING(case_id) WHERE case_id=?", (case_id,)
                ).fetchone()
                if row is None or row["workflow_state"] in {"closed", "purged"}:
                    raise AuthorizationError("case is not available for intake")
                existing = self._decrypt(row["payload_ciphertext"]) or {}
                existing.update(payload)
                now = self._now()
                new_state = "quarantined" if intake_status == "quarantined" else "consented"
                self.db.execute(
                    "UPDATE case_workflows SET payload_ciphertext=?,intake_status=?,workflow_state=?,updated_at=? "
                    "WHERE case_id=?",
                    (self._encrypt(existing), intake_status, new_state, now, case_id),
                )
                raw_language = str(payload.get("language", "en")).strip()[:20]
                safe_language = raw_language if (2 <= len(raw_language) <= 20
                                                  and raw_language.replace("-", "").isalnum()) else "und"
                _insert_background_event(
                    self.db, "intake_received", case_id=case_id, facility_id=row["facility_id"],
                    revision=row["revision"],
                    payload={"status": intake_status, "language": safe_language},
                    occurred_at=now,
                )
                if intake_status == "quarantined":
                    _insert_background_event(
                        self.db, "case_quarantined", case_id=case_id, facility_id=row["facility_id"],
                        revision=row["revision"], payload={"reason_code": "sentinel_block"}, occurred_at=now,
                    )
                self.db.execute("COMMIT")
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def add_document_result(self, case_id: str, document: dict[str, Any]) -> None:
        self.ensure_case(case_id)
        with self._lock:
            row = self.db.execute(
                "SELECT payload_ciphertext,workflow_state FROM case_workflows WHERE case_id=?", (case_id,)
            ).fetchone()
            if row is None or row["workflow_state"] in {"closed", "purged", "quarantined"}:
                raise AuthorizationError("case is not available for documents")
            payload = self._decrypt(row["payload_ciphertext"]) or {}
            docs = list(payload.get("documents", []))
            docs.append(document)
            payload["documents"] = docs
            self.db.execute(
                "UPDATE case_workflows SET payload_ciphertext=?,updated_at=? WHERE case_id=?",
                (self._encrypt(payload), self._now(), case_id),
            )

    def case_payload(self, case_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.db.execute("SELECT payload_ciphertext FROM case_workflows WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise AuthorizationError("case unavailable")
        return self._decrypt(row["payload_ciphertext"]) or {}

    def begin_step(self, case_id: str, agent_name: str, input_fingerprint: str) -> tuple[str, str]:
        step_key = hashlib.sha256(f"{case_id}\0{agent_name}\0{input_fingerprint}".encode()).hexdigest()
        now = self._now()
        with self._lock:
            row = self.db.execute(
                "SELECT status,output_ciphertext FROM agent_steps WHERE step_key=?", (step_key,)
            ).fetchone()
            if row:
                return step_key, row["status"]
            self.db.execute(
                "INSERT INTO agent_steps(step_key,case_id,agent_name,input_fingerprint,status,started_at) "
                "VALUES(?,?,?,?, 'started',?)", (step_key, case_id, agent_name, input_fingerprint, now)
            )
            return step_key, "started"

    def cached_step(self, step_key: str) -> Any:
        with self._lock:
            row = self.db.execute(
                "SELECT status,output_ciphertext FROM agent_steps WHERE step_key=?", (step_key,)
            ).fetchone()
        if row is None or row["status"] != "committed":
            return None
        return {"__committed_step_value__": self._decrypt(row["output_ciphertext"])}

    def commit_step(self, step_key: str, output: Any) -> None:
        encrypted = self._encrypt(output)
        with self._lock:
            self.db.execute(
                "UPDATE agent_steps SET status='committed',output_ciphertext=?,error_code=NULL,committed_at=? "
                "WHERE step_key=?", (encrypted, self._now(), step_key)
            )

    def fail_step(self, step_key: str, error_code: str) -> None:
        # Only stable error codes are accepted; exception text and patient content are never stored.
        safe_code = "".join(ch for ch in str(error_code) if ch.isalnum() or ch in "_-.")[:80] or "agent_failure"
        with self._lock:
            self.db.execute("UPDATE agent_steps SET status='failed',error_code=? WHERE step_key=?",
                            (safe_code, step_key))

    def set_processing(self, case_id: str) -> None:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT w.workflow_state,w.revision,c.facility_id FROM case_workflows w "
                    "JOIN cases c USING(case_id) WHERE case_id=?", (case_id,)
                ).fetchone()
                if row is None or row["workflow_state"] in {"purged", "closed", "quarantined"}:
                    raise AuthorizationError("case cannot be submitted")
                now = self._now()
                self.db.execute("UPDATE case_workflows SET workflow_state='processing',updated_at=? WHERE case_id=?",
                                (now, case_id))
                _insert_background_event(
                    self.db, "case_submitted", case_id=case_id, facility_id=row["facility_id"],
                    revision=row["revision"], payload={"revision": row["revision"]}, occurred_at=now,
                )
                self.db.execute("COMMIT")
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def quarantine(self, case_id: str) -> None:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT w.revision,c.facility_id FROM case_workflows w JOIN cases c USING(case_id) "
                    "WHERE case_id=?", (case_id,)
                ).fetchone()
                now = self._now()
                self.db.execute(
                    "UPDATE case_workflows SET workflow_state='quarantined',degraded_mode=1,updated_at=? WHERE case_id=?",
                    (now, case_id),
                )
                if row:
                    _insert_background_event(
                        self.db, "case_quarantined", case_id=case_id, facility_id=row["facility_id"],
                        revision=row["revision"], payload={"reason_code": "workflow_quarantined"}, occurred_at=now,
                    )
                self.db.execute("COMMIT")
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def save_triage_note(self, case_id: str, note: dict[str, Any], degraded_mode: bool = False) -> None:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT w.revision,c.facility_id FROM case_workflows w JOIN cases c USING(case_id) "
                    "WHERE case_id=?", (case_id,)
                ).fetchone()
                if row is None:
                    raise AuthorizationError("case unavailable")
                now = self._now()
                category = str(note.get("risk_category", "insufficient_information"))
                score = note.get("queue_priority_score")
                self.db.execute(
                    "UPDATE case_workflows SET workflow_state='awaiting_review',triage_note_ciphertext=?,"
                    "degraded_mode=?,risk_category=?,priority_score=?,updated_at=? WHERE case_id=?",
                    (self._encrypt(note), int(degraded_mode), category, score, now, case_id),
                )
                _insert_background_event(
                    self.db, "triage_note_created", case_id=case_id, facility_id=row["facility_id"],
                    revision=row["revision"],
                    payload={"revision": row["revision"], "risk_category": category,
                             "priority_score": score}, occurred_at=now,
                )
                self.db.execute("COMMIT")
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def update_reviewed_note(self, case_id: str, note: dict[str, Any], state: str) -> None:
        if state not in {"awaiting_review", "reviewed", "escalated"}:
            raise ValueError("invalid review state")
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT w.revision,c.facility_id FROM case_workflows w JOIN cases c USING(case_id) "
                    "WHERE case_id=?", (case_id,)
                ).fetchone()
                if row is None:
                    raise AuthorizationError("case unavailable")
                now = self._now()
                decision = str((note.get("reviewer_action_panel") or {}).get("decision") or state)
                self.db.execute(
                    "UPDATE case_workflows SET workflow_state=?,triage_note_ciphertext=?,"
                    "acknowledged_at=COALESCE(acknowledged_at,?),risk_category=?,priority_score=?,updated_at=? "
                    "WHERE case_id=?",
                    (state, self._encrypt(note), now, str(note.get("risk_category", "insufficient_information")),
                     note.get("queue_priority_score"), now, case_id),
                )
                _insert_background_event(
                    self.db, "review_action", case_id=case_id, facility_id=row["facility_id"],
                    revision=row["revision"], payload={"decision": decision, "state": state,
                                                        "revision": row["revision"]}, occurred_at=now,
                )
                self.db.execute("COMMIT")
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def get_note(self, case_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.db.execute("SELECT triage_note_ciphertext FROM case_workflows WHERE case_id=?",
                                  (case_id,)).fetchone()
        return self._decrypt(row["triage_note_ciphertext"]) if row else None

    def get_case_envelope_data(self, case_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        metadata = self.case_metadata(case_id)
        if metadata is None:
            raise AuthorizationError("case unavailable")
        return metadata, self.get_note(case_id)

    def list_queue_cases(self, facility_id: str, category: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute(
                "SELECT c.case_id,c.pseudonymous_id,c.facility_id,c.scenario,c.created_at,c.language,"
                "w.workflow_state,w.degraded_mode,w.triage_note_ciphertext "
                "FROM cases c JOIN case_workflows w ON w.case_id=c.case_id "
                "WHERE c.facility_id=? AND w.workflow_state IN ('processing','awaiting_review','reviewed','escalated') "
                "ORDER BY c.created_at", (facility_id,)
            ).fetchall()
        result = []
        for row in rows:
            note = self._decrypt(row["triage_note_ciphertext"]) if row["triage_note_ciphertext"] else None
            if note is None:
                continue
            risk = note.get("risk_category", "insufficient_information")
            if category and category != risk:
                continue
            result.append({
                "case_id": row["case_id"], "risk_category": risk,
                "created_at": row["created_at"], "pseudonymous_id": row["pseudonymous_id"], "note": note,
                "workflow_state": row["workflow_state"],
            })
        return result

    def insert_outbox(self, case_id: str, idempotency_key: str, action_type: str, payload: dict[str, Any]) -> bool:
        if action_type not in {"red_alert", "referral_packet", "reminder"}:
            raise ValueError("unsupported outbox action")
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                case = self.db.execute(
                    "SELECT c.facility_id,w.revision FROM cases c JOIN case_workflows w USING(case_id) "
                    "WHERE c.case_id=?", (case_id,)
                ).fetchone()
                if case is None:
                    raise AuthorizationError("case unavailable")
                now = self._now()
                cursor = self.db.execute(
                    "INSERT OR IGNORE INTO outbox(outbox_id,case_id,idempotency_key,action_type,payload_ciphertext,status,created_at) "
                    "VALUES(?,?,?,?,?,'pending',?)",
                    (str(uuid.uuid4()), case_id, idempotency_key, action_type, self._encrypt(payload), now),
                )
                if cursor.rowcount == 1 and action_type in {"referral_packet", "reminder"}:
                    if action_type == "reminder":
                        event_type = "reminder_approved"
                        event_payload = {"plan_id": idempotency_key[:100], "version": case["revision"]}
                    else:
                        event_type = "referral_drafted"
                        event_payload = {"revision": case["revision"]}
                    _insert_background_event(
                        self.db, event_type, case_id=case_id, facility_id=case["facility_id"],
                        revision=case["revision"], payload=event_payload,
                        idempotency_key=f"outbox-event:{idempotency_key}", occurred_at=now,
                    )
                self.db.execute("COMMIT")
                return cursor.rowcount == 1
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def count_outbox(self, case_id: str) -> int:
        with self._lock:
            row = self.db.execute("SELECT COUNT(*) FROM outbox WHERE case_id=? AND status='pending'", (case_id,)).fetchone()
        return int(row[0])

    def outbox_entries(self, case_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM outbox"
        params: tuple[Any, ...] = ()
        if case_id is not None:
            query += " WHERE case_id=?"
            params = (case_id,)
        query += " ORDER BY created_at,outbox_id"
        with self._lock:
            rows = self.db.execute(query, params).fetchall()
        return [{"outbox_id": row["outbox_id"], "case_id": row["case_id"],
                 "idempotency_key": row["idempotency_key"], "action_type": row["action_type"],
                 "status": row["status"], "payload": self._decrypt(row["payload_ciphertext"])}
                for row in rows]

    def append_audit(self, actor: Actor, action: str, case_id: str | None = None,
                     details: dict[str, Any] | None = None) -> str:
        permitted = {"decision", "state", "endpoint", "result", "count", "reason_sha256", "input_sha256",
                     "agent_name", "rule_version", "reason_code", "revision", "level"}
        safe_details = {}
        for key, value in (details or {}).items():
            if key not in permitted:
                continue
            if isinstance(value, (str, int, bool)):
                safe_details[key] = str(value)[:120]
        details_json = json.dumps(safe_details, sort_keys=True, separators=(",", ":"))
        case_hash = hashlib.sha256(case_id.encode()).hexdigest() if case_id else None
        occurred_at = self._now()
        reference = str(uuid.uuid4())
        with self._lock:
            previous = self.db.execute("SELECT event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1").fetchone()
            previous_hash = previous[0] if previous else "0" * 64
            fields = [reference, actor.user_id, actor.role, actor.facility_id, action,
                      case_hash or "", occurred_at, details_json, previous_hash]
            event_hash = hashlib.sha256("\0".join(fields).encode()).hexdigest()
            self.db.execute(
                "INSERT INTO audit_events(audit_reference,actor_id,actor_role,facility_id,action,"
                "case_reference_hash,occurred_at,details_json,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (reference, actor.user_id, actor.role, actor.facility_id, action, case_hash,
                 occurred_at, details_json, previous_hash, event_hash),
            )
        return reference

    def audit_entries(self, since: str | None = None, until: str | None = None,
                      facility_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_events WHERE 1=1"
        values: list[str] = []
        if since:
            query += " AND occurred_at>=?"
            values.append(since)
        if until:
            query += " AND occurred_at<=?"
            values.append(until)
        if facility_id:
            query += " AND facility_id=?"
            values.append(facility_id)
        query += " ORDER BY sequence LIMIT 10000"
        with self._lock:
            rows = self.db.execute(query, values).fetchall()
        return [{"audit_reference": row["audit_reference"], "actor_id": row["actor_id"],
                 "actor_role": row["actor_role"], "facility_id": row["facility_id"],
                 "action": row["action"], "case_reference_hash": row["case_reference_hash"],
                 "occurred_at": row["occurred_at"], "details": json.loads(row["details_json"]),
                 "previous_hash": row["previous_hash"], "event_hash": row["event_hash"]}
                for row in rows]

    def verify_audit_chain(self) -> bool:
        previous_hash = "0" * 64
        with self._lock:
            rows = self.db.execute("SELECT * FROM audit_events ORDER BY sequence")
            for row in rows:
                if row["previous_hash"] != previous_hash:
                    return False
                fields = [row["audit_reference"], row["actor_id"], row["actor_role"],
                          row["facility_id"], row["action"], row["case_reference_hash"] or "",
                          row["occurred_at"], json.dumps(json.loads(row["details_json"]),
                                                         sort_keys=True, separators=(",", ":")),
                          previous_hash]
                expected = hashlib.sha256("\0".join(fields).encode()).hexdigest()
                if not hmac.compare_digest(expected, row["event_hash"]):
                    return False
                previous_hash = row["event_hash"]
        return True

    def delete_case(self, case_id: str, *, preserve_delete_idempotency: bool = True) -> None:
        endpoints = [
            f"/cases/{case_id}/intake", f"/cases/{case_id}/documents",
            f"/cases/{case_id}/submit", f"/cases/{case_id}/review",
            f"/cases/{case_id}/reminders",
        ]
        if not preserve_delete_idempotency:
            endpoints.append(f"DELETE /cases/{case_id}")
        with self._lock:
            if endpoints:
                placeholders = ",".join("?" for _ in endpoints)
                self.db.execute(f"DELETE FROM idempotency_records WHERE endpoint IN ({placeholders})", endpoints)
            self.db.execute("DELETE FROM cases WHERE case_id=?", (case_id,))

    def purge_case(self, case_id: str, actor: Actor, action: str, details: dict[str, Any] | None = None,
                   *, preserve_delete_idempotency: bool = True) -> str:
        """Atomically append a hash-chained purge event and remove encrypted case records."""
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                reference = self.append_audit(actor, action, case_id, details)
                self.delete_case(case_id, preserve_delete_idempotency=preserve_delete_idempotency)
                self.db.execute("COMMIT")
                return reference
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def purge_expired(self, now: str | None = None) -> int:
        """Remove expired case data and dependent encrypted workflow artifacts.

        The append-only audit chain keeps only a pseudonymous case-reference hash.
        Deployments with a retention period should call this on startup and periodically.
        """
        if self.retention_days is None or self.retention_days <= 0:
            return 0
        cutoff = now or self._now()
        with self._lock:
            rows = self.db.execute(
                "SELECT case_id FROM case_workflows WHERE retention_expires_at IS NOT NULL "
                "AND retention_expires_at<=? ORDER BY retention_expires_at", (cutoff,),
            ).fetchall()
        system_actor = Actor("system:retention", "admin", "system")
        for row in rows:
            case_id = row["case_id"]
            self.purge_case(case_id, system_actor, "retention_purge",
                            {"state": "purged", "result": "retention_expired"},
                            preserve_delete_idempotency=False)
        return len(rows)

    def begin_idempotency(self, actor_id: str, endpoint: str, key: str,
                          request_payload: Any) -> dict[str, Any] | None:
        if not isinstance(key, str) or not (1 <= len(key) <= 128) or any(
                not (char.isalnum() or char in "_-.:" ) for char in key):
            raise ValueError("invalid idempotency key")
        canonical = json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        with self._lock:
            row = self.db.execute(
                "SELECT request_fingerprint,response_ciphertext,status FROM idempotency_records "
                "WHERE actor_id=? AND endpoint=? AND idempotency_key=?", (actor_id, endpoint, key)
            ).fetchone()
            if row:
                if row["request_fingerprint"] != fingerprint:
                    raise IdempotencyConflict("key reused with different request")
                if row["status"] != "completed":
                    raise IdempotencyConflict("request with this key is already in progress")
                return {"__cached_result__": json.loads(self._decrypt_idempotent(row["response_ciphertext"]))}
            self.db.execute(
                "INSERT INTO idempotency_records(record_id,actor_id,endpoint,idempotency_key,request_fingerprint,status,created_at) "
                "VALUES(?,?,?,?,?,'started',?)",
                (str(uuid.uuid4()), actor_id, endpoint, key, fingerprint, self._now()),
            )
        return None

    def finish_idempotency(self, actor_id: str, endpoint: str, key: str, response: Any) -> None:
        serialized = json.dumps(response, ensure_ascii=False, separators=(",", ":"), default=str)
        encrypted = self._encrypt_idempotent(serialized)
        with self._lock:
            self.db.execute(
                "UPDATE idempotency_records SET status='completed',response_ciphertext=? "
                "WHERE actor_id=? AND endpoint=? AND idempotency_key=? AND status='started'",
                (encrypted, actor_id, endpoint, key),
            )

    def cancel_idempotency(self, actor_id: str, endpoint: str, key: str) -> None:
        with self._lock:
            self.db.execute(
                "DELETE FROM idempotency_records WHERE actor_id=? AND endpoint=? AND idempotency_key=? AND status='started'",
                (actor_id, endpoint, key),
            )

    def _idempotency_fernet(self):
        secret = os.getenv("DATA_ENCRYPTION_KEY")
        if not secret:
            raise ServiceNotConfigured("data encryption key is not configured")
        from cryptography.fernet import Fernet
        try:
            return Fernet(secret.encode("ascii"))
        except Exception as exc:
            raise ServiceNotConfigured("data encryption key is invalid") from exc

    def ensure_idempotency_encryption(self) -> None:
        """Check the data key before any idempotent operation can mutate application state."""
        self._idempotency_fernet()

    def _encrypt_idempotent(self, value: str) -> str:
        try:
            return self._idempotency_fernet().encrypt(value.encode()).decode("ascii")
        except Exception as exc:
            raise ServiceNotConfigured("idempotency encryption is unavailable") from exc

    def _decrypt_idempotent(self, value: str) -> str:
        try:
            return self._idempotency_fernet().decrypt(value.encode("ascii")).decode("utf-8")
        except Exception as exc:
            raise ServiceNotConfigured("idempotency response is unavailable") from exc

    def background_store(self) -> "BackgroundStore":
        """Return the restricted service facade; background code is not given the DB handle."""
        return BackgroundStore(self)

    def close(self) -> None:
        # Connection lifecycle is owned by Gatekeeper.
        return None


class StructuredBackgroundReader:
    """Read-only structured projection; SQLite denies every unallowlisted table/column."""

    _COLUMNS = {
        "cases": {"case_id", "pseudonymous_id", "facility_id", "scenario", "created_at", "language"},
        "case_workflows": {"case_id", "workflow_state", "intake_status", "updated_at", "retention_expires_at",
                           "revision", "risk_category", "priority_score", "acknowledged_at",
                           "symptom_groups_json", "onset_week", "age_band", "sex", "site_id", "coarse_unit"},
        "background_events": {"sequence", "event_id", "idempotency_key", "event_type", "case_id",
                              "facility_id", "revision", "payload_json", "occurred_at"},
        "background_alerts": {"alert_id", "case_id", "pseudonymous_id", "facility_id", "revision", "level",
                              "reason_code", "waiting_minutes", "current_category", "score", "next_role",
                              "acknowledged_by", "acknowledged_at", "status", "created_at", "updated_at"},
    }
    _WRITE_ACTIONS = {
        getattr(sqlite3, name) for name in (
            "SQLITE_INSERT", "SQLITE_UPDATE", "SQLITE_DELETE", "SQLITE_CREATE_TABLE", "SQLITE_DROP_TABLE",
            "SQLITE_ALTER_TABLE", "SQLITE_CREATE_INDEX", "SQLITE_DROP_INDEX", "SQLITE_ATTACH",
            "SQLITE_DETACH", "SQLITE_PRAGMA", "SQLITE_TRANSACTION",
        ) if hasattr(sqlite3, name)
    }

    def __init__(self, workflow_store: WorkflowStore):
        self.__store = workflow_store

    def _select(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        def authorize(action: int, table: str | None, column: str | None,
                      _database: str | None, _source: str | None) -> int:
            if action == sqlite3.SQLITE_READ:
                if table not in self._COLUMNS or (column or "") not in self._COLUMNS[table]:
                    return sqlite3.SQLITE_DENY
            elif action in self._WRITE_ACTIONS:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        store = self.__store
        with store._lock:
            store.db.set_authorizer(authorize)
            try:
                return store.db.execute(query, params).fetchall()
            finally:
                store.db.set_authorizer(None)

    def case(self, case_id: str) -> dict[str, Any] | None:
        rows = self._select(
            "SELECT c.case_id,c.pseudonymous_id,c.facility_id,c.scenario,c.created_at,c.language,"
            "w.workflow_state,w.intake_status,w.updated_at,w.revision,w.risk_category,w.priority_score,"
            "w.acknowledged_at,w.symptom_groups_json,w.onset_week,w.age_band,w.sex,w.site_id,w.coarse_unit "
            "FROM cases c JOIN case_workflows w USING(case_id) WHERE c.case_id=?", (case_id,)
        )
        if not rows:
            return None
        result = dict(rows[0])
        result["symptom_groups"] = json.loads(result.pop("symptom_groups_json") or "[]")
        return result

    def waiting_cases(self, facility_id: str | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT c.case_id,c.pseudonymous_id,c.facility_id,c.scenario,c.created_at,c.language,"
            "w.workflow_state,w.intake_status,w.updated_at,w.revision,w.risk_category,w.priority_score,"
            "w.acknowledged_at,w.symptom_groups_json,w.onset_week,w.age_band,w.sex,w.site_id,w.coarse_unit "
            "FROM cases c JOIN case_workflows w USING(case_id) "
            "WHERE w.workflow_state IN ('processing','awaiting_review','reviewed','escalated')"
        )
        params: tuple[Any, ...] = ()
        if facility_id is not None:
            query += " AND c.facility_id=?"
            params = (facility_id,)
        query += " ORDER BY c.created_at,c.case_id"
        rows = self._select(query, params)
        results = []
        for row in rows:
            item = dict(row)
            item["symptom_groups"] = json.loads(item.pop("symptom_groups_json") or "[]")
            results.append(item)
        return results

    def events_after(self, sequence: int, limit: int = 500) -> list[dict[str, Any]]:
        if not 1 <= limit <= 5000:
            raise ValueError("event page size is out of range")
        rows = self._select(
            "SELECT sequence,event_id,idempotency_key,event_type,case_id,facility_id,revision,payload_json,occurred_at "
            "FROM background_events WHERE sequence>? ORDER BY sequence LIMIT ?", (max(0, sequence), limit)
        )
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result


class BackgroundStore:
    """Narrow read/coordination facade intended for scheduled service agents."""

    def __init__(self, workflow_store: WorkflowStore):
        self.__store = workflow_store
        self.reader = StructuredBackgroundReader(workflow_store)

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def cursor(self, agent_name: str) -> int:
        with self.__store._lock:
            row = self.__store.db.execute(
                "SELECT last_sequence FROM background_cursors WHERE agent_name=?", (agent_name,)
            ).fetchone()
        return int(row[0]) if row else 0

    def advance_cursor(self, agent_name: str, sequence: int) -> None:
        now = self._now()
        with self.__store._lock:
            self.__store.db.execute(
                "INSERT INTO background_cursors(agent_name,last_sequence,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(agent_name) DO UPDATE SET last_sequence=MAX(last_sequence,excluded.last_sequence),"
                "updated_at=excluded.updated_at", (agent_name, max(0, int(sequence)), now)
            )

    def heartbeat(self, agent_name: str, status: str = "ok", last_event_sequence: int = 0,
                  error_code: str | None = None) -> None:
        if status not in {"ok", "degraded", "failed", "offline"}:
            raise ValueError("invalid heartbeat status")
        safe_error = "".join(ch for ch in str(error_code or "") if ch.isalnum() or ch in "_-. ")[:80] or None
        with self.__store._lock:
            self.__store.db.execute(
                "INSERT INTO background_heartbeats(agent_name,last_seen,status,last_event_sequence,last_error_code) "
                "VALUES(?,?,?,?,?) ON CONFLICT(agent_name) DO UPDATE SET last_seen=excluded.last_seen,"
                "status=excluded.status,last_event_sequence=MAX(last_event_sequence,excluded.last_event_sequence),"
                "last_error_code=excluded.last_error_code",
                (agent_name, self._now(), status, max(0, int(last_event_sequence)), safe_error),
            )

    def heartbeat_status(self, agent_name: str) -> dict[str, Any] | None:
        with self.__store._lock:
            row = self.__store.db.execute(
                "SELECT agent_name,last_seen,status,last_event_sequence,last_error_code "
                "FROM background_heartbeats WHERE agent_name=?", (agent_name,)
            ).fetchone()
        return dict(row) if row else None

    def acquire_lease(self, job_name: str, owner_id: str, lease_seconds: int = 60,
                      now_epoch: float | None = None) -> str | None:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease duration is out of range")
        now = time.time() if now_epoch is None else float(now_epoch)
        run_id = str(uuid.uuid4())
        with self.__store._lock:
            cursor = self.__store.db.execute(
                "INSERT INTO background_leases(job_name,owner_id,run_id,lease_until,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(job_name) DO UPDATE SET owner_id=excluded.owner_id,run_id=excluded.run_id,"
                "lease_until=excluded.lease_until,updated_at=excluded.updated_at "
                "WHERE background_leases.lease_until<=?",
                (job_name, owner_id, run_id, now + lease_seconds, self._now(), now),
            )
        return run_id if cursor.rowcount == 1 else None

    def release_lease(self, job_name: str, owner_id: str, run_id: str) -> bool:
        with self.__store._lock:
            cursor = self.__store.db.execute(
                "DELETE FROM background_leases WHERE job_name=? AND owner_id=? AND run_id=?",
                (job_name, owner_id, run_id),
            )
        return cursor.rowcount == 1

    def begin_run(self, agent_name: str, run_key: str) -> bool:
        now = self._now()
        with self.__store._lock:
            self.__store.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.__store.db.execute(
                    "SELECT status FROM background_runs WHERE agent_name=? AND run_key=?",
                    (agent_name, run_key),
                ).fetchone()
                if row and row["status"] == "committed":
                    self.__store.db.execute("COMMIT")
                    return False
                self.__store.db.execute(
                    "INSERT INTO background_runs(agent_name,run_key,status,attempts,created_at,updated_at) "
                    "VALUES(?,?,'started',1,?,?) ON CONFLICT(agent_name,run_key) DO UPDATE SET "
                    "status='started',attempts=attempts+1,error_code=NULL,updated_at=excluded.updated_at",
                    (agent_name, run_key, now, now),
                )
                self.__store.db.execute("COMMIT")
                return True
            except Exception:
                if self.__store.db.in_transaction:
                    self.__store.db.execute("ROLLBACK")
                raise

    def finish_run(self, agent_name: str, run_key: str, cursor_sequence: int) -> None:
        now = self._now()
        with self.__store._lock:
            self.__store.db.execute("BEGIN IMMEDIATE")
            try:
                self.__store.db.execute(
                    "UPDATE background_runs SET status='committed',cursor_sequence=MAX(cursor_sequence,?),"
                    "updated_at=? WHERE agent_name=? AND run_key=?",
                    (max(0, int(cursor_sequence)), now, agent_name, run_key),
                )
                self.__store.db.execute(
                    "INSERT INTO background_cursors(agent_name,last_sequence,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(agent_name) DO UPDATE SET last_sequence=MAX(last_sequence,excluded.last_sequence),"
                    "updated_at=excluded.updated_at", (agent_name, max(0, int(cursor_sequence)), now)
                )
                self.__store.db.execute("COMMIT")
            except Exception:
                if self.__store.db.in_transaction:
                    self.__store.db.execute("ROLLBACK")
                raise

    def fail_run(self, agent_name: str, run_key: str, error_code: str) -> None:
        safe_code = "".join(ch for ch in str(error_code) if ch.isalnum() or ch in "_-. ")[:80]
        with self.__store._lock:
            self.__store.db.execute(
                "UPDATE background_runs SET status='failed',error_code=?,updated_at=? "
                "WHERE agent_name=? AND run_key=?",
                (safe_code or "job_failure", self._now(), agent_name, run_key),
            )

    def create_alert(self, *, case_id: str, revision: int, level: str, reason_code: str,
                     waiting_minutes: float, current_category: str, score: float | None,
                     next_role: str, rule_version: str, idempotency_key: str) -> bool:
        import math
        safe_token = lambda value: (isinstance(value, str) and 1 <= len(value) <= 80
                                    and all(ch.isalnum() or ch in "_.-" for ch in value))
        if not safe_token(level) or not safe_token(reason_code) or not safe_token(rule_version):
            raise ValueError("alert identifiers must be safe tokens")
        if next_role not in {"nurse", "doctor", "medical_officer"}:
            raise ValueError("unsupported alert recipient role")
        if current_category not in {"red", "amber", "green", "insufficient_information"}:
            raise ValueError("unsupported risk category")
        if not math.isfinite(waiting_minutes) or waiting_minutes < 0:
            raise ValueError("waiting time is invalid")
        if score is not None and not math.isfinite(score):
            raise ValueError("alert score is invalid")
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise ValueError("alert idempotency key is invalid")
        now = self._now()
        store = self.__store
        with store._lock:
            store.db.execute("BEGIN IMMEDIATE")
            try:
                case = store.db.execute(
                    "SELECT c.pseudonymous_id,c.facility_id,w.revision FROM cases c "
                    "JOIN case_workflows w USING(case_id) WHERE c.case_id=?", (case_id,)
                ).fetchone()
                if case is None:
                    store.db.execute("ROLLBACK")
                    return False
                alert_id = str(uuid.uuid4())
                cursor = store.db.execute(
                    "INSERT OR IGNORE INTO background_alerts(alert_id,idempotency_key,case_id,pseudonymous_id,"
                    "facility_id,revision,level,reason_code,waiting_minutes,current_category,score,next_role,status,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?)",
                    (alert_id, idempotency_key, case_id, case["pseudonymous_id"], case["facility_id"],
                     int(revision), level, reason_code, float(waiting_minutes), current_category, score,
                     next_role, now, now),
                )
                if cursor.rowcount == 1:
                    _insert_background_event(
                        store.db, "alert_created", case_id=case_id, facility_id=case["facility_id"],
                        revision=revision, payload={"level": level, "reason_code": reason_code,
                                                   "next_role": next_role, "revision": revision},
                        idempotency_key=f"alert-event:{idempotency_key}", occurred_at=now,
                    )
                    store.append_audit(
                        Actor("background:re_triage_watchdog", "service", case["facility_id"]),
                        "watchdog_alert_created", case_id,
                        {"agent_name": "re_triage_watchdog", "rule_version": rule_version,
                         "reason_code": reason_code, "level": level, "revision": revision},
                    )
                store.db.execute("COMMIT")
                return cursor.rowcount == 1
            except Exception:
                if store.db.in_transaction:
                    store.db.execute("ROLLBACK")
                raise

    def alerts_for_facility(self, facility_id: str, *, status: str | None = None,
                            limit: int = 500) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000 or status not in {None, "open", "acknowledged", "closed"}:
            raise ValueError("alert query is invalid")
        query = (
            "SELECT alert_id,pseudonymous_id,facility_id,revision,level,reason_code,waiting_minutes,"
            "current_category,score,next_role,acknowledged_by,acknowledged_at,status,created_at,updated_at "
            "FROM background_alerts WHERE facility_id=?"
        )
        params: list[Any] = [facility_id]
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY created_at DESC,alert_id LIMIT ?"
        params.append(limit)
        with self.__store._lock:
            rows = self.__store.db.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def acknowledge_alert(self, alert_id: str, actor: Actor) -> bool:
        if actor.role not in {"nurse", "doctor", "medical_officer"}:
            raise AuthorizationError("role cannot acknowledge alerts")
        store = self.__store
        now = self._now()
        with store._lock:
            store.db.execute("BEGIN IMMEDIATE")
            try:
                row = store.db.execute(
                    "SELECT case_id,facility_id,revision,level,reason_code,next_role,status "
                    "FROM background_alerts WHERE alert_id=?", (alert_id,)
                ).fetchone()
                if row is None or row["facility_id"] != actor.facility_id:
                    raise AuthorizationError("alert unavailable")
                if row["status"] != "open":
                    store.db.execute("COMMIT")
                    return False
                store.db.execute(
                    "UPDATE background_alerts SET status='acknowledged',acknowledged_by=?,acknowledged_at=?,updated_at=? "
                    "WHERE alert_id=? AND status='open'", (actor.user_id, now, now, alert_id)
                )
                _insert_background_event(
                    store.db, "alert_acknowledged", case_id=row["case_id"], facility_id=row["facility_id"],
                    revision=row["revision"], payload={"level": row["level"],
                                                        "reason_code": row["reason_code"],
                                                        "next_role": row["next_role"],
                                                        "revision": row["revision"]},
                    idempotency_key=f"alert-ack:{alert_id}", occurred_at=now,
                )
                store.append_audit(actor, "watchdog_alert_acknowledged", row["case_id"],
                                   {"agent_name": "re_triage_watchdog", "level": row["level"],
                                    "reason_code": row["reason_code"], "revision": row["revision"]})
                store.db.execute("COMMIT")
                return True
            except Exception:
                if store.db.in_transaction:
                    store.db.execute("ROLLBACK")
                raise


def decrypt_sensitive_text(ciphertext: str, key: str | bytes | None = None) -> str:
    """Decrypt a field written by encrypt_sensitive_text; key remains environment-only."""
    secret = key if key is not None else os.getenv("DATA_ENCRYPTION_KEY")
    if not secret:
        raise ServiceNotConfigured("data encryption key is not configured")
    try:
        from cryptography.fernet import Fernet
        raw_key = secret.encode("ascii") if isinstance(secret, str) else secret
        return Fernet(raw_key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise ServiceNotConfigured("data encryption key is invalid") from exc
