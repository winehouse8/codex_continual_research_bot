# Phase 19 Web UX Audit

## Scope

This audit checks whether a first user can follow README, create a sample
research topic, start a first run request, and inspect the same backend-owned
state in the localhost web dashboard.

The audit does not change source-of-truth contracts. It verifies the Phase 17
and Phase 18 web surfaces against the Phase 19 first research demo.

## Final Notes

- README now has a textual web walkthrough for the ISO26262 sample topic.
- The sample demo fixture fixes the expected topic, run, queue, and artifact
  identifiers.
- The web API exposes queued run requests in `timeline_items`, so a first user
  can see the run request immediately after `crb run start`, before any worker
  claim creates a run ledger row.
- `/api/web/runs/{run_id}/timeline` returns a read-only run timeline bundle
  based on backend run status and audit data.
- Dashboard copy preserves the authority boundary: web views are projections,
  not a source of truth.

## Visual QA Checklist

- Authority notice remains visible above topic state.
- Overview names the loaded topic and shows current best hypotheses.
- Active Conflicts remains visible as a first-screen concept even when the
  count is zero for the fresh ISO26262 topic.
- Runs tab shows queued run requests with `timeline_source=queue_request`.
- Queue tab shows the same `queue_item_id` and `requested_run_id` as CLI output.
- Memory tab keeps graph digest and projection counts separate from source of
  truth language.
- Graph tab keeps latest/history, current best, challenger, evidence, conflict,
  and provenance controls available.
- Empty states describe missing backend state without inventing research
  conclusions.

## Failure Modes Checked

- A web UI exists but the user does not know what to inspect:
  README and the walkthrough list the exact dashboard tabs and expected signals.
- CLI and web UI show different state:
  tests compare CLI-created ISO26262 run and queue ids against web timeline and
  queue responses.
- README diverges from actual commands:
  tests load the fixture commands and verify README/docs contain the same
  sample topic, commands, and artifacts.
- Conflict and authority language disappears:
  tests grep README, docs, and artifacts for authority notice and active
  conflict visibility.

## Validation Targets

- README web quickstart smoke test
- sample topic dashboard generation test
- graph artifact/link sanity test
- UX copy grep test for authority notice and conflict visibility
- final `pytest` run
