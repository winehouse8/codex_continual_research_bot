"""Phase 4 queue worker, retry policy, and dead-letter routing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final

from codex_continual_research_bot.contracts import (
    FailureCode,
    QueueJobKind,
    QueueJobState,
    RunIntent,
    RunMode,
)
from codex_continual_research_bot.orchestrator import (
    InvalidTopicSnapshotError,
    MalformedRunInputError,
    MissingTopicSnapshotError,
    RunOrchestrator,
    StaleTopicSnapshotError,
)
from codex_continual_research_bot.persistence import (
    DuplicateRunStartError,
    MalformedTopicSnapshotError,
    QueueMutationMismatchError,
    SQLitePersistenceLedger,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RetryPolicy:
    retryable: bool
    human_review_required: bool
    base_backoff_seconds: int = 0

    def next_available_at(self, *, attempts: int, now: datetime) -> datetime:
        if not self.retryable:
            return now
        multiplier = max(1, 2 ** max(0, attempts))
        return now + timedelta(seconds=self.base_backoff_seconds * multiplier)


RETRY_MATRIX: Final[dict[FailureCode, RetryPolicy]] = {
    FailureCode.RUNNER_HOST_UNAVAILABLE: RetryPolicy(
        retryable=True,
        human_review_required=False,
        base_backoff_seconds=60,
    ),
    FailureCode.REFRESH_FAILED: RetryPolicy(
        retryable=True,
        human_review_required=False,
        base_backoff_seconds=120,
    ),
    FailureCode.STALE_SESSION: RetryPolicy(
        retryable=True,
        human_review_required=False,
        base_backoff_seconds=300,
    ),
    FailureCode.OUTPUT_SCHEMA_VALIDATION_FAILED: RetryPolicy(
        retryable=True,
        human_review_required=False,
        base_backoff_seconds=90,
    ),
    FailureCode.AUTH_MATERIAL_MISSING: RetryPolicy(
        retryable=False,
        human_review_required=True,
    ),
    FailureCode.PRINCIPAL_MISMATCH: RetryPolicy(
        retryable=False,
        human_review_required=True,
    ),
    FailureCode.WORKSPACE_MISMATCH: RetryPolicy(
        retryable=False,
        human_review_required=True,
    ),
    FailureCode.MALFORMED_PROPOSAL: RetryPolicy(
        retryable=False,
        human_review_required=True,
    ),
    FailureCode.MALFORMED_CODEX_EVENT: RetryPolicy(
        retryable=False,
        human_review_required=True,
    ),
    FailureCode.CODEX_TRANSPORT_TIMEOUT: RetryPolicy(
        retryable=True,
        human_review_required=False,
        base_backoff_seconds=60,
    ),
    FailureCode.CODEX_PROCESS_CRASH: RetryPolicy(
        retryable=True,
        human_review_required=False,
        base_backoff_seconds=60,
    ),
    FailureCode.BUDGET_EXCEEDED: RetryPolicy(
        retryable=False,
        human_review_required=True,
    ),
    FailureCode.QUEUE_MUTATION_MISMATCH: RetryPolicy(
        retryable=False,
        human_review_required=True,
    ),
    FailureCode.DUPLICATE_QUEUE_DELIVERY: RetryPolicy(
        retryable=False,
        human_review_required=False,
    ),
    FailureCode.CONCURRENT_SESSION_MUTATION: RetryPolicy(
        retryable=True,
        human_review_required=False,
        base_backoff_seconds=30,
    ),
    FailureCode.CLI_LOGIN_PATH_BLOCKED: RetryPolicy(
        retryable=False,
        human_review_required=True,
    ),
}

SUPPORTED_WORKER_JOB_KINDS: Final[frozenset[QueueJobKind]] = frozenset(
    {
        QueueJobKind.RUN_EXECUTE,
        QueueJobKind.RUN_RESUME,
        QueueJobKind.USER_INPUT_PROCESS,
        QueueJobKind.TOPIC_REFRESH_SCHEDULE,
        QueueJobKind.GRAPH_REPAIR,
    }
)


class QueueWorkerError(RuntimeError):
    """Base error for Phase 4 worker failures."""

    def __init__(self, *, failure_code: FailureCode, detail: str) -> None:
        super().__init__(detail)
        self.failure_code = failure_code
        self.detail = detail


class RetryableQueueWorkerError(QueueWorkerError):
    """Raised by a handler when the queue item should be retried."""


class TerminalQueueWorkerError(QueueWorkerError):
    """Raised by a handler when the queue item should be dead-lettered."""


@dataclass(frozen=True)
class WorkerExecutionResult:
    queue_item_id: str
    run_id: str | None
    state: QueueJobState
    action: str
    failure_code: FailureCode | None = None


QueueJobHandler = Callable[[RunIntent], None]


class QueueWorker:
    """Consumes queued jobs and records ack/nack/dead-letter outcomes."""

    def __init__(
        self,
        ledger: SQLitePersistenceLedger,
        *,
        worker_id: str,
        orchestrator: RunOrchestrator | None = None,
    ) -> None:
        self._ledger = ledger
        self._worker_id = worker_id
        self._orchestrator = orchestrator or RunOrchestrator(ledger)

    def execute_next(
        self,
        *,
        handler: QueueJobHandler,
        now: datetime | None = None,
    ) -> WorkerExecutionResult | None:
        row = self._ledger.fetch_next_claimable_queue_item(now=now)
        if row is None:
            return None
        return self.execute_item(
            queue_item_id=row["id"],
            run_id=row["requested_run_id"],
            handler=handler,
            now=now,
        )

    def execute_item(
        self,
        *,
        queue_item_id: str,
        run_id: str,
        handler: QueueJobHandler,
        now: datetime | None = None,
    ) -> WorkerExecutionResult:
        current = self._ledger.fetch_queue_item(queue_item_id)
        if current is None:
            raise KeyError(f"queue item {queue_item_id} does not exist")

        current_state = QueueJobState(current["state"])
        if current_state in {QueueJobState.COMPLETED, QueueJobState.DEAD_LETTER}:
            return WorkerExecutionResult(
                queue_item_id=queue_item_id,
                run_id=self._run_id_for_queue_item(queue_item_id),
                state=current_state,
                action="duplicate_delivery_ignored",
            )
        if current_state == QueueJobState.CLAIMED:
            return WorkerExecutionResult(
                queue_item_id=queue_item_id,
                run_id=self._run_id_for_queue_item(queue_item_id),
                state=current_state,
                action="duplicate_delivery_in_progress",
                failure_code=FailureCode.DUPLICATE_QUEUE_DELIVERY,
            )

        try:
            kind = QueueJobKind(current["kind"])
            if kind not in SUPPORTED_WORKER_JOB_KINDS:
                raise TerminalQueueWorkerError(
                    failure_code=FailureCode.QUEUE_MUTATION_MISMATCH,
                    detail=f"unsupported queue job kind {current['kind']}",
                )

            intent = self._orchestrator.start_queued_run(
                queue_item_id=queue_item_id,
                run_id=run_id,
                worker_id=self._worker_id,
                mode=RunMode.SCHEDULED,
            )
            handler(intent)
            self._ledger.complete_queue_item(
                queue_item_id=queue_item_id,
                run_id=run_id,
                worker_id=self._worker_id,
            )
            return WorkerExecutionResult(
                queue_item_id=queue_item_id,
                run_id=run_id,
                state=QueueJobState.COMPLETED,
                action="completed",
            )
        except RetryableQueueWorkerError as exc:
            return self._nack(
                queue_item_id=queue_item_id,
                run_id=run_id,
                failure_code=exc.failure_code,
                detail=exc.detail,
                now=now,
            )
        except TerminalQueueWorkerError as exc:
            return self._dead_letter(
                queue_item_id=queue_item_id,
                run_id=run_id,
                failure_code=exc.failure_code,
                detail=exc.detail,
            )
        except (
            DuplicateRunStartError,
            InvalidTopicSnapshotError,
            MalformedRunInputError,
            MalformedTopicSnapshotError,
            MissingTopicSnapshotError,
            StaleTopicSnapshotError,
        ) as exc:
            return self._dead_letter(
                queue_item_id=queue_item_id,
                run_id=self._run_id_for_queue_item(queue_item_id),
                failure_code=FailureCode.QUEUE_MUTATION_MISMATCH,
                detail=str(exc),
            )
        except QueueMutationMismatchError:
            raise
        except Exception as exc:
            return self._nack(
                queue_item_id=queue_item_id,
                run_id=run_id,
                failure_code=FailureCode.RUNNER_HOST_UNAVAILABLE,
                detail=str(exc),
                now=now,
            )

    def _nack(
        self,
        *,
        queue_item_id: str,
        run_id: str,
        failure_code: FailureCode,
        detail: str,
        now: datetime | None,
    ) -> WorkerExecutionResult:
        current = self._ledger.fetch_queue_item(queue_item_id)
        if current is None:
            raise KeyError(f"queue item {queue_item_id} does not exist")

        policy = RETRY_MATRIX[failure_code]
        current_attempts = int(current["attempts"])
        next_attempt_count = current_attempts + 1
        if not policy.retryable or next_attempt_count >= int(current["max_attempts"]):
            return self._dead_letter(
                queue_item_id=queue_item_id,
                run_id=run_id,
                failure_code=failure_code,
                detail=detail,
            )

        current_time = now or _utcnow()
        self._ledger.record_queue_retry(
            queue_item_id=queue_item_id,
            run_id=run_id,
            worker_id=self._worker_id,
            failure_code=failure_code.value,
            detail=detail,
            next_available_at=policy.next_available_at(
                attempts=current_attempts,
                now=current_time,
            ),
        )
        return WorkerExecutionResult(
            queue_item_id=queue_item_id,
            run_id=run_id,
            state=QueueJobState.QUEUED,
            action="requeued",
            failure_code=failure_code,
        )

    def _dead_letter(
        self,
        *,
        queue_item_id: str,
        run_id: str | None,
        failure_code: FailureCode,
        detail: str,
    ) -> WorkerExecutionResult:
        policy = RETRY_MATRIX[failure_code]
        kwargs = {}
        current = self._ledger.fetch_queue_item(queue_item_id)
        if current is not None and QueueJobState(current["state"]) == QueueJobState.CLAIMED:
            kwargs = {"run_id": run_id, "worker_id": self._worker_id}
        self._ledger.record_queue_dead_letter(
            queue_item_id=queue_item_id,
            failure_code=failure_code.value,
            detail=detail,
            retryable=policy.retryable,
            human_review_required=policy.human_review_required,
            **kwargs,
        )
        return WorkerExecutionResult(
            queue_item_id=queue_item_id,
            run_id=run_id,
            state=QueueJobState.DEAD_LETTER,
            action="dead_lettered",
            failure_code=failure_code,
        )

    def _run_id_for_queue_item(self, queue_item_id: str) -> str | None:
        run = self._ledger.fetch_run_by_queue_item(queue_item_id)
        return None if run is None else str(run["id"])
