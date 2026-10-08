"""Phase 6A FastAPI surface for the educational, synthetic/public-data triage prototype."""
from __future__ import annotations

import base64
import hashlib
import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, TypeVar

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from apscheduler.schedulers.background import BackgroundScheduler
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from agents.intake import LanguageVoiceAgent
from agents.safety import DISCLAIMER, SafetyAgent
from agents.triage import QueueScheduler
from background.watchdog import ReTriageWatchdog
from config import settings
from graph import WorkflowRunner
from schemas import (
    AuditResponse, AuthRequest, CaseCreatedResponse, CaseEnvelope,
    ConsentRequest, ConsentResponse, CreateCaseRequest, DeleteCaseResponse,
    DocumentUploadAcknowledgement, HealthResponse, IntakeAcknowledgement,
    IntakeRequest, LogoutRequest, QueueResponse, RefreshRequest,
    ReminderApprovalRequest, ReminderApprovalResponse, ReviewRequest,
    ReviewResponse, TokenPair,
)
from store import (
    Actor, AuthenticationError, AuthorizationError, ConsentRequired,
    Gatekeeper, GatekeeperError, IdempotencyConflict, ServiceNotConfigured,
    WorkflowStore, encrypt_sensitive_text,
)
from tools import ReadOnlyToolRegistry


app = FastAPI(
    title="Multimodal Healthcare Triage Assistant",
    version="0.6.0-phase6a",
    description=(
        "Educational synthetic/public-data prototype. It is not a medical device, "
        "does not diagnose or prescribe, and always pauses for qualified human review."
    ),
)
app.state.background_scheduler = None
app.state.background_monitoring_state = "disabled"
app.state.background_monitoring_banner = "Background monitoring is not configured; clinician review remains required."
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.api_rate_limit])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.allowed_origins),
        allow_credentials=True, allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
    )


@app.middleware("http")
async def request_security_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    declared_length = request.headers.get("content-length")
    limit = settings.max_request_bytes
    if request.url.path.endswith("/documents"):
        limit = settings.max_upload_bytes + 64 * 1024
    if declared_length:
        try:
            if int(declared_length) > limit:
                response = JSONResponse(status_code=413, content={
                    "error": "request_too_large", "request_id": request_id,
                })
                response.headers["X-Request-ID"] = request_id
                response.headers["Cache-Control"] = "no-store"
                return response
        except ValueError:
            response = JSONResponse(status_code=400, content={
                "error": "invalid_content_length", "request_id": request_id,
            })
            response.headers["X-Request-ID"] = request_id
            return response
    received_bytes = 0
    body_over_limit = False
    original_receive = request.receive

    async def limited_receive():
        nonlocal received_bytes, body_over_limit
        message = await original_receive()
        if message.get("type") == "http.request":
            received_bytes += len(message.get("body", b""))
            if received_bytes > limit:
                body_over_limit = True
                return {"type": "http.request", "body": b"", "more_body": False}
        return message

    request._receive = limited_receive
    try:
        response = await call_next(request)
    except Exception:
        if not body_over_limit:
            raise
        response = JSONResponse(status_code=413, content={
            "error": "request_too_large", "request_id": request_id,
        })
    if body_over_limit:
        response = JSONResponse(status_code=413, content={
            "error": "request_too_large", "request_id": request_id,
        })
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


@app.exception_handler(RequestValidationError)
async def generic_validation_error(request: Request, _exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "request_validation_failed", "request_id": getattr(request.state, "request_id", "")},
    )


@lru_cache(maxsize=1)
def _configured_gatekeeper() -> Gatekeeper:
    secret = os.getenv("JWT_SIGNING_SECRET")
    if not secret:
        raise ServiceNotConfigured("JWT signing secret is not configured")
    database_path = os.getenv("TRIAGE_DATABASE_PATH", "triage_assistant.sqlite3")
    try:
        return Gatekeeper(database_path=database_path, jwt_secret=secret)
    except ServiceNotConfigured:
        raise
    except Exception as exc:
        raise ServiceNotConfigured("Gatekeeper is unavailable") from exc


@lru_cache(maxsize=1)
def _configured_workflow_store() -> WorkflowStore:
    return WorkflowStore(_configured_gatekeeper(), retention_days=settings.retention_days)


def _service() -> Gatekeeper:
    try:
        return _configured_gatekeeper()
    except ServiceNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Service is not configured") from exc


def _workflow_store() -> WorkflowStore:
    try:
        workflow = _configured_workflow_store()
        workflow.purge_expired()
        return workflow
    except ServiceNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Service is not configured") from exc


@app.on_event("startup")
def startup_retention_sweep() -> None:
    if settings.retention_days is not None and settings.retention_days > 0 and os.getenv("JWT_SIGNING_SECRET"):
        _workflow_store()


def _run_watchdog_job() -> None:
    try:
        workflow = _configured_workflow_store()
        result = ReTriageWatchdog(workflow.background_store()).run_once()
        app.state.background_monitoring_state = "online" if result.status in {"ok", "degraded"} else "offline"
        app.state.background_monitoring_banner = (
            "Some background policies are unconfigured; automatic escalation is disabled."
            if result.status == "degraded" else None
        )
    except Exception:
        # Do not log case data or exception text from background processing.
        app.state.background_monitoring_state = "offline"
        app.state.background_monitoring_banner = "Background monitoring is unavailable; use manual review."


@app.on_event("startup")
def startup_background_scheduler() -> None:
    if not settings.background_scheduler_enabled:
        app.state.background_monitoring_state = "disabled"
        app.state.background_monitoring_banner = "Background monitoring is disabled; clinician review remains required."
        return
    if not settings.jwt_secret_configured or not settings.data_encryption_key_configured:
        app.state.background_monitoring_state = "disabled"
        app.state.background_monitoring_banner = "Background monitoring is not configured; clinician review remains required."
        return
    try:
        _configured_workflow_store()
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(
            _run_watchdog_job, "interval", seconds=settings.watchdog_interval_seconds,
            id="re_triage_watchdog", max_instances=1, coalesce=True, misfire_grace_time=30,
            next_run_time=datetime.now(timezone.utc),
        )
        scheduler.start()
        app.state.background_scheduler = scheduler
        app.state.background_monitoring_state = "offline"
        app.state.background_monitoring_banner = "Waiting for the first watchdog heartbeat."
    except Exception:
        app.state.background_scheduler = None
        app.state.background_monitoring_state = "offline"
        app.state.background_monitoring_banner = "Background monitoring could not start; use manual review."


@app.on_event("shutdown")
def shutdown_background_scheduler() -> None:
    scheduler = app.state.background_scheduler
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)
    app.state.background_scheduler = None


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or str(uuid.uuid4())


def _bearer_actor(authorization: str, gatekeeper: Gatekeeper) -> Actor:
    scheme, sep, token = authorization.partition(" ")
    if not sep or scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Request could not be authorized")
    try:
        return gatekeeper.verify_access_token(token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="Request could not be authorized") from exc


def _map_error(exc: GatekeeperError) -> HTTPException:
    if isinstance(exc, AuthenticationError):
        return HTTPException(status_code=401, detail="Request could not be authorized")
    if isinstance(exc, AuthorizationError):
        return HTTPException(status_code=403, detail="Request is not permitted")
    if isinstance(exc, ConsentRequired):
        return HTTPException(status_code=403, detail="Required consent is not available")
    if isinstance(exc, IdempotencyConflict):
        return HTTPException(status_code=409, detail="Request conflicts with an earlier request")
    if isinstance(exc, ServiceNotConfigured):
        return HTTPException(status_code=503, detail="Service is not configured")
    return HTTPException(status_code=400, detail="Request could not be completed")


T = TypeVar("T")


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if value is None:
        return None
    return value


def _idempotent_call(
    store: WorkflowStore, *, actor_id: str, endpoint: str, key: str,
    payload: Any, response_model: type[T] | None, operation: Callable[[], T],
) -> T:
    try:
        store.ensure_idempotency_encryption()
        cached = store.begin_idempotency(actor_id, endpoint, key, payload)
    except GatekeeperError as exc:
        raise _map_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Idempotency key is invalid") from exc
    if cached is not None:
        if isinstance(cached, dict) and "__cached_result__" in cached:
            cached = cached["__cached_result__"]
        if isinstance(cached, dict) and "__http_error__" in cached:
            raise HTTPException(status_code=int(cached["__http_error__"]),
                                detail=str(cached.get("detail", "Request could not be completed")))
        return response_model.model_validate(cached) if response_model else cached  # type: ignore[return-value]
    try:
        result = operation()
        store.finish_idempotency(actor_id, endpoint, key, _jsonable(result))
        return result
    except HTTPException as exc:
        # Cache safe, generic HTTP failures too so retries cannot repeat mutations.
        try:
            store.finish_idempotency(actor_id, endpoint, key,
                                     {"__http_error__": exc.status_code,
                                      "detail": str(exc.detail)[:160]})
        except Exception:
            store.cancel_idempotency(actor_id, endpoint, key)
        raise
    except Exception:
        store.cancel_idempotency(actor_id, endpoint, key)
        raise


def _case_access(gatekeeper: Gatekeeper, actor: Actor, case_id: str,
                 *, creators_only: bool = False) -> dict[str, Any]:
    if creators_only and actor.role not in Gatekeeper.CASE_CREATORS:
        raise HTTPException(status_code=403, detail="Request is not permitted")
    try:
        return gatekeeper.authorize_case_access(actor, case_id)
    except GatekeeperError as exc:
        raise _map_error(exc) from exc


def _require_reviewer(actor: Actor) -> None:
    if actor.role not in {"doctor", "medical_officer"}:
        raise HTTPException(status_code=403, detail="Request is not permitted")


def _case_envelope(request: Request, store: WorkflowStore, case_id: str) -> CaseEnvelope:
    try:
        metadata, note_data = store.get_case_envelope_data(case_id)
        return CaseEnvelope(
            request_id=_request_id(request), case_id=case_id,
            status=metadata["workflow_state"], degraded_mode=bool(metadata["degraded_mode"]),
            triage_note=note_data, disclaimer=DISCLAIMER,
        )
    except GatekeeperError as exc:
        raise _map_error(exc) from exc
    except ServiceNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Secure case storage is not configured") from exc


@app.get("/health", response_model=HealthResponse, tags=["service"])
def health() -> HealthResponse:
    monitoring = app.state.background_monitoring_state
    banner = app.state.background_monitoring_banner
    scheduler = app.state.background_scheduler
    if scheduler is not None and scheduler.running:
        try:
            heartbeat = _configured_workflow_store().background_store().heartbeat_status("re_triage_watchdog")
            if heartbeat is None:
                monitoring = "offline"
                banner = "Waiting for the first watchdog heartbeat; use manual review."
            else:
                seen = datetime.fromisoformat(str(heartbeat["last_seen"]).replace("Z", "+00:00"))
                stale_after = max(120, settings.watchdog_interval_seconds * 3)
                if (datetime.now(timezone.utc) - seen.astimezone(timezone.utc)).total_seconds() > stale_after \
                        or heartbeat["status"] in {"failed", "offline"}:
                    monitoring = "offline"
                    banner = "Background monitoring is offline; use manual review."
                else:
                    monitoring = "online"
                    if heartbeat["status"] == "degraded":
                        banner = "Some background policies are unconfigured; automatic escalation is disabled."
                    else:
                        banner = None
        except Exception:
            monitoring = "offline"
            banner = "Background monitoring health is unavailable; use manual review."
    elif settings.background_scheduler_enabled and monitoring != "disabled":
        monitoring = "offline"
        banner = "Background monitoring is offline; use manual review."
    return HealthResponse(status="ok", service="triage-assistant-backend", mode="prototype",
                          disclaimer=DISCLAIMER, background_monitoring=monitoring,
                          background_banner=banner)


@app.post("/auth/login", response_model=TokenPair, tags=["authentication"])
@limiter.limit(settings.login_rate_limit)
def login(request: Request, body: AuthRequest,
          idempotency_key: str = Header(..., alias="Idempotency-Key")) -> TokenPair:
    gatekeeper = _service()
    workflow = _workflow_store()

    def operation() -> TokenPair:
        try:
            token_pair = TokenPair.model_validate(gatekeeper.login(body.user_id, body.password))
            actor = gatekeeper.verify_access_token(token_pair.access_token)
            workflow.append_audit(actor, "login_succeeded", details={"endpoint": "/auth/login"})
            return token_pair
        except GatekeeperError as exc:
            raise _map_error(exc) from exc

    return _idempotent_call(workflow, actor_id=f"login:{body.user_id}", endpoint="/auth/login",
                            key=idempotency_key, payload=body.model_dump(mode="json"),
                            response_model=TokenPair, operation=operation)


@app.post("/auth/refresh", response_model=TokenPair, tags=["authentication"])
@limiter.limit(settings.api_rate_limit)
def refresh(request: Request, body: RefreshRequest,
            idempotency_key: str = Header(..., alias="Idempotency-Key")) -> TokenPair:
    gatekeeper = _service()
    workflow = _workflow_store()
    token_fingerprint = hashlib.sha256(body.refresh_token.encode()).hexdigest()

    def operation() -> TokenPair:
        try:
            token_pair = TokenPair.model_validate(gatekeeper.refresh(body.refresh_token))
            actor = gatekeeper.verify_access_token(token_pair.access_token)
            workflow.append_audit(actor, "refresh_succeeded", details={"endpoint": "/auth/refresh"})
            return token_pair
        except GatekeeperError as exc:
            raise _map_error(exc) from exc

    return _idempotent_call(workflow, actor_id=f"refresh:{token_fingerprint}", endpoint="/auth/refresh",
                            key=idempotency_key, payload={"refresh_token_sha256": token_fingerprint},
                            response_model=TokenPair, operation=operation)


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["authentication"])
@limiter.limit(settings.api_rate_limit)
def logout(request: Request, authorization: str = Header(...), body: LogoutRequest | None = None,
           idempotency_key: str = Header(..., alias="Idempotency-Key")) -> None:
    gatekeeper = _service()
    workflow = _workflow_store()
    actor = _bearer_actor(authorization, gatekeeper)
    _scheme, _sep, token = authorization.partition(" ")

    def operation() -> None:
        try:
            gatekeeper.logout(token, body.refresh_token if body else None)
            workflow.append_audit(actor, "logout_succeeded", details={"endpoint": "/auth/logout"})
            return None
        except GatekeeperError as exc:
            raise _map_error(exc) from exc

    _idempotent_call(workflow, actor_id=actor.user_id, endpoint="/auth/logout",
                     key=idempotency_key,
                     payload={"access_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                              "refresh_token_sha256": hashlib.sha256(body.refresh_token.encode()).hexdigest()
                              if body and body.refresh_token else None},
                     response_model=None, operation=operation)
    return None


@app.post("/consent", response_model=ConsentResponse, status_code=status.HTTP_201_CREATED,
          tags=["cases"])
@limiter.limit(settings.api_rate_limit)
def create_consent(request: Request, body: ConsentRequest, authorization: str = Header(...),
                   idempotency_key: str = Header(..., alias="Idempotency-Key")) -> ConsentResponse:
    gatekeeper = _service()
    workflow = _workflow_store()
    actor = _bearer_actor(authorization, gatekeeper)

    def operation() -> ConsentResponse:
        try:
            consent_id = gatekeeper.record_consent(actor, body.purpose, body.consented, body.language)
            response = ConsentResponse(consent_id=consent_id, status="recorded")
            workflow.append_audit(actor, "consent_recorded", details={"endpoint": "/consent"})
            return response
        except GatekeeperError as exc:
            raise _map_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Consent request is invalid") from exc

    return _idempotent_call(workflow, actor_id=actor.user_id, endpoint="/consent", key=idempotency_key,
                            payload=body.model_dump(mode="json"), response_model=ConsentResponse,
                            operation=operation)


@app.post("/cases", response_model=CaseCreatedResponse, status_code=status.HTTP_201_CREATED,
          tags=["cases"])
@limiter.limit(settings.api_rate_limit)
def create_case(request: Request, body: CreateCaseRequest, authorization: str = Header(...),
                idempotency_key: str = Header(..., alias="Idempotency-Key")) -> CaseCreatedResponse:
    gatekeeper = _service()
    workflow = _workflow_store()
    actor = _bearer_actor(authorization, gatekeeper)

    def operation() -> CaseCreatedResponse:
        try:
            result = gatekeeper.start_case(actor, body.consent_id, body.scenario, idempotency_key,
                                           language=body.language)
            workflow.ensure_case(result.case_id)
            workflow.append_audit(actor, "case_created", result.case_id,
                                  {"endpoint": "/cases", "state": result.state})
            return CaseCreatedResponse(
                case_id=result.case_id, pseudonymous_id=result.pseudonymous_id,
                state=result.state, created_at=result.created_at,
                idempotent_replay=result.idempotent_replay,
            )
        except GatekeeperError as exc:
            raise _map_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Case request is invalid") from exc

    return _idempotent_call(workflow, actor_id=actor.user_id, endpoint="/cases", key=idempotency_key,
                            payload=body.model_dump(mode="json"), response_model=CaseCreatedResponse,
                            operation=operation)


@app.post("/cases/{case_id}/intake", response_model=IntakeAcknowledgement,
          status_code=status.HTTP_202_ACCEPTED, tags=["cases"])
@limiter.limit(settings.api_rate_limit)
def submit_intake(request: Request, case_id: str, body: IntakeRequest,
                  authorization: str = Header(...),
                  idempotency_key: str = Header(..., alias="Idempotency-Key")) -> IntakeAcknowledgement:
    gatekeeper = _service()
    workflow = _workflow_store()
    actor = _bearer_actor(authorization, gatekeeper)
    _case_access(gatekeeper, actor, case_id, creators_only=True)

    def operation() -> IntakeAcknowledgement:
        safety = SafetyAgent()
        language_agent = LanguageVoiceAgent(original_encryptor=encrypt_sensitive_text, safety_agent=safety)
        if body.text is not None:
            result = language_agent.process_text(body.text, body.language)
            if result.status == "quarantined":
                workflow.quarantine(case_id)
                workflow.append_audit(actor, "intake_quarantined", case_id,
                                      {"result": "sentinel_or_encryption_block"})
                return IntakeAcknowledgement(case_id=case_id, status="quarantined",
                                             encrypted_at_rest=False, flags=result.flags)
            source = body.text.strip()
            redacted = safety.redact(source, pass_number=1)
            redacted = safety.redact(redacted, pass_number=2)
            english = result.english_text or ""
            english = safety.redact(safety.redact(english, 1), 2) if english else ""
            state = "accepted" if result.status == "complete" else "needs_interpreter"
            payload = {
                "language": result.detected_language,
                "redacted_original": redacted[:2000],
                "redacted_english": english[:20000],
                "original_ciphertext": result.encrypted_original_text,
                "translation_flags": result.flags,
                "audio_persisted": False,
            }
        else:
            try:
                audio = base64.b64decode(body.audio_base64 or "", validate=True)
            except Exception as exc:
                raise HTTPException(status_code=422, detail="Audio encoding is invalid") from exc
            result = language_agent.process_audio(audio, body.language, synthetic_audio=False)
            state = "needs_interpreter"
            payload = {
                "language": result.detected_language,
                "redacted_original": "", "redacted_english": "",
                "original_ciphertext": None,
                "translation_flags": result.flags,
                "audio_persisted": False,
            }
        try:
            workflow.save_intake(case_id, payload, state)
        except ServiceNotConfigured as exc:
            workflow.quarantine(case_id)
            raise HTTPException(status_code=503, detail="Secure case storage is not configured") from exc
        workflow.append_audit(actor, "intake_received", case_id,
                              {"result": state,
                               "input_sha256": hashlib.sha256(
                                   (body.text or body.audio_base64 or "").encode()).hexdigest()})
        return IntakeAcknowledgement(case_id=case_id, status=state,
                                     encrypted_at_rest=bool(payload.get("original_ciphertext") or body.text),
                                     flags=result.flags)

    return _idempotent_call(workflow, actor_id=actor.user_id, endpoint=f"/cases/{case_id}/intake",
                            key=idempotency_key, payload=body.model_dump(mode="json"),
                            response_model=IntakeAcknowledgement, operation=operation)


@app.post("/cases/{case_id}/documents", response_model=DocumentUploadAcknowledgement,
          tags=["cases"])
@limiter.limit(settings.api_rate_limit)
async def upload_document(request: Request, case_id: str, file: UploadFile = File(...),
                          authorization: str = Header(...),
                          idempotency_key: str = Header(..., alias="Idempotency-Key")) -> DocumentUploadAcknowledgement:
    gatekeeper = _service()
    workflow = _workflow_store()
    actor = _bearer_actor(authorization, gatekeeper)
    _case_access(gatekeeper, actor, case_id, creators_only=True)
    raw = await file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Upload exceeds the configured size limit")
    if not (raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"%PDF-")):
        raise HTTPException(status_code=415, detail="Upload file signature is unsupported")
    fingerprint = hashlib.sha256(raw).hexdigest()
    del raw

    def operation() -> DocumentUploadAcknowledgement:
        # The supplied plan requires an OCR sandbox with network isolation. This sandbox
        # cannot create a network namespace, so no upload is parsed, stored, or OCR'd.
        workflow.append_audit(actor, "document_upload_manual_entry_required", case_id,
                              {"result": "secure_ocr_sandbox_unavailable", "input_sha256": fingerprint})
        return DocumentUploadAcknowledgement(
            case_id=case_id, status="rejected", sanitized=False, page_count=0,
            manual_entry_required=True,
            flags=["secure_ocr_sandbox_unavailable", "upload_not_retained", "manual_entry_required"],
        )

    return _idempotent_call(workflow, actor_id=actor.user_id, endpoint=f"/cases/{case_id}/documents",
                            key=idempotency_key, payload={"sha256": fingerprint},
                            response_model=DocumentUploadAcknowledgement, operation=operation)


@app.post("/cases/{case_id}/submit", response_model=CaseEnvelope,
          status_code=status.HTTP_202_ACCEPTED, tags=["cases"])
@limiter.limit(settings.api_rate_limit)
def submit_case(request: Request, case_id: str, authorization: str = Header(...),
                idempotency_key: str = Header(..., alias="Idempotency-Key")) -> CaseEnvelope:
    gatekeeper = _service()
    workflow = _workflow_store()
    actor = _bearer_actor(authorization, gatekeeper)
    _case_access(gatekeeper, actor, case_id, creators_only=True)

    def operation() -> CaseEnvelope:
        result = WorkflowRunner(workflow).run(case_id, actor)
        if result.state == "quarantined":
            workflow.append_audit(actor, "case_submission_quarantined", case_id,
                                  {"result": "human_review_required"})
        return _case_envelope(request, workflow, case_id)

    return _idempotent_call(workflow, actor_id=actor.user_id, endpoint=f"/cases/{case_id}/submit",
                            key=idempotency_key, payload={"case_id": case_id},
                            response_model=CaseEnvelope, operation=operation)


@app.get("/cases/{case_id}", response_model=CaseEnvelope, tags=["cases"])
@limiter.limit(settings.api_rate_limit)
def get_case(request: Request, case_id: str, authorization: str = Header(...)) -> CaseEnvelope:
    gatekeeper = _service()
    workflow = _workflow_store()
    actor = _bearer_actor(authorization, gatekeeper)
    _case_access(gatekeeper, actor, case_id)
    workflow.append_audit(actor, "case_viewed", case_id, {"endpoint": "GET /cases/{case_id}"})
    return _case_envelope(request, workflow, case_id)


@app.get("/queue", response_model=QueueResponse, tags=["review"])
@limiter.limit(settings.api_rate_limit)
def get_queue(request: Request, authorization: str = Header(...),
              category: str | None = Query(default=None, pattern="^(red|amber|green|insufficient_information)$"),
              scenario: str | None = Query(
                  default=None,
                  pattern="^(outpatient_queue|occupational_screening|campus_fever|maternal_follow_up|chronic_check_in|camp_screening)$",
              )) -> QueueResponse:
    gatekeeper = _service()
    workflow = _workflow_store()
    actor = _bearer_actor(authorization, gatekeeper)
    if actor.role not in {"nurse", "doctor"}:
        raise HTTPException(status_code=403, detail="Request is not permitted")
    rows = workflow.list_queue_cases(actor.facility_id, category=category)
    if scenario:
        rows = [row for row in rows if row["note"]["case_details"]["scenario"] == scenario]
    now = datetime.now(timezone.utc)
    inputs = []
    for row in rows:
        arrival = datetime_from_iso(row["created_at"])
        inputs.append({
            "case_id": row["pseudonymous_id"], "risk_category": row["risk_category"],
            "waiting_minutes": max(0.0, (now - arrival).total_seconds() / 60.0),
            "arrival_time": arrival,
        })
    if scenario:
        registry = ReadOnlyToolRegistry(
            Path(__file__).parent / "rulepacks" / "scenarios.yaml"
        )
        scheduler = registry.call(agent="queue_scheduler", tool="queue_scoring_config", scenario=scenario)
    else:
        # Cross-scenario scores are not comparable unless an approved unified scale exists.
        scheduler = QueueScheduler()
    result = scheduler.schedule(inputs)
    workflow.append_audit(actor, "queue_viewed", details={"count": len(result.items), "result": result.strategy})
    return QueueResponse(request_id=_request_id(request), items=result.items, alerts=result.alerts,
                         strategy=result.strategy, flags=result.flags, disclaimer=DISCLAIMER)


def datetime_from_iso(value: str):
    from datetime import datetime, timezone
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


@app.post("/cases/{case_id}/review", response_model=ReviewResponse, tags=["review"])
@limiter.limit(settings.api_rate_limit)
def review_case(request: Request, case_id: str, body: ReviewRequest,
                authorization: str = Header(...),
                idempotency_key: str = Header(..., alias="Idempotency-Key")) -> ReviewResponse:
    gatekeeper = _service()
    workflow = _workflow_store()
    actor = _bearer_actor(authorization, gatekeeper)
    _require_reviewer(actor)
    _case_access(gatekeeper, actor, case_id)

    def operation() -> ReviewResponse:
        metadata, note_data = workflow.get_case_envelope_data(case_id)
        if note_data is None or metadata["workflow_state"] != "awaiting_review":
            raise HTTPException(status_code=409, detail="Case is not awaiting review")
        safety = SafetyAgent()
        if not safety.scan_input(body.reason).safe or safety.guard_output(body.reason).status != "cleared":
            raise HTTPException(status_code=422, detail="Reviewer input was not accepted")
        from schemas import TriageNote
        note = TriageNote.model_validate(note_data)
        if body.edits:
            edits = body.edits.model_dump(exclude_none=True)
            for field, value in edits.items():
                guarded = safety.guard_output(value)
                if guarded.status != "cleared":
                    raise HTTPException(status_code=422, detail="Reviewer edit was not accepted")
                setattr(note, field, guarded.text)
        new_state = {"approve": "reviewed", "edit": "awaiting_review", "escalate": "escalated"}[body.decision]
        note.status = new_state
        note.reviewer_action_panel.status = new_state
        note.reviewer_action_panel.reviewer_role = actor.role
        note.reviewer_action_panel.decision = body.decision
        note.reviewer_action_panel.reason = body.reason.strip()
        note.reviewer_action_panel.action_time = WorkflowStore._now()
        workflow.update_reviewed_note(case_id, note.model_dump(mode="json"), new_state)
        queued = 0
        if body.decision in {"approve", "escalate"}:
            queued = int(workflow.insert_outbox(
                case_id, f"referral:{case_id}:{body.decision}", "referral_packet",
                {"case_reference": metadata["pseudonymous_id"], "decision": body.decision,
                 "triage_note": note.model_dump(mode="json")},
            ))
        reason_hash = hashlib.sha256(body.reason.encode()).hexdigest()
        audit_reference = workflow.append_audit(
            actor, "case_reviewed", case_id,
            {"decision": body.decision, "state": new_state, "reason_sha256": reason_hash,
                 "count": queued},
        )
        return ReviewResponse(
            request_id=_request_id(request), case_id=case_id, status=new_state,
            audit_reference=audit_reference, queued_outbox_count=queued, disclaimer=DISCLAIMER,
        )

    return _idempotent_call(workflow, actor_id=actor.user_id, endpoint=f"/cases/{case_id}/review",
                            key=idempotency_key, payload=body.model_dump(mode="json"),
                            response_model=ReviewResponse, operation=operation)


@app.post("/cases/{case_id}/reminders", response_model=ReminderApprovalResponse, tags=["review"])
@limiter.limit(settings.api_rate_limit)
def approve_reminders(request: Request, case_id: str, body: ReminderApprovalRequest,
                      authorization: str = Header(...),
                      idempotency_key: str = Header(..., alias="Idempotency-Key")) -> ReminderApprovalResponse:
    gatekeeper = _service()
    workflow = _workflow_store()
    actor = _bearer_actor(authorization, gatekeeper)
    _require_reviewer(actor)
    _case_access(gatekeeper, actor, case_id)

    def operation() -> ReminderApprovalResponse:
        metadata, note_data = workflow.get_case_envelope_data(case_id)
        if metadata["workflow_state"] not in {"reviewed", "escalated"} or note_data is None:
            raise HTTPException(status_code=409, detail="Reminder drafts require a completed review")
        from schemas import ReminderDraft, TriageNote
        note = TriageNote.model_validate(note_data)
        queued = 0
        for index in body.draft_indices:
            if index >= len(note.reminder_drafts):
                raise HTTPException(status_code=422, detail="Reminder draft index is invalid")
            draft = note.reminder_drafts[index]
            if draft.status == "approved":
                continue
            note.reminder_drafts[index] = ReminderDraft(
                text=draft.text, language=draft.language, status="approved"
            )
            queued += int(workflow.insert_outbox(
                case_id, f"reminder:{case_id}:{index}", "reminder",
                {"case_reference": metadata["pseudonymous_id"], "draft_index": index,
                 "text": draft.text, "language": draft.language},
            ))
        workflow.update_reviewed_note(case_id, note.model_dump(mode="json"), metadata["workflow_state"])
        workflow.append_audit(actor, "reminder_drafts_approved", case_id,
                              {"count": queued, "result": "pending_outbox"})
        return ReminderApprovalResponse(
            request_id=_request_id(request), case_id=case_id, queued_count=queued,
            status="queued_for_delivery", disclaimer=DISCLAIMER,
        )

    return _idempotent_call(workflow, actor_id=actor.user_id, endpoint=f"/cases/{case_id}/reminders",
                            key=idempotency_key, payload=body.model_dump(mode="json"),
                            response_model=ReminderApprovalResponse, operation=operation)


@app.delete("/cases/{case_id}", response_model=DeleteCaseResponse, tags=["cases"])
@limiter.limit(settings.api_rate_limit)
def delete_case(request: Request, case_id: str, authorization: str = Header(...),
                idempotency_key: str = Header(..., alias="Idempotency-Key")) -> DeleteCaseResponse:
    gatekeeper = _service()
    workflow = _workflow_store()
    actor = _bearer_actor(authorization, gatekeeper)

    def operation() -> DeleteCaseResponse:
        metadata = workflow.case_metadata(case_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Case is unavailable")
        if metadata["facility_id"] != actor.facility_id or (
                actor.role != "admin" and not (
                    actor.role in Gatekeeper.CASE_CREATORS and metadata["created_by"] == actor.user_id
                )):
            raise HTTPException(status_code=403, detail="Request is not permitted")
        reference = workflow.purge_case(
            case_id, actor, "case_purged",
            {"state": "purged", "endpoint": "DELETE /cases/{case_id}"},
            preserve_delete_idempotency=True,
        )
        return DeleteCaseResponse(request_id=_request_id(request), case_id=case_id,
                                  status="purged", deleted=True,
                                  audit_reference=reference, disclaimer=DISCLAIMER)

    return _idempotent_call(workflow, actor_id=actor.user_id, endpoint=f"DELETE /cases/{case_id}",
                            key=idempotency_key, payload={"case_id": case_id},
                            response_model=DeleteCaseResponse, operation=operation)


@app.get("/audit", response_model=AuditResponse, tags=["audit"])
@limiter.limit(settings.api_rate_limit)
def get_audit(request: Request, authorization: str = Header(...),
              since: str | None = Query(default=None, max_length=80),
              until: str | None = Query(default=None, max_length=80)) -> AuditResponse:
    gatekeeper = _service()
    workflow = _workflow_store()
    actor = _bearer_actor(authorization, gatekeeper)
    if actor.role != "admin":
        raise HTTPException(status_code=403, detail="Request is not permitted")
    normalized: dict[str, str | None] = {"since": None, "until": None}
    for label, value in (("since", since), ("until", until)):
        if value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone required")
                normalized[label] = parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (ValueError, TypeError):
                raise HTTPException(status_code=422, detail=f"{label} must be an ISO timestamp with timezone")
    if normalized["since"] and normalized["until"] and normalized["since"] > normalized["until"]:
        raise HTTPException(status_code=422, detail="Time range is invalid")
    if not workflow.verify_audit_chain():
        raise HTTPException(status_code=503, detail="Audit integrity check failed")
    entries = workflow.audit_entries(normalized["since"], normalized["until"],
                                     facility_id=actor.facility_id)
    safe_entries = [{key: item[key] for key in (
        "audit_reference", "actor_id", "actor_role", "facility_id", "action",
        "case_reference_hash", "occurred_at", "details",
    )} for item in entries]
    return AuditResponse(request_id=_request_id(request), entries=safe_entries)
