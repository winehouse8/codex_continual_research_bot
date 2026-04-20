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
| `test_missing_current_best_snapshot_quarantines_challenger_linkage` | Challenger anchor snapshot integrity | Challenger edge points at a current-best node absent from the backend snapshot |
| `test_neo4j_schema_constraints_cover_phase2_labels` | Neo4j constraint contract | Schema drift between code and future graph migration |
| `test_mapping_spec_covers_world_epistemic_and_provenance_layers` | Canonical mapping spec | Node and edge mapping stops matching the layer model |

# Phase 3 Test Matrix

This matrix defines the minimum validation evidence for the topic snapshot read model and orchestrator state machine added in `DEE-25`.

| Test | Coverage | Failure guarded |
| --- | --- | --- |
| `test_happy_path_state_transition_builds_runtime_intent` | persisted run lifecycle through `codex_executing`; generated `RunExecutionRequest` competition plan | Runtime starts before backend fixes the competition loop requirements |
| `test_invalid_transition_rejected` | executable state transition map | Ad hoc state jumps bypass the documented lifecycle |
| `test_missing_topic_snapshot_fail_closed` | topic snapshot loader | Runtime starts from missing backend context |
| `test_duplicate_run_start_is_idempotent` | queue item claim and run intent rebuild | Duplicate worker delivery creates a second run or divergent request |
| `test_run_resume_from_persisted_state` | persisted run state and snapshot reload | Resume depends on in-memory orchestrator state |
| `test_queue_item_to_run_intent_mapping` | queue row to `FrontierSelectionInput` / `RunExecutionRequest` mapping | Queue objective or idempotency key is dropped from runtime intent |
| `test_stale_snapshot_version_mismatch_rejected` | snapshot version gate | Runtime persists decisions based on stale topic context |
| `test_current_best_attack_omitted_proposal_rejected` | minimum proposal competition gate | Support-only research output proceeds without attacking current best |
| `test_challenger_target_attack_does_not_satisfy_current_best_gate` | current-best attack target specificity | A challenge against a non-current-best target satisfies the current-best attack requirement |
| `test_challenger_generation_omitted_proposal_rejected` | challenger generation gate | Runtime output proceeds without producing a competing hypothesis |
| `test_support_argument_omitted_proposal_rejected` | support-plus-challenge evidence gate | Challenge-only output proceeds without support evidence for selected targets |
| `test_reconciliation_or_retirement_pressure_omitted_proposal_rejected` | reconciliation / retirement pressure gate | Runtime output proceeds without conflict reconciliation, escalation, or hypothesis weakening / retirement pressure |
| `test_complete_competition_proposal_is_accepted` | full proposal gate acceptance | Valid competition output is over-rejected by the Phase 3 gate |
