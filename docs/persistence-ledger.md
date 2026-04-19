# Phase 1 Persistence Ledger

`DEE-18` closes the minimum relational authority needed before orchestrator work starts.

## Tables

- `topics`: topic metadata anchor for queue, run, and scheduler rows.
- `queue_items`: retryable execution queue with claim state, failure bookkeeping, and dedupe/idempotency keys.
- `idempotency_keys`: write ledger that rejects duplicate execution keys at the database boundary.
- `runs`: one run row per claimed queue item, linked to the queue item and idempotency key.
- `run_events`: append-only runtime event ledger guarded by `UPDATE`/`DELETE` denial triggers.
- `session_ledger`, `session_leases`, `session_events`: session authority, active lease tracking, and append-only session audit trail.
- `scheduler_policies`: per-topic scheduler policy state.

## Transaction Boundary

The critical Phase 1 boundary is `claim_next_queue_item_for_run(...)`.

Inside one `BEGIN IMMEDIATE` transaction it:

1. selects one claimable queue row
2. marks that row as `claimed`
3. inserts the matching `runs` row using the same idempotency authority
4. commits only if both writes succeed

If the run insert fails, the queue claim rolls back with it, which prevents orphaned claimed work.

## Validation Intent

Phase 1 tests specifically guard:

- migration success and re-run idempotency
- duplicate idempotency rejection
- append-only event immutability
- concurrent queue claim races
- retry counter updates
- stale lease cleanup
- rollback of partial queue-claim transactions
