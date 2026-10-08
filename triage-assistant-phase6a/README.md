# Multimodal Healthcare Triage Assistant — Phases 1–6A

**Educational prototype for triage-support purposes only. Not a medical device. It does not diagnose or prescribe, and it does not replace a qualified health professional. Use synthetic or public data only.**

## Delivered agent batches

### Phase 1 — agents 1–4

1. **Intake** — quote-backed structured extraction, one schema-repair attempt, then conservative fallback marked insufficient.
2. **Document and Vision** — local OCR adapters and manual-entry fallback; no image-description model is enabled. The API currently blocks OCR uploads because the required no-network OCR sandbox is unavailable in this execution environment.
3. **Triage and Risk** — only approved deterministic rules can assign risk. The supplied rule pack has no clinical rules, so the current fallback is `insufficient_information`.
4. **Safety and Privacy** — identifier-pattern redaction, optional supplied English name/place gazetteer, injection screening, output safety checks, and disclaimer injection.

### Phase 2 — agents 5–8

5. **Referral and Handoff** — builds a strict, whitelisted Triage Note draft and keeps it pending qualified review.
6. **Gatekeeper** — signed short-lived access tokens, refresh rotation, logout revocation, bcrypt password verification/lockout, consent gating, facility/role/owner authorization, pseudonymous case references, and idempotent requests.
7. **Injection Sentinel** — hard-blocks hostile patterns; ambiguous instructions are quarantined if a configured judge is unavailable.
8. **Language and Voice** — local-first callback boundary; original text is encrypted before persistence. Missing translation/transcription produces `needs_interpreter`; audio is not persisted.

### Phase 3 — agents 9–12

9. **Scenario Router** — uses only scenario names and keywords declared in `rulepacks/scenarios.yaml`.
10. **Vitals and Lab Analyst** — deterministic, configurable comparison with explicit units/demographics; no unsupplied reference values are added.
11. **Clinical Knowledge** — local public-guideline retrieval with citations. The supplied corpus is empty, so it returns no verified match until approved documents are ingested.
12. **Follow-Up Planner** — uses only rule-pack-required fields and fixed templates; empty requirements remain a configuration gap.

### Phase 4 — agents 13–15

13. **Queue Scheduler** — uses approved configured weights when available; otherwise sorts by category then arrival. Missing weighted inputs cause fallback rather than being treated as negative.
14. **Verifier** — checks quote support, rules and consistency; it can preserve or raise risk, never lower it. AI verification is optional and disabled unless explicitly wired through the gateway.
15. **Reminder and Outreach** — drafts maternal/chronic follow-ups. No reminder or referral is sent automatically; approved actions remain in the encrypted outbox.

### Phase 5 — workflow, tools, gateway, and API

- `graph.py` composes the agents, records encrypted resumable step outputs, routes read-only tools, writes the reviewer-pending Triage Note, and stops at `awaiting_review`.
- `tools.py` exposes agent-specific, read-only rule, lab-range, queue-configuration, and guideline tools; agents have no database-write tool.
- `llm_gateway.py` is the only optional model boundary. It applies egress redaction, payload bounds, provider gates, prompt-fingerprint caching, bounded retry/circuit-breaker behavior, and a per-case call cap. No provider SDK/client is enabled or required by default; adapters are explicit injection points.
- `store.py` encrypts consent-purpose text, clinical payloads, notes, agent outputs, outbox payloads, and idempotency responses with `DATA_ENCRYPTION_KEY`. Legacy plaintext consent purposes are migrated only when that key is available; startup fails closed if migration cannot encrypt them. The audit chain is hash-linked and SQLite triggers reject updates/deletes to audit rows.
- Mutating endpoints require an `Idempotency-Key`; responses carry an `X-Request-ID`. The API uses role/facility scopes, request limits, security headers, configurable CORS, IP rate limits, and generic validation errors.

### Phase 6A — background foundation and Re-Triage Watchdog

- `background/watchdog.py` consumes the committed structured event feed using a persistent cursor, run ledger, expiring lease, and heartbeat. Its reader uses SQLite's authorizer to deny encrypted narrative/note columns and unallowlisted tables even if a query is accidentally broadened.
- Case creation, intake, submission, triage-note persistence, review, alert, and outbox changes emit allowlisted structured events transactionally. Event payloads are checked for allowed keys and bounded size; they do not contain free-text intake or note content.
- The watchdog checks only explicitly configured escalation ladder entries. `rulepacks/escalation.yaml` is intentionally empty, so it creates **no clinical alerts** by default. It does not send notifications. A `vitals_updated` event is left uncommitted for retry if the workflow re-triage callback is not configured.
- APScheduler is wired to run the watchdog at the configured interval (`WATCHDOG_INTERVAL_SECONDS`, default 60). The scheduler is gated by `BACKGROUND_SCHEDULER_ENABLED` and the configured JWT/data-encryption keys. `/health` reports online/offline/disabled monitoring plus a banner. The `.env.example` keeps the scheduler off by default.
- This stage does **not** yet implement follow-up scheduling, facility routing, cohort surveillance, governance reporting, or the corresponding alert/dashboard endpoints. Those are later staged items from the extension plan.

## API endpoints

| Method | Endpoint | Access and behavior |
|---|---|---|
| `GET` | `/health` | Service status and background-monitoring state/banner; no case data |
| `POST` | `/auth/login` | Staff login; strict IP rate limit |
| `POST` | `/auth/refresh` | Rotates refresh credentials |
| `POST` | `/auth/logout` | Revokes the current session |
| `POST` | `/consent` | Records consent and capturing staff metadata |
| `POST` | `/cases` | Creates a consent-gated, pseudonymous case |
| `POST` | `/cases/{case_id}/intake` | Accepts one text or base64 audio payload; original text encrypted; unsupported speech remains manual/interpreter-only |
| `POST` | `/cases/{case_id}/documents` | Signature/size checks; currently returns a manual-entry-required rejection without retaining or parsing the file because network-isolated OCR cannot be launched here |
| `POST` | `/cases/{case_id}/submit` | Runs the local workflow and returns an `awaiting_review` note or a generic quarantined envelope |
| `GET` | `/cases/{case_id}` | Authorized owner/staff case envelope; administrators are barred from clinical content |
| `GET` | `/queue` | Facility queue for nurses/doctors; optional category/scenario filters |
| `POST` | `/cases/{case_id}/review` | Doctor/medical-officer edit, approve, or escalate action with reason |
| `POST` | `/cases/{case_id}/reminders` | Approves selected drafts into the pending outbox; no sending |
| `DELETE` | `/cases/{case_id}` | Case creator or same-facility admin; atomic audit event and dependent-data purge |
| `GET` | `/audit` | Same-facility admin-only metadata with optional timezone-aware `since`/`until` filters |

## Configuration

Copy `.env.example` to a private environment file, populate secrets locally, and never commit it:

- `JWT_SIGNING_SECRET`: at least 32 bytes; required for authentication.
- `DATA_ENCRYPTION_KEY`: a valid Fernet key; required before any idempotent mutation or encrypted case storage.
- `TRIAGE_DATABASE_PATH`: local SQLite path.
- `RETENTION_DAYS`: optional positive number of days. Expiry is set at case creation; expired cases are swept at service startup and API requests. Leave blank if a retention period has not been approved.
- `ALLOW_EXTERNAL_LLM=false` and `MOCK_LLM=true` are safe defaults. Provider keys/model IDs alone do not create adapters or enable calls. External text remains blocked until the approved name/place gazetteer is supplied.
- `MAX_REQUEST_BYTES`, `MAX_UPLOAD_BYTES`, `MAX_DOCUMENT_PAGES`, `MAX_LLM_CALLS_PER_CASE`, `MAX_LLM_INPUT_CHARS`, `MAX_LLM_OUTPUT_TOKENS`, `API_RATE_LIMIT`, `LOGIN_RATE_LIMIT`, and `ALLOWED_ORIGINS` are runtime controls.
- `BACKGROUND_SCHEDULER_ENABLED` and `WATCHDOG_INTERVAL_SECONDS` control the local APScheduler watchdog. Leave it disabled until the service secrets and operational ownership are configured. The clinical escalation ladder remains empty independently of scheduler enablement.
- Staff accounts must be provisioned through a controlled local/admin operation; there is no public self-registration API.

## Run and test

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m unittest discover -s tests -v
uvicorn main:app --host 0.0.0.0 --port 8000
```

Terminate TLS at an approved reverse proxy for any network deployment. Do not expose the prototype publicly or use real patient information.

## Known gaps from the supplied plan/environment

- The supplied `scenarios.yaml` intentionally has empty clinical rules, required fields, lab ranges, and queue weights. The guideline corpus is empty. Therefore automated risk assignment and lab interpretation remain unconfigured, and case submissions are degraded/manual-review outputs; no thresholds have been invented.
- The execution container denies network namespace creation (`unshare -n`), so the planned network-isolated OCR worker cannot be safely provided. The document route fails closed and directs the operator to manual entry; uploaded bytes are not persisted or OCR'd. Do not treat the signature check as sanitization.
- No Gemini, Groq, Sarvam, or other provider calls were made. The gateway is injectable, but no network adapters or external credentials were added.
- Phase 6A adds coordination tables and an event-driven Watchdog, but the re-triage callback and subsequent Phase 6 agents/endpoints are not yet wired. Its required health banner is visible; an empty escalation rule pack means no automatic escalation.
- Local speech, translation, and OCR weights/adapters are not configured. Audio is not retained; it returns an interpreter fallback.
- This is not a production clinical deployment. Production requires approved clinical governance/rule packs, an isolated OCR sandbox, validated local models, legal/privacy review, operational key management, TLS/reverse-proxy deployment, and monitoring.
