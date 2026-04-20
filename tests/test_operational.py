from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from codex_continual_research_bot.contracts import (
    BackendStateUpdateSummary,
    FailureCode,
    InteractiveRunStatus,
    QueueJob,
    QueueJobState,
    RunCompletedPayload,
    RunReportViewModel,
    RuntimeEvent,
    RuntimeEventType,
)
from codex_continual_research_bot.operational import (
    OperationalControlService,
    ReplayArtifactMissingError,
)
from codex_continual_research_bot.persistence import SQLitePersistenceLedger
from codex_continual_research_bot.scheduler import TopicScheduleCandidate


NOW = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)


def graph_digest(payload: object) -> str:
    return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def make_ledger(tmp_path: Path) -> SQLitePersistenceLedger:
    ledger = SQLitePersistenceLedger(tmp_path / "ops.sqlite3")
    ledger.initialize()
    ledger.create_topic(topic_id="topic_001", slug="ai-drug-discovery", title="AI drug discovery")
    return ledger


def make_queue_job(
    *,
    queue_item_id: str = "queue_001",
    requested_run_id: str = "run_001",
    idempotency_key: str = "idem_001",
) -> QueueJob:
    return QueueJob.model_validate(
        {
            "queue_item_id": queue_item_id,
            "kind": "run.execute",
            "state": "queued",
            "topic_id": "topic_001",
            "requested_run_id": requested_run_id,
            "dedupe_key": f"dedupe_{queue_item_id}",
            "idempotency_key": idempotency_key,
            "priority": 10,
            "attempts": 0,
            "max_attempts": 5,
            "available_at": NOW,
            "payload": {
                "initiator": "scheduler",
                "objective": "Challenge the current best hypothesis",
                "selected_queue_item_ids": [queue_item_id],
            },
            "last_failure": None,
        }
    )


def seed_claimed_run(ledger: SQLitePersistenceLedger) -> None:
    ledger.reserve_idempotency_key(
        idempotency_key="idem_001",
        scope="run.execute",
        request_digest="sha256:queue_001",
    )
    ledger.enqueue_job(make_queue_job())
    claimed = ledger.claim_queue_item_for_run(
        queue_item_id="queue_001",
        worker_id="worker-a",
        run_id="run_001",
        mode="scheduled",
        now=NOW,
    )
    assert claimed is not None


def seed_successful_run(ledger: SQLitePersistenceLedger) -> None:
    seed_claimed_run(ledger)
    graph_payload = {
        "nodes": [{"id": "hyp_001", "kind": "Hypothesis"}],
        "edges": [{"id": "edge_001", "kind": "SUPPORTED_BY"}],
    }
    digest = graph_digest(graph_payload)
    report = RunReportViewModel(
        report_id="report_001",
        run_id="run_001",
        topic_id="topic_001",
        trigger_id="trigger_001",
        idempotency_key="idem_001",
        snapshot_version=1,
        status=InteractiveRunStatus.COMPLETED,
        summary="Replayable successful run.",
        proposal_digest="sha256:proposal",
        backend_state_update=BackendStateUpdateSummary(
            graph_digest=digest,
            node_count=1,
            edge_count=1,
            review_flags=[],
        ),
        operator_failure_summary=None,
        created_at=NOW,
    )
    ledger.record_interactive_run_success(
        report=report,
        proposal_id="proposal_001",
        graph_payload=graph_payload,
        graph_digest=digest,
        node_count=1,
        edge_count=1,
    )
    ledger.append_run_event(
        RuntimeEvent(
            run_id="run_001",
            seq=0,
            event_type=RuntimeEventType.RUN_COMPLETED,
            turn_index=0,
            timestamp=NOW,
            payload=RunCompletedPayload(
                proposal_bundle_digest="sha256:proposal",
                summary_digest="sha256:summary",
            ),
        )
    )


def seed_dead_letter(
    ledger: SQLitePersistenceLedger,
    *,
    failure_code: FailureCode = FailureCode.MALFORMED_PROPOSAL,
) -> None:
    seed_claimed_run(ledger)
    ledger.record_queue_dead_letter(
        queue_item_id="queue_001",
        failure_code=failure_code.value,
        detail="canonicalization failed after validator accepted shape",
        retryable=False,
        human_review_required=True,
        run_id="run_001",
        worker_id="worker-a",
    )


def test_happy_path_replay_consistency_records_audit(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    seed_successful_run(ledger)
    service = OperationalControlService(ledger)

    replay = service.replay_run(run_id="run_001", actor="operator-a", created_at=NOW)

    assert replay.run_id == "run_001"
    assert replay.topic_id == "topic_001"
    assert replay.event_count == 1
    assert replay.graph_digest == ledger.fetch_canonical_graph_write("run_001")["graph_digest"]
    audit = ledger.list_operation_audit_events(scope="run", subject_id="run_001")
    assert [event["event_type"] for event in audit] == ["run.replayed"]
    assert audit[0]["payload_json"]["graph_digest"] == replay.graph_digest


def test_same_artifact_repeated_replay_is_deterministic(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    seed_successful_run(ledger)
    service = OperationalControlService(ledger)

    first = service.replay_run(run_id="run_001", actor="operator-a", created_at=NOW)
    second = service.replay_run(
        run_id="run_001",
        actor="operator-a",
        created_at=NOW + timedelta(seconds=1),
    )

    assert first.graph_digest == second.graph_digest
    assert first.report_digest == second.report_digest
    assert first.event_count == second.event_count


def test_repair_job_after_canonicalization_failure_preserves_source_queue_item(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    seed_dead_letter(ledger)
    service = OperationalControlService(ledger)

    repair = service.submit_repair_job(
        source_queue_item_id="queue_001",
        actor="operator-a",
        reason="repair canonical graph projection only",
        created_at=NOW,
    )

    source = ledger.fetch_queue_item("queue_001")
    repair_job = ledger.fetch_queue_item(repair.repair_queue_item_id)
    assert source is not None
    assert source["state"] == QueueJobState.DEAD_LETTER.value
    assert source["last_failure_detail"] == "canonicalization failed after validator accepted shape"
    assert repair_job is not None
    assert repair_job["kind"] == "graph.repair"
    assert repair_job["state"] == QueueJobState.QUEUED.value
    assert repair_job["requested_run_id"] == repair.repair_run_id
    audit = ledger.list_operation_audit_events(scope="queue", subject_id="queue_001")
    assert audit[0]["event_type"] == "graph_repair.queued"
    assert audit[0]["payload_json"]["repair_queue_item_id"] == repair.repair_queue_item_id


def test_dead_letter_recovery_requeues_item_with_explicit_audit(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    seed_dead_letter(ledger, failure_code=FailureCode.CODEX_TRANSPORT_TIMEOUT)
    service = OperationalControlService(ledger)

    service.recover_dead_letter(
        queue_item_id="queue_001",
        actor="operator-a",
        reason="transport recovered; retry original queue item",
        available_at=NOW + timedelta(minutes=5),
    )

    queue_item = ledger.fetch_queue_item("queue_001")
    run = ledger.fetch_run("run_001")
    assert queue_item is not None
    assert queue_item["state"] == QueueJobState.QUEUED.value
    assert queue_item["claimed_by"] is None
    assert run is not None
    assert run["status"] == "queued"
    audit = ledger.list_operation_audit_events(scope="queue", subject_id="queue_001")
    assert audit[0]["event_type"] == "dead_letter.recovered"
    assert audit[0]["payload_json"]["previous_failure_code"] == "codex_transport_timeout"


def test_missing_artifact_replay_rejection(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    seed_claimed_run(ledger)
    service = OperationalControlService(ledger)

    with pytest.raises(ReplayArtifactMissingError, match="no report artifact"):
        service.replay_run(run_id="run_001", actor="operator-a")


def test_alert_emission_on_repeated_auth_failure(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    ledger.create_session_record(
        session_id="sess_001",
        principal_id="principal_001",
        workspace_id="ws_001",
        state="reauth_required",
        credential_locator="~/.codex/auth.json",
    )
    for index in range(3):
        ledger.append_session_event(
            session_id="sess_001",
            event_type="scheduled_run.operator_notification",
            payload={
                "failure_code": FailureCode.AUTH_MATERIAL_MISSING.value,
                "detail": f"auth failure {index}",
            },
            created_at=NOW + timedelta(seconds=index),
        )
    service = OperationalControlService(ledger)

    alert = service.emit_repeated_auth_failure_alert(
        session_id="sess_001",
        threshold=3,
        created_at=NOW,
    )

    assert alert is not None
    assert alert.emitted is True
    alerts = ledger.list_operator_alerts(alert_type="repeated_auth_failure")
    assert len(alerts) == 1
    assert alerts[0]["session_id"] == "sess_001"
    assert alerts[0]["payload_json"]["observed_failures"] == 3


def test_alert_emission_on_stagnation_threshold_breach(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    service = OperationalControlService(ledger)
    candidate = TopicScheduleCandidate(
        topic_id="topic_001",
        next_run_after=NOW,
        current_best_hypothesis_count=1,
        challenger_target_count=1,
        recent_challenger_count=0,
        recent_revision_count=0,
        unresolved_conflict_count=2,
        open_question_count=1,
        queued_user_input_count=0,
        support_challenge_imbalance=0.7,
        consecutive_stagnant_runs=3,
    )

    alert = service.emit_stagnation_threshold_alert(
        candidate=candidate,
        threshold=3,
        created_at=NOW,
    )

    assert alert is not None
    assert alert.emitted is True
    alerts = ledger.list_operator_alerts(alert_type="stagnation_threshold_breach")
    assert len(alerts) == 1
    assert alerts[0]["topic_id"] == "topic_001"
    assert alerts[0]["payload_json"]["consecutive_stagnant_runs"] == 3


def test_audit_trail_completeness_for_operational_actions(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    seed_successful_run(ledger)
    service = OperationalControlService(ledger)

    service.replay_run(run_id="run_001", actor="operator-a", created_at=NOW)
    candidate = TopicScheduleCandidate(
        topic_id="topic_001",
        next_run_after=NOW,
        current_best_hypothesis_count=1,
        challenger_target_count=0,
        recent_challenger_count=0,
        recent_revision_count=0,
        unresolved_conflict_count=0,
        open_question_count=0,
        queued_user_input_count=0,
        support_challenge_imbalance=0.0,
        consecutive_stagnant_runs=4,
    )
    service.emit_stagnation_threshold_alert(
        candidate=candidate,
        threshold=3,
        actor="system",
        created_at=NOW,
    )

    run_audit = ledger.list_operation_audit_events(scope="run", subject_id="run_001")
    alert_audit = ledger.list_operation_audit_events(scope="alert", subject_id="topic_001")
    assert run_audit[0]["event_type"] == "run.replayed"
    assert run_audit[0]["actor"] == "operator-a"
    assert alert_audit[0]["event_type"] == "alert.emitted"
    assert alert_audit[0]["payload_json"]["alert_type"] == "stagnation_threshold_breach"

    with ledger.connect() as connection, pytest.raises(
        sqlite3.IntegrityError, match="append-only"
    ):
        connection.execute(
            "UPDATE operation_audit_events SET event_type = ? WHERE id = ?",
            ("mutated", run_audit[0]["id"]),
        )
