from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

import pytest

from codex_continual_research_bot.contracts import (
    InteractiveRunStatus,
    InteractiveRunTriggerRequest,
    ProposalBundle,
    QueueJob,
    QueueJobKind,
    QueueJobState,
    RunIntent,
    RunLifecycleState,
    RunMode,
    TopicSnapshot,
    UserInputKind,
)
from codex_continual_research_bot.interactive import (
    InteractiveRunService,
    InteractiveWorkspaceMismatchError,
    InvalidInteractiveUserInputError,
)
from codex_continual_research_bot.orchestrator import RunOrchestrator, StaleTopicSnapshotError
from codex_continual_research_bot.persistence import SQLitePersistenceLedger
from codex_continual_research_bot.runtime import (
    RuntimeExecutionResult,
    RuntimeMetrics,
)


ROOT = Path(__file__).resolve().parent.parent


@dataclass
class FakeRuntime:
    proposal: ProposalBundle
    intents: list[RunIntent] = field(default_factory=list)

    def execute(self, intent: RunIntent) -> RuntimeExecutionResult:
        self.intents.append(intent)
        return RuntimeExecutionResult(
            run_id=intent.run_id,
            proposal=self.proposal,
            metrics=RuntimeMetrics(
                raw_event_count=1,
                normalized_event_count=4,
                artifact_count=3,
                exit_code=0,
                timed_out=False,
            ),
            artifacts_dir=ROOT / "tmp-artifacts" / intent.run_id,
        )


def make_topic_snapshot(*, version: int = 1) -> TopicSnapshot:
    return TopicSnapshot.model_validate(
        {
            "topic_id": "topic_001",
            "snapshot_version": version,
            "topic_summary": "Topic tracks interactive run path safety.",
            "current_best_hypotheses": [
                {
                    "hypothesis_id": "hyp_001",
                    "title": "Interactive runs must persist canonical updates",
                    "summary": "A run is only successful when report and backend update agree.",
                }
            ],
            "challenger_targets": [
                {
                    "hypothesis_id": "hyp_001",
                    "title": "Interactive runs must persist canonical updates",
                    "summary": "The current best hypothesis remains the attack target.",
                }
            ],
            "active_conflicts": [
                {
                    "conflict_id": "conf_001",
                    "summary": "A UI summary can drift from backend persistence state.",
                }
            ],
            "open_questions": ["Can the interactive path fail closed on stale snapshots?"],
            "recent_provenance_digest": "sha256:interactive-snapshot",
            "queued_user_inputs": [
                {
                    "user_input_id": "uin_001",
                    "input_type": "counterargument",
                    "summary": "Backend state update could fail after the user summary is shown.",
                    "submitted_at": "2026-04-19T10:30:00Z",
                }
            ],
        }
    )


def make_valid_proposal() -> ProposalBundle:
    payload = json.loads((ROOT / "fixtures" / "proposal_bundle.json").read_text())
    payload["arguments"].append(
        {
            "argument_id": "arg_002",
            "stance": "challenge",
            "target_hypothesis_id": "hyp_001",
            "claim_ids": ["claim_001"],
            "rationale": "The interactive path must prove persistence before success.",
        }
    )
    payload["conflict_assessments"][0]["status"] = "escalated"
    return ProposalBundle.model_validate(payload)


def make_malformed_canonical_proposal() -> ProposalBundle:
    payload = make_valid_proposal().model_dump(mode="json")
    payload["claims"][0]["temporal_scope"] = "unknown"
    return ProposalBundle.model_validate(payload)


def make_ledger(tmp_path: Path, *, snapshot_version: int = 1) -> SQLitePersistenceLedger:
    ledger = SQLitePersistenceLedger(tmp_path / "interactive.sqlite3")
    ledger.initialize()
    ledger.create_topic(topic_id="topic_001", slug="interactive", title="Interactive")
    ledger.store_topic_snapshot(make_topic_snapshot(version=snapshot_version))
    return ledger


def make_request(*, workspace_root: Path = ROOT, version: int = 1) -> InteractiveRunTriggerRequest:
    return InteractiveRunTriggerRequest(
        topic_id="topic_001",
        trigger_id="trigger_001",
        user_input="counterargument: backend update may fail after summary display",
        expected_snapshot_version=version,
        workspace_root=str(workspace_root),
    )


def make_service(
    ledger: SQLitePersistenceLedger,
    runtime: FakeRuntime,
    *,
    workspace_root: Path = ROOT,
) -> InteractiveRunService:
    return InteractiveRunService(
        ledger,
        runtime=runtime,
        workspace_root=workspace_root,
    )


def test_happy_path_interactive_e2e_persists_report_and_state_update(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    runtime = FakeRuntime(make_valid_proposal())
    service = make_service(ledger, runtime)

    response = service.trigger_run(make_request())

    assert response.topic.snapshot_version == 1
    assert response.report.status == InteractiveRunStatus.COMPLETED
    assert response.report.user_input_kind == UserInputKind.COUNTERARGUMENT
    assert response.report.backend_state_update is not None
    run = ledger.fetch_run(response.report.run_id)
    queue = ledger.fetch_queue_item(runtime.intents[0].queue_item_id or "")
    graph_write = ledger.fetch_canonical_graph_write(response.report.run_id)
    assert run is not None
    assert queue is not None
    assert graph_write is not None
    assert run["mode"] == RunMode.INTERACTIVE.value
    assert run["status"] == RunLifecycleState.COMPLETED.value
    assert queue["state"] == QueueJobState.COMPLETED.value


def test_invalid_user_input_classification_fails_before_queue(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    service = make_service(ledger, FakeRuntime(make_valid_proposal()))
    request = make_request().model_copy(update={"user_input": "please research this"})

    with pytest.raises(InvalidInteractiveUserInputError):
        service.trigger_run(request)

    assert ledger.fetch_next_claimable_queue_item() is None


def test_workspace_mismatch_fails_closed_before_queue(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    service = make_service(ledger, FakeRuntime(make_valid_proposal()))

    with pytest.raises(InteractiveWorkspaceMismatchError):
        service.trigger_run(make_request(workspace_root=tmp_path / "other"))

    assert ledger.fetch_next_claimable_queue_item() is None


def test_duplicate_trigger_returns_existing_report_without_reexecution(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    runtime = FakeRuntime(make_valid_proposal())
    service = make_service(ledger, runtime)
    request = make_request()

    first = service.trigger_run(request)
    second = service.trigger_run(request)

    assert second.duplicate is True
    assert second.resumed is False
    assert second.report == first.report
    assert len(runtime.intents) == 1


def test_duplicate_trigger_returns_existing_report_after_topic_advances(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    runtime = FakeRuntime(make_valid_proposal())
    service = make_service(ledger, runtime)
    request = make_request()
    first = service.trigger_run(request)
    ledger.store_topic_snapshot(make_topic_snapshot(version=2))

    second = service.trigger_run(request)

    assert second.duplicate is True
    assert second.resumed is False
    assert second.topic.snapshot_version == first.topic.snapshot_version
    assert second.report == first.report
    assert len(runtime.intents) == 1


def test_malformed_proposal_is_quarantined_without_graph_write(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    runtime = FakeRuntime(make_malformed_canonical_proposal())
    service = make_service(ledger, runtime)

    response = service.trigger_run(make_request())

    assert response.report.status == InteractiveRunStatus.QUARANTINED
    assert response.report.backend_state_update is None
    assert response.report.operator_failure_summary is not None
    assert response.report.operator_failure_summary.quarantine_reasons
    assert ledger.fetch_canonical_graph_write(response.report.run_id) is None
    run = ledger.fetch_run(response.report.run_id)
    assert run is not None
    assert run["status"] == RunLifecycleState.DEAD_LETTER.value


def test_stale_snapshot_during_interactive_run_is_rejected_before_queue(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path, snapshot_version=1)
    ledger.store_topic_snapshot(make_topic_snapshot(version=2))
    service = make_service(ledger, FakeRuntime(make_valid_proposal()))

    with pytest.raises(StaleTopicSnapshotError):
        service.trigger_run(make_request(version=1))

    assert ledger.fetch_next_claimable_queue_item() is None


def test_user_visible_summary_matches_backend_state_update(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    proposal = make_valid_proposal()
    service = make_service(ledger, FakeRuntime(proposal))

    response = service.trigger_run(make_request())

    graph_write = ledger.fetch_canonical_graph_write(response.report.run_id)
    assert graph_write is not None
    assert response.report.summary == proposal.summary_draft
    assert response.report.backend_state_update is not None
    assert response.report.backend_state_update.graph_digest == graph_write["graph_digest"]
    assert response.report.backend_state_update.node_count == graph_write["node_count"]
    assert response.report.backend_state_update.edge_count == graph_write["edge_count"]


def test_interrupted_run_resume_uses_existing_run_without_duplicate_write(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    runtime = FakeRuntime(make_valid_proposal())
    service = make_service(ledger, runtime)
    request = make_request()
    ids = service._ids_for_request(request)
    user_input_kind = service._classify_user_input(request.user_input)
    request_digest = service._request_digest(
        request=request,
        user_input_kind=user_input_kind,
    )
    ledger.reserve_idempotency_key(
        idempotency_key=ids["idempotency_key"],
        scope=QueueJobKind.RUN_EXECUTE.value,
        request_digest=request_digest,
    )
    ledger.enqueue_job(
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
            available_at="2026-04-19T00:00:00Z",
            payload={
                "initiator": "interactive",
                "objective": "Resume an interrupted interactive run.",
                "selected_queue_item_ids": [ids["queue_item_id"]],
            },
            last_failure=None,
        )
    )
    RunOrchestrator(ledger).start_queued_run(
        queue_item_id=ids["queue_item_id"],
        run_id=ids["run_id"],
        worker_id="interactive-api",
        mode=RunMode.INTERACTIVE,
        expected_snapshot_version=1,
    )
    ledger.store_topic_snapshot(make_topic_snapshot(version=2))

    resumed = service.trigger_run(request)
    duplicate = service.trigger_run(request)

    assert resumed.duplicate is True
    assert resumed.resumed is True
    assert duplicate.duplicate is True
    assert duplicate.resumed is False
    assert len(runtime.intents) == 1
    assert resumed.report.run_id == ids["run_id"]
    assert ledger.fetch_canonical_graph_write(ids["run_id"]) is not None
