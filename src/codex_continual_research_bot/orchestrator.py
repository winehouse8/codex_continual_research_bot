"""Phase 3 topic snapshot read model and run orchestrator state machine."""

from __future__ import annotations

import json
from typing import Any, Final

from codex_continual_research_bot.contracts import (
    ArgumentStance,
    ConflictStatus,
    ExecutionBudgets,
    FrontierSelectionInput,
    OutputContract,
    ProposalBundle,
    QueueSelection,
    RevisionAction,
    RunExecutionRequest,
    RunIntent,
    RunLifecycleState,
    RunMode,
    RunPlan,
    SandboxMode,
    ToolPolicy,
    NetworkMode,
    TopicSnapshot,
)
from codex_continual_research_bot.persistence import SQLitePersistenceLedger


class RunOrchestratorError(RuntimeError):
    """Base error for Phase 3 orchestration failures."""


class InvalidRunTransitionError(RunOrchestratorError):
    """Raised when a run state transition is not allowed by the state machine."""


class MissingTopicSnapshotError(RunOrchestratorError):
    """Raised when a run cannot load a persisted topic snapshot."""


class StaleTopicSnapshotError(RunOrchestratorError):
    """Raised when the caller tries to run against an older snapshot version."""


class CompetitionValidationError(RunOrchestratorError):
    """Raised when a proposal does not satisfy the minimum competition loop."""


STATE_TRANSITIONS: Final[dict[RunLifecycleState, frozenset[RunLifecycleState]]] = {
    RunLifecycleState.QUEUED: frozenset({RunLifecycleState.LOADING_STATE}),
    RunLifecycleState.LOADING_STATE: frozenset(
        {RunLifecycleState.SELECTING_FRONTIER, RunLifecycleState.FAILED}
    ),
    RunLifecycleState.SELECTING_FRONTIER: frozenset({RunLifecycleState.PLANNING}),
    RunLifecycleState.PLANNING: frozenset({RunLifecycleState.ATTACKING_CURRENT_BEST}),
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


DEFAULT_TOOL_POLICY: Final = ToolPolicy(
    allowed_tools=["web.search", "web.fetch", "internal.graph_query"],
    network_mode=NetworkMode.RESTRICTED,
    sandbox_mode=SandboxMode.WORKSPACE_WRITE,
)
DEFAULT_OUTPUT_CONTRACT: Final = OutputContract(
    schema_id="research_run_v1",
    max_repair_attempts=2,
)
DEFAULT_BUDGETS: Final = ExecutionBudgets(
    max_turns=12,
    max_tool_calls=40,
    max_runtime_seconds=1800,
    soft_input_tokens=120000,
    hard_input_tokens=180000,
)


class RunStateMachine:
    """Persisted run lifecycle transition guard."""

    def __init__(self, ledger: SQLitePersistenceLedger) -> None:
        self._ledger = ledger

    def transition(
        self,
        *,
        run_id: str,
        to_state: RunLifecycleState,
        snapshot_version: int | None = None,
    ) -> None:
        run = self._ledger.fetch_run(run_id)
        if run is None:
            raise KeyError(f"run {run_id} does not exist")

        from_state = RunLifecycleState(run["status"])
        if to_state not in STATE_TRANSITIONS[from_state]:
            raise InvalidRunTransitionError(
                f"cannot transition run {run_id} from {from_state.value} to {to_state.value}"
            )
        self._ledger.transition_run_state(
            run_id=run_id,
            state=to_state,
            snapshot_version=snapshot_version,
        )


class RunOrchestrator:
    """Builds run intents from persisted snapshots and advances run state."""

    def __init__(
        self,
        ledger: SQLitePersistenceLedger,
        *,
        tool_policy: ToolPolicy = DEFAULT_TOOL_POLICY,
        output_contract: OutputContract = DEFAULT_OUTPUT_CONTRACT,
        budgets: ExecutionBudgets = DEFAULT_BUDGETS,
    ) -> None:
        self._ledger = ledger
        self._state_machine = RunStateMachine(ledger)
        self._tool_policy = tool_policy
        self._output_contract = output_contract
        self._budgets = budgets

    def start_queued_run(
        self,
        *,
        queue_item_id: str,
        run_id: str,
        worker_id: str,
        mode: RunMode = RunMode.SCHEDULED,
        expected_snapshot_version: int | None = None,
    ) -> RunIntent:
        claimed = self._ledger.claim_queue_item_for_run(
            queue_item_id=queue_item_id,
            worker_id=worker_id,
            run_id=run_id,
            mode=mode.value,
        )
        if claimed is None:
            raise KeyError(f"queue item {queue_item_id} is not claimable")

        run = self._ledger.fetch_run(run_id)
        if run is None:
            raise KeyError(f"run {run_id} does not exist after claim")
        if RunLifecycleState(run["status"]) != RunLifecycleState.QUEUED:
            return self.resume_run(
                run_id=run_id,
                expected_snapshot_version=expected_snapshot_version,
            )

        self._state_machine.transition(
            run_id=run_id,
            to_state=RunLifecycleState.LOADING_STATE,
        )
        try:
            snapshot = self.load_topic_snapshot(
                topic_id=claimed.topic_id,
                expected_snapshot_version=expected_snapshot_version,
            )
        except (MissingTopicSnapshotError, StaleTopicSnapshotError):
            self._state_machine.transition(
                run_id=run_id,
                to_state=RunLifecycleState.FAILED,
            )
            raise

        self._state_machine.transition(
            run_id=run_id,
            to_state=RunLifecycleState.SELECTING_FRONTIER,
            snapshot_version=snapshot.snapshot_version,
        )
        frontier = self.build_frontier_selection_input(
            snapshot=snapshot,
            queue_item_id=claimed.queue_item_id,
        )
        self._state_machine.transition(
            run_id=run_id,
            to_state=RunLifecycleState.PLANNING,
        )
        self._state_machine.transition(
            run_id=run_id,
            to_state=RunLifecycleState.ATTACKING_CURRENT_BEST,
        )
        self._state_machine.transition(
            run_id=run_id,
            to_state=RunLifecycleState.GENERATING_CHALLENGERS,
        )
        self._state_machine.transition(
            run_id=run_id,
            to_state=RunLifecycleState.CODEX_EXECUTING,
        )

        return self.build_run_intent(
            run_id=run_id,
            queue_item_id=claimed.queue_item_id,
            mode=mode,
            snapshot=snapshot,
            frontier=frontier,
        )

    def resume_run(
        self,
        *,
        run_id: str,
        expected_snapshot_version: int | None = None,
    ) -> RunIntent:
        run = self._ledger.fetch_run(run_id)
        if run is None:
            raise KeyError(f"run {run_id} does not exist")
        state = RunLifecycleState(run["status"])
        if state in {RunLifecycleState.COMPLETED, RunLifecycleState.DEAD_LETTER}:
            raise InvalidRunTransitionError(f"run {run_id} is terminal")

        snapshot_version = run.get("snapshot_version")
        if (
            snapshot_version is not None
            and expected_snapshot_version is not None
            and snapshot_version != expected_snapshot_version
        ):
            raise StaleTopicSnapshotError(
                "snapshot version mismatch: "
                f"run uses {snapshot_version}, caller expected {expected_snapshot_version}"
            )
        snapshot = self.load_topic_snapshot(
            topic_id=run["topic_id"],
            snapshot_version=snapshot_version,
            expected_snapshot_version=(
                None if snapshot_version is not None else expected_snapshot_version
            ),
        )
        frontier = self.build_frontier_selection_input(
            snapshot=snapshot,
            queue_item_id=run["queue_item_id"],
        )
        intent = self.build_run_intent(
            run_id=run_id,
            queue_item_id=run["queue_item_id"],
            mode=RunMode(run["mode"]),
            snapshot=snapshot,
            frontier=frontier,
        )
        return intent.model_copy(update={"lifecycle_state": state})

    def load_topic_snapshot(
        self,
        *,
        topic_id: str,
        snapshot_version: int | None = None,
        expected_snapshot_version: int | None = None,
    ) -> TopicSnapshot:
        if snapshot_version is not None:
            snapshot = self._ledger.fetch_topic_snapshot(
                topic_id=topic_id,
                snapshot_version=snapshot_version,
            )
            if snapshot is None:
                raise MissingTopicSnapshotError(
                    f"topic {topic_id} has no persisted snapshot version {snapshot_version}"
                )
            return snapshot

        latest = self._ledger.fetch_topic_snapshot(topic_id=topic_id)
        if latest is None:
            raise MissingTopicSnapshotError(f"topic {topic_id} has no persisted snapshot")
        if (
            expected_snapshot_version is not None
            and latest.snapshot_version != expected_snapshot_version
        ):
            raise StaleTopicSnapshotError(
                "snapshot version mismatch: "
                f"expected {expected_snapshot_version}, latest is {latest.snapshot_version}"
            )
        return latest

    def build_frontier_selection_input(
        self,
        *,
        snapshot: TopicSnapshot,
        queue_item_id: str,
    ) -> FrontierSelectionInput:
        selected_queue_items = [self._queue_selection_from_row(queue_item_id)]
        return FrontierSelectionInput(
            topic_id=snapshot.topic_id,
            snapshot_version=snapshot.snapshot_version,
            current_best_hypotheses=snapshot.current_best_hypotheses,
            challenger_targets=snapshot.challenger_targets,
            active_conflicts=snapshot.active_conflicts,
            open_questions=snapshot.open_questions,
            selected_queue_items=selected_queue_items,
            queued_user_inputs=snapshot.queued_user_inputs,
            requires_current_best_attack=True,
            requires_challenger_generation=True,
            requires_reconciliation_or_retirement=True,
        )

    def build_run_intent(
        self,
        *,
        run_id: str,
        queue_item_id: str,
        mode: RunMode,
        snapshot: TopicSnapshot,
        frontier: FrontierSelectionInput,
    ) -> RunIntent:
        queue_row = self._require_queue_row(queue_item_id)
        payload = json.loads(queue_row["payload_json"])
        request = RunExecutionRequest(
            run_id=run_id,
            topic_id=snapshot.topic_id,
            mode=mode,
            objective=payload["objective"],
            plan=RunPlan(
                must_attack_current_best=True,
                must_generate_challenger=True,
                must_collect_support_and_challenge=True,
            ),
            context_snapshot={
                "topic_summary": snapshot.topic_summary,
                "current_best_hypotheses": snapshot.current_best_hypotheses,
                "challenger_targets": snapshot.challenger_targets,
                "active_conflicts": snapshot.active_conflicts,
                "open_questions": snapshot.open_questions,
                "recent_provenance_digest": snapshot.recent_provenance_digest,
                "selected_queue_items": frontier.selected_queue_items,
                "queued_user_inputs": snapshot.queued_user_inputs,
            },
            tool_policy=self._tool_policy,
            output_contract=self._output_contract,
            budgets=self._budgets,
            idempotency_key=queue_row["idempotency_key"],
        )
        run = self._ledger.fetch_run(run_id)
        state = (
            RunLifecycleState.CODEX_EXECUTING
            if run is None
            else RunLifecycleState(run["status"])
        )
        return RunIntent(
            run_id=run_id,
            topic_id=snapshot.topic_id,
            queue_item_id=queue_item_id,
            mode=mode,
            snapshot_version=snapshot.snapshot_version,
            lifecycle_state=state,
            frontier=frontier,
            execution_request=request,
        )

    def validate_proposal_for_competition(
        self,
        *,
        intent: RunIntent,
        proposal: ProposalBundle,
    ) -> None:
        current_best_ids = {
            hypothesis.hypothesis_id
            for hypothesis in intent.frontier.current_best_hypotheses
        }
        challenger_target_ids = {
            hypothesis.hypothesis_id
            for hypothesis in intent.frontier.challenger_targets
        }
        attack_target_ids = current_best_ids | challenger_target_ids
        has_current_best_attack = any(
            argument.stance == ArgumentStance.CHALLENGE
            and argument.target_hypothesis_id in current_best_ids
            for argument in proposal.arguments
        )
        if intent.frontier.requires_current_best_attack and not has_current_best_attack:
            raise CompetitionValidationError(
                "proposal must include a challenge argument against the current best hypothesis"
            )

        has_support_argument = any(
            argument.stance == ArgumentStance.SUPPORT
            and argument.target_hypothesis_id in attack_target_ids
            for argument in proposal.arguments
        )
        if (
            intent.execution_request.plan.must_collect_support_and_challenge
            and not has_support_argument
        ):
            raise CompetitionValidationError(
                "proposal must include support and challenge arguments for the selected hypothesis targets"
            )

        if (
            intent.frontier.requires_challenger_generation
            and not proposal.challenger_hypotheses
        ):
            raise CompetitionValidationError(
                "proposal must generate at least one challenger hypothesis"
            )

        has_reconciliation = any(
            assessment.status in {ConflictStatus.RECONCILED, ConflictStatus.ESCALATED}
            for assessment in proposal.conflict_assessments
        )
        has_retirement_or_revision_pressure = any(
            revision.action
            in {
                RevisionAction.WEAKEN,
                RevisionAction.RETIRE,
                RevisionAction.SUPERSEDE,
            }
            for revision in proposal.revision_proposals
        )
        if (
            intent.frontier.requires_reconciliation_or_retirement
            and not has_reconciliation
            and not has_retirement_or_revision_pressure
        ):
            raise CompetitionValidationError(
                "proposal must reconcile, escalate, weaken, retire, or supersede a hypothesis"
            )

    def _queue_selection_from_row(self, queue_item_id: str) -> QueueSelection:
        row = self._require_queue_row(queue_item_id)
        payload = json.loads(row["payload_json"])
        return QueueSelection(
            queue_item_id=row["id"],
            kind=row["kind"],
            summary=payload["objective"],
        )

    def _require_queue_row(self, queue_item_id: str) -> dict[str, Any]:
        row = self._ledger.fetch_queue_item(queue_item_id)
        if row is None:
            raise KeyError(f"queue item {queue_item_id} does not exist")
        return row
