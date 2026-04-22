"""Phase 21 autonomous topic worker loop and convergence stop policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from codex_continual_research_bot.contracts import (
    Argument,
    ArgumentStance,
    BackendStateUpdateSummary,
    ChallengerHypothesis,
    ChallengerStatus,
    Claim,
    ConflictAssessment,
    ConflictStatus,
    EvidenceCandidate,
    EvidenceKind,
    ExecutionMeta,
    FailureCode,
    InteractiveRunStatus,
    NextAction,
    NextActionKind,
    OperatorFailureSummary,
    ProposalBundle,
    QueueJobState,
    RevisionAction,
    RevisionProposal,
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
    InvalidRunTransitionError,
    RunOrchestrator,
    RunStateMachine,
)
from codex_continual_research_bot.persistence import SQLitePersistenceLedger
from codex_continual_research_bot.runtime import (
    CodexRuntimeError,
    RuntimeExecutionResult,
    RuntimeMetrics,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _digest_text(value: str) -> str:
    return f"sha256:{sha256(value.encode('utf-8')).hexdigest()}"


class WorkerLoopStopReason(str, Enum):
    EMPTY_QUEUE_CONVERGED = "empty_queue_converged"
    EMPTY_QUEUE_PAUSED_ACTIVE_CONFLICTS = "empty_queue_paused_active_conflicts"
    MAX_CONSECUTIVE_NO_YIELD = "max_consecutive_no_yield"
    MAX_ITERATIONS = "max_iterations"
    BUDGET_EXHAUSTED = "budget_exhausted"
    REPEATED_MALFORMED_PROPOSAL = "repeated_malformed_proposal"
    ACTIVE_LOOP_EXISTS = "active_loop_exists"
    STOPPED_BY_OPERATOR = "stopped_by_operator"


@dataclass(frozen=True)
class WorkerLoopPolicy:
    max_iterations: int = 10
    max_consecutive_no_yield: int = 2
    max_malformed_proposals: int = 2
    max_runtime_seconds: int = 1800
    lease_ttl_seconds: int = 120


@dataclass(frozen=True)
class GraphChangeSummary:
    graph_digest: str | None
    node_count: int | None
    edge_count: int | None


@dataclass(frozen=True)
class YieldDecision:
    yielded: bool
    reason: str
    last_meaningful_change: str | None


@dataclass(frozen=True)
class LoopExecutionResult:
    queue_item_id: str
    run_id: str | None
    queue_state: QueueJobState
    failure_code: FailureCode | None = None
    report: RunReportViewModel | None = None


class WorkerLoopRunExecutor(Protocol):
    def execute_item(
        self,
        *,
        queue_item_id: str,
        run_id: str,
        worker_id: str,
    ) -> LoopExecutionResult:
        """Execute one claimed queue item through runtime and backend persistence."""


class YieldAnalyzer:
    """Classifies whether a completed iteration produced meaningful graph yield."""

    def analyze(
        self,
        *,
        before: GraphChangeSummary,
        after: GraphChangeSummary,
        result: LoopExecutionResult,
    ) -> YieldDecision:
        report = result.report
        if report is None or report.status != InteractiveRunStatus.COMPLETED:
            return YieldDecision(
                yielded=False,
                reason="run did not complete with a backend state update",
                last_meaningful_change=None,
            )
        if after.graph_digest is None:
            return YieldDecision(
                yielded=False,
                reason="no canonical graph write was recorded",
                last_meaningful_change=None,
            )
        changed = (
            before.graph_digest != after.graph_digest
            or before.node_count != after.node_count
            or before.edge_count != after.edge_count
        )
        if not changed:
            return YieldDecision(
                yielded=False,
                reason="canonical graph digest and meaningful graph counts were unchanged",
                last_meaningful_change=None,
            )
        return YieldDecision(
            yielded=True,
            reason="canonical graph digest or meaningful graph counts changed",
            last_meaningful_change=after.graph_digest,
        )


class LocalProposalRunExecutor:
    """Deterministic local executor used by the CLI fixture path.

    Production deployments can inject a Codex-backed executor. This default
    keeps the local CLI and tests end-to-end without treating CLI/UI state as
    graph authority; all writes still pass through orchestrator validation and
    canonical graph persistence.
    """

    def __init__(
        self,
        ledger: SQLitePersistenceLedger,
        *,
        artifacts_root: Path,
        canonicalizer: CanonicalGraphService | None = None,
        orchestrator: RunOrchestrator | None = None,
    ) -> None:
        self._ledger = ledger
        self._artifacts_root = artifacts_root
        self._canonicalizer = canonicalizer or CanonicalGraphService()
        self._orchestrator = orchestrator or RunOrchestrator(ledger)
        self._state_machine = RunStateMachine(ledger)

    def execute_item(
        self,
        *,
        queue_item_id: str,
        run_id: str,
        worker_id: str,
    ) -> LoopExecutionResult:
        intent: RunIntent | None = None
        try:
            intent = self._orchestrator.start_queued_run(
                queue_item_id=queue_item_id,
                run_id=run_id,
                worker_id=worker_id,
                mode=RunMode.SCHEDULED,
            )
            runtime_result = RuntimeExecutionResult(
                run_id=run_id,
                proposal=self._proposal_for_intent(intent),
                metrics=RuntimeMetrics(
                    raw_event_count=1,
                    normalized_event_count=1,
                    artifact_count=1,
                    exit_code=0,
                    timed_out=False,
                ),
                artifacts_dir=self._artifacts_root / run_id,
            )
            self._orchestrator.accept_competition_proposal(
                intent=intent,
                proposal=runtime_result.proposal,
            )
            report = self._canonicalize_and_persist(
                intent=intent,
                runtime_result=runtime_result,
                worker_id=worker_id,
            )
            return LoopExecutionResult(
                queue_item_id=queue_item_id,
                run_id=run_id,
                queue_state=QueueJobState.COMPLETED,
                report=report,
            )
        except CodexRuntimeError as exc:
            self._fail_run_if_possible(run_id)
            self._record_failure(
                queue_item_id=queue_item_id,
                run_id=run_id,
                worker_id=worker_id,
                failure_code=exc.failure_code,
                detail=exc.detail,
                retryable=exc.retryable,
            )
            return LoopExecutionResult(
                queue_item_id=queue_item_id,
                run_id=run_id,
                queue_state=QueueJobState.QUEUED if exc.retryable else QueueJobState.DEAD_LETTER,
                failure_code=exc.failure_code,
            )
        except (CompetitionValidationError, InvalidRunTransitionError) as exc:
            self._fail_run_if_possible(run_id)
            if intent is not None:
                self._ledger.record_interactive_run_failure(
                    self._failure_report(intent=intent, detail=str(exc))
                )
            self._record_failure(
                queue_item_id=queue_item_id,
                run_id=run_id,
                worker_id=worker_id,
                failure_code=FailureCode.MALFORMED_PROPOSAL,
                detail=str(exc),
                retryable=False,
            )
            return LoopExecutionResult(
                queue_item_id=queue_item_id,
                run_id=run_id,
                queue_state=QueueJobState.DEAD_LETTER,
                failure_code=FailureCode.MALFORMED_PROPOSAL,
            )

    def _proposal_for_intent(self, intent: RunIntent) -> ProposalBundle:
        current = intent.frontier.current_best_hypotheses[0]
        conflict_id = (
            intent.frontier.active_conflicts[0].conflict_id
            if intent.frontier.active_conflicts
            else "conf_worker_loop_generated"
        )
        suffix = sha256(intent.run_id.encode("utf-8")).hexdigest()[:10]
        artifact_id = f"artifact_{suffix}"
        claim_id = f"claim_{suffix}"
        challenger_id = f"challenger_{suffix}"
        accessed_at = _utcnow()
        return ProposalBundle(
            summary_draft=(
                "Autonomous worker loop executed one queued research task "
                f"for {intent.topic_id}."
            ),
            evidence_candidates=[
                EvidenceCandidate(
                    artifact_id=artifact_id,
                    kind=EvidenceKind.INTERNAL_NOTE,
                    source_url=f"internal://worker-loop/{intent.run_id}",
                    title="Worker loop execution note",
                    accessed_at=accessed_at,
                    extraction_note="Local worker loop fixture produced deterministic evidence.",
                )
            ],
            claims=[
                Claim(
                    claim_id=claim_id,
                    text=(
                        f"As of {accessed_at.date().isoformat()}, worker loop run "
                        f"{intent.run_id} produced a challenger against {current.hypothesis_id}."
                    ),
                    artifact_ids=[artifact_id],
                    temporal_scope=f"as of {accessed_at.date().isoformat()}",
                )
            ],
            arguments=[
                Argument(
                    argument_id=f"arg_support_{suffix}",
                    stance=ArgumentStance.SUPPORT,
                    target_hypothesis_id=current.hypothesis_id,
                    claim_ids=[claim_id],
                    rationale="The run preserved the current best as an explicit target.",
                ),
                Argument(
                    argument_id=f"arg_challenge_{suffix}",
                    stance=ArgumentStance.CHALLENGE,
                    target_hypothesis_id=current.hypothesis_id,
                    claim_ids=[claim_id],
                    rationale="The generated challenger creates explicit selection pressure.",
                ),
            ],
            challenger_hypotheses=[
                ChallengerHypothesis(
                    hypothesis_id=challenger_id,
                    title="Worker loop challenger",
                    statement=(
                        f"Queued task {intent.queue_item_id} may reveal a better "
                        "hypothesis than the current best."
                    ),
                    status=ChallengerStatus.PROPOSED,
                )
            ],
            conflict_assessments=[
                ConflictAssessment(
                    conflict_id=conflict_id,
                    status=ConflictStatus.ESCALATED,
                    summary="Worker loop preserved unresolved tension for the next frontier.",
                )
            ],
            revision_proposals=[
                RevisionProposal(
                    hypothesis_id=current.hypothesis_id,
                    action=RevisionAction.WEAKEN,
                    rationale="Autonomous loop generated challenger pressure.",
                )
            ],
            next_actions=[
                NextAction(
                    action_id=f"next_{suffix}",
                    kind=NextActionKind.ATTACK_CURRENT_BEST,
                    description="Continue attacking the current best if new queued work remains.",
                )
            ],
            execution_meta=ExecutionMeta(
                turn_count=1,
                tool_call_count=0,
                compactions=0,
                repair_attempts=0,
            ),
        )

    def _canonicalize_and_persist(
        self,
        *,
        intent: RunIntent,
        runtime_result: RuntimeExecutionResult,
        worker_id: str,
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
            self._fail_run_if_possible(intent.run_id)
            report = self._failure_report(
                intent=intent,
                detail="; ".join(canonical.quarantine_reasons),
            )
            self._ledger.record_interactive_run_failure(report)
            self._record_failure(
                queue_item_id=intent.queue_item_id or "",
                run_id=intent.run_id,
                worker_id=worker_id,
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
        report = RunReportViewModel(
            report_id=f"report:{intent.run_id}",
            run_id=intent.run_id,
            topic_id=intent.topic_id,
            trigger_id=f"worker-loop:{intent.queue_item_id}",
            idempotency_key=intent.execution_request.idempotency_key,
            snapshot_version=intent.snapshot_version,
            status=InteractiveRunStatus.COMPLETED,
            user_input_kind=None,
            summary=runtime_result.proposal.summary_draft,
            proposal_digest=_digest_text(runtime_result.proposal.model_dump_json()),
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
            graph_payload=canonical.graph.model_dump(mode="json"),
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
            worker_id=worker_id,
        )
        return report

    def _failure_report(self, *, intent: RunIntent, detail: str) -> RunReportViewModel:
        return RunReportViewModel(
            report_id=f"report:{intent.run_id}",
            run_id=intent.run_id,
            topic_id=intent.topic_id,
            trigger_id=f"worker-loop:{intent.queue_item_id}",
            idempotency_key=intent.execution_request.idempotency_key,
            snapshot_version=intent.snapshot_version,
            status=InteractiveRunStatus.QUARANTINED,
            user_input_kind=None,
            summary="Worker loop run was quarantined before backend state update.",
            proposal_digest=None,
            backend_state_update=None,
            operator_failure_summary=OperatorFailureSummary(
                failure_code=FailureCode.MALFORMED_PROPOSAL,
                retryable=False,
                human_review_required=True,
                detail=detail,
            ),
            created_at=_utcnow(),
        )

    def _record_failure(
        self,
        *,
        queue_item_id: str,
        run_id: str,
        worker_id: str,
        failure_code: FailureCode,
        detail: str,
        retryable: bool,
    ) -> None:
        if retryable:
            self._ledger.record_queue_retry(
                queue_item_id=queue_item_id,
                run_id=run_id,
                worker_id=worker_id,
                failure_code=failure_code.value,
                detail=detail,
                next_available_at=_utcnow(),
            )
            return
        self._ledger.record_queue_dead_letter(
            queue_item_id=queue_item_id,
            run_id=run_id,
            worker_id=worker_id,
            failure_code=failure_code.value,
            detail=detail,
            retryable=False,
            human_review_required=True,
        )

    def _fail_run_if_possible(self, run_id: str) -> None:
        try:
            self._state_machine.transition(
                run_id=run_id,
                to_state=RunLifecycleState.FAILED,
            )
        except (InvalidRunTransitionError, KeyError):
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


@dataclass(frozen=True)
class WorkerLoopRunResult:
    topic_id: str
    loop_id: str | None
    state: str
    stop_reason: WorkerLoopStopReason
    iteration_count: int
    consecutive_no_yield: int
    malformed_proposal_streak: int
    yielded_count: int
    last_graph_digest: str | None


class WorkerLoopService:
    """Runs queued topic work until policy or convergence stops the loop."""

    def __init__(
        self,
        ledger: SQLitePersistenceLedger,
        *,
        worker_id: str = "worker-loop",
        executor: WorkerLoopRunExecutor | None = None,
        yield_analyzer: YieldAnalyzer | None = None,
        artifacts_root: Path | None = None,
    ) -> None:
        self._ledger = ledger
        self._worker_id = worker_id
        self._executor = executor or LocalProposalRunExecutor(
            ledger,
            artifacts_root=artifacts_root or Path(".crb") / "worker-artifacts",
        )
        self._yield_analyzer = yield_analyzer or YieldAnalyzer()

    def run(
        self,
        *,
        topic_id: str,
        policy: WorkerLoopPolicy | None = None,
        now: datetime | None = None,
    ) -> WorkerLoopRunResult:
        policy = policy or WorkerLoopPolicy()
        start = now or _utcnow()
        previous = self._ledger.fetch_worker_loop(topic_id=topic_id)
        previous_loop_id = "" if previous is None else str(previous["loop_id"])
        loop_seed = f"{topic_id}|{self._worker_id}|{start.isoformat()}|{previous_loop_id}"
        loop_id = f"worker_loop_{sha256(loop_seed.encode('utf-8')).hexdigest()[:16]}"
        lease = self._ledger.acquire_worker_loop(
            loop_id=loop_id,
            topic_id=topic_id,
            worker_id=self._worker_id,
            lease_expires_at=start + timedelta(seconds=policy.lease_ttl_seconds),
            now=start,
        )
        if lease is None:
            current = self._ledger.fetch_worker_loop(topic_id=topic_id)
            return WorkerLoopRunResult(
                topic_id=topic_id,
                loop_id=None if current is None else str(current["loop_id"]),
                state="blocked",
                stop_reason=WorkerLoopStopReason.ACTIVE_LOOP_EXISTS,
                iteration_count=0,
                consecutive_no_yield=0,
                malformed_proposal_streak=0,
                yielded_count=0,
                last_graph_digest=None,
            )

        iterations = 0
        yielded_count = 0
        consecutive_no_yield = 0
        malformed_streak = 0
        last_meaningful_change: str | None = None
        stop_reason: WorkerLoopStopReason | None = None
        stop_state = "stopped"

        while True:
            current_time = start + timedelta(seconds=iterations)
            self._ledger.heartbeat_worker_loop(
                loop_id=loop_id,
                lease_expires_at=current_time + timedelta(seconds=policy.lease_ttl_seconds),
                now=current_time,
            )
            if iterations >= policy.max_iterations:
                stop_reason = WorkerLoopStopReason.MAX_ITERATIONS
                break
            if (current_time - start).total_seconds() >= policy.max_runtime_seconds:
                stop_reason = WorkerLoopStopReason.BUDGET_EXHAUSTED
                stop_state = "blocked"
                break

            row = self._ledger.fetch_next_claimable_queue_item_for_topic(
                topic_id=topic_id,
                now=current_time,
            )
            if row is None:
                snapshot = self._ledger.fetch_topic_snapshot(topic_id=topic_id)
                if snapshot is not None and snapshot.active_conflicts:
                    stop_reason = WorkerLoopStopReason.EMPTY_QUEUE_PAUSED_ACTIVE_CONFLICTS
                    stop_state = "blocked"
                else:
                    stop_reason = WorkerLoopStopReason.EMPTY_QUEUE_CONVERGED
                    stop_state = "converged"
                break

            before = self._graph_summary(topic_id)
            result = self._executor.execute_item(
                queue_item_id=str(row["id"]),
                run_id=str(row["requested_run_id"]),
                worker_id=self._worker_id,
            )
            after = self._graph_summary(topic_id)
            decision = self._yield_analyzer.analyze(
                before=before,
                after=after,
                result=result,
            )
            iterations += 1
            if decision.yielded:
                yielded_count += 1
                consecutive_no_yield = 0
                last_meaningful_change = decision.last_meaningful_change
            else:
                consecutive_no_yield += 1
            if result.failure_code == FailureCode.MALFORMED_PROPOSAL or (
                result.report is not None
                and result.report.operator_failure_summary is not None
                and result.report.operator_failure_summary.failure_code
                == FailureCode.MALFORMED_PROPOSAL
            ):
                malformed_streak += 1
            else:
                malformed_streak = 0

            self._ledger.append_worker_loop_iteration(
                loop_id=loop_id,
                topic_id=topic_id,
                iteration=iterations,
                yielded=decision.yielded,
                yield_reason=decision.reason,
                graph_digest_before=before.graph_digest,
                graph_digest_after=after.graph_digest,
                node_count_before=before.node_count,
                node_count_after=after.node_count,
                edge_count_before=before.edge_count,
                edge_count_after=after.edge_count,
                queue_item_id=result.queue_item_id,
                run_id=result.run_id,
                queue_state=result.queue_state.value,
                failure_code=None if result.failure_code is None else result.failure_code.value,
                consecutive_no_yield=consecutive_no_yield,
                malformed_proposal_streak=malformed_streak,
                last_meaningful_change=last_meaningful_change,
                created_at=current_time,
            )

            if malformed_streak >= policy.max_malformed_proposals:
                stop_reason = WorkerLoopStopReason.REPEATED_MALFORMED_PROPOSAL
                stop_state = "blocked"
                break
            if consecutive_no_yield >= policy.max_consecutive_no_yield:
                stop_reason = WorkerLoopStopReason.MAX_CONSECUTIVE_NO_YIELD
                stop_state = "converged"
                break

        assert stop_reason is not None
        stopped = self._ledger.stop_worker_loop(
            loop_id=loop_id,
            state=stop_state,
            stop_reason=stop_reason.value,
        )
        return WorkerLoopRunResult(
            topic_id=topic_id,
            loop_id=loop_id,
            state=stop_state,
            stop_reason=stop_reason,
            iteration_count=iterations,
            consecutive_no_yield=consecutive_no_yield,
            malformed_proposal_streak=malformed_streak,
            yielded_count=yielded_count,
            last_graph_digest=(
                None if stopped is None else stopped.get("last_graph_digest")
            ),
        )

    def stop(self, *, topic_id: str) -> dict[str, object]:
        stopped = self._ledger.stop_worker_loop_for_topic(
            topic_id=topic_id,
            stop_reason=WorkerLoopStopReason.STOPPED_BY_OPERATOR.value,
        )
        if stopped is None:
            return {
                "topic_id": topic_id,
                "state": "idle",
                "stop_reason": "no_loop",
            }
        stopped["yield_history"] = []
        return stopped

    def status(self, *, topic_id: str) -> dict[str, object]:
        loop = self._ledger.fetch_worker_loop(topic_id=topic_id)
        if loop is None:
            return {
                "schema_id": "crb.worker_loop.status.v1",
                "topic_id": topic_id,
                "state": "idle",
                "active": False,
                "iteration_count": 0,
                "consecutive_no_yield": 0,
                "malformed_proposal_streak": 0,
                "stop_reason": None,
                "yield_history": [],
                "last_meaningful_graph_change": None,
            }
        iterations = self._ledger.list_worker_loop_iterations(loop_id=str(loop["loop_id"]))
        return {
            "schema_id": "crb.worker_loop.status.v1",
            "topic_id": topic_id,
            "loop_id": loop["loop_id"],
            "worker_id": loop["worker_id"],
            "state": loop["state"],
            "active": loop["state"] == "running",
            "started_at": loop["started_at"],
            "heartbeat_at": loop["heartbeat_at"],
            "lease_expires_at": loop["lease_expires_at"],
            "stopped_at": loop["stopped_at"],
            "stop_reason": loop["stop_reason"],
            "iteration_count": loop["iteration_count"],
            "consecutive_no_yield": loop["consecutive_no_yield"],
            "malformed_proposal_streak": loop["malformed_proposal_streak"],
            "last_queue_item_id": loop["last_queue_item_id"],
            "last_run_id": loop["last_run_id"],
            "last_graph_digest": loop["last_graph_digest"],
            "last_meaningful_graph_change": loop["last_meaningful_change"],
            "yield_history": loop["yield_history"],
            "iterations": iterations,
        }

    def _graph_summary(self, topic_id: str) -> GraphChangeSummary:
        graph = self._ledger.fetch_latest_canonical_graph_write(topic_id=topic_id)
        if graph is None:
            return GraphChangeSummary(
                graph_digest=None,
                node_count=None,
                edge_count=None,
            )
        return GraphChangeSummary(
            graph_digest=str(graph["graph_digest"]),
            node_count=int(graph["node_count"]),
            edge_count=int(graph["edge_count"]),
        )
