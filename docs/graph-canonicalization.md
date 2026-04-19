# Phase 2 Graph Canonicalization

`DEE-21` adds the canonicalization boundary between runtime proposal output and any future graph persistence path.

## Scope

- `ProposalBundle` remains the runtime-facing proposal payload.
- `CanonicalGraphService` is the only Phase 2 entrypoint that turns a proposal into graph-shaped data.
- Invalid proposals are quarantined with explicit reasons instead of being silently repaired or partially written.

## Layer Model

- `world`: `Evidence`, `Claim`
- `epistemic`: `Hypothesis`
- `provenance`: `ProvenanceRecord`

This keeps source material, revisable belief state, and process lineage separate before Neo4j writes exist.

## Canonicalization Rules

- Evidence dedupes on `kind + source_url`.
- Claims dedupe on normalized `text + temporal_scope + canonical evidence lineage`.
- Claims without canonical provenance evidence are quarantined.
- Temporal scope must be explicit enough for contradiction-safe storage. Free-form placeholders such as `unknown` are rejected.
- Challenger hypotheses are linked to the current best hypothesis through a `CHALLENGES` edge.
- `RevisionAction.SUPERSEDE` creates a versioned hypothesis node and a `SUPERSEDES` edge to the stale predecessor.
- Support-only argument sets are flagged as `stagnation_risk_support_only` for later revision pressure.

## Neo4j Constraints

The Phase 2 schema contract currently emits these constraint statements:

- unique `id` constraints for `Evidence`, `Claim`, `Hypothesis`, `ProvenanceRecord`
- unique `key` constraints for `Evidence`, `Claim`, `Hypothesis`

These statements are exposed by `neo4j_constraints()` so later migration work can consume the exact same contract.

## Quarantine Boundary

The service returns `CanonicalizationResult.quarantine_reasons` when any of the following occur:

- malformed argument references
- missing provenance evidence references
- non-canonical temporal scope
- missing current-best linkage for challengers
- invalid supersession lineage

When quarantine reasons exist, the result is considered blocked for graph persistence.
