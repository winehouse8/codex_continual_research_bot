# Continual Research Bot

`Continual Research Bot` is a backend-owned belief revision system for repeated
research. Codex is the user-facing research execution path, while the backend
owns topic state, queue state, provenance, graph writes, and revision decisions.

## CLI Contract Preview

Phase 12 defines the CLI information architecture before the executable CLI is
implemented. The examples below are contract examples checked against
`fixtures/cli_command_spec.json`.

```bash
crb init --json
crb doctor --json
crb topic create "Codex auth boundary" --objective "Track session ownership risk" --json
crb topic list --json
crb topic show topic_codex_auth_boundary
crb topic show topic_codex_auth_boundary --json
```

```bash
crb run start topic_codex_auth_boundary --input "counterargument: warning-only stale sessions may be safe" --json
crb run status run_2026_04_19_001 --json
crb run resume run_2026_04_19_001
```

```bash
crb queue list --topic topic_codex_auth_boundary --json
crb queue retry queue_001 --reason "operator confirmed transient transport failure" --json
crb queue dead-letter queue_001
```

```bash
crb memory snapshot topic_codex_auth_boundary --json
crb memory conflicts topic_codex_auth_boundary --json
crb memory hypotheses topic_codex_auth_boundary --json
crb graph export topic_codex_auth_boundary --format json --output graph.json
crb graph view topic_codex_auth_boundary --format html --output graph.html
```

```bash
crb ops health --json
crb ops audit run_2026_04_19_001 --json
crb ops replay run_2026_04_19_001 --reason "operator replay audit" --json
```

## UX Guarantees

- Mutating commands route through backend APIs and idempotency policy.
- CLI output is a read model or enqueue receipt, not authoritative storage.
- Human summaries expose uncertainty and active conflicts when present.
- Graph exports are visualization artifacts, not a source of truth.

The detailed contract is in `docs/cli-ux-contract.md`.
