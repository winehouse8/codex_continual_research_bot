# Phase 0 Test Matrix

This matrix defines the minimum validation evidence for the canonical contract fixtures added in `DEE-16`.

| Test | Contract coverage | Failure guarded |
| --- | --- | --- |
| `test_canonical_fixture_parses` | `RunExecutionRequest`, `ProposalBundle`, `RuntimeEvent`, `SessionInspectResult`, `QueueJob` | Broken fixture shape or parser drift |
| `test_fixture_round_trip_matches_snapshot` | All canonical fixture files | Backward-incompatible contract drift |
| `test_rejects_unknown_enum_value` | Enum-backed fields across all primary fixtures | Unknown enum acceptance |
| `test_rejects_additional_properties` | Nested object trees across all primary fixtures | Silent `additionalProperties` acceptance |
| `test_rejects_missing_required_field` | All primary fixtures | Malformed fixture acceptance |
| `test_failure_taxonomy_matches_enum` | `FailureCode` taxonomy | Retry classification drift |

Validation intent:

- Canonical fixtures must be valid source-of-truth payloads for later phases.
- Parsers must fail closed on unknown enums and undeclared fields.
- Failure code taxonomy must remain aligned with the executable enum used by queue and runtime contracts.
