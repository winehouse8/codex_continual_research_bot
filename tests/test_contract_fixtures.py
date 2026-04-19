from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from codex_continual_research_bot.contracts import (
    FailureCode,
    ProposalBundle,
    QueueJob,
    RunExecutionRequest,
    RuntimeEvent,
    RuntimeEventType,
    SessionInspectResult,
    derive_principal_fingerprint,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"

MODEL_BY_FIXTURE = {
    "run_execution_request.json": RunExecutionRequest,
    "proposal_bundle.json": ProposalBundle,
    "runtime_event.json": RuntimeEvent,
    "session_inspect.json": SessionInspectResult,
    "queue_job.json": QueueJob,
}


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.mark.parametrize("fixture_name,model", MODEL_BY_FIXTURE.items())
def test_canonical_fixture_parses(fixture_name: str, model: type) -> None:
    payload = load_fixture(fixture_name)
    parsed = model.model_validate(payload)
    assert parsed.model_dump(mode="json") == payload


@pytest.mark.parametrize("fixture_name,model", MODEL_BY_FIXTURE.items())
def test_fixture_round_trip_matches_snapshot(fixture_name: str, model: type) -> None:
    fixture_path = FIXTURES_DIR / fixture_name
    payload = json.loads(fixture_path.read_text())
    parsed = model.model_validate(payload)
    assert json.dumps(parsed.model_dump(mode="json"), indent=2) + "\n" == fixture_path.read_text()


@pytest.mark.parametrize(
    ("fixture_name", "path", "invalid_value"),
    [
        ("run_execution_request.json", ("mode",), "batch"),
        ("proposal_bundle.json", ("revision_proposals", 0, "action"), "rewrite"),
        ("runtime_event.json", ("event_type",), "tool.finished"),
        ("session_inspect.json", ("state",), "healthy"),
        ("queue_job.json", ("kind",), "run.now"),
    ],
)
def test_rejects_unknown_enum_value(
    fixture_name: str,
    path: tuple[str | int, ...],
    invalid_value: Any,
) -> None:
    payload = load_fixture(fixture_name)
    cursor: Any = payload
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = invalid_value

    with pytest.raises(ValidationError):
        MODEL_BY_FIXTURE[fixture_name].model_validate(payload)


@pytest.mark.parametrize(
    ("fixture_name", "path", "key", "value"),
    [
        ("run_execution_request.json", (), "unexpected_root", True),
        ("proposal_bundle.json", ("execution_meta",), "extra_counter", 1),
        ("runtime_event.json", (), "unknown_top_level", "x"),
        ("session_inspect.json", ("checks",), "notes", "unexpected"),
        ("queue_job.json", ("payload",), "extra_field", "unexpected"),
    ],
)
def test_rejects_additional_properties(
    fixture_name: str,
    path: tuple[str | int, ...],
    key: str,
    value: Any,
) -> None:
    payload = load_fixture(fixture_name)
    cursor: Any = payload
    for step in path:
        cursor = cursor[step]
    cursor[key] = value

    with pytest.raises(ValidationError):
        MODEL_BY_FIXTURE[fixture_name].model_validate(payload)


@pytest.mark.parametrize(
    ("fixture_name", "path", "missing_key"),
    [
        ("run_execution_request.json", ("plan",), "must_generate_challenger"),
        ("proposal_bundle.json", ("evidence_candidates", 0), "source_url"),
        ("runtime_event.json", (), "payload"),
        ("session_inspect.json", ("account",), "email"),
        ("queue_job.json", ("payload",), "objective"),
    ],
)
def test_rejects_missing_required_field(
    fixture_name: str,
    path: tuple[str | int, ...],
    missing_key: str,
) -> None:
    payload = load_fixture(fixture_name)
    cursor: Any = payload
    for step in path:
        cursor = cursor[step]
    del cursor[missing_key]

    with pytest.raises(ValidationError):
        MODEL_BY_FIXTURE[fixture_name].model_validate(payload)


def test_session_inspect_fixture_fingerprint_is_derived_from_account_and_config() -> None:
    payload = load_fixture("session_inspect.json")
    expected = derive_principal_fingerprint(
        email=payload["account"]["email"],
        account_type=payload["account"]["type"],
        workspace_id=payload["config"]["forced_chatgpt_workspace_id"],
    )

    assert payload["principal_fingerprint"] == expected
    parsed = SessionInspectResult.model_validate(payload)
    assert parsed.principal_fingerprint == expected


def test_session_inspect_rejects_mismatched_fingerprint() -> None:
    payload = load_fixture("session_inspect.json")
    payload["principal_fingerprint"] = "researcher@example.com|chatgpt|ws_123456"

    with pytest.raises(ValidationError):
        SessionInspectResult.model_validate(payload)


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("run.started", {"objective": "Challenge the current best hypothesis.", "mode": "scheduled"}),
        ("tool.started", {"tool_call_id": "call_007", "tool_name": "web.search"}),
        (
            "tool.completed",
            {
                "tool_call_id": "call_008",
                "tool_name": "web.search",
                "result_digest": "sha256:2c7ed8e0a3de4977dd4c67d9390fe2e20ce95ccf0df238dab28c166a4630bf46",
            },
        ),
        ("output.validated", {"schema_id": "proposal_bundle.v1", "repair_attempts": 1}),
        (
            "run.completed",
            {
                "proposal_bundle_digest": "sha256:proposal-bundle",
                "summary_digest": "sha256:summary",
            },
        ),
        (
            "run.failed",
            {"failure_code": "output_schema_validation_failed", "detail": "final output did not match the schema"},
        ),
    ],
)
def test_runtime_event_accepts_only_declared_payload_shape(
    event_type: str,
    payload: dict[str, Any],
) -> None:
    event = {
        "run_id": "run_2026_04_19_001",
        "seq": 17,
        "event_type": event_type,
        "turn_index": 3,
        "timestamp": "2026-04-19T11:00:00Z",
        "payload": payload,
    }

    parsed = RuntimeEvent.model_validate(event)
    assert parsed.event_type == RuntimeEventType(event_type)


def test_runtime_event_rejects_payload_variant_mismatch() -> None:
    payload = load_fixture("runtime_event.json")
    payload["event_type"] = "run.failed"

    with pytest.raises(ValidationError):
        RuntimeEvent.model_validate(payload)


def test_runtime_event_rejects_nested_additional_properties() -> None:
    payload = load_fixture("runtime_event.json")
    payload["payload"]["unexpected_nested"] = "drift"

    with pytest.raises(ValidationError):
        RuntimeEvent.model_validate(payload)


def test_failure_taxonomy_matches_enum() -> None:
    taxonomy = json.loads((FIXTURES_DIR / "failure_taxonomy.json").read_text())
    codes = {entry["code"] for entry in taxonomy}
    assert codes == {code.value for code in FailureCode}
    assert all(isinstance(entry["retryable"], bool) for entry in taxonomy)
    assert all(isinstance(entry["human_review_required"], bool) for entry in taxonomy)
