from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from codex_continual_research_bot.codex_app_server_inspector import (
    AuthJsonInspection,
    CodexAppServerInspector,
)
from codex_continual_research_bot.contracts import (
    FailureCode,
    ProposalBundle,
    QueueJob,
    QueueJobKind,
    QueuePayload,
    QueueJobState,
    RunIntent,
    RunLifecycleState,
    RunMode,
    SessionInspectResult,
    SessionState,
    TopicSnapshot,
    derive_principal_fingerprint,
)
from codex_continual_research_bot.credential_locator import (
    credential_locator_for_principal,
)
from codex_continual_research_bot.persistence import (
    SessionLeaseRecord,
    SQLitePersistenceLedger,
)
from codex_continual_research_bot.output_validation import append_proposal_safety_clause
from codex_continual_research_bot.runtime import (
    CodexRuntimeError,
    CodexTransportTimeoutError,
    RuntimeExecutionResult,
    RuntimeMetrics,
)
from codex_continual_research_bot.scheduled import (
    ScheduledOperatorNotification,
    ScheduledRunAction,
    ScheduledRunPolicy,
    ScheduledRunService,
)
from codex_continual_research_bot.scheduler import TopicScheduleCandidate
from codex_continual_research_bot.session_manager import SessionManager


ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 4, 19, 11, 5, tzinfo=timezone.utc)
LAST_REFRESH = datetime(2026, 4, 19, 10, 45, tzinfo=timezone.utc)


@dataclass
class FakeAppServerClient:
    email: str = "researcher@example.com"
    account_type: str = "chatgpt"
    plan_type: str = "max"
    workspace_id: str = "ws_123456"
    trusted_project_paths: tuple[str, ...] = ()

    def account_read(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "type": self.account_type,
            "planType": self.plan_type,
            "requiresOpenaiAuth": False,
        }

    def config_read(self) -> dict[str, Any]:
        return {
            "forced_login_method": "chatgpt",
            "forced_chatgpt_workspace_id": self.workspace_id,
            "trusted_project_paths": list(self.trusted_project_paths),
        }


@dataclass
class FakeScheduledRuntime:
    proposal: ProposalBundle
    failures: list[CodexRuntimeError] = field(default_factory=list)
    intents: list[RunIntent] = field(default_factory=list)
    leases: list[SessionLeaseRecord] = field(default_factory=list)

    def execute(
        self,
        intent: RunIntent,
        lease: SessionLeaseRecord,
    ) -> RuntimeExecutionResult:
        self.intents.append(intent)
        self.leases.append(lease)
        if self.failures:
            raise self.failures.pop(0)
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


@dataclass
class RecordingNotifier:
    notifications: list[ScheduledOperatorNotification] = field(default_factory=list)

    def notify(self, notification: ScheduledOperatorNotification) -> None:
        self.notifications.append(notification)


def fingerprint(
    *,
    email: str = "researcher@example.com",
    workspace_id: str = "ws_123456",
) -> str:
    return derive_principal_fingerprint(
        email=email,
        account_type="chatgpt",
        workspace_id=workspace_id,
    )


def make_paths(
    tmp_path: Path,
    *,
    principal_fingerprint: str | None = None,
) -> tuple[str, str]:
    fp = principal_fingerprint or fingerprint()
    base_dir = tmp_path / "runner"
    locator = credential_locator_for_principal(
        base_dir=base_dir,
        principal_fingerprint=fp,
    )
    workspace_root = (
        base_dir
        / "principals"
        / fp
        / "worktrees"
        / "codex_continual_research_bot"
    )
    return locator, str(workspace_root.resolve())


def make_inspection(
    tmp_path: Path,
    *,
    session_id: str = "sess_001",
    principal_id: str = "user_01",
    email: str = "researcher@example.com",
    workspace_id: str = "ws_123456",
    expected_workspace_id: str | None = "ws_123456",
    host_id: str = "runner-seoul-01",
    last_refresh: datetime | None = LAST_REFRESH,
) -> SessionInspectResult:
    fp = fingerprint(email=email, workspace_id=workspace_id)
    locator, workspace_root = make_paths(tmp_path, principal_fingerprint=fp)
    client = FakeAppServerClient(
        email=email,
        workspace_id=workspace_id,
        trusted_project_paths=(workspace_root,),
    )
    inspector = CodexAppServerInspector(
        client=client,
        stale_after=timedelta(hours=24),
    )
    return inspector.inspect(
        session_id=session_id,
        principal_id=principal_id,
        purpose="scheduled_run",
        host_id=host_id,
        credential_locator=locator,
        workspace_root=workspace_root,
        expected_workspace_id=expected_workspace_id,
        auth_json=AuthJsonInspection(
            auth_mode="chatgpt",
            last_refresh=last_refresh,
            has_access_token=True,
            has_id_token=True,
            has_refresh_token=True,
        ),
        now=NOW,
    )


def make_topic_snapshot() -> TopicSnapshot:
    return TopicSnapshot.model_validate(
        {
            "topic_id": "topic_001",
            "snapshot_version": 1,
            "topic_summary": "Topic tracks scheduled run path safety.",
            "current_best_hypotheses": [
                {
                    "hypothesis_id": "hyp_001",
                    "title": "Scheduled runs must reuse interactive validation",
                    "summary": "A scheduled run can only persist after competition validation.",
                }
            ],
            "challenger_targets": [
                {
                    "hypothesis_id": "hyp_001",
                    "title": "Scheduled runs must reuse interactive validation",
                    "summary": "The current best hypothesis remains the attack target.",
                }
            ],
            "active_conflicts": [
                {
                    "conflict_id": "conf_001",
                    "summary": "A scheduled worker could otherwise skip preflight.",
                }
            ],
            "open_questions": ["Can headless execution fail closed on auth drift?"],
            "recent_provenance_digest": "sha256:scheduled-snapshot",
            "queued_user_inputs": [
                {
                    "user_input_id": "uin_001",
                    "input_type": "counterargument",
                    "summary": "Headless execution may run under the wrong principal.",
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
            "rationale": "The scheduled path must prove auth and persistence first.",
        }
    )
    payload["conflict_assessments"][0]["status"] = "escalated"
    return ProposalBundle.model_validate(payload)


def make_queue_job(
    *,
    queue_item_id: str = "scheduled_queue_001",
    run_id: str = "scheduled_run_001",
    attempts: int = 0,
    max_attempts: int = 3,
) -> QueueJob:
    return QueueJob(
        queue_item_id=queue_item_id,
        kind=QueueJobKind.RUN_EXECUTE,
        state=QueueJobState.QUEUED,
        topic_id="topic_001",
        requested_run_id=run_id,
        dedupe_key=f"scheduled:topic_001:{queue_item_id}",
        idempotency_key=f"scheduled.run:topic_001:{queue_item_id}:v1",
        priority=100,
        attempts=attempts,
        max_attempts=max_attempts,
        available_at=NOW,
        payload={
            "initiator": "scheduler",
            "objective": "Scheduled competition refresh for topic_001.",
            "selected_queue_item_ids": [queue_item_id],
        },
        last_failure=None,
    )


def make_ledger(tmp_path: Path, *jobs: QueueJob) -> SQLitePersistenceLedger:
    ledger = SQLitePersistenceLedger(tmp_path / "scheduled.sqlite3")
    ledger.initialize()
    ledger.create_topic(topic_id="topic_001", slug="scheduled", title="Scheduled")
    ledger.store_topic_snapshot(make_topic_snapshot())
    for job in jobs or (make_queue_job(),):
        ledger.reserve_idempotency_key(
            idempotency_key=job.idempotency_key,
            scope=job.kind.value,
            request_digest=f"sha256:{job.queue_item_id}",
        )
        ledger.enqueue_job(job)
    return ledger


def make_service(
    tmp_path: Path,
    ledger: SQLitePersistenceLedger,
    manager: SessionManager,
    runtime: FakeScheduledRuntime,
    *,
    notifier: RecordingNotifier | None = None,
    max_consecutive_stagnant_runs: int = 3,
) -> ScheduledRunService:
    return ScheduledRunService(
        ledger,
        session_manager=manager,
        runtime=runtime,
        session_id="sess_001",
        host_id="runner-seoul-01",
        policy=ScheduledRunPolicy(
            trusted_host_ids=frozenset({"runner-seoul-01"}),
            max_consecutive_stagnant_runs=max_consecutive_stagnant_runs,
        ),
        notifier=notifier,
    )


def bootstrap_manager(
    tmp_path: Path,
    ledger: SQLitePersistenceLedger,
) -> SessionManager:
    manager = SessionManager(ledger)
    manager.bootstrap_interactive_session(make_inspection(tmp_path))
    return manager


def test_happy_path_scheduled_e2e_reuses_validation_and_persistence(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    manager = bootstrap_manager(tmp_path, ledger)
    runtime = FakeScheduledRuntime(make_valid_proposal())
    service = make_service(tmp_path, ledger, manager, runtime)

    result = service.execute_item(
        queue_item_id="scheduled_queue_001",
        run_id="scheduled_run_001",
        load_inspection=lambda: make_inspection(tmp_path),
        now=NOW,
    )

    assert result.action == ScheduledRunAction.COMPLETED
    assert result.report is not None
    assert runtime.intents[0].mode == RunMode.SCHEDULED
    assert runtime.leases[0].purpose == "scheduled_run"
    run = ledger.fetch_run("scheduled_run_001")
    queue = ledger.fetch_queue_item("scheduled_queue_001")
    graph_write = ledger.fetch_canonical_graph_write("scheduled_run_001")
    session = ledger.fetch_session_record("sess_001")
    assert run is not None
    assert queue is not None
    assert graph_write is not None
    assert session is not None
    assert run["status"] == RunLifecycleState.COMPLETED.value
    assert queue["state"] == QueueJobState.COMPLETED.value
    assert session["lease_count"] == 0
    assert result.report.backend_state_update is not None
    assert result.report.backend_state_update.graph_digest == graph_write["graph_digest"]


def test_default_policy_rejects_headless_run_without_trusted_host(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    manager = bootstrap_manager(tmp_path, ledger)
    runtime = FakeScheduledRuntime(make_valid_proposal())
    service = ScheduledRunService(
        ledger,
        session_manager=manager,
        runtime=runtime,
        session_id="sess_001",
        host_id="runner-seoul-01",
    )

    result = service.execute_item(
        queue_item_id="scheduled_queue_001",
        run_id="scheduled_run_001",
        load_inspection=lambda: make_inspection(tmp_path),
        now=NOW,
    )

    queue = ledger.fetch_queue_item("scheduled_queue_001")
    assert queue is not None
    assert result.action == ScheduledRunAction.TERMINAL_FAILED
    assert result.failure_code == FailureCode.EXECUTION_POLICY_REJECTED
    assert queue["state"] == QueueJobState.DEAD_LETTER.value
    assert ledger.fetch_run("scheduled_run_001") is None
    assert runtime.intents == []


def test_session_expired_preflight_rejects_before_run_and_notifies(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    manager = bootstrap_manager(tmp_path, ledger)
    notifier = RecordingNotifier()
    service = make_service(
        tmp_path,
        ledger,
        manager,
        FakeScheduledRuntime(make_valid_proposal()),
        notifier=notifier,
    )

    result = service.execute_item(
        queue_item_id="scheduled_queue_001",
        run_id="scheduled_run_001",
        load_inspection=lambda: make_inspection(
            tmp_path,
            last_refresh=NOW - timedelta(days=2),
        ),
        now=NOW,
    )

    queue = ledger.fetch_queue_item("scheduled_queue_001")
    session = ledger.fetch_session_record("sess_001")
    assert queue is not None
    assert session is not None
    assert result.action == ScheduledRunAction.TERMINAL_FAILED
    assert result.failure_code == FailureCode.STALE_SESSION
    assert queue["state"] == QueueJobState.DEAD_LETTER.value
    assert queue["last_failure_retryable"] == 0
    assert session["state"] == SessionState.REAUTH_REQUIRED.value
    assert ledger.fetch_run("scheduled_run_001") is None
    assert notifier.notifications[-1].failure_code == FailureCode.STALE_SESSION


def test_no_active_lease_available_defers_without_starting_run(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    manager = bootstrap_manager(tmp_path, ledger)
    manager.acquire_execution_lease(
        session_id="sess_001",
        purpose="interactive_run",
        holder="interactive-api",
        host_id="runner-seoul-01",
        ttl_seconds=300,
        now=NOW,
    )
    service = make_service(
        tmp_path,
        ledger,
        manager,
        FakeScheduledRuntime(make_valid_proposal()),
    )

    result = service.execute_item(
        queue_item_id="scheduled_queue_001",
        run_id="scheduled_run_001",
        load_inspection=lambda: make_inspection(tmp_path),
        now=NOW,
    )

    queue = ledger.fetch_queue_item("scheduled_queue_001")
    assert queue is not None
    assert result.action == ScheduledRunAction.DEFERRED
    assert result.failure_code == FailureCode.CONCURRENT_SESSION_MUTATION
    assert queue["state"] == QueueJobState.QUEUED.value
    assert queue["attempts"] == 1
    assert ledger.fetch_run("scheduled_run_001") is None


def test_principal_mismatch_blocks_scheduled_run_before_lease(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    manager = bootstrap_manager(tmp_path, ledger)
    service = make_service(
        tmp_path,
        ledger,
        manager,
        FakeScheduledRuntime(make_valid_proposal()),
    )

    result = service.execute_item(
        queue_item_id="scheduled_queue_001",
        run_id="scheduled_run_001",
        load_inspection=lambda: make_inspection(
            tmp_path,
            session_id="sess_001",
            email="other@example.com",
        ),
        now=NOW,
    )

    queue = ledger.fetch_queue_item("scheduled_queue_001")
    assert queue is not None
    assert result.action == ScheduledRunAction.TERMINAL_FAILED
    assert result.failure_code == FailureCode.PRINCIPAL_MISMATCH
    assert queue["state"] == QueueJobState.DEAD_LETTER.value
    assert ledger.fetch_run("scheduled_run_001") is None


def test_workspace_mismatch_blocks_scheduled_run_before_lease(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    manager = bootstrap_manager(tmp_path, ledger)
    service = make_service(
        tmp_path,
        ledger,
        manager,
        FakeScheduledRuntime(make_valid_proposal()),
    )

    result = service.execute_item(
        queue_item_id="scheduled_queue_001",
        run_id="scheduled_run_001",
        load_inspection=lambda: make_inspection(
            tmp_path,
            expected_workspace_id="ws_other",
        ),
        now=NOW,
    )

    queue = ledger.fetch_queue_item("scheduled_queue_001")
    assert queue is not None
    assert result.action == ScheduledRunAction.TERMINAL_FAILED
    assert result.failure_code == FailureCode.WORKSPACE_MISMATCH
    assert queue["state"] == QueueJobState.DEAD_LETTER.value
    assert ledger.fetch_run("scheduled_run_001") is None


def test_repeated_run_stagnation_is_detected_before_enqueue(
    tmp_path: Path,
) -> None:
    ledger = SQLitePersistenceLedger(tmp_path / "scheduled-empty.sqlite3")
    ledger.initialize()
    ledger.create_topic(topic_id="topic_001", slug="scheduled", title="Scheduled")
    ledger.store_topic_snapshot(make_topic_snapshot())
    manager = bootstrap_manager(tmp_path, ledger)
    service = make_service(
        tmp_path,
        ledger,
        manager,
        FakeScheduledRuntime(make_valid_proposal()),
        max_consecutive_stagnant_runs=3,
    )

    decisions = service.enqueue_due_runs(
        [
            TopicScheduleCandidate(
                topic_id="topic_001",
                next_run_after=NOW,
                current_best_hypothesis_count=1,
                challenger_target_count=1,
                recent_challenger_count=0,
                recent_revision_count=0,
                unresolved_conflict_count=0,
                open_question_count=0,
                queued_user_input_count=0,
                support_challenge_imbalance=0.0,
                consecutive_stagnant_runs=3,
            )
        ],
        now=NOW,
    )

    assert decisions[0].action == ScheduledRunAction.DEFERRED
    assert "stagnation threshold" in decisions[0].reason
    assert ledger.fetch_next_claimable_queue_item(now=NOW) is None
    assert ledger.fetch_queue_item(decisions[0].queue_item_id or "") is None


def test_scheduler_avoids_repeated_identical_malformed_follow_up(
    tmp_path: Path,
) -> None:
    objective = append_proposal_safety_clause(
        "Scheduled competition refresh for topic_001: "
        "no recent challenger generation, no recent revision pressure"
    )
    dead_letter_job = make_queue_job(
        queue_item_id="scheduled_queue_dead",
        run_id="scheduled_run_dead",
    ).model_copy(
        update={
            "payload": QueuePayload(
                initiator="scheduler",
                objective=objective,
                selected_queue_item_ids=["scheduled_queue_dead"],
            )
        }
    )
    ledger = make_ledger(tmp_path, dead_letter_job)
    ledger.record_queue_dead_letter(
        queue_item_id="scheduled_queue_dead",
        failure_code=FailureCode.MALFORMED_PROPOSAL.value,
        detail="argument arg_001: missing claim references claim_missing",
        retryable=False,
        human_review_required=True,
    )
    manager = bootstrap_manager(tmp_path, ledger)
    service = make_service(
        tmp_path,
        ledger,
        manager,
        FakeScheduledRuntime(make_valid_proposal()),
    )

    decisions = service.enqueue_due_runs(
        [
            TopicScheduleCandidate(
                topic_id="topic_001",
                next_run_after=NOW,
                current_best_hypothesis_count=1,
                challenger_target_count=1,
                recent_challenger_count=0,
                recent_revision_count=0,
                unresolved_conflict_count=0,
                open_question_count=0,
                queued_user_input_count=0,
                support_challenge_imbalance=0.0,
                consecutive_stagnant_runs=0,
            )
        ],
        now=NOW,
    )

    assert decisions[0].action == ScheduledRunAction.DEFERRED
    assert decisions[0].failure_code == FailureCode.MALFORMED_PROPOSAL
    assert "identical malformed_proposal follow-up" in decisions[0].reason
    assert ledger.fetch_next_claimable_queue_item(now=NOW) is None


def test_retry_after_transient_transport_failure_reuses_same_run(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    manager = bootstrap_manager(tmp_path, ledger)
    runtime = FakeScheduledRuntime(
        make_valid_proposal(),
        failures=[
            CodexTransportTimeoutError(
                failure_code=FailureCode.CODEX_TRANSPORT_TIMEOUT,
                detail="codex exec timed out before final output",
                retryable=True,
            )
        ],
    )
    service = make_service(tmp_path, ledger, manager, runtime)

    first = service.execute_item(
        queue_item_id="scheduled_queue_001",
        run_id="scheduled_run_001",
        load_inspection=lambda: make_inspection(tmp_path),
        now=NOW,
    )
    second = service.execute_item(
        queue_item_id="scheduled_queue_001",
        run_id="scheduled_run_001",
        load_inspection=lambda: make_inspection(tmp_path),
        now=NOW + timedelta(minutes=5),
    )

    queue = ledger.fetch_queue_item("scheduled_queue_001")
    run = ledger.fetch_run("scheduled_run_001")
    assert queue is not None
    assert run is not None
    assert first.action == ScheduledRunAction.RETRYABLE_FAILED
    assert second.action == ScheduledRunAction.COMPLETED
    assert queue["attempts"] == 1
    assert queue["state"] == QueueJobState.COMPLETED.value
    assert run["status"] == RunLifecycleState.COMPLETED.value
    assert [intent.run_id for intent in runtime.intents] == [
        "scheduled_run_001",
        "scheduled_run_001",
    ]


def test_terminal_auth_failure_does_not_retry(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    manager = bootstrap_manager(tmp_path, ledger)
    runtime = FakeScheduledRuntime(
        make_valid_proposal(),
        failures=[
            CodexRuntimeError(
                failure_code=FailureCode.AUTH_MATERIAL_MISSING,
                detail="auth.json disappeared before codex exec launch",
                retryable=False,
            )
        ],
    )
    service = make_service(tmp_path, ledger, manager, runtime)

    result = service.execute_item(
        queue_item_id="scheduled_queue_001",
        run_id="scheduled_run_001",
        load_inspection=lambda: make_inspection(tmp_path),
        now=NOW,
    )

    queue = ledger.fetch_queue_item("scheduled_queue_001")
    run = ledger.fetch_run("scheduled_run_001")
    assert queue is not None
    assert run is not None
    assert result.action == ScheduledRunAction.TERMINAL_FAILED
    assert result.failure_code == FailureCode.AUTH_MATERIAL_MISSING
    assert queue["state"] == QueueJobState.DEAD_LETTER.value
    assert queue["attempts"] == 1
    assert queue["last_failure_retryable"] == 0
    assert run["status"] == RunLifecycleState.DEAD_LETTER.value
