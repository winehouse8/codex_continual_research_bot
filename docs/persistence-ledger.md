# Phase 1 Persistence Ledger

`DEE-18` closes the minimum relational authority needed before orchestrator work starts.

## Tables

- `topics`: topic metadata anchor for queue, run, and scheduler rows.
- `queue_items`: retryable execution queue with claim state, failure bookkeeping, dedupe/idempotency keys, and a non-authoritative `requested_run_id` captured at enqueue time.
- `idempotency_keys`: write ledger that rejects duplicate execution keys at the database boundary.
- `runs`: one run row per claimed queue item, linked to the queue item and idempotency key.
- `run_events`: append-only runtime event ledger guarded by `UPDATE`/`DELETE` denial triggers.
- `topic_snapshots`: strict Phase 3 topic read model snapshots keyed by `(topic_id, snapshot_version)`.
- `session_ledger`, `session_leases`, `session_events`: session authority, active lease tracking, and append-only session audit trail.
- `scheduler_policies`: per-topic scheduler policy state.

## Run Linkage Semantics

- `queue_items.requested_run_id` is a seed/request identifier from enqueue time. It is not the authoritative execution record and it does not change during claim.
- `runs.id` is the authoritative run identifier created inside `claim_next_queue_item_for_run(...)`.
- `runs.queue_item_id` is the canonical audit join from a queue row to the actual claimed run row.
- `idempotency_keys.run_id` is filled only when the queue item is claimed and must match `runs.id`.
- `runs.snapshot_version` is filled when the orchestrator successfully loads the latest topic snapshot before runtime execution.

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
- queue seed identifier versus authoritative run linkage
- retry counter updates
- stale lease cleanup
- rollback of partial queue-claim transactions
