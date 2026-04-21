# Phase 19 First Research Walkthrough

This walkthrough is the first-user ISO26262 demo for the local CLI plus
localhost web dashboard. It is intentionally fixture-backed so README commands,
CLI behavior, graph artifacts, and web API state can be tested together.

## Setup

```bash
export CRB_DB_PATH=.crb/iso26262-demo.sqlite3
crb init --json
crb doctor --json
```

Expected signal:

- `crb init --json` creates local backend storage.
- `crb doctor --json` reports the same `CRB_DB_PATH` and workspace.

## Create The Sample Topic

```bash
crb topic create "ISO 26262 safety case drift" --objective "Track whether ASIL decomposition evidence still supports the current safety case after a supplier tool qualification change." --json
```

Expected signal:

- `topic_id` is `topic_iso_26262_safety_case_drift`.
- The initialized current best hypothesis mirrors the objective.
- This hypothesis is not final truth; it is backend-owned starting state.

## Start The First Research Request

```bash
crb run start topic_iso_26262_safety_case_drift --input "counterargument: supplier tool confidence may be stale after the latest qualification delta" --json
```

Expected signal:

- `run_id` is `run_5e3826f4ce35`.
- `queue_item_id` is `queue_5e3826f4ce35`.
- The request is queued for backend-controlled execution rather than directly
  mutating memory or graph state.

## Inspect CLI State

```bash
crb run status run_5e3826f4ce35 --json
crb queue list --topic topic_iso_26262_safety_case_drift --json
crb memory snapshot topic_iso_26262_safety_case_drift --json
crb memory hypotheses topic_iso_26262_safety_case_drift --json
```

Expected signal:

- `run status` and `queue list` agree on the same run and queue identifiers.
- `memory snapshot` includes an authority notice for backend graph and
  provenance ledgers.
- `memory hypotheses` shows the current best hypothesis, and future challengers
  must arrive through validated backend updates.

## Export Graph Artifacts

```bash
crb graph export topic_iso_26262_safety_case_drift --scope latest --format json --output iso26262-graph.json --json
crb graph view topic_iso_26262_safety_case_drift --scope latest --format html --output iso26262-graph.html --json
```

Expected signal:

- Both artifacts include the not-a-source-of-truth authority notice.
- The JSON artifact is the machine-readable graph projection.
- The HTML artifact is a local inspection page, not backend authority.

## Open The Web Dashboard

```bash
crb web serve
```

Open `http://127.0.0.1:8765/dashboard`.

Expected signal:

- Overview shows the ISO26262 topic and initialized current best hypothesis.
- Runs shows `run_5e3826f4ce35` with `timeline_source=queue_request` before a
  worker claims it.
- Queue shows `queue_5e3826f4ce35` and the same requested run id.
- Memory and Graph repeat that dashboard projections are read-only and backend
  ledgers remain authoritative.

## Fixture References

- `fixtures/iso26262_sample_research_demo.json`
- `fixtures/iso26262_web_dashboard_artifact.json`
