# Phase 6A Verification — Background Foundation and Re-Triage Watchdog

**Status:** Phase 6A implemented in the sandbox project. This is the first staged increment of the supplied background-agent extension, not completion of the full five-agent extension.

## Implemented

- Added SQLite tables for the structured background event feed, per-agent event cursors, expiring job leases, idempotent run ledger, heartbeats, alerts, and case revisions.
- Added structured case projection fields for risk, priority, acknowledgement, symptom groups, onset week, demographic band, site, and coarse unit. Existing encrypted narrative and note columns remain separate.
- Case creation, intake, submission, note persistence, review, and alert/outbox actions write their associated background event in the same SQLite transaction as the state mutation. Event payload keys are allowlisted and size-limited.
- Added `StructuredBackgroundReader`, which uses SQLite's authorizer to reject reads of encrypted case narratives, triage notes, consent purpose, unapproved tables, and unapproved columns.
- Added `BackgroundStore` coordination functions for cursors, leases, heartbeats, run retry state, facility-scoped alert listing, idempotent alert creation, and facility-checked acknowledgment.
- Added `background/watchdog.py` with resumable event consumption, lease protection, retry-on-failure, heartbeat reporting, and escalation only from an explicitly configured YAML ladder.
- Added the application-owned APScheduler runtime and health banner. Scheduler execution requires the environment flag plus JWT and data-encryption keys.
- Added `rulepacks/escalation.yaml` with an empty ladder. No clinical escalation timers or routing destinations were invented.

## Validation

- `python3 -m unittest discover -s tests -v` — **81 tests passed** across Phases 1–6A.
- `python3 -m compileall -q agents background config.py schemas.py store.py main.py rag.py graph.py tools.py tests` — passed.
- Live local HTTP smoke test with temporary synthetic secrets and a temporary SQLite database — `/health` returned `background_monitoring: online` and the expected warning that escalation is disabled while policies remain unconfigured.
- APScheduler 3.11.3 installed to match `APScheduler>=3.10,<4`.

## Safety and current limits

- With the supplied empty escalation ladder, the watchdog creates **no automatic clinical alerts**. It never sends notifications.
- A `vitals_updated` event remains retryable if the re-triage callback is absent; this stage does not yet expose the vitals endpoint or connect a revision-producing re-triage callback.
- The remaining planned agents—Scheduled Follow-Up, Facility Routing, Cohort Surveillance, and Governance—plus their API/dashboard surfaces are **not yet implemented**. They are reserved for subsequent stages.
- No external model/provider calls were made; no new API keys were added. Tests use synthetic inputs only.
- Changes are in `/home/ubuntu/projects/triage-healthcare-agent-b9fa0069`. The user's Windows desktop directory was not edited because the connected desktop operator was reported outdated and must be updated/reconnected first.
