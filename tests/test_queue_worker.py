from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from codex_continual_research_bot.contracts import (
    FailureCode,
    QueueJob,
    QueueJobKind,
    QueueJobState,
    RunIntent,
    RunLifecycleState,
    TopicSnapshot,
)
from codex_continual_research_bot.persistence import (
    QueueMutationMismatchError,
    SQLitePersistenceLedger,
)
from codex_continual_research_bot.queue_worker import (
    QueueWorker,
    RetryableQueueWorkerError,
    TerminalQueueWorkerError,
)


def make_topic_snapshot() -> TopicSnapshot:
    return TopicSnapshot.model_validate(
        {
            "topic_id": "topic_001",
            "snapshot_version": 1,
            "topic_summary": "Topic tracks worker delivery and retry safety.",
            "current_best_hypotheses": [
                {
                    "hypothesis_id": "hyp_001",
                    "title": "Worker must preserve idempotency",
                    "summary": "Queue delivery must not duplicate downstream writes.",
                }
            ],
            "challenger_targets": [
                {
                    "hypothesis_id": "hyp_001",
                    "title": "Worker must preserve idempotency",
                    "summary": "The current best hypothesis remains the attack target.",
                }
            ],
            "active_conflicts": [
                {
                    "conflict_id": "conf_001",
                    "summary": "Retries can otherwise create duplicate writes.",
                }
            ],
            "open_questions": ["Can retry stay bounded and auditable?"],
            "recent_provenance_digest": "sha256:worker-snapshot",
            "queued_user_inputs": [
                {
                    "user_input_id": "uin_001",
                    "input_type": "counterargument",
                    "summary": "A duplicate delivery should be ignored after ack.",
                    "submitted_at": "2026-04-19T10:30:00Z",
                }
            ],
        }
    )


def make_queue_job(
    *,
    queue_item_id: str = "queue_001",
    kind: QueueJobKind = QueueJobKind.RUN_EXECUTE,
    priority: int = 10,
    attempts: int = 0,
    max_attempts: int = 5,
    available_at: str = "2026-04-19T00:00:00Z",
) -> QueueJob:
    return QueueJob.model_validate(
        {
            "queue_item_id": queue_item_id,
            "kind": kind.value,
            "state": "queued",
            "topic_id": "topic_001",
            "requested_run_id": f"run_{queue_item_id}",
            "dedupe_key": f"dedupe_{queue_item_id}",
            "idempotency_key": f"{kind.value}:{queue_item_id}:v1",
            "priority": priority,
            "attempts": attempts,
            "max_attempts": max_attempts,
            "available_at": available_at,
            "payload": {
                "initiator": "scheduler",
                "objective": f"Execute {kind.value} through the shared worker contract.",
                "selected_queue_item_ids": [queue_item_id],
            },
            "last_failure": None,
        }
    )


def make_ledger(tmp_path: Path, *jobs: QueueJob) -> SQLitePersistenceLedger:
    ledger = SQLitePersistenceLedger(tmp_path / "phase4.sqlite3")
    ledger.initialize()
    ledger.create_topic(topic_id="topic_001", slug="phase-4", title="Phase 4")
    ledger.store_topic_snapshot(make_topic_snapshot())
    for job in jobs or (make_queue_job(),):
        ledger.reserve_idempotency_key(
            idempotency_key=job.idempotency_key,
            scope=job.kind.value,
            request_digest=f"sha256:{job.queue_item_id}",
        )
        ledger.enqueue_job(job)
    return ledger


@pytest.mark.parametrize(
    "kind",
    [
        QueueJobKind.RUN_EXECUTE,
        QueueJobKind.RUN_RESUME,
        QueueJobKind.USER_INPUT_PROCESS,
        QueueJobKind.TOPIC_REFRESH_SCHEDULE,
        QueueJobKind.GRAPH_REPAIR,
    ],
)
def test_happy_path_worker_execution_acknowledges_supported_job_kinds(
    tmp_path: Path,
    kind: QueueJobKind,
) -> None:
    ledger = make_ledger(tmp_path, make_queue_job(kind=kind))
    seen: list[RunIntent] = []

    result = QueueWorker(ledger, worker_id="worker-a").execute_next(
        handler=seen.append,
        now=datetime(2026, 4, 19, tzinfo=timezone.utc),
    )

    assert result is not None
    assert result.state == QueueJobState.COMPLETED
    assert result.action == "completed"
    assert [intent.queue_item_id for intent in seen] == ["queue_001"]
    queue_row = ledger.fetch_queue_item("queue_001")
    assert queue_row is not None
    assert queue_row["state"] == QueueJobState.COMPLETED.value


def test_duplicate_queue_delivery_after_ack_is_idempotent(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    seen: list[RunIntent] = []
    worker = QueueWorker(ledger, worker_id="worker-a")
    first = worker.execute_item(
        queue_item_id="queue_001",
        run_id="run_queue_001",
        handler=seen.append,
    )

    duplicate = worker.execute_item(
        queue_item_id="queue_001",
        run_id="run_queue_001",
        handler=seen.append,
    )

    assert first.state == QueueJobState.COMPLETED
    assert duplicate.state == QueueJobState.COMPLETED
    assert duplicate.action == "duplicate_delivery_ignored"
    assert len(seen) == 1


def test_retryable_failure_requeues_with_backoff(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    now = datetime(2026, 4, 19, tzinfo=timezone.utc)

    def fail_retryable(_: RunIntent) -> None:
        raise RetryableQueueWorkerError(
            failure_code=FailureCode.RUNNER_HOST_UNAVAILABLE,
            detail="runner host did not accept the lease",
        )

    result = QueueWorker(ledger, worker_id="worker-a").execute_item(
        queue_item_id="queue_001",
        run_id="run_queue_001",
        handler=fail_retryable,
        now=now,
    )

    queue_row = ledger.fetch_queue_item("queue_001")
    assert queue_row is not None
    assert result.state == QueueJobState.QUEUED
    assert result.action == "requeued"
    assert queue_row["attempts"] == 1
    assert queue_row["state"] == QueueJobState.QUEUED.value
    assert queue_row["last_failure_code"] == FailureCode.RUNNER_HOST_UNAVAILABLE.value
    assert queue_row["available_at"] > now.isoformat()


def test_terminal_failure_routes_to_dead_letter_queue(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)

    def fail_terminal(_: RunIntent) -> None:
        raise TerminalQueueWorkerError(
            failure_code=FailureCode.MALFORMED_PROPOSAL,
            detail="proposal omitted required provenance",
        )

    result = QueueWorker(ledger, worker_id="worker-a").execute_item(
        queue_item_id="queue_001",
        run_id="run_queue_001",
        handler=fail_terminal,
    )

    queue_row = ledger.fetch_queue_item("queue_001")
    run_row = ledger.fetch_run("run_queue_001")
    dead_letters = ledger.list_dead_letter_queue()
    assert queue_row is not None
    assert run_row is not None
    assert result.state == QueueJobState.DEAD_LETTER
    assert queue_row["state"] == QueueJobState.DEAD_LETTER.value
    assert queue_row["last_failure_code"] == FailureCode.MALFORMED_PROPOSAL.value
    assert queue_row["last_failure_human_review"] == 1
    assert run_row["status"] == RunLifecycleState.DEAD_LETTER.value
    assert [row["id"] for row in dead_letters] == ["queue_001"]


def test_retryable_failure_dead_letters_after_max_attempts(tmp_path: Path) -> None:
    ledger = make_ledger(
        tmp_path,
        make_queue_job(attempts=1, max_attempts=2),
    )

    def fail_retryable(_: RunIntent) -> None:
        raise RetryableQueueWorkerError(
            failure_code=FailureCode.OUTPUT_SCHEMA_VALIDATION_FAILED,
            detail="repair budget exhausted",
        )

    result = QueueWorker(ledger, worker_id="worker-a").execute_item(
        queue_item_id="queue_001",
        run_id="run_queue_001",
        handler=fail_retryable,
    )

    queue_row = ledger.fetch_queue_item("queue_001")
    assert queue_row is not None
    assert result.state == QueueJobState.DEAD_LETTER
    assert queue_row["state"] == QueueJobState.DEAD_LETTER.value
    assert queue_row["attempts"] == 2
    assert queue_row["last_failure_retryable"] == 1


def test_queue_mutation_mismatch_rejects_wrong_worker_ack(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    claimed = ledger.claim_queue_item_for_run(
        queue_item_id="queue_001",
        worker_id="worker-a",
        run_id="run_queue_001",
        mode="scheduled",
    )
    assert claimed is not None

    with pytest.raises(QueueMutationMismatchError, match="claimed by worker-a"):
        ledger.complete_queue_item(
            queue_item_id="queue_001",
            run_id="run_queue_001",
            worker_id="worker-b",
        )

    queue_row = ledger.fetch_queue_item("queue_001")
    assert queue_row is not None
    assert queue_row["state"] == QueueJobState.CLAIMED.value
    assert queue_row["claimed_by"] == "worker-a"


def test_backlog_prioritization_claims_highest_priority_first(tmp_path: Path) -> None:
    ledger = make_ledger(
        tmp_path,
        make_queue_job(queue_item_id="queue_low", priority=10),
        make_queue_job(queue_item_id="queue_high", priority=100),
    )
    seen: list[str] = []

    result = QueueWorker(ledger, worker_id="worker-a").execute_next(
        handler=lambda intent: seen.append(str(intent.queue_item_id)),
        now=datetime(2026, 4, 19, tzinfo=timezone.utc),
    )

    assert result is not None
    assert result.queue_item_id == "queue_high"
    assert seen == ["queue_high"]
