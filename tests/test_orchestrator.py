from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codex_continual_research_bot.contracts import (
    ProposalBundle,
    QueueJob,
    RunLifecycleState,
    RunMode,
    TopicSnapshot,
)
from codex_continual_research_bot.orchestrator import (
    CompetitionValidationError,
    InvalidRunTransitionError,
    InvalidTopicSnapshotError,
    MalformedRunInputError,
    MissingTopicSnapshotError,
    RunOrchestrator,
    RunStateMachine,
    STATE_TRANSITIONS,
    StaleTopicSnapshotError,
)
from codex_continual_research_bot.persistence import (
    DuplicateRunStartError,
    MalformedTopicSnapshotError,
    SQLitePersistenceLedger,
)

ROOT = Path(__file__).resolve().parent.parent


def make_queue_job(
    *,
    queue_item_id: str = "queue_001",
    idempotency_key: str = "run.execute:run_001:v1",
) -> QueueJob:
    return QueueJob.model_validate(
        {
            "queue_item_id": queue_item_id,
            "kind": "run.execute",
            "state": "queued",
            "topic_id": "topic_001",
            "requested_run_id": f"seed_{queue_item_id}",
            "dedupe_key": f"dedupe_{queue_item_id}",
            "idempotency_key": idempotency_key,
            "priority": 10,
            "attempts": 0,
            "max_attempts": 5,
            "available_at": "2026-04-19T00:00:00Z",
            "payload": {
                "initiator": "scheduler",
                "objective": "Attack the current best hypothesis and test a challenger.",
                "selected_queue_item_ids": [queue_item_id],
            },
            "last_failure": None,
        }
    )


def make_topic_snapshot(
    *,
    version: int = 1,
    challenger_target_id: str = "hyp_001",
) -> TopicSnapshot:
    return TopicSnapshot.model_validate(
        {
            "topic_id": "topic_001",
            "snapshot_version": version,
            "topic_summary": "Topic tracks whether scheduled runs preserve competition pressure.",
            "current_best_hypotheses": [
                {
                    "hypothesis_id": "hyp_001",
                    "title": "Fail closed on weak competition",
                    "summary": "Runs must not proceed without attacking the current best hypothesis.",
                }
            ],
            "challenger_targets": [
                {
                    "hypothesis_id": challenger_target_id,
                    "title": "Fail closed on weak competition",
                    "summary": "Use this current best hypothesis as the attack target.",
                }
            ],
            "active_conflicts": [
                {
                    "conflict_id": "conf_001",
                    "summary": "A simple evidence refresh may skip challenger generation.",
                }
            ],
            "open_questions": [
                "Can a scheduled run produce support and challenge evidence in one attempt?"
            ],
            "recent_provenance_digest": "sha256:topic-snapshot",
            "queued_user_inputs": [
                {
                    "user_input_id": "uin_001",
                    "input_type": "counterargument",
                    "summary": "A warning-only run path could preserve throughput.",
                    "submitted_at": "2026-04-19T10:30:00Z",
                }
            ],
        }
    )


def make_ledger(
    tmp_path: Path,
    *,
    with_snapshot: bool = True,
    queue_item_id: str = "queue_001",
    idempotency_key: str = "run.execute:run_001:v1",
    snapshot: TopicSnapshot | None = None,
) -> SQLitePersistenceLedger:
    ledger = SQLitePersistenceLedger(tmp_path / "phase3.sqlite3")
    ledger.initialize()
    ledger.create_topic(topic_id="topic_001", slug="phase-3", title="Phase 3")
    if with_snapshot:
        ledger.store_topic_snapshot(snapshot or make_topic_snapshot())
    ledger.reserve_idempotency_key(
        idempotency_key=idempotency_key,
        scope="run.execute",
        request_digest=f"sha256:{queue_item_id}",
    )
    ledger.enqueue_job(
        make_queue_job(
            queue_item_id=queue_item_id,
            idempotency_key=idempotency_key,
        )
    )
    return ledger


def proposal_data_with_current_best_challenge() -> dict[str, Any]:
    proposal_data = json.loads((ROOT / "fixtures" / "proposal_bundle.json").read_text())
    proposal_data["arguments"].append(
        {
            "argument_id": "arg_002",
            "stance": "challenge",
            "target_hypothesis_id": "hyp_001",
            "claim_ids": ["claim_001"],
            "rationale": "The run must pressure-test the current best hypothesis.",
        }
    )
    return proposal_data


def insert_raw_topic_snapshot(
    ledger: SQLitePersistenceLedger,
    *,
    snapshot_json: str,
    version: int = 1,
) -> None:
    with ledger.connect() as connection, connection:
        connection.execute(
            """
            INSERT INTO topic_snapshots(
                topic_id,
                snapshot_version,
                snapshot_json,
                created_at
            ) VALUES (?, ?, ?, ?)
            """,
            ("topic_001", version, snapshot_json, "2026-04-19T00:00:00+00:00"),
        )


def test_happy_path_state_transition_builds_runtime_intent(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)

    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.CODEX_EXECUTING.value
    assert run["snapshot_version"] == 1
    assert intent.lifecycle_state == RunLifecycleState.CODEX_EXECUTING
    assert intent.execution_request.plan.must_attack_current_best is True
    assert intent.execution_request.plan.must_generate_challenger is True
    assert intent.execution_request.plan.must_collect_support_and_challenge is True


def test_invalid_transition_rejected(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    claimed = ledger.claim_queue_item_for_run(
        queue_item_id="queue_001",
        worker_id="worker-a",
        run_id="run_001",
        mode=RunMode.SCHEDULED.value,
    )
    assert claimed is not None

    with pytest.raises(InvalidRunTransitionError):
        RunStateMachine(ledger).transition(
            run_id="run_001",
            to_state=RunLifecycleState.PLANNING,
        )


def test_documented_state_machine_matches_executable_transition_map() -> None:
    assert STATE_TRANSITIONS == {
        RunLifecycleState.QUEUED: frozenset({RunLifecycleState.LOADING_STATE}),
        RunLifecycleState.LOADING_STATE: frozenset(
            {RunLifecycleState.SELECTING_FRONTIER, RunLifecycleState.FAILED}
        ),
        RunLifecycleState.SELECTING_FRONTIER: frozenset(
            {RunLifecycleState.PLANNING, RunLifecycleState.FAILED}
        ),
        RunLifecycleState.PLANNING: frozenset(
            {RunLifecycleState.ATTACKING_CURRENT_BEST}
        ),
        RunLifecycleState.ATTACKING_CURRENT_BEST: frozenset(
            {RunLifecycleState.GENERATING_CHALLENGERS}
        ),
        RunLifecycleState.GENERATING_CHALLENGERS: frozenset(
            {RunLifecycleState.CODEX_EXECUTING}
        ),
        RunLifecycleState.CODEX_EXECUTING: frozenset(
            {RunLifecycleState.NORMALIZING, RunLifecycleState.FAILED}
        ),
        RunLifecycleState.NORMALIZING: frozenset(
            {RunLifecycleState.ADJUDICATING, RunLifecycleState.FAILED}
        ),
        RunLifecycleState.ADJUDICATING: frozenset(
            {
                RunLifecycleState.RETIRING_WEAK_HYPOTHESES,
                RunLifecycleState.PERSISTING,
                RunLifecycleState.FAILED,
            }
        ),
        RunLifecycleState.RETIRING_WEAK_HYPOTHESES: frozenset(
            {RunLifecycleState.PERSISTING, RunLifecycleState.FAILED}
        ),
        RunLifecycleState.PERSISTING: frozenset(
            {RunLifecycleState.SUMMARIZING, RunLifecycleState.FAILED}
        ),
        RunLifecycleState.SUMMARIZING: frozenset({RunLifecycleState.COMPLETED}),
        RunLifecycleState.FAILED: frozenset(
            {RunLifecycleState.QUEUED, RunLifecycleState.DEAD_LETTER}
        ),
        RunLifecycleState.COMPLETED: frozenset(),
        RunLifecycleState.DEAD_LETTER: frozenset(),
    }


def test_missing_topic_snapshot_fail_closed(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path, with_snapshot=False)
    orchestrator = RunOrchestrator(ledger)

    with pytest.raises(MissingTopicSnapshotError):
        orchestrator.start_queued_run(
            queue_item_id="queue_001",
            run_id="run_001",
            worker_id="worker-a",
        )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.FAILED.value


def test_malformed_topic_snapshot_json_fail_closed(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path, with_snapshot=False)
    insert_raw_topic_snapshot(ledger, snapshot_json="{")
    orchestrator = RunOrchestrator(ledger)

    with pytest.raises(MalformedTopicSnapshotError, match="malformed snapshot payload"):
        orchestrator.start_queued_run(
            queue_item_id="queue_001",
            run_id="run_001",
            worker_id="worker-a",
        )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.FAILED.value


def test_schema_invalid_topic_snapshot_fail_closed(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path, with_snapshot=False)
    insert_raw_topic_snapshot(ledger, snapshot_json='{"topic_id": "topic_001"}')
    orchestrator = RunOrchestrator(ledger)

    with pytest.raises(MalformedTopicSnapshotError, match="malformed snapshot payload"):
        orchestrator.start_queued_run(
            queue_item_id="queue_001",
            run_id="run_001",
            worker_id="worker-a",
        )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.FAILED.value


def test_topic_snapshot_payload_authority_mismatch_fail_closed(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path, with_snapshot=False)
    snapshot_data = make_topic_snapshot().model_dump(mode="json")
    snapshot_data["topic_id"] = "topic_999"
    insert_raw_topic_snapshot(ledger, snapshot_json=json.dumps(snapshot_data))
    orchestrator = RunOrchestrator(ledger)

    with pytest.raises(MalformedTopicSnapshotError, match="payload authority"):
        orchestrator.start_queued_run(
            queue_item_id="queue_001",
            run_id="run_001",
            worker_id="worker-a",
        )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.FAILED.value


def test_empty_current_best_snapshot_fail_closed_before_runtime(tmp_path: Path) -> None:
    snapshot = make_topic_snapshot().model_copy(update={"current_best_hypotheses": []})
    ledger = make_ledger(tmp_path, snapshot=snapshot)
    orchestrator = RunOrchestrator(ledger)

    with pytest.raises(InvalidTopicSnapshotError, match="no current-best hypothesis"):
        orchestrator.start_queued_run(
            queue_item_id="queue_001",
            run_id="run_001",
            worker_id="worker-a",
        )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.FAILED.value


def test_missing_queue_objective_fail_closed_after_snapshot_pin(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    with ledger.connect() as connection, connection:
        connection.execute(
            "UPDATE queue_items SET payload_json = ? WHERE id = ?",
            ("{}", "queue_001"),
        )
    orchestrator = RunOrchestrator(ledger)

    with pytest.raises(MalformedRunInputError, match="malformed QueuePayload"):
        orchestrator.start_queued_run(
            queue_item_id="queue_001",
            run_id="run_001",
            worker_id="worker-a",
        )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.FAILED.value
    assert run["snapshot_version"] == 1


def test_schema_invalid_queue_payload_fail_closed_after_snapshot_pin(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    with ledger.connect() as connection, connection:
        connection.execute(
            "UPDATE queue_items SET payload_json = ? WHERE id = ?",
            (
                json.dumps({"objective": "Missing canonical QueuePayload fields."}),
                "queue_001",
            ),
        )
    orchestrator = RunOrchestrator(ledger)

    with pytest.raises(MalformedRunInputError, match="malformed QueuePayload"):
        orchestrator.start_queued_run(
            queue_item_id="queue_001",
            run_id="run_001",
            worker_id="worker-a",
        )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.FAILED.value
    assert run["snapshot_version"] == 1


def test_queue_payload_selected_item_mismatch_fail_closed(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    with ledger.connect() as connection, connection:
        connection.execute(
            "UPDATE queue_items SET payload_json = ? WHERE id = ?",
            (
                json.dumps(
                    {
                        "initiator": "scheduler",
                        "objective": "Try to run a different queue item silently.",
                        "selected_queue_item_ids": ["queue_999"],
                    }
                ),
                "queue_001",
            ),
        )
    orchestrator = RunOrchestrator(ledger)

    with pytest.raises(MalformedRunInputError, match="must include itself"):
        orchestrator.start_queued_run(
            queue_item_id="queue_001",
            run_id="run_001",
            worker_id="worker-a",
        )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.FAILED.value
    assert run["snapshot_version"] == 1


def test_invalid_queue_kind_fail_closed_after_snapshot_pin(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    with ledger.connect() as connection, connection:
        connection.execute(
            "UPDATE queue_items SET kind = ? WHERE id = ?",
            ("run.mystery", "queue_001"),
        )
    orchestrator = RunOrchestrator(ledger)

    with pytest.raises(MalformedRunInputError, match="queue row fields"):
        orchestrator.start_queued_run(
            queue_item_id="queue_001",
            run_id="run_001",
            worker_id="worker-a",
        )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.FAILED.value
    assert run["snapshot_version"] == 1


def test_failed_run_cannot_resume_without_requeue(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path, with_snapshot=False)
    orchestrator = RunOrchestrator(ledger)
    with pytest.raises(MissingTopicSnapshotError):
        orchestrator.start_queued_run(
            queue_item_id="queue_001",
            run_id="run_001",
            worker_id="worker-a",
        )

    ledger.store_topic_snapshot(make_topic_snapshot())

    with pytest.raises(InvalidRunTransitionError, match="must be requeued"):
        orchestrator.resume_run(run_id="run_001")


def test_duplicate_run_start_is_idempotent(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)

    first = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    second = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )

    assert second.execution_request == first.execution_request
    assert second.lifecycle_state == RunLifecycleState.CODEX_EXECUTING


def test_duplicate_run_start_with_different_run_id_is_rejected(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)

    orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )

    with pytest.raises(DuplicateRunStartError, match="already linked to run_001"):
        orchestrator.start_queued_run(
            queue_item_id="queue_001",
            run_id="run_002",
            worker_id="worker-b",
        )

    assert ledger.fetch_run("run_002") is None
    idempotency_record = ledger.get_idempotency_record("run.execute:run_001:v1")
    assert idempotency_record is not None
    assert idempotency_record["run_id"] == "run_001"


def test_duplicate_start_in_loading_state_cannot_resume_without_snapshot_pin(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    claimed = ledger.claim_queue_item_for_run(
        queue_item_id="queue_001",
        worker_id="worker-a",
        run_id="run_001",
        mode=RunMode.SCHEDULED.value,
    )
    assert claimed is not None
    RunStateMachine(ledger).transition(
        run_id="run_001",
        to_state=RunLifecycleState.LOADING_STATE,
    )
    ledger.store_topic_snapshot(make_topic_snapshot(version=2))

    with pytest.raises(InvalidRunTransitionError, match="not ready for runtime resume"):
        RunOrchestrator(ledger).start_queued_run(
            queue_item_id="queue_001",
            run_id="run_001",
            worker_id="worker-b",
        )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.LOADING_STATE.value
    assert run["snapshot_version"] is None


def test_run_resume_from_persisted_state(tmp_path: Path) -> None:
    db_path = tmp_path / "phase3.sqlite3"
    ledger = SQLitePersistenceLedger(db_path)
    ledger.initialize()
    ledger.create_topic(topic_id="topic_001", slug="phase-3", title="Phase 3")
    ledger.store_topic_snapshot(make_topic_snapshot())
    ledger.reserve_idempotency_key(
        idempotency_key="run.execute:run_001:v1",
        scope="run.execute",
        request_digest="sha256:queue_001",
    )
    ledger.enqueue_job(make_queue_job())
    original = RunOrchestrator(ledger).start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    ledger.store_topic_snapshot(make_topic_snapshot(version=2))

    reopened = SQLitePersistenceLedger(db_path)
    reopened.initialize()
    resumed = RunOrchestrator(reopened).resume_run(run_id="run_001")

    assert resumed.lifecycle_state == RunLifecycleState.CODEX_EXECUTING
    assert resumed.snapshot_version == 1
    assert resumed.execution_request == original.execution_request


def test_run_resume_rejects_mismatched_expected_snapshot(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    RunOrchestrator(ledger).start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )

    with pytest.raises(StaleTopicSnapshotError, match="run uses 1"):
        RunOrchestrator(ledger).resume_run(
            run_id="run_001",
            expected_snapshot_version=2,
        )


def test_queue_item_to_run_intent_mapping(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    intent = RunOrchestrator(ledger).start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )

    selected = intent.execution_request.context_snapshot.selected_queue_items
    assert [item.queue_item_id for item in selected] == ["queue_001"]
    assert selected[0].kind.value == "run.execute"
    assert selected[0].summary == "Attack the current best hypothesis and test a challenger."
    assert intent.execution_request.idempotency_key == "run.execute:run_001:v1"


def test_stale_snapshot_version_mismatch_rejected(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    ledger.store_topic_snapshot(make_topic_snapshot(version=2))

    with pytest.raises(StaleTopicSnapshotError):
        RunOrchestrator(ledger).start_queued_run(
            queue_item_id="queue_001",
            run_id="run_001",
            worker_id="worker-a",
            expected_snapshot_version=1,
        )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.FAILED.value


def test_current_best_attack_omitted_proposal_rejected(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)
    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    proposal = ProposalBundle.model_validate(
        json.loads((ROOT / "fixtures" / "proposal_bundle.json").read_text())
    )

    with pytest.raises(CompetitionValidationError, match="challenge argument"):
        orchestrator.validate_proposal_for_competition(
            intent=intent,
            proposal=proposal,
        )


def test_invalid_competition_proposal_cannot_advance_to_normalizing(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)
    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    proposal = ProposalBundle.model_validate(
        json.loads((ROOT / "fixtures" / "proposal_bundle.json").read_text())
    )

    with pytest.raises(CompetitionValidationError, match="challenge argument"):
        orchestrator.accept_competition_proposal(
            intent=intent,
            proposal=proposal,
        )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.CODEX_EXECUTING.value


def test_challenger_target_attack_does_not_satisfy_current_best_gate(
    tmp_path: Path,
) -> None:
    snapshot = make_topic_snapshot(challenger_target_id="hyp_999")
    ledger = make_ledger(tmp_path, snapshot=snapshot)
    orchestrator = RunOrchestrator(ledger)
    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    proposal_data = json.loads((ROOT / "fixtures" / "proposal_bundle.json").read_text())
    proposal_data["arguments"] = [
        {
            "argument_id": "arg_001",
            "stance": "support",
            "target_hypothesis_id": "hyp_001",
            "claim_ids": ["claim_001"],
            "rationale": "The run supports the current best hypothesis.",
        },
        {
            "argument_id": "arg_002",
            "stance": "challenge",
            "target_hypothesis_id": "hyp_999",
            "claim_ids": ["claim_001"],
            "rationale": "The run challenges only a non-current-best target.",
        },
    ]
    proposal = ProposalBundle.model_validate(proposal_data)

    with pytest.raises(CompetitionValidationError, match="challenge argument"):
        orchestrator.validate_proposal_for_competition(
            intent=intent,
            proposal=proposal,
        )


def test_partial_current_best_coverage_rejected(tmp_path: Path) -> None:
    snapshot_data = make_topic_snapshot().model_dump(mode="json")
    snapshot_data["current_best_hypotheses"].append(
        {
            "hypothesis_id": "hyp_002",
            "title": "Second current best",
            "summary": "This current-best hypothesis also needs direct pressure.",
        }
    )
    ledger = make_ledger(tmp_path, snapshot=TopicSnapshot.model_validate(snapshot_data))
    orchestrator = RunOrchestrator(ledger)
    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    proposal_data = proposal_data_with_current_best_challenge()
    proposal_data["revision_proposals"][0]["action"] = "weaken"
    proposal = ProposalBundle.model_validate(proposal_data)

    with pytest.raises(CompetitionValidationError, match="each current best"):
        orchestrator.validate_proposal_for_competition(
            intent=intent,
            proposal=proposal,
        )


def test_challenger_generation_omitted_proposal_rejected(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)
    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    proposal_data = proposal_data_with_current_best_challenge()
    proposal_data["challenger_hypotheses"] = []
    proposal_data["revision_proposals"][0]["action"] = "weaken"
    proposal = ProposalBundle.model_validate(proposal_data)

    with pytest.raises(CompetitionValidationError, match="challenger hypothesis"):
        orchestrator.validate_proposal_for_competition(
            intent=intent,
            proposal=proposal,
        )


def test_support_argument_omitted_proposal_rejected(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)
    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    proposal_data = json.loads((ROOT / "fixtures" / "proposal_bundle.json").read_text())
    proposal_data["arguments"] = [
        {
            "argument_id": "arg_002",
            "stance": "challenge",
            "target_hypothesis_id": "hyp_001",
            "claim_ids": ["claim_001"],
            "rationale": "The run must pressure-test the current best hypothesis.",
        }
    ]
    proposal = ProposalBundle.model_validate(proposal_data)

    with pytest.raises(CompetitionValidationError, match="support and challenge"):
        orchestrator.validate_proposal_for_competition(
            intent=intent,
            proposal=proposal,
        )


def test_competition_argument_with_unknown_claim_rejected(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)
    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    proposal_data = proposal_data_with_current_best_challenge()
    proposal_data["arguments"][0]["claim_ids"] = ["claim_missing"]
    proposal_data["revision_proposals"][0]["action"] = "weaken"
    proposal = ProposalBundle.model_validate(proposal_data)

    with pytest.raises(CompetitionValidationError, match="declared claims"):
        orchestrator.validate_proposal_for_competition(
            intent=intent,
            proposal=proposal,
        )


def test_competition_claim_with_unknown_artifact_rejected(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)
    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    proposal_data = proposal_data_with_current_best_challenge()
    proposal_data["claims"][0]["artifact_ids"] = ["src_missing"]
    proposal_data["revision_proposals"][0]["action"] = "weaken"
    proposal = ProposalBundle.model_validate(proposal_data)

    with pytest.raises(CompetitionValidationError, match="declared evidence artifacts"):
        orchestrator.validate_proposal_for_competition(
            intent=intent,
            proposal=proposal,
        )


def test_reconciliation_or_retirement_pressure_omitted_proposal_rejected(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)
    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    proposal = ProposalBundle.model_validate(proposal_data_with_current_best_challenge())

    with pytest.raises(CompetitionValidationError, match="reconcile"):
        orchestrator.validate_proposal_for_competition(
            intent=intent,
            proposal=proposal,
        )


def test_unrelated_revision_pressure_rejected(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)
    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    proposal_data = proposal_data_with_current_best_challenge()
    proposal_data["revision_proposals"][0]["action"] = "weaken"
    proposal_data["revision_proposals"][0]["hypothesis_id"] = "hyp_999"
    proposal = ProposalBundle.model_validate(proposal_data)

    with pytest.raises(CompetitionValidationError, match="reconcile"):
        orchestrator.validate_proposal_for_competition(
            intent=intent,
            proposal=proposal,
        )


def test_complete_competition_proposal_is_accepted(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)
    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    proposal_data = proposal_data_with_current_best_challenge()
    proposal_data["revision_proposals"][0]["action"] = "weaken"
    proposal = ProposalBundle.model_validate(proposal_data)

    orchestrator.validate_proposal_for_competition(
        intent=intent,
        proposal=proposal,
    )


def test_valid_competition_proposal_advances_to_normalizing(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)
    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    proposal_data = proposal_data_with_current_best_challenge()
    proposal_data["revision_proposals"][0]["action"] = "weaken"
    proposal = ProposalBundle.model_validate(proposal_data)

    orchestrator.accept_competition_proposal(
        intent=intent,
        proposal=proposal,
    )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.NORMALIZING.value


def test_accepted_proposal_cannot_resume_runtime_execution(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)
    intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    proposal_data = proposal_data_with_current_best_challenge()
    proposal_data["revision_proposals"][0]["action"] = "weaken"
    proposal = ProposalBundle.model_validate(proposal_data)
    orchestrator.accept_competition_proposal(
        intent=intent,
        proposal=proposal,
    )

    with pytest.raises(InvalidRunTransitionError, match="normalizing"):
        orchestrator.resume_run(run_id="run_001")
    with pytest.raises(InvalidRunTransitionError, match="normalizing"):
        orchestrator.start_queued_run(
            queue_item_id="queue_001",
            run_id="run_001",
            worker_id="worker-b",
        )


def test_stale_intent_cannot_advance_requeued_run_from_new_snapshot(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    orchestrator = RunOrchestrator(ledger)
    stale_intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    RunStateMachine(ledger).transition(
        run_id="run_001",
        to_state=RunLifecycleState.FAILED,
    )
    RunStateMachine(ledger).transition(
        run_id="run_001",
        to_state=RunLifecycleState.QUEUED,
    )
    ledger.store_topic_snapshot(make_topic_snapshot(version=2))
    fresh_intent = orchestrator.start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-b",
    )
    proposal_data = proposal_data_with_current_best_challenge()
    proposal_data["revision_proposals"][0]["action"] = "weaken"
    proposal = ProposalBundle.model_validate(proposal_data)

    with pytest.raises(StaleTopicSnapshotError, match="intent uses 1"):
        orchestrator.accept_competition_proposal(
            intent=stale_intent,
            proposal=proposal,
        )

    run = ledger.fetch_run("run_001")
    assert run is not None
    assert run["status"] == RunLifecycleState.CODEX_EXECUTING.value
    assert fresh_intent.snapshot_version == 2
