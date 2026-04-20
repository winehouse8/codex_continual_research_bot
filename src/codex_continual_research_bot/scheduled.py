"""Phase 10 scheduled run path with auth preflight and lease gating."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from typing import Protocol

from codex_continual_research_bot.contracts import (
    BackendStateUpdateSummary,
    FailureCode,
    InteractiveRunStatus,
    OperatorFailureSummary,
    QueueJob,
    QueueJobKind,
    QueueJobState,
    RunIntent,
    RunLifecycleState,
    RunMode,
    RunReportViewModel,
)
from codex_continual_research_bot.graph_canonicalization import (
    CanonicalGraphService,
    CanonicalizationContext,
    HypothesisSnapshot,
)
from codex_continual_research_bot.orchestrator import (
    CompetitionValidationError,
    InvalidTopicSnapshotError,
    InvalidRunTransitionError,
    MalformedRunInputError,
    MissingTopicSnapshotError,
    RunOrchestrator,
    RunStateMachine,
    StaleTopicSnapshotError,
)
from codex_continual_research_bot.persistence import (
    DuplicateRunStartError,
    MalformedTopicSnapshotError,
    SessionLeaseRecord,
    SQLitePersistenceLedger,
)
from codex_continual_research_bot.queue_worker import RETRY_MATRIX
from codex_continual_research_bot.runtime import CodexRuntimeError, RuntimeExecutionResult
from codex_continual_research_bot.scheduler import (
    SchedulerPolicyEvaluator,
    SchedulerSelection,
    TopicScheduleCandidate,
)
from codex_continual_research_bot.session_healthcheck_job import (
    SessionHealthcheckJob,
    SessionInspectionLoader,
)
from codex_continual_research_bot.session_lease_store import SessionLeaseConflictError
from codex_continual_research_bot.session_manager import SessionManager, SessionPolicyError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(data: object) -> str:
    return f"sha256:{sha256(_canonical_json(data).encode('utf-8')).hexdigest()}"


class ScheduledRunAction(str, Enum):
    ENQUEUED = "enqueued"
    DEFERRED = "deferred"
    COMPLETED = "completed"
    RETRYABLE_FAILED = "retryable_failed"
    TERMINAL_FAILED = "terminal_failed"
    DUPLICATE_IGNORED = "duplicate_ignored"


@dataclass(frozen=True)
class ScheduledRunPolicy:
    trusted_host_ids: frozenset[str]
    max_consecutive_stagnant_runs: int = 3
    lease_ttl_seconds: int = 900
    max_attempts: int = 3
    base_priority: int = 50


@dataclass(frozen=True)
class ScheduledEnqueueDecision:
    topic_id: str
    action: ScheduledRunAction
    reason: str
    selection: SchedulerSelection | None = None
    queue_item_id: str | None = None
    run_id: str | None = None
    failure_code: FailureCode | None = None


@dataclass(frozen=True)
class ScheduledRunExecutionResult:
    queue_item_id: str
    run_id: str | None
    action: ScheduledRunAction
    queue_state: QueueJobState
    failure_code: FailureCode | None = None
    report: RunReportViewModel | None = None


@dataclass(frozen=True)
class ScheduledOperatorNotification:
    topic_id: str
    queue_item_id: str
    run_id: str | None
    action: ScheduledRunAction
    failure_code: FailureCode | None
    detail: str
    retryable: bool
    human_review_required: bool
    created_at: datetime


class ScheduledRuntime(Protocol):
    def execute(
        self,
        intent: RunIntent,
        lease: SessionLeaseRecord,
    ) -> RuntimeExecutionResult:
        """Execute a scheduled run under an acquired session lease."""


class OperatorNotifier(Protocol):
    def notify(self, notification: ScheduledOperatorNotification) -> None:
        """Emit an operator-visible scheduled run notification."""


class ScheduledRunService:
    """Enqueues and executes trusted scheduled runs with fail-closed preflight."""

    def __init__(
        self,
        ledger: SQLitePersistenceLedger,
        *,
        session_manager: SessionManager,
        runtime: ScheduledRuntime,
        session_id: str,
        host_id: str,
        worker_id: str = "scheduled-worker",
        policy: ScheduledRunPolicy | None = None,
        scheduler: SchedulerPolicyEvaluator | None = None,
        healthcheck: SessionHealthcheckJob | None = None,
        canonicalizer: CanonicalGraphService | None = None,
        notifier: OperatorNotifier | None = None,
        orchestrator: RunOrchestrator | None = None,
    ) -> None:
        self._ledger = ledger
        self._session_manager = session_manager
        self._runtime = runtime
        self._session_id = session_id
        self._host_id = host_id
        self._worker_id = worker_id
        self._policy = policy or ScheduledRunPolicy(trusted_host_ids=frozenset({host_id}))
        self._scheduler = scheduler or SchedulerPolicyEvaluator()
        self._healthcheck = healthcheck or SessionHealthcheckJob(session_manager)
        self._canonicalizer = canonicalizer or CanonicalGraphService()
        self._notifier = notifier
        self._orchestrator = orchestrator or RunOrchestrator(ledger)
        self._state_machine = RunStateMachine(ledger)

    def enqueue_due_runs(
        self,
        candidates: list[TopicScheduleCandidate],
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[ScheduledEnqueueDecision]:
        current_time = now or _utcnow()
        selections = self._scheduler.select_refresh_topics(
            candidates,
            now=current_time,
            limit=limit,
        )
        candidates_by_topic = {candidate.topic_id: candidate for candidate in candidates}
        decisions: list[ScheduledEnqueueDecision] = []
        for selection in selections:
            candidate = candidates_by_topic[selection.topic_id]
            if (
                candidate.consecutive_stagnant_runs
                >= self._policy.max_consecutive_stagnant_runs
            ):
                decisions.append(
                    ScheduledEnqueueDecision(
                        topic_id=selection.topic_id,
                        action=ScheduledRunAction.DEFERRED,
                        reason="stagnation threshold reached before enqueue",
                        selection=selection,
                        failure_code=FailureCode.EXECUTION_POLICY_REJECTED,
                    )
                )
                continue
            decisions.append(self._enqueue_selection(selection, now=current_time))
        return decisions

    def execute_next(
        self,
        *,
        load_inspection: SessionInspectionLoader,
        now: datetime | None = None,
    ) -> ScheduledRunExecutionResult | None:
        row = self._ledger.fetch_next_claimable_queue_item(now=now)
        if row is None:
            return None
        return self.execute_item(
            queue_item_id=str(row["id"]),
            run_id=str(row["requested_run_id"]),
            load_inspection=load_inspection,
            now=now,
        )

    def execute_item(
        self,
        *,
        queue_item_id: str,
        run_id: str,
        load_inspection: SessionInspectionLoader,
        now: datetime | None = None,
    ) -> ScheduledRunExecutionResult:
        current = self._require_queue_item(queue_item_id)
        state = QueueJobState(current["state"])
        if state == QueueJobState.COMPLETED:
            return ScheduledRunExecutionResult(
                queue_item_id=queue_item_id,
                run_id=self._run_id_for_queue_item(queue_item_id),
                action=ScheduledRunAction.DUPLICATE_IGNORED,
                queue_state=state,
            )
        if state in {QueueJobState.DEAD_LETTER, QueueJobState.CLAIMED}:
            return ScheduledRunExecutionResult(
                queue_item_id=queue_item_id,
                run_id=self._run_id_for_queue_item(queue_item_id),
                action=ScheduledRunAction.DEFERRED,
                queue_state=state,
                failure_code=(
                    FailureCode.DUPLICATE_QUEUE_DELIVERY
                    if state == QueueJobState.CLAIMED
                    else None
                ),
            )

        trusted_failure = self._trusted_runner_failure(
            queue_item_id=queue_item_id,
            run_id=None,
            topic_id=str(current["topic_id"]),
            now=now,
        )
        if trusted_failure is not None:
            return trusted_failure

        preflight_failure = self._preflight_failure(
            queue_item_id=queue_item_id,
            run_id=None,
            topic_id=str(current["topic_id"]),
            load_inspection=load_inspection,
            now=now,
        )
        if preflight_failure is not None:
            return preflight_failure

        lease = self._acquire_lease(
            queue_item_id=queue_item_id,
            run_id=run_id,
            topic_id=str(current["topic_id"]),
            now=now,
        )
        if isinstance(lease, ScheduledRunExecutionResult):
            return lease

        intent: RunIntent | None = None
        try:
            intent = self._orchestrator.start_queued_run(
                queue_item_id=queue_item_id,
                run_id=run_id,
                worker_id=self._worker_id,
                mode=RunMode.SCHEDULED,
            )
            runtime_result = self._runtime.execute(intent, lease)
            self._orchestrator.accept_competition_proposal(
                intent=intent,
                proposal=runtime_result.proposal,
            )
            report = self._canonicalize_and_persist(
                intent=intent,
                runtime_result=runtime_result,
            )
            if report.status != InteractiveRunStatus.COMPLETED:
                return ScheduledRunExecutionResult(
                    queue_item_id=queue_item_id,
                    run_id=run_id,
                    action=ScheduledRunAction.TERMINAL_FAILED,
                    queue_state=QueueJobState.DEAD_LETTER,
                    failure_code=FailureCode.MALFORMED_PROPOSAL,
                    report=report,
                )
            self._ledger.complete_queue_item(
                queue_item_id=queue_item_id,
                run_id=run_id,
                worker_id=self._worker_id,
            )
            return ScheduledRunExecutionResult(
                queue_item_id=queue_item_id,
                run_id=run_id,
                action=ScheduledRunAction.COMPLETED,
                queue_state=QueueJobState.COMPLETED,
                report=report,
            )
        except CodexRuntimeError as exc:
            return self._handle_runtime_failure(
                queue_item_id=queue_item_id,
                run_id=run_id,
                topic_id=str(current["topic_id"]),
                intent=intent,
                failure_code=exc.failure_code,
                detail=exc.detail,
                retryable=exc.retryable,
                now=now,
            )
        except (CompetitionValidationError, InvalidRunTransitionError) as exc:
            return self._handle_runtime_failure(
                queue_item_id=queue_item_id,
                run_id=run_id,
                topic_id=str(current["topic_id"]),
                intent=intent,
                failure_code=FailureCode.MALFORMED_PROPOSAL,
                detail=str(exc),
                retryable=False,
                now=now,
            )
        except (
            DuplicateRunStartError,
            InvalidTopicSnapshotError,
            MalformedRunInputError,
            MalformedTopicSnapshotError,
            MissingTopicSnapshotError,
            StaleTopicSnapshotError,
        ) as exc:
            return self._handle_runtime_failure(
                queue_item_id=queue_item_id,
                run_id=run_id,
                topic_id=str(current["topic_id"]),
                intent=intent,
                failure_code=FailureCode.QUEUE_MUTATION_MISMATCH,
                detail=str(exc),
                retryable=False,
                now=now,
            )
        finally:
            self._session_manager.release_execution_lease(
                session_id=lease.session_id,
                lease_id=lease.lease_id,
            )

    def _enqueue_selection(
        self,
        selection: SchedulerSelection,
        *,
        now: datetime,
    ) -> ScheduledEnqueueDecision:
        stable = sha256(
            f"{selection.topic_id}|{now.isoformat()}|{','.join(selection.reasons)}".encode(
                "utf-8"
            )
        ).hexdigest()[:16]
        queue_item_id = f"scheduled_queue_{stable}"
        run_id = f"scheduled_run_{stable}"
        idempotency_key = (
            f"scheduled.run:{selection.topic_id}:"
            f"{now.strftime('%Y%m%dT%H%M%SZ')}:v1"
        )
        objective = (
            "Scheduled competition refresh for "
            f"{selection.topic_id}: {', '.join(selection.reasons)}"
        )
        self._ledger.reserve_idempotency_key(
            idempotency_key=idempotency_key,
            scope=QueueJobKind.RUN_EXECUTE.value,
            request_digest=_digest(
                {
                    "topic_id": selection.topic_id,
                    "score": selection.score,
                    "reasons": selection.reasons,
                    "scheduled_at": now.isoformat(),
                }
            ),
        )
        self._ledger.enqueue_job(
            QueueJob(
                queue_item_id=queue_item_id,
                kind=QueueJobKind.RUN_EXECUTE,
                state=QueueJobState.QUEUED,
                topic_id=selection.topic_id,
                requested_run_id=run_id,
                dedupe_key=f"scheduled:{selection.topic_id}:{now.isoformat()}",
                idempotency_key=idempotency_key,
                priority=min(100, self._policy.base_priority + int(selection.score)),
                attempts=0,
                max_attempts=self._policy.max_attempts,
                available_at=now,
                payload={
                    "initiator": "scheduler",
                    "objective": objective,
                    "selected_queue_item_ids": [queue_item_id],
                },
                last_failure=None,
            )
        )
        return ScheduledEnqueueDecision(
            topic_id=selection.topic_id,
            action=ScheduledRunAction.ENQUEUED,
            reason=objective,
            selection=selection,
            queue_item_id=queue_item_id,
            run_id=run_id,
        )

    def _trusted_runner_failure(
        self,
        *,
        queue_item_id: str,
        run_id: str | None,
        topic_id: str,
        now: datetime | None,
    ) -> ScheduledRunExecutionResult | None:
        if self._host_id in self._policy.trusted_host_ids:
            return None
        detail = f"scheduled host {self._host_id} is not trusted for headless execution"
        return self._record_preclaim_failure(
            queue_item_id=queue_item_id,
            run_id=run_id,
            topic_id=topic_id,
            failure_code=FailureCode.EXECUTION_POLICY_REJECTED,
            detail=detail,
            retryable=False,
            human_review_required=True,
            now=now,
        )

    def _preflight_failure(
        self,
        *,
        queue_item_id: str,
        run_id: str | None,
        topic_id: str,
        load_inspection: SessionInspectionLoader,
        now: datetime | None,
    ) -> ScheduledRunExecutionResult | None:
        inspection = load_inspection()
        if inspection.session_id != self._session_id:
            return self._record_preclaim_failure(
                queue_item_id=queue_item_id,
                run_id=run_id,
                topic_id=topic_id,
                failure_code=FailureCode.PRINCIPAL_MISMATCH,
                detail="scheduled preflight returned a different session_id",
                retryable=False,
                human_review_required=True,
                now=now,
            )
        if inspection.host_id != self._host_id:
            return self._record_preclaim_failure(
                queue_item_id=queue_item_id,
                run_id=run_id,
                topic_id=topic_id,
                failure_code=FailureCode.RUNNER_HOST_UNAVAILABLE,
                detail="scheduled preflight host_id does not match worker host",
                retryable=True,
                human_review_required=False,
                now=now,
            )
        result = self._healthcheck.run(lambda: inspection)
        if result.leaseable:
            return None
        return self._record_preclaim_failure(
            queue_item_id=queue_item_id,
            run_id=run_id,
            topic_id=topic_id,
            failure_code=result.failure_code or FailureCode.AUTH_MATERIAL_MISSING,
            detail=result.failure_detail or "scheduled session preflight failed",
            retryable=False,
            human_review_required=True,
            now=now,
        )

    def _acquire_lease(
        self,
        *,
        queue_item_id: str,
        run_id: str,
        topic_id: str,
        now: datetime | None,
    ) -> SessionLeaseRecord | ScheduledRunExecutionResult:
        try:
            return self._session_manager.acquire_execution_lease(
                session_id=self._session_id,
                purpose="scheduled_run",
                holder=self._worker_id,
                host_id=self._host_id,
                ttl_seconds=self._policy.lease_ttl_seconds,
                run_id=run_id,
                now=now,
            )
        except SessionLeaseConflictError as exc:
            return self._record_preclaim_failure(
                queue_item_id=queue_item_id,
                run_id=None,
                topic_id=topic_id,
                failure_code=FailureCode.CONCURRENT_SESSION_MUTATION,
                detail=str(exc),
                retryable=True,
                human_review_required=False,
                now=now,
            )
        except SessionPolicyError as exc:
            retryable = RETRY_MATRIX[exc.failure_code].retryable
            return self._record_preclaim_failure(
                queue_item_id=queue_item_id,
                run_id=None,
                topic_id=topic_id,
                failure_code=exc.failure_code,
                detail=exc.detail,
                retryable=retryable,
                human_review_required=not retryable,
                now=now,
            )

    def _canonicalize_and_persist(
        self,
        *,
        intent: RunIntent,
        runtime_result: RuntimeExecutionResult,
    ) -> RunReportViewModel:
        proposal_id = f"proposal:{intent.run_id}"
        canonical = self._canonicalizer.canonicalize(
            proposal=runtime_result.proposal,
            context=CanonicalizationContext(
                topic_id=intent.topic_id,
                run_id=intent.run_id,
                proposal_id=proposal_id,
                current_best_hypothesis_id=(
                    intent.frontier.current_best_hypotheses[0].hypothesis_id
                ),
                existing_hypotheses=self._existing_hypotheses(intent),
            ),
        )
        if canonical.quarantined:
            self._state_machine.transition(
                run_id=intent.run_id,
                to_state=RunLifecycleState.FAILED,
            )
            report = self._failure_report(
                intent=intent,
                status=InteractiveRunStatus.QUARANTINED,
                failure=OperatorFailureSummary(
                    failure_code=FailureCode.MALFORMED_PROPOSAL,
                    retryable=False,
                    human_review_required=True,
                    detail="canonical graph validation quarantined the proposal",
                    quarantine_reasons=canonical.quarantine_reasons,
                ),
                summary="Scheduled run was quarantined before backend state update.",
            )
            self._ledger.record_interactive_run_failure(report)
            self._ledger.record_queue_dead_letter(
                queue_item_id=intent.queue_item_id or "",
                run_id=intent.run_id,
                worker_id=self._worker_id,
                failure_code=FailureCode.MALFORMED_PROPOSAL.value,
                detail="; ".join(canonical.quarantine_reasons),
                retryable=False,
                human_review_required=True,
            )
            return report

        self._state_machine.transition(
            run_id=intent.run_id,
            to_state=RunLifecycleState.ADJUDICATING,
        )
        self._state_machine.transition(
            run_id=intent.run_id,
            to_state=RunLifecycleState.PERSISTING,
        )
        graph_payload = canonical.graph.model_dump(mode="json")
        proposal_digest = _digest(runtime_result.proposal.model_dump(mode="json"))
        report = RunReportViewModel(
            report_id=f"report:{intent.run_id}",
            run_id=intent.run_id,
            topic_id=intent.topic_id,
            trigger_id=f"scheduled:{intent.queue_item_id}",
            idempotency_key=intent.execution_request.idempotency_key,
            snapshot_version=intent.snapshot_version,
            status=InteractiveRunStatus.COMPLETED,
            user_input_kind=None,
            summary=runtime_result.proposal.summary_draft,
            proposal_digest=proposal_digest,
            backend_state_update=BackendStateUpdateSummary(
                graph_digest=canonical.digest,
                node_count=len(canonical.graph.nodes),
                edge_count=len(canonical.graph.edges),
                review_flags=[flag.code for flag in canonical.review_flags],
            ),
            operator_failure_summary=None,
            created_at=_utcnow(),
        )
        self._ledger.record_interactive_run_success(
            report=report,
            proposal_id=proposal_id,
            graph_payload=graph_payload,
            graph_digest=canonical.digest,
            node_count=len(canonical.graph.nodes),
            edge_count=len(canonical.graph.edges),
        )
        self._state_machine.transition(
            run_id=intent.run_id,
            to_state=RunLifecycleState.SUMMARIZING,
        )
        self._state_machine.transition(
            run_id=intent.run_id,
            to_state=RunLifecycleState.COMPLETED,
        )
        return report

    def _handle_runtime_failure(
        self,
        *,
        queue_item_id: str,
        run_id: str,
        topic_id: str,
        intent: RunIntent | None,
        failure_code: FailureCode,
        detail: str,
        retryable: bool,
        now: datetime | None,
    ) -> ScheduledRunExecutionResult:
        if retryable:
            return self._record_runtime_retry(
                queue_item_id=queue_item_id,
                run_id=run_id,
                topic_id=topic_id,
                failure_code=failure_code,
                detail=detail,
                now=now,
            )
        if intent is not None:
            self._fail_run_if_possible(run_id)
            report = self._failure_report(
                intent=intent,
                status=InteractiveRunStatus.FAILED,
                failure=OperatorFailureSummary(
                    failure_code=failure_code,
                    retryable=False,
                    human_review_required=True,
                    detail=detail,
                ),
            )
            self._ledger.record_interactive_run_failure(report)
        else:
            report = None
        mutation_owner = self._claimed_run_mutation_owner(
            queue_item_id=queue_item_id,
            run_id=run_id,
        )
        self._ledger.record_queue_dead_letter(
            queue_item_id=queue_item_id,
            failure_code=failure_code.value,
            detail=detail,
            retryable=False,
            human_review_required=True,
            **mutation_owner,
        )
        self._notify(
            topic_id=topic_id,
            queue_item_id=queue_item_id,
            run_id=run_id,
            action=ScheduledRunAction.TERMINAL_FAILED,
            failure_code=failure_code,
            detail=detail,
            retryable=False,
            human_review_required=True,
            now=now,
        )
        return ScheduledRunExecutionResult(
            queue_item_id=queue_item_id,
            run_id=run_id,
            action=ScheduledRunAction.TERMINAL_FAILED,
            queue_state=QueueJobState.DEAD_LETTER,
            failure_code=failure_code,
            report=report,
        )

    def _record_runtime_retry(
        self,
        *,
        queue_item_id: str,
        run_id: str,
        topic_id: str,
        failure_code: FailureCode,
        detail: str,
        now: datetime | None,
    ) -> ScheduledRunExecutionResult:
        current = self._require_queue_item(queue_item_id)
        attempts = int(current["attempts"])
        if attempts + 1 >= int(current["max_attempts"]):
            self._fail_run_if_possible(run_id)
            self._ledger.record_queue_dead_letter(
                queue_item_id=queue_item_id,
                run_id=run_id,
                worker_id=self._worker_id,
                failure_code=failure_code.value,
                detail=detail,
                retryable=True,
                human_review_required=False,
            )
            action = ScheduledRunAction.TERMINAL_FAILED
            queue_state = QueueJobState.DEAD_LETTER
        else:
            current_time = now or _utcnow()
            self._ledger.record_queue_retry(
                queue_item_id=queue_item_id,
                run_id=run_id,
                worker_id=self._worker_id,
                failure_code=failure_code.value,
                detail=detail,
                next_available_at=RETRY_MATRIX[failure_code].next_available_at(
                    attempts=attempts,
                    now=current_time,
                ),
            )
            action = ScheduledRunAction.RETRYABLE_FAILED
            queue_state = QueueJobState.QUEUED
        self._notify(
            topic_id=topic_id,
            queue_item_id=queue_item_id,
            run_id=run_id,
            action=action,
            failure_code=failure_code,
            detail=detail,
            retryable=True,
            human_review_required=False,
            now=now,
        )
        return ScheduledRunExecutionResult(
            queue_item_id=queue_item_id,
            run_id=run_id,
            action=action,
            queue_state=queue_state,
            failure_code=failure_code,
        )

    def _record_preclaim_failure(
        self,
        *,
        queue_item_id: str,
        run_id: str | None,
        topic_id: str,
        failure_code: FailureCode,
        detail: str,
        retryable: bool,
        human_review_required: bool,
        now: datetime | None,
    ) -> ScheduledRunExecutionResult:
        if retryable:
            current = self._require_queue_item(queue_item_id)
            attempts = int(current["attempts"])
            if attempts + 1 < int(current["max_attempts"]):
                current_time = now or _utcnow()
                self._ledger.record_queue_retry(
                    queue_item_id=queue_item_id,
                    failure_code=failure_code.value,
                    detail=detail,
                    next_available_at=RETRY_MATRIX[failure_code].next_available_at(
                        attempts=attempts,
                        now=current_time,
                    ),
                )
                action = ScheduledRunAction.DEFERRED
                queue_state = QueueJobState.QUEUED
            else:
                self._ledger.record_queue_dead_letter(
                    queue_item_id=queue_item_id,
                    failure_code=failure_code.value,
                    detail=detail,
                    retryable=True,
                    human_review_required=human_review_required,
                )
                action = ScheduledRunAction.TERMINAL_FAILED
                queue_state = QueueJobState.DEAD_LETTER
        else:
            self._ledger.record_queue_dead_letter(
                queue_item_id=queue_item_id,
                failure_code=failure_code.value,
                detail=detail,
                retryable=False,
                human_review_required=human_review_required,
            )
            action = ScheduledRunAction.TERMINAL_FAILED
            queue_state = QueueJobState.DEAD_LETTER
        self._notify(
            topic_id=topic_id,
            queue_item_id=queue_item_id,
            run_id=run_id,
            action=action,
            failure_code=failure_code,
            detail=detail,
            retryable=retryable,
            human_review_required=human_review_required,
            now=now,
        )
        return ScheduledRunExecutionResult(
            queue_item_id=queue_item_id,
            run_id=run_id,
            action=action,
            queue_state=queue_state,
            failure_code=failure_code,
        )

    def _failure_report(
        self,
        *,
        intent: RunIntent,
        status: InteractiveRunStatus,
        failure: OperatorFailureSummary,
        summary: str = "Scheduled run failed before backend state update.",
    ) -> RunReportViewModel:
        return RunReportViewModel(
            report_id=f"report:{intent.run_id}",
            run_id=intent.run_id,
            topic_id=intent.topic_id,
            trigger_id=f"scheduled:{intent.queue_item_id}",
            idempotency_key=intent.execution_request.idempotency_key,
            snapshot_version=intent.snapshot_version,
            status=status,
            user_input_kind=None,
            summary=summary,
            proposal_digest=None,
            backend_state_update=None,
            operator_failure_summary=failure,
            created_at=_utcnow(),
        )

    def _fail_run_if_possible(self, run_id: str) -> None:
        run = self._ledger.fetch_run(run_id)
        if run is None:
            return
        try:
            self._state_machine.transition(
                run_id=run_id,
                to_state=RunLifecycleState.FAILED,
            )
        except InvalidRunTransitionError:
            pass

    def _claimed_run_mutation_owner(
        self,
        *,
        queue_item_id: str,
        run_id: str,
    ) -> dict[str, str]:
        queue = self._ledger.fetch_queue_item(queue_item_id)
        run = self._ledger.fetch_run_by_queue_item(queue_item_id)
        if (
            queue is not None
            and run is not None
            and queue["state"] == QueueJobState.CLAIMED.value
            and run["id"] == run_id
        ):
            return {"run_id": run_id, "worker_id": self._worker_id}
        return {}

    def _existing_hypotheses(self, intent: RunIntent) -> list[HypothesisSnapshot]:
        snapshots: dict[str, HypothesisSnapshot] = {}
        for hypothesis in (
            list(intent.frontier.current_best_hypotheses)
            + list(intent.frontier.challenger_targets)
        ):
            snapshots[hypothesis.hypothesis_id] = HypothesisSnapshot(
                hypothesis_id=hypothesis.hypothesis_id,
                title=hypothesis.title,
                statement=hypothesis.summary,
                version=1,
            )
        return list(snapshots.values())

    def _notify(
        self,
        *,
        topic_id: str,
        queue_item_id: str,
        run_id: str | None,
        action: ScheduledRunAction,
        failure_code: FailureCode | None,
        detail: str,
        retryable: bool,
        human_review_required: bool,
        now: datetime | None,
    ) -> None:
        notification = ScheduledOperatorNotification(
            topic_id=topic_id,
            queue_item_id=queue_item_id,
            run_id=run_id,
            action=action,
            failure_code=failure_code,
            detail=detail,
            retryable=retryable,
            human_review_required=human_review_required,
            created_at=now or _utcnow(),
        )
        if self._notifier is not None:
            self._notifier.notify(notification)
        if self._ledger.fetch_session_record(self._session_id) is None:
            return
        self._ledger.append_session_event(
            session_id=self._session_id,
            event_type="scheduled_run.operator_notification",
            payload={
                "topic_id": topic_id,
                "queue_item_id": queue_item_id,
                "run_id": run_id,
                "action": action.value,
                "failure_code": None if failure_code is None else failure_code.value,
                "detail": detail,
                "retryable": retryable,
                "human_review_required": human_review_required,
            },
            created_at=notification.created_at,
        )

    def _require_queue_item(self, queue_item_id: str) -> dict[str, object]:
        row = self._ledger.fetch_queue_item(queue_item_id)
        if row is None:
            raise KeyError(f"queue item {queue_item_id} does not exist")
        return row

    def _run_id_for_queue_item(self, queue_item_id: str) -> str | None:
        run = self._ledger.fetch_run_by_queue_item(queue_item_id)
        return None if run is None else str(run["id"])
