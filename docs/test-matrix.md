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
| `test_documented_state_machine_matches_executable_transition_map` | full Phase 3 lifecycle transition map | Diagram/code drift hides an undocumented transition path |
| `test_missing_topic_snapshot_fail_closed` | topic snapshot loader | Runtime starts from missing backend context |
| `test_malformed_topic_snapshot_json_fail_closed` | snapshot payload decoding | Invalid persisted snapshot JSON strands a claimed run |
| `test_schema_invalid_topic_snapshot_fail_closed` | snapshot contract validation | Schema-invalid snapshot payload reaches runtime execution |
| `test_topic_snapshot_payload_authority_mismatch_fail_closed` | topic snapshot row authority | Snapshot JSON can override the persisted `(topic_id, snapshot_version)` row key |
| `test_empty_current_best_snapshot_fail_closed_before_runtime` | current-best availability in topic snapshots | Runtime starts with no attackable current-best hypothesis |
| `test_missing_queue_objective_fail_closed_after_snapshot_pin` | queue payload validation during intent building | Malformed queue payload strands a run after snapshot pinning |
| `test_schema_invalid_queue_payload_fail_closed_after_snapshot_pin` | canonical `QueuePayload` validation during intent building | Ad-hoc queue JSON bypasses the Phase 0 queue contract |
| `test_queue_payload_selected_item_mismatch_fail_closed` | selected queue item authority | Queue payload points at a different item than the claimed run |
| `test_invalid_queue_kind_fail_closed_after_snapshot_pin` | persisted queue row validation during frontier selection | Invalid queue kind strands a run in `selecting_frontier` |
| `test_duplicate_run_start_is_idempotent` | queue item claim and run intent rebuild | Duplicate worker delivery creates a second run or divergent request |
| `test_duplicate_run_start_with_different_run_id_is_rejected` | queue item to run id authority | Duplicate delivery with a new run id creates a divergent run |
| `test_duplicate_start_in_loading_state_cannot_resume_without_snapshot_pin` | duplicate delivery during pre-runtime lifecycle states | Duplicate delivery builds an intent from an unpinned or drifted snapshot |
| `test_guarded_run_transition_rejects_stale_source_state` | persisted run transition compare-and-set | Concurrent workers overwrite a newer lifecycle state with a stale transition |
| `test_run_resume_from_persisted_state` | persisted run state and snapshot reload | Resume depends on in-memory orchestrator state |
| `test_queue_item_to_run_intent_mapping` | queue row to `FrontierSelectionInput` / `RunExecutionRequest` mapping | Queue objective or idempotency key is dropped from runtime intent |
| `test_stale_snapshot_version_mismatch_rejected` | snapshot version gate | Runtime persists decisions based on stale topic context |
| `test_current_best_attack_omitted_proposal_rejected` | minimum proposal competition gate | Support-only research output proceeds without attacking current best |
| `test_invalid_competition_proposal_cannot_advance_to_normalizing` | proposal gate before lifecycle advancement | Weak runtime output advances toward normalization or persistence |
| `test_challenger_target_attack_does_not_satisfy_current_best_gate` | current-best attack target specificity | A challenge against a non-current-best target satisfies the current-best attack requirement |
| `test_partial_current_best_coverage_rejected` | complete current-best stance coverage | One current-best hypothesis receives token pressure while another is ignored |
| `test_challenger_generation_omitted_proposal_rejected` | challenger generation gate | Runtime output proceeds without producing a competing hypothesis |
| `test_support_argument_omitted_proposal_rejected` | support-plus-challenge evidence gate | Challenge-only output proceeds without support evidence for selected targets |
| `test_competition_argument_with_unknown_claim_rejected` | support-plus-challenge evidence backing | Malformed argument references satisfy competition pressure without declared claims |
| `test_competition_claim_with_unknown_artifact_rejected` | support-plus-challenge provenance backing | Claims with undeclared evidence artifacts satisfy competition pressure |
| `test_reconciliation_or_retirement_pressure_omitted_proposal_rejected` | reconciliation / retirement pressure gate | Runtime output proceeds without conflict reconciliation, escalation, or hypothesis weakening / retirement pressure |
| `test_unrelated_revision_pressure_rejected` | snapshot-relevant revision pressure | Unrelated hypothesis revision satisfies the retirement-pressure gate |
| `test_complete_competition_proposal_is_accepted` | full proposal gate acceptance | Valid competition output is over-rejected by the Phase 3 gate |
| `test_valid_competition_proposal_advances_to_normalizing` | accepted proposal lifecycle advancement | Valid runtime output cannot proceed after satisfying the Phase 3 gate |
| `test_accepted_proposal_cannot_resume_runtime_execution` | runtime resume boundary after proposal acceptance | Accepted runtime output is re-executed after the run has already advanced to `normalizing` |
| `test_stale_intent_cannot_advance_requeued_run_from_new_snapshot` | proposal acceptance replay guard | A stale runtime intent can advance a requeued run pinned to a newer snapshot |

# Phase 7 Test Matrix

This matrix defines the minimum validation evidence for the output validator, repair loop, quarantine store, and compaction artifact boundary added in `DEE-49`.

| Test | Coverage | Failure guarded |
| --- | --- | --- |
| `test_happy_path_exec_ingestion_persists_events_and_artifacts` | validated proposal persistence artifacts | Valid runtime output is quarantined or misses validation evidence |
| `test_malformed_json_proposal_is_repaired_before_validation` | syntax validator and minimal repair prompt path | Malformed JSON reaches persistence or fails without a bounded repair attempt |
| `test_invalid_enum_and_missing_required_field_are_repaired` | schema validator and repair attempt orchestration | Enum drift or missing required fields bypass the repair loop |
| `test_unresolved_citation_placeholder_is_rejected_and_quarantined` | policy validator and quarantine artifact | Placeholder citations reach graph-write candidates |
| `test_hypothesis_id_inconsistency_is_rejected_and_quarantined` | semantic validator for hypothesis references | Proposal references a hypothesis outside the backend snapshot or challengers |
| `test_repair_budget_exhausted_quarantines_invalid_output` | repair budget handling | The runtime loops indefinitely or drops invalid output without quarantine |
| `test_compaction_artifact_preserves_referential_integrity` | `context.compacted` payload and retained artifact references | Compaction breaks evidence reference integrity |
| `test_tool_result_omitted_after_compaction_is_rejected` | compaction-aware semantic validation | Proposal uses tool/artifact output omitted from compacted context |
