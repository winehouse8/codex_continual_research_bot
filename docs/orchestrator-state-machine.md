# Phase 3 Orchestrator State Machine

`DEE-25` fixes the backend-owned boundary before any runtime call:

1. claim a queue item through the relational ledger
2. load the latest persisted topic snapshot
3. reject missing or stale snapshots before runtime execution
4. build the frontier selection input and `RunExecutionRequest`
5. advance the persisted run state to `codex_executing`
6. reject runtime proposals that omit the minimum competition loop

The executable transition map is `STATE_TRANSITIONS` in
`src/codex_continual_research_bot/orchestrator.py`.

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> loading_state
    loading_state --> selecting_frontier
    loading_state --> failed: missing or stale topic snapshot
    selecting_frontier --> planning
    planning --> attacking_current_best
    attacking_current_best --> generating_challengers
    generating_challengers --> codex_executing
    codex_executing --> normalizing
    codex_executing --> failed
    normalizing --> adjudicating
    normalizing --> failed
    adjudicating --> retiring_weak_hypotheses
    adjudicating --> persisting
    adjudicating --> failed
    retiring_weak_hypotheses --> persisting
    retiring_weak_hypotheses --> failed
    persisting --> summarizing
    persisting --> failed
    summarizing --> completed
    failed --> queued: retryable
    failed --> dead_letter: terminal
```

## Snapshot Read Model

`topic_snapshots` stores strict `TopicSnapshot` payloads by
`(topic_id, snapshot_version)`. The orchestrator reads only the latest snapshot.
If a caller supplies `expected_snapshot_version` and the latest version differs,
the run transitions to `failed` and no runtime request is returned.
Resume uses the `runs.snapshot_version` recorded when the run first reached
frontier selection, so replay does not drift to a newer topic snapshot.

## Intent Builder

The run intent builder maps the claimed queue row into:

- `FrontierSelectionInput`
- `RunExecutionRequest.context_snapshot.selected_queue_items`
- `RunExecutionRequest.objective`
- `RunExecutionRequest.idempotency_key`

All generated requests set the Phase 3 competition requirements:

- `must_attack_current_best`
- `must_generate_challenger`
- `must_collect_support_and_challenge`

## Proposal Gate

Before later phases may normalize or persist runtime output,
`validate_proposal_for_competition(...)` requires:

- support and challenge arguments for the selected hypothesis targets
- a challenge argument targeting the current-best/challenger target
- at least one challenger hypothesis
- either conflict reconciliation/escalation or weaken/retire/supersede pressure
