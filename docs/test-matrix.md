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
