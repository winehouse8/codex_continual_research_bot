from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

import pytest

from codex_continual_research_bot.contracts import (
    ExecutionBudgets,
    FailureCode,
    QueueJob,
    RuntimeEventType,
    TopicSnapshot,
)
from codex_continual_research_bot.orchestrator import RunOrchestrator
from codex_continual_research_bot.persistence import SQLitePersistenceLedger
from codex_continual_research_bot.runtime import (
    BudgetExceededError,
    CodexExecInvocation,
    CodexExecProcessResult,
    CodexProcessCrashError,
    CodexRuntimeConfig,
    CodexRuntimeCoordinator,
    CodexTransportTimeoutError,
    MalformedJSONLEventError,
    WorkspaceRootMismatchError,
)


ROOT = Path(__file__).resolve().parent.parent


@dataclass
class FakeLauncher:
    stdout_lines: tuple[str, ...]
    exit_code: int = 0
    timed_out: bool = False
    stderr: str = ""
    final_payload: str | None = None
    invocations: list[CodexExecInvocation] = field(default_factory=list)

    def run(self, invocation: CodexExecInvocation) -> CodexExecProcessResult:
        self.invocations.append(invocation)
        if self.final_payload is not None:
            invocation.final_message_path.write_text(
                self.final_payload,
                encoding="utf-8",
            )
        return CodexExecProcessResult(
            stdout_lines=self.stdout_lines,
            exit_code=self.exit_code,
            timed_out=self.timed_out,
            stderr=self.stderr,
        )


def make_topic_snapshot() -> TopicSnapshot:
    return TopicSnapshot.model_validate(
        {
            "topic_id": "topic_001",
            "snapshot_version": 1,
            "topic_summary": "Topic tracks runtime ingestion safety.",
            "current_best_hypotheses": [
                {
                    "hypothesis_id": "hyp_001",
                    "title": "Runtime ledger is authority",
                    "summary": "Codex final output must not bypass event ingestion.",
                }
            ],
            "challenger_targets": [
                {
                    "hypothesis_id": "hyp_001",
                    "title": "Runtime ledger is authority",
                    "summary": "Challenge whether final output alone is enough.",
                }
            ],
            "active_conflicts": [
                {
                    "conflict_id": "conf_001",
                    "summary": "Transport events can be lost if only artifacts are stored.",
                }
            ],
            "open_questions": ["Can JSONL replay reconstruct the attempt boundary?"],
            "recent_provenance_digest": "sha256:runtime-snapshot",
            "queued_user_inputs": [
                {
                    "user_input_id": "uin_001",
                    "input_type": "counterargument",
                    "summary": "Retry classification should distinguish model and transport failure.",
                    "submitted_at": "2026-04-19T10:30:00Z",
                }
            ],
        }
    )


def make_queue_job() -> QueueJob:
    return QueueJob.model_validate(
        {
            "queue_item_id": "queue_001",
            "kind": "run.execute",
            "state": "queued",
            "topic_id": "topic_001",
            "requested_run_id": "run_001",
            "dedupe_key": "dedupe_queue_001",
            "idempotency_key": "run.execute:run_001:v1",
            "priority": 10,
            "attempts": 0,
            "max_attempts": 5,
            "available_at": "2026-04-19T00:00:00Z",
            "payload": {
                "initiator": "scheduler",
                "objective": "Execute Codex and ingest JSONL events.",
                "selected_queue_item_ids": ["queue_001"],
            },
            "last_failure": None,
        }
    )


def make_intent(tmp_path: Path):
    ledger = SQLitePersistenceLedger(tmp_path / "runtime.sqlite3")
    ledger.initialize()
    ledger.create_topic(topic_id="topic_001", slug="runtime", title="Runtime")
    ledger.store_topic_snapshot(make_topic_snapshot())
    job = make_queue_job()
    ledger.reserve_idempotency_key(
        idempotency_key=job.idempotency_key,
        scope=job.kind.value,
        request_digest="sha256:queue_001",
    )
    ledger.enqueue_job(job)
    intent = RunOrchestrator(ledger).start_queued_run(
        queue_item_id="queue_001",
        run_id="run_001",
        worker_id="worker-a",
    )
    return ledger, intent


def make_config(tmp_path: Path, *, workspace_root: Path = ROOT) -> CodexRuntimeConfig:
    schema_path = tmp_path / "research_run_v1.json"
    schema_path.write_text("{}\n", encoding="utf-8")
    return CodexRuntimeConfig(
        workspace_root=workspace_root,
        artifact_root=tmp_path / "artifacts",
        output_schema_path=schema_path,
    )


def proposal_payload() -> str:
    return (ROOT / "fixtures" / "proposal_bundle.json").read_text(encoding="utf-8")


def raw_event(event_type: str, **extra: object) -> str:
    payload = {"type": event_type, **extra}
    return json.dumps(payload, sort_keys=True)


def event_types(ledger: SQLitePersistenceLedger) -> list[RuntimeEventType]:
    return [event.event_type for event in ledger.list_run_events("run_001")]


def test_happy_path_exec_ingestion_persists_events_and_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    ledger, intent = make_intent(tmp_path)
    launcher = FakeLauncher(
        stdout_lines=(
            raw_event("thread.started", thread_id="thread_001"),
            raw_event("turn.completed", turn_index=1),
        ),
        final_payload=proposal_payload(),
    )

    result = CodexRuntimeCoordinator(
        ledger,
        make_config(tmp_path),
        launcher=launcher,
    ).execute(intent)

    assert result.metrics.raw_event_count == 2
    assert launcher.invocations
    command = launcher.invocations[0].command
    assert command[:3] == ("codex", "exec", "--json")
    assert "--output-schema" in command
    assert "-C" in command
    assert event_types(ledger) == [
        RuntimeEventType.RUN_STARTED,
        RuntimeEventType.CODEX_EVENT,
        RuntimeEventType.CODEX_EVENT,
        RuntimeEventType.OUTPUT_VALIDATED,
        RuntimeEventType.RUN_COMPLETED,
    ]
    assert (result.artifacts_dir / "raw_events.jsonl").exists()
    assert (result.artifacts_dir / "proposal_bundle.json").exists()
    assert (result.artifacts_dir / "metrics.json").exists()


def test_malformed_jsonl_event_rejected_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    ledger, intent = make_intent(tmp_path)
    launcher = FakeLauncher(
        stdout_lines=('{"type": "thread.started"}', "{not json"),
        final_payload=proposal_payload(),
    )

    with pytest.raises(MalformedJSONLEventError) as excinfo:
        CodexRuntimeCoordinator(
            ledger,
            make_config(tmp_path),
            launcher=launcher,
        ).execute(intent)

    assert excinfo.value.failure_code == FailureCode.MALFORMED_CODEX_EVENT
    assert excinfo.value.retryable is False
    assert event_types(ledger) == [
        RuntimeEventType.RUN_STARTED,
        RuntimeEventType.CODEX_EVENT,
        RuntimeEventType.RUN_FAILED,
    ]
    attempt_dir = tmp_path / "artifacts" / "run_001" / "attempt_001"
    assert (attempt_dir / "malformed_event_000002.txt").exists()


def test_partial_event_stream_is_replayable_after_process_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    ledger, intent = make_intent(tmp_path)
    launcher = FakeLauncher(
        stdout_lines=(
            raw_event("thread.started", thread_id="thread_001"),
            raw_event("turn.started", turn_index=1),
        ),
        exit_code=2,
        stderr="transport closed",
    )
    coordinator = CodexRuntimeCoordinator(
        ledger,
        make_config(tmp_path),
        launcher=launcher,
    )

    with pytest.raises(CodexProcessCrashError) as excinfo:
        coordinator.execute(intent)

    assert excinfo.value.failure_code == FailureCode.CODEX_PROCESS_CRASH
    assert excinfo.value.retryable is True
    replayed = coordinator.replay_events(run_id="run_001")
    assert [event.event_type for event in replayed] == [
        RuntimeEventType.RUN_STARTED,
        RuntimeEventType.CODEX_EVENT,
        RuntimeEventType.CODEX_EVENT,
        RuntimeEventType.RUN_FAILED,
    ]
    assert [
        event.model_dump(mode="json") for event in replayed
    ] == [
        event.model_dump(mode="json") for event in ledger.list_run_events("run_001")
    ]


def test_timeout_handling_records_retryable_transport_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    ledger, intent = make_intent(tmp_path)
    launcher = FakeLauncher(
        stdout_lines=(raw_event("turn.started"),),
        timed_out=True,
        exit_code=-1,
    )

    with pytest.raises(CodexTransportTimeoutError) as excinfo:
        CodexRuntimeCoordinator(
            ledger,
            make_config(tmp_path),
            launcher=launcher,
        ).execute(intent)

    assert excinfo.value.failure_code == FailureCode.CODEX_TRANSPORT_TIMEOUT
    assert excinfo.value.retryable is True
    failed_event = ledger.list_run_events("run_001")[-1]
    assert failed_event.event_type == RuntimeEventType.RUN_FAILED
    assert failed_event.payload.failure_code == FailureCode.CODEX_TRANSPORT_TIMEOUT


def test_process_crash_retry_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    ledger, intent = make_intent(tmp_path)
    launcher = FakeLauncher(
        stdout_lines=(raw_event("turn.started"),),
        exit_code=9,
        stderr="killed",
    )

    with pytest.raises(CodexProcessCrashError) as excinfo:
        CodexRuntimeCoordinator(
            ledger,
            make_config(tmp_path),
            launcher=launcher,
        ).execute(intent)

    assert excinfo.value.failure_code == FailureCode.CODEX_PROCESS_CRASH
    assert excinfo.value.retryable is True
    failed_event = ledger.list_run_events("run_001")[-1]
    assert failed_event.payload.failure_code == FailureCode.CODEX_PROCESS_CRASH
    assert "killed" in failed_event.payload.detail


def test_budget_exceeded_fails_closed_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    ledger, intent = make_intent(tmp_path)
    request = intent.execution_request.model_copy(
        update={
            "budgets": ExecutionBudgets(
                max_turns=1,
                max_tool_calls=1,
                max_runtime_seconds=30,
                soft_input_tokens=1,
                hard_input_tokens=1,
            )
        }
    )
    intent = intent.model_copy(update={"execution_request": request})
    launcher = FakeLauncher(stdout_lines=(raw_event("turn.started"),))

    with pytest.raises(BudgetExceededError) as excinfo:
        CodexRuntimeCoordinator(
            ledger,
            make_config(tmp_path),
            launcher=launcher,
        ).execute(intent)

    assert excinfo.value.failure_code == FailureCode.BUDGET_EXCEEDED
    assert excinfo.value.retryable is False
    assert launcher.invocations == []
    assert ledger.list_run_events("run_001")[-1].payload.failure_code == FailureCode.BUDGET_EXCEEDED


def test_wrong_workspace_root_invocation_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    ledger, intent = make_intent(tmp_path)
    wrong_root = tmp_path / "wrong-workspace"
    wrong_root.mkdir()
    launcher = FakeLauncher(stdout_lines=(raw_event("turn.started"),))

    with pytest.raises(WorkspaceRootMismatchError) as excinfo:
        CodexRuntimeCoordinator(
            ledger,
            make_config(tmp_path, workspace_root=wrong_root),
            launcher=launcher,
        ).execute(intent)

    assert excinfo.value.failure_code == FailureCode.WORKSPACE_MISMATCH
    assert excinfo.value.retryable is False
    assert launcher.invocations == []
    assert ledger.list_run_events("run_001")[-1].payload.failure_code == FailureCode.WORKSPACE_MISMATCH


def test_replay_from_stored_artifact_matches_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    ledger, intent = make_intent(tmp_path)
    coordinator = CodexRuntimeCoordinator(
        ledger,
        make_config(tmp_path),
        launcher=FakeLauncher(
            stdout_lines=(raw_event("turn.started"),),
            final_payload=proposal_payload(),
        ),
    )

    coordinator.execute(intent)

    replayed = coordinator.replay_events(run_id="run_001")
    ledgered = ledger.list_run_events("run_001")
    assert [event.model_dump(mode="json") for event in replayed] == [
        event.model_dump(mode="json") for event in ledgered
    ]
