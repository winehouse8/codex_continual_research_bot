# Phase 0 Test Matrix

This matrix defines the minimum validation evidence for the canonical contract fixtures added in `DEE-16`.

| Test | Contract coverage | Failure guarded |
| --- | --- | --- |
| `test_canonical_fixture_parses` | `RunExecutionRequest`, `ProposalBundle`, `RuntimeEvent`, `SessionInspectResult`, `QueueJob` | Broken fixture shape or parser drift |
| `test_fixture_round_trip_matches_snapshot` | All canonical fixture files | Backward-incompatible contract drift |
| `test_rejects_unknown_enum_value` | Enum-backed fields across all primary fixtures | Unknown enum acceptance |
| `test_rejects_additional_properties` | Nested object trees across all primary fixtures | Silent `additionalProperties` acceptance |
| `test_rejects_missing_required_field` | All primary fixtures | Malformed fixture acceptance |
| `test_session_inspect_fixture_fingerprint_is_derived_from_account_and_config` | `SessionInspectResult.principal_fingerprint` | Drift from the canonical fingerprint derivation rule |
| `test_session_inspect_rejects_mismatched_fingerprint` | `SessionInspectResult.principal_fingerprint` | Acceptance of non-canonical ledger fingerprints |
| `test_runtime_event_accepts_only_declared_payload_shape` | `RuntimeEvent.payload` variants | Free-form payload shapes bypassing the contract |
| `test_runtime_event_rejects_payload_variant_mismatch` | `RuntimeEvent.event_type` to payload binding | Mismatched event and payload acceptance |
| `test_runtime_event_rejects_nested_additional_properties` | Nested `RuntimeEvent.payload` models | Nested payload drift under representative runtime events |
| `test_failure_taxonomy_matches_enum` | `FailureCode` taxonomy | Retry classification drift |

Validation intent:

- Canonical fixtures must be valid source-of-truth payloads for later phases.
- Parsers must fail closed on unknown enums and undeclared fields.
- `principal_fingerprint` must be derived from `account + config`, not treated as an opaque free-form string.
- Representative runtime events must bind `event_type` to an explicit payload model rather than a generic dict.
- Failure code taxonomy must remain aligned with the executable enum used by queue and runtime contracts.

# Phase 2 Test Matrix

This matrix defines the minimum validation evidence for the graph canonicalization boundary added in `DEE-21`.

| Test | Coverage | Failure guarded |
| --- | --- | --- |
| `test_happy_path_canonicalization_builds_layered_graph` | World / epistemic / provenance node and edge generation | Proposal output bypasses canonical graph layering |
| `test_malformed_argument_reference_is_quarantined` | Argument-to-claim linkage validation | Malformed edge payload reaches canonical graph |
| `test_duplicate_evidence_and_claims_are_deduped` | Evidence and claim dedupe policy | Duplicate source material or claims accumulate as separate canonical nodes |
| `test_temporal_scope_missing_rejected` | Temporal scope normalization gate | Temporal ambiguity reaches contradiction handling |
| `test_stale_hypothesis_version_supersession_creates_new_version_edge` | Hypothesis versioning and supersession relation | Stale hypothesis replacement loses lineage |
| `test_missing_provenance_reference_is_quarantined` | Claim provenance enforcement | Provenance hole reaches canonical graph |
| `test_repeated_canonicalization_is_idempotent` | Repeat execution determinism | Same proposal produces different canonical graph shapes |
| `test_same_proposal_replay_is_order_independent` | Replay consistency under input ordering drift | Proposal replay changes canonical output |
| `test_support_only_repetition_gets_stagnation_flag` | Revision-pressure review flagging | Support-only repetition passes without stagnation signal |
| `test_challenger_links_to_existing_best_hypothesis` | Challenger competition linkage | Competition loop breaks because challenger is unanchored |
| `test_neo4j_schema_constraints_cover_phase2_labels` | Neo4j constraint contract | Schema drift between code and future graph migration |
| `test_mapping_spec_covers_world_epistemic_and_provenance_layers` | Canonical mapping spec | Node and edge mapping stops matching the layer model |
