# Continual Research Bot

`Continual Research Bot` is a backend-owned belief revision system for repeated
research. Codex is the user-facing research execution path, while the backend
owns topic state, queue state, provenance, graph writes, and revision decisions.

The first-user path below is the supported Phase 15 walkthrough. It is designed
so a clean checkout can create a sample topic, enqueue a research run, inspect
the run and failure state, and generate graph artifacts without treating CLI
output as authoritative storage.

## Install And Start

The package exposes the `crb` CLI. In development, run commands from the
repository root with the package installed in editable mode, or with
`PYTHONPATH=src`.

By default the local operator database is `.crb/crb.sqlite3`. Set `CRB_DB_PATH`
when you want an isolated tutorial database:

```bash
export CRB_DB_PATH=.crb/tutorial.sqlite3
```

Initialize local backend storage and verify the operator environment:

```bash
crb init --json
crb doctor --json
```

The same first-run path is also summarized in `crb --help`.

## CLI Quickstart

Create the sample topic used by the tutorial fixtures:

```bash
crb topic create "Codex auth boundary" --objective "Track session ownership risk" --json
crb topic list --json
crb topic show topic_codex_auth_boundary
crb topic show topic_codex_auth_boundary --json
```

Enqueue an interactive research run. This does not make the CLI the state
authority; it asks the backend queue to create work and returns the generated
run and queue ids.

```bash
crb run start topic_codex_auth_boundary --input "counterargument: warning-only stale sessions may be safe" --json
```

For a copy/paste flow, capture the generated ids from the enqueue receipt:

```bash
run_start_json=$(crb run start topic_codex_auth_boundary --input "counterargument: warning-only stale sessions may be safe" --json)
printf '%s\n' "$run_start_json"
run_id=$(RUN_START_JSON="$run_start_json" python -c 'import json, os; print(json.loads(os.environ["RUN_START_JSON"])["data"]["run_id"])')
queue_item_id=$(RUN_START_JSON="$run_start_json" python -c 'import json, os; print(json.loads(os.environ["RUN_START_JSON"])["data"]["queue_item_id"])')
crb run status "$run_id" --json
crb run resume "$run_id"
```

Inspect queue and memory state without bypassing backend authority:

```bash
crb queue list --topic topic_codex_auth_boundary --json
crb queue dead-letter "$queue_item_id"
crb memory snapshot topic_codex_auth_boundary --json
crb memory conflicts topic_codex_auth_boundary --json
crb memory hypotheses topic_codex_auth_boundary --json
```

If `queue list` shows a dead-letter item, retry it with an explicit operator
reason:

```bash
crb queue retry "<dead-letter-queue-item-id>" --reason "operator confirmed transient transport failure" --json
```

Use operational commands for health, audit, and replay checks:

```bash
crb ops health --json
crb ops audit "$run_id" --json
```

After a run has completed canonical artifacts, replay it for audit with:

```bash
crb ops replay "<completed-run-id>" --reason "operator replay audit" --json
```

## Graph Visualization Walkthrough

Graph artifacts are generated from the latest backend-owned canonical graph
write when one exists, with the latest topic snapshot used as a fallback
projection. Exports preserve deterministic node and edge ordering so JSON, DOT,
and Mermaid output can be diffed during replay or review.

Generate all supported inspection artifacts:

```bash
crb graph export topic_codex_auth_boundary --format json --output graph.json
crb graph export topic_codex_auth_boundary --format dot --output graph.dot
crb graph export topic_codex_auth_boundary --format mermaid --output graph.mmd
crb graph view topic_codex_auth_boundary --format html --output graph.html
```

Expected artifact roles:

- `graph.json`: stable machine-readable visualization payload, including
  `memory_explorer`.
- `graph.dot`: Graphviz-compatible projection for review diffs.
- `graph.mmd`: Mermaid projection for lightweight docs and issue comments.
- `graph.html`: dependency-free inspection page for the current memory
  explorer view.

The JSON and HTML artifacts include an authority notice: graph exports are
visualization artifacts, not a source of truth. Backend graph and provenance
ledgers remain authoritative.

## Understanding The First Result

A first run usually leaves a queued or resumable backend job rather than a final
research conclusion. Use these commands to find the next visible state:

- `crb run status "$run_id" --json` shows lifecycle state, report availability,
  and failure classification when present.
- `crb queue list --topic topic_codex_auth_boundary --json` shows queued,
  retryable, and human-review-required work.
- `crb queue dead-letter "$queue_item_id"` shows the failure code,
  retryability, and whether human review is required.
- `crb memory hypotheses topic_codex_auth_boundary --json` shows the current
  best hypothesis and challenger hypotheses visible to the operator.
- `crb memory conflicts topic_codex_auth_boundary --json` shows unresolved
  tension that must not be hidden by a success summary.

The tutorial fixture is `fixtures/sample_topic_run.json`, and the golden
walkthrough transcript is `fixtures/tutorial_transcript.txt`.

## Troubleshooting

`backend_not_initialized`: run `crb init --json` with the same `CRB_DB_PATH` you
will use for later commands.

`topic_not_found`: run `crb topic list --json` and use the returned `topic_id`.
The tutorial topic id is `topic_codex_auth_boundary`.

`run_not_found`: confirm the run id was read from the `run.start` JSON response.
The CLI does not invent placeholder run ids.

`queue_item_not_found`: confirm the queue id was read from the `run.start` JSON
response or from `crb queue list --topic topic_codex_auth_boundary --json`.

`queue_retry_rejected`: inspect the dead-letter item first. The backend may
reject retry when the item is not in a retryable dead-letter state.

`replay_rejected`: replay requires completed run artifacts. Use
`crb run status "$run_id" --json` and `crb ops audit "$run_id" --json` before
requesting replay.

Graph file is missing: graph commands write to the exact `--output` path. Check
the current working directory and parent directory permissions.

## Terminology Glossary

`current best hypothesis`: the backend's present best explanation. It is still
revisable when new evidence or stronger challengers arrive; do not treat it as
absolute truth.

`challenger target`: a hypothesis or conflict selected for attack, alternative
generation, or adversarial verification.

`active conflict`: preserved tension that remains visible until reconciled,
escalated, or retired by backend policy.

`memory`: backend-owned hypothesis, evidence, conflict, and provenance state
exposed through read models. Codex session context is not long-term memory.

`provenance`: the source and process trace that explains where a claim,
hypothesis, conflict, or graph write came from.

`graph export`: an inspection artifact derived from backend memory. It is not a
source of truth.

## UX Guarantees

- Mutating commands route through backend APIs and idempotency policy.
- CLI output is a read model or enqueue receipt, not authoritative storage.
- Human summaries expose uncertainty and active conflicts when present.
- Graph exports are visualization artifacts, not a source of truth.

The detailed contract is in [docs/cli-ux-contract.md](docs/cli-ux-contract.md).
