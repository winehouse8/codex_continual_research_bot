"""Phase 9 interactive run API facade."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Protocol

from codex_continual_research_bot.contracts import (
    BackendStateUpdateSummary,
    FailureCode,
    InteractiveRunStatus,
    InteractiveRunTriggerRequest,
    InteractiveRunTriggerResponse,
    OperatorFailureSummary,
    QueueJob,
    QueueJobKind,
    QueueJobState,
    RunIntent,
    RunLifecycleState,
    RunMode,
    RunReportViewModel,
    TopicReadModel,
    TopicSnapshot,
    UserInputKind,
)
from codex_continual_research_bot.graph_canonicalization import (
    CanonicalGraphService,
    CanonicalizationContext,
    HypothesisSnapshot,
)
from codex_continual_research_bot.orchestrator import (
    CompetitionValidationError,
    InvalidRunTransitionError,
    RunOrchestrator,
    RunStateMachine,
)
from codex_continual_research_bot.persistence import SQLitePersistenceLedger
from codex_continual_research_bot.runtime import CodexRuntimeError, RuntimeExecutionResult


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(data: object) -> str:
    return f"sha256:{sha256(_canonical_json(data).encode('utf-8')).hexdigest()}"


class InteractiveRunError(RuntimeError):
    """Base error for fail-closed interactive run API failures."""

    def __init__(
        self,
        *,
        failure_code: FailureCode,
        detail: str,
        retryable: bool = False,
        human_review_required: bool = True,
    ) -> None:
        super().__init__(detail)
        self.failure_code = failure_code
        self.detail = detail
        self.retryable = retryable
        self.human_review_required = human_review_required


class InvalidInteractiveUserInputError(InteractiveRunError):
    """Raised when user input cannot be classified as structured research input."""


class DuplicateInteractiveTriggerError(InteractiveRunError):
    """Raised when a trigger id is reused with a different request body."""


class InteractiveWorkspaceMismatchError(InteractiveRunError):
    """Raised when the API request targets a different workspace root."""


class InteractiveRuntime(Protocol):
    def execute(self, intent: RunIntent) -> RuntimeExecutionResult:
        """Execute a prepared run intent and return a validated proposal."""


class InteractiveRunService:
    """Closes the topic-read, trigger, runtime, persistence, and report path."""

    def __init__(
        self,
        ledger: SQLitePersistenceLedger,
        *,
        runtime: InteractiveRuntime,
        workspace_root: Path,
        worker_id: str = "interactive-api",
        orchestrator: RunOrchestrator | None = None,
        canonicalizer: CanonicalGraphService | None = None,
    ) -> None:
        self._ledger = ledger
        self._runtime = runtime
        self._workspace_root = workspace_root.resolve()
        self._worker_id = worker_id
        self._orchestrator = orchestrator or RunOrchestrator(ledger)
        self._state_machine = RunStateMachine(ledger)
        self._canonicalizer = canonicalizer or CanonicalGraphService()

    def read_topic(self, topic_id: str) -> TopicReadModel:
        snapshot = self._orchestrator.load_topic_snapshot(topic_id=topic_id)
        return self._topic_read_model(snapshot)

    def trigger_run(
        self,
        request: InteractiveRunTriggerRequest,
    ) -> InteractiveRunTriggerResponse:
        self._validate_workspace(request.workspace_root)
        user_input_kind = self._classify_user_input(request.user_input)
        ids = self._ids_for_request(request)
        request_digest = self._request_digest(
            request=request,
            user_input_kind=user_input_kind,
        )

        existing = self._ledger.get_idempotency_record(ids["idempotency_key"])
        if existing is not None:
            if existing["request_digest"] != request_digest:
                raise DuplicateInteractiveTriggerError(
                    failure_code=FailureCode.DUPLICATE_TRIGGER_MISMATCH,
                    detail=(
                        "interactive trigger id was reused with a different "
                        "topic, snapshot, workspace, or classified user input"
                    ),
                    retryable=False,
                    human_review_required=True,
                )
            existing_report = self._ledger.fetch_interactive_run_report(
                idempotency_key=ids["idempotency_key"],
            )
            if existing_report is not None:
                topic = self._topic_read_model(
                    self._orchestrator.load_topic_snapshot(
                        topic_id=request.topic_id,
                        snapshot_version=existing_report.snapshot_version,
                    )
                )
                return InteractiveRunTriggerResponse(
                    topic=topic,
                    report=existing_report,
                    duplicate=True,
                    resumed=False,
                )
            intent = self._orchestrator.resume_run(
                run_id=existing["run_id"],
                expected_snapshot_version=request.expected_snapshot_version,
            )
            topic = self._topic_read_model(
                self._orchestrator.load_topic_snapshot(
                    topic_id=request.topic_id,
                    snapshot_version=intent.snapshot_version,
                )
            )
            report = self._execute_intent(
                intent=intent,
                trigger_id=request.trigger_id,
                user_input_kind=user_input_kind,
            )
            return InteractiveRunTriggerResponse(
                topic=topic,
                report=report,
                duplicate=True,
                resumed=True,
            )

        snapshot = self._orchestrator.load_topic_snapshot(
            topic_id=request.topic_id,
            expected_snapshot_version=request.expected_snapshot_version,
        )
        topic = self._topic_read_model(snapshot)
        self._ledger.reserve_idempotency_key(
            idempotency_key=ids["idempotency_key"],
            scope=QueueJobKind.RUN_EXECUTE.value,
            request_digest=request_digest,
        )
        self._ledger.enqueue_job(
            QueueJob(
                queue_item_id=ids["queue_item_id"],
                kind=QueueJobKind.RUN_EXECUTE,
                state=QueueJobState.QUEUED,
                topic_id=request.topic_id,
                requested_run_id=ids["run_id"],
                dedupe_key=ids["dedupe_key"],
                idempotency_key=ids["idempotency_key"],
                priority=100,
                attempts=0,
                max_attempts=1,
                available_at=_utcnow(),
                payload={
                    "initiator": "interactive",
                    "objective": (
                        f"Run interactive research for {user_input_kind.value}: "
                        f"{self._classified_body(request.user_input)}"
                    ),
                    "selected_queue_item_ids": [ids["queue_item_id"]],
                },
                last_failure=None,
            )
        )
        intent = self._orchestrator.start_queued_run(
            queue_item_id=ids["queue_item_id"],
            run_id=ids["run_id"],
            worker_id=self._worker_id,
            mode=RunMode.INTERACTIVE,
            expected_snapshot_version=request.expected_snapshot_version,
        )
        report = self._execute_intent(
            intent=intent,
            trigger_id=request.trigger_id,
            user_input_kind=user_input_kind,
        )
        return InteractiveRunTriggerResponse(topic=topic, report=report)

    def _execute_intent(
        self,
        *,
        intent: RunIntent,
        trigger_id: str,
        user_input_kind: UserInputKind,
    ) -> RunReportViewModel:
        try:
            runtime_result = self._runtime.execute(intent)
            self._orchestrator.accept_competition_proposal(
                intent=intent,
                proposal=runtime_result.proposal,
            )
            report = self._canonicalize_and_persist(
                intent=intent,
                trigger_id=trigger_id,
                user_input_kind=user_input_kind,
                runtime_result=runtime_result,
            )
        except CodexRuntimeError as exc:
            self._fail_run_if_possible(intent.run_id)
            report = self._failure_report(
                intent=intent,
                trigger_id=trigger_id,
                user_input_kind=user_input_kind,
                status=InteractiveRunStatus.FAILED,
                failure=OperatorFailureSummary(
                    failure_code=exc.failure_code,
                    retryable=exc.retryable,
                    human_review_required=not exc.retryable,
                    detail=exc.detail,
                ),
            )
            self._ledger.record_interactive_run_failure(report)
            self._record_queue_failure(
                intent=intent,
                failure_code=exc.failure_code,
                detail=exc.detail,
                retryable=exc.retryable,
            )
        except (CompetitionValidationError, InvalidRunTransitionError) as exc:
            self._fail_run_if_possible(intent.run_id)
            report = self._failure_report(
                intent=intent,
                trigger_id=trigger_id,
                user_input_kind=user_input_kind,
                status=InteractiveRunStatus.QUARANTINED,
                failure=OperatorFailureSummary(
                    failure_code=FailureCode.MALFORMED_PROPOSAL,
                    retryable=False,
                    human_review_required=True,
                    detail=str(exc),
                ),
            )
            self._ledger.record_interactive_run_failure(report)
            self._record_queue_failure(
                intent=intent,
                failure_code=FailureCode.MALFORMED_PROPOSAL,
                detail=str(exc),
                retryable=False,
            )
        return report

    def _canonicalize_and_persist(
        self,
        *,
        intent: RunIntent,
        trigger_id: str,
        user_input_kind: UserInputKind,
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
                trigger_id=trigger_id,
                user_input_kind=user_input_kind,
                status=InteractiveRunStatus.QUARANTINED,
                summary="Interactive run was quarantined before backend state update.",
                failure=OperatorFailureSummary(
                    failure_code=FailureCode.MALFORMED_PROPOSAL,
                    retryable=False,
                    human_review_required=True,
                    detail="canonical graph validation quarantined the proposal",
                    quarantine_reasons=canonical.quarantine_reasons,
                ),
            )
            self._ledger.record_interactive_run_failure(report)
            self._record_queue_failure(
                intent=intent,
                failure_code=FailureCode.MALFORMED_PROPOSAL,
                detail="; ".join(canonical.quarantine_reasons),
                retryable=False,
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
            trigger_id=trigger_id,
            idempotency_key=intent.execution_request.idempotency_key,
            snapshot_version=intent.snapshot_version,
            status=InteractiveRunStatus.COMPLETED,
            user_input_kind=user_input_kind,
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
        self._ledger.complete_queue_item(
            queue_item_id=intent.queue_item_id or "",
            run_id=intent.run_id,
            worker_id=self._worker_id,
        )
        return report

    def _record_queue_failure(
        self,
        *,
        intent: RunIntent,
        failure_code: FailureCode,
        detail: str,
        retryable: bool,
    ) -> None:
        if retryable:
            self._ledger.record_queue_retry(
                queue_item_id=intent.queue_item_id or "",
                run_id=intent.run_id,
                worker_id=self._worker_id,
                failure_code=failure_code.value,
                detail=detail,
                next_available_at=_utcnow(),
            )
            return
        self._ledger.record_queue_dead_letter(
            queue_item_id=intent.queue_item_id or "",
            run_id=intent.run_id,
            worker_id=self._worker_id,
            failure_code=failure_code.value,
            detail=detail,
            retryable=False,
            human_review_required=True,
        )

    def _failure_report(
        self,
        *,
        intent: RunIntent,
        trigger_id: str,
        user_input_kind: UserInputKind,
        status: InteractiveRunStatus,
        failure: OperatorFailureSummary,
        summary: str = "Interactive run failed before backend state update.",
    ) -> RunReportViewModel:
        return RunReportViewModel(
            report_id=f"report:{intent.run_id}",
            run_id=intent.run_id,
            topic_id=intent.topic_id,
            trigger_id=trigger_id,
            idempotency_key=intent.execution_request.idempotency_key,
            snapshot_version=intent.snapshot_version,
            status=status,
            user_input_kind=user_input_kind,
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

    def _topic_read_model(self, snapshot: TopicSnapshot) -> TopicReadModel:
        return TopicReadModel(
            topic_id=snapshot.topic_id,
            snapshot_version=snapshot.snapshot_version,
            topic_summary=snapshot.topic_summary,
            current_best_hypotheses=snapshot.current_best_hypotheses,
            challenger_targets=snapshot.challenger_targets,
            active_conflicts=snapshot.active_conflicts,
            open_questions=snapshot.open_questions,
            queued_user_inputs=snapshot.queued_user_inputs,
        )

    def _validate_workspace(self, workspace_root: str) -> None:
        if Path(workspace_root).resolve() != self._workspace_root:
            raise InteractiveWorkspaceMismatchError(
                failure_code=FailureCode.WORKSPACE_MISMATCH,
                detail=(
                    "interactive run workspace root mismatch: "
                    f"{Path(workspace_root).resolve()} != {self._workspace_root}"
                ),
                retryable=False,
                human_review_required=True,
            )

    def _classify_user_input(self, user_input: str) -> UserInputKind:
        prefix = user_input.split(":", 1)[0].strip().lower()
        body = self._classified_body(user_input)
        mapping = {
            "idea": UserInputKind.IDEA,
            "counterargument": UserInputKind.COUNTERARGUMENT,
            "source": UserInputKind.SOURCE_REQUEST,
            "source_request": UserInputKind.SOURCE_REQUEST,
            "question": UserInputKind.QUESTION,
        }
        kind = mapping.get(prefix)
        if kind is None or len(body) < 5:
            raise InvalidInteractiveUserInputError(
                failure_code=FailureCode.INVALID_USER_INPUT,
                detail=(
                    "interactive user input must be prefixed with one of "
                    "idea:, counterargument:, source:, or question:"
                ),
                retryable=False,
                human_review_required=False,
            )
        return kind

    def _classified_body(self, user_input: str) -> str:
        if ":" not in user_input:
            return ""
        return user_input.split(":", 1)[1].strip()

    def _ids_for_request(self, request: InteractiveRunTriggerRequest) -> dict[str, str]:
        stable = sha256(
            f"{request.topic_id}|{request.trigger_id}".encode("utf-8")
        ).hexdigest()[:16]
        return {
            "queue_item_id": f"interactive_queue_{stable}",
            "run_id": f"interactive_run_{stable}",
            "dedupe_key": f"interactive:{request.topic_id}:{request.trigger_id}",
            "idempotency_key": (
                f"interactive.run:{request.topic_id}:{request.trigger_id}:v1"
            ),
        }

    def _request_digest(
        self,
        *,
        request: InteractiveRunTriggerRequest,
        user_input_kind: UserInputKind,
    ) -> str:
        return _digest(
            {
                "topic_id": request.topic_id,
                "trigger_id": request.trigger_id,
                "expected_snapshot_version": request.expected_snapshot_version,
                "workspace_root": str(Path(request.workspace_root).resolve()),
                "user_input_kind": user_input_kind.value,
                "user_input_body": self._classified_body(request.user_input),
            }
        )
