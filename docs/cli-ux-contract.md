# CLI UX Contract

Phase 12 fixes the user-facing command contract before the Phase 13 CLI is
implemented. The contract is represented by strict fixtures and Pydantic models,
not by executable command handlers.

## Command Taxonomy

The CLI namespace is:

- `topic`: create, list, and inspect backend-owned topic state.
- `run`: enqueue, resume, and inspect research runs.
- `queue`: inspect and request backend-mediated queue actions.
- `memory`: inspect backend-owned hypothesis, evidence, conflict, and provenance
  memory.
- `graph`: export visualization artifacts derived from backend graph state.

The canonical command spec is `fixtures/cli_command_spec.json` and parses as
`CliCommandSpec`.

## Authority Boundary

Every mutating command is an API request to the backend. The CLI must not write
topic rows, run rows, queue attempts, graph nodes, Graphiti memory, provenance,
or revision decisions directly. Runtime outputs remain proposals until backend
validation, canonicalization, adjudication, and persistence accept them.

Read commands return view models. They are intentionally not persistence
contracts.

## User-Facing Terms

- Current best hypothesis: the backend's present best explanation; never call it
  a fact or truth.
- Challenger target: a hypothesis or conflict selected for attack, alternative
  generation, or adversarial verification.
- Active conflict: unresolved tension that must remain visible in summaries.
- Memory: backend-owned hypothesis/evidence/conflict/provenance state, not Codex
  session context.

## Output Shapes

Human summaries must include:

- current best hypotheses
- challenger targets
- active conflicts
- uncertainty
- backend state update status when a run is shown
- queue or next-action state
- graph visualization disclaimer when graph or memory state is shown

JSON output uses `fixtures/ux_read_models.json`, which parses as
`UXReadModelBundle`.

## Graph Export Contract

`fixtures/graph_export.json` is a visualization artifact contract, parsed as
`GraphExportArtifact`. It must include an authority notice saying that the export
is not a source of truth and that backend graph/provenance ledgers remain
authoritative.
