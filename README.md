# Continual Research Bot

`Continual Research Bot` is a backend-owned belief revision system for repeated
research. Codex is the user-facing research execution path, while the backend
owns topic state, queue state, provenance, graph writes, and revision decisions.

## CLI Quickstart

Phase 13 provides the executable `crb` CLI. By default it stores local operator
state in `.crb/crb.sqlite3`; set `CRB_DB_PATH` to use a different SQLite file.
The examples below are checked against `fixtures/cli_command_spec.json`.

Start with local backend storage and a topic:

```bash
crb init --json
crb doctor --json
crb topic create "Codex auth boundary" --objective "Track session ownership risk" --json
crb topic list --json
crb topic show topic_codex_auth_boundary
crb topic show topic_codex_auth_boundary --json
```

Enqueue and inspect an interactive research run:

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
```

If `queue list` shows a dead-letter item, retry it with:

```bash
crb queue retry "<dead-letter-queue-item-id>" --reason "operator confirmed transient transport failure" --json
```

```bash
crb memory snapshot topic_codex_auth_boundary --json
crb memory conflicts topic_codex_auth_boundary --json
crb memory hypotheses topic_codex_auth_boundary --json
crb graph export topic_codex_auth_boundary --format json --output graph.json
crb graph export topic_codex_auth_boundary --format dot --output graph.dot
crb graph export topic_codex_auth_boundary --format mermaid --output graph.mmd
crb graph view topic_codex_auth_boundary --format html --output graph.html
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

## UX Guarantees

- Mutating commands route through backend APIs and idempotency policy.
- CLI output is a read model or enqueue receipt, not authoritative storage.
- Human summaries expose uncertainty and active conflicts when present.
- Graph exports are visualization artifacts, not a source of truth.

The detailed contract is in `docs/cli-ux-contract.md`.

## Graph Visualization

Phase 14 graph artifacts are generated from the latest backend-owned canonical
graph write when one exists, with the latest topic snapshot used as a fallback
projection. Exports preserve deterministic node and edge ordering so JSON, DOT,
and Mermaid output can be diffed during replay or review.

Supported artifact formats:

```bash
crb graph export topic_codex_auth_boundary --format json --output graph.json
crb graph export topic_codex_auth_boundary --format dot --output graph.dot
crb graph export topic_codex_auth_boundary --format mermaid --output graph.mmd
crb graph view topic_codex_auth_boundary --format html --output graph.html
```

The JSON export includes a `memory_explorer` section that groups current-best
hypotheses, challengers, evidence, conflicts, and provenance nodes. The HTML
view is intentionally lightweight and dependency-free; it is an inspection
artifact only, while backend graph and provenance ledgers remain authoritative.
