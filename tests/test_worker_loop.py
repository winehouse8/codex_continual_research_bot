from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from codex_continual_research_bot.contracts import (
    BackendStateUpdateSummary,
    FailureCode,
    InteractiveRunStatus,
    QueueJob,
    QueueJobKind,
    QueueJobState,
    QueuePayload,
    RunReportViewModel,
    TopicSnapshot,
)
from codex_continual_research_bot.persistence import SQLitePersistenceLedger
from codex_continual_research_bot.worker_loop import (
    GraphChangeSummary,
    LoopExecutionResult,
    WorkerLoopPolicy,
    WorkerLoopService,
    WorkerLoopStopReason,
    YieldAnalyzer,
)


NOW = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)


def make_topic_snapshot(*, conflicts: bool = True) -> TopicSnapshot:
    return TopicSnapshot.model_validate(
        {
            "topic_id": "topic_001",
            "snapshot_version": 1,
            "topic_summary": "Topic tracks autonomous worker loop convergence.",
            "current_best_hypotheses": [
                {
                    "hypothesis_id": "hyp_001",
                    "title": "Worker loop must stop safely",
                    "summary": "Autonomous research should stop when yield disappears.",
                }
            ],
            "challenger_targets": [],
            "active_conflicts": (
                [
                    {
                        "conflict_id": "conf_001",
                        "summary": "Queue exhaustion is not convergence when conflict remains.",
                    }
                ]
                if conflicts
                else []
            ),
            "open_questions": ["What counts as a meaningful graph update?"],
            "recent_provenance_digest": "sha256:worker-loop-snapshot",
            "queued_user_inputs": [],
        }
    )


def make_job(index: int) -> QueueJob:
    queue_item_id = f"queue_{index:03d}"
    return QueueJob(
        queue_item_id=queue_item_id,
        kind=QueueJobKind.RUN_EXECUTE,
        state=QueueJobState.QUEUED,
        topic_id="topic_001",
        requested_run_id=f"run_{index:03d}",
        dedupe_key=f"dedupe_{index:03d}",
        idempotency_key=f"run.execute:queue_{index:03d}",
        priority=100 - index,
        attempts=0,
        max_attempts=3,
        available_at=NOW,
        payload=QueuePayload(
            initiator="test",
            objective=f"Execute worker loop fixture job {index}.",
            selected_queue_item_ids=[queue_item_id],
        ),
    )


def make_ledger(
    tmp_path: Path,
    *,
    job_count: int = 0,
    conflicts: bool = True,
) -> SQLitePersistenceLedger:
    ledger = SQLitePersistenceLedger(tmp_path / "worker-loop.sqlite3")
    ledger.initialize()
    ledger.create_topic(topic_id="topic_001", slug="worker-loop", title="Worker loop")
    ledger.store_topic_snapshot(make_topic_snapshot(conflicts=conflicts), created_at=NOW)
    for index in range(1, job_count + 1):
        job = make_job(index)
        ledger.reserve_idempotency_key(
            idempotency_key=job.idempotency_key,
            scope=job.kind.value,
            request_digest=f"sha256:{index}",
        )
        ledger.enqueue_job(job)
    return ledger


def report(
    *,
    run_id: str,
    queue_item_id: str,
    graph_digest: str,
    node_count: int,
    edge_count: int,
) -> RunReportViewModel:
    return RunReportViewModel(
        report_id=f"report:{run_id}",
        run_id=run_id,
        topic_id="topic_001",
        trigger_id=f"worker-loop:{queue_item_id}",
        idempotency_key=f"run.execute:{queue_item_id}",
        snapshot_version=1,
        status=InteractiveRunStatus.COMPLETED,
        summary="Worker loop fixture completed.",
        proposal_digest=f"sha256:proposal:{run_id}",
        backend_state_update=BackendStateUpdateSummary(
            graph_digest=graph_digest,
            node_count=node_count,
            edge_count=edge_count,
            review_flags=[],
        ),
        operator_failure_summary=None,
        created_at=NOW,
    )


@dataclass
class FakeLoopExecutor:
    ledger: SQLitePersistenceLedger
    outcomes: list[str]
    current_digest: str | None = None
    sequence: int = 0

    def execute_item(
        self,
        *,
        queue_item_id: str,
        run_id: str,
        worker_id: str,
    ) -> LoopExecutionResult:
        outcome = self.outcomes.pop(0)
        self.ledger.claim_queue_item_for_run(
            queue_item_id=queue_item_id,
            worker_id=worker_id,
            run_id=run_id,
            mode="scheduled",
            now=NOW,
        )
        self.sequence += 1
        if outcome == "malformed":
            self.ledger.record_queue_dead_letter(
                queue_item_id=queue_item_id,
                run_id=run_id,
                worker_id=worker_id,
                failure_code=FailureCode.MALFORMED_PROPOSAL.value,
                detail="proposal omitted challenger_hypotheses",
                retryable=False,
                human_review_required=True,
            )
            return LoopExecutionResult(
                queue_item_id=queue_item_id,
                run_id=run_id,
                queue_state=QueueJobState.DEAD_LETTER,
                failure_code=FailureCode.MALFORMED_PROPOSAL,
            )

        digest = (
            f"sha256:graph:{self.sequence}" if outcome == "yield" else self.current_digest
        )
        digest = digest or "sha256:graph:stable"
        self.current_digest = digest
        graph_payload = {"nodes": [{"id": digest}], "edges": []}
        with self.ledger.connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO canonical_graph_writes(
                    run_id, topic_id, proposal_id, graph_digest, node_count,
                    edge_count, graph_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "topic_001",
                    f"proposal:{run_id}",
                    digest,
                    1,
                    0,
                    json.dumps(graph_payload, sort_keys=True),
                    f"2026-04-22T12:00:0{self.sequence}+00:00",
                ),
            )
        self.ledger.complete_queue_item(
            queue_item_id=queue_item_id,
            run_id=run_id,
            worker_id=worker_id,
        )
        return LoopExecutionResult(
            queue_item_id=queue_item_id,
            run_id=run_id,
            queue_state=QueueJobState.COMPLETED,
            report=report(
                run_id=run_id,
                queue_item_id=queue_item_id,
                graph_digest=digest,
                node_count=1,
                edge_count=0,
            ),
        )


def test_yielded_iteration_when_graph_digest_changes() -> None:
    decision = YieldAnalyzer().analyze(
        before=GraphChangeSummary("sha256:before", 1, 1),
        after=GraphChangeSummary("sha256:after", 1, 1),
        result=LoopExecutionResult(
            queue_item_id="queue_001",
            run_id="run_001",
            queue_state=QueueJobState.COMPLETED,
            report=report(
                run_id="run_001",
                queue_item_id="queue_001",
                graph_digest="sha256:after",
                node_count=1,
                edge_count=1,
            ),
        ),
    )

    assert decision.yielded is True
    assert decision.last_meaningful_change == "sha256:after"


def test_no_yield_when_run_quarantines_or_graph_unchanged() -> None:
    unchanged = YieldAnalyzer().analyze(
        before=GraphChangeSummary("sha256:same", 1, 1),
        after=GraphChangeSummary("sha256:same", 1, 1),
        result=LoopExecutionResult(
            queue_item_id="queue_001",
            run_id="run_001",
            queue_state=QueueJobState.COMPLETED,
            report=report(
                run_id="run_001",
                queue_item_id="queue_001",
                graph_digest="sha256:same",
                node_count=1,
                edge_count=1,
            ),
        ),
    )
    quarantined = YieldAnalyzer().analyze(
        before=GraphChangeSummary("sha256:same", 1, 1),
        after=GraphChangeSummary("sha256:other", 2, 1),
        result=LoopExecutionResult(
            queue_item_id="queue_002",
            run_id="run_002",
            queue_state=QueueJobState.DEAD_LETTER,
            failure_code=FailureCode.MALFORMED_PROPOSAL,
        ),
    )

    assert unchanged.yielded is False
    assert quarantined.yielded is False


def test_convergence_policy_stops_at_max_consecutive_no_yield(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path, job_count=3)
    executor = FakeLoopExecutor(ledger, ["yield", "no_yield", "no_yield"])
    result = WorkerLoopService(ledger, executor=executor).run(
        topic_id="topic_001",
        policy=WorkerLoopPolicy(
            max_iterations=5,
            max_consecutive_no_yield=2,
            max_malformed_proposals=2,
        ),
        now=NOW,
    )

    assert result.stop_reason == WorkerLoopStopReason.MAX_CONSECUTIVE_NO_YIELD
    assert result.state == "converged"
    assert result.iteration_count == 3
    assert result.yielded_count == 1


def test_convergence_policy_stops_at_max_iterations(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path, job_count=3)
    executor = FakeLoopExecutor(ledger, ["yield", "yield", "yield"])
    result = WorkerLoopService(ledger, executor=executor).run(
        topic_id="topic_001",
        policy=WorkerLoopPolicy(max_iterations=2, max_consecutive_no_yield=5),
        now=NOW,
    )

    assert result.stop_reason == WorkerLoopStopReason.MAX_ITERATIONS
    assert result.iteration_count == 2


def test_convergence_policy_stops_at_budget_threshold(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path, job_count=1)
    executor = FakeLoopExecutor(ledger, ["yield"])
    result = WorkerLoopService(ledger, executor=executor).run(
        topic_id="topic_001",
        policy=WorkerLoopPolicy(max_runtime_seconds=0),
        now=NOW,
    )

    assert result.stop_reason == WorkerLoopStopReason.BUDGET_EXHAUSTED
    assert result.state == "blocked"
    assert result.iteration_count == 0


def test_repeated_malformed_proposal_stops_without_infinite_retry(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path, job_count=3)
    executor = FakeLoopExecutor(ledger, ["malformed", "malformed", "yield"])
    result = WorkerLoopService(ledger, executor=executor).run(
        topic_id="topic_001",
        policy=WorkerLoopPolicy(max_iterations=5, max_malformed_proposals=2),
        now=NOW,
    )

    assert result.stop_reason == WorkerLoopStopReason.REPEATED_MALFORMED_PROPOSAL
    assert result.state == "blocked"
    assert result.iteration_count == 2


def test_empty_queue_with_no_active_conflicts_stops_as_converged(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path, job_count=0, conflicts=False)
    result = WorkerLoopService(ledger).run(topic_id="topic_001", now=NOW)

    assert result.stop_reason == WorkerLoopStopReason.EMPTY_QUEUE_CONVERGED
    assert result.state == "converged"
    assert result.iteration_count == 0


def test_empty_queue_with_active_conflicts_pauses_without_claiming_convergence(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path, job_count=0, conflicts=True)
    result = WorkerLoopService(ledger).run(topic_id="topic_001", now=NOW)

    assert result.stop_reason == WorkerLoopStopReason.EMPTY_QUEUE_PAUSED_ACTIVE_CONFLICTS
    assert result.state == "blocked"
    assert result.iteration_count == 0


def test_worker_loop_can_restart_after_persisting_iteration_history(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path, job_count=1, conflicts=False)
    executor = FakeLoopExecutor(ledger, ["yield"])
    first = WorkerLoopService(ledger, executor=executor).run(
        topic_id="topic_001",
        policy=WorkerLoopPolicy(max_iterations=3),
        now=NOW,
    )

    second = WorkerLoopService(ledger).run(topic_id="topic_001", now=NOW)

    assert first.stop_reason == WorkerLoopStopReason.EMPTY_QUEUE_CONVERGED
    assert first.iteration_count == 1
    assert second.stop_reason == WorkerLoopStopReason.EMPTY_QUEUE_CONVERGED
    assert second.state == "converged"
    assert second.loop_id != first.loop_id
    assert len(ledger.list_worker_loop_iterations(loop_id=str(first.loop_id))) == 1


def test_single_active_worker_loop_lease_blocks_second_loop(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path, job_count=0)
    acquired = ledger.acquire_worker_loop(
        loop_id="loop_existing",
        topic_id="topic_001",
        worker_id="worker-a",
        lease_expires_at=datetime(2026, 4, 22, 12, 5, tzinfo=timezone.utc),
        now=NOW,
    )
    assert acquired is not None

    result = WorkerLoopService(ledger, worker_id="worker-b").run(
        topic_id="topic_001",
        now=NOW,
    )

    assert result.stop_reason == WorkerLoopStopReason.ACTIVE_LOOP_EXISTS
    assert result.state == "blocked"
