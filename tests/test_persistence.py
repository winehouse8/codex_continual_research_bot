from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from codex_continual_research_bot.contracts import (
    QueueJob,
    QueueJobState,
    RunFailedPayload,
    RuntimeEvent,
    RuntimeEventType,
)
from codex_continual_research_bot.persistence import (
    DuplicateIdempotencyKeyError,
    SQLitePersistenceLedger,
)


def make_ledger(tmp_path: Path) -> SQLitePersistenceLedger:
    ledger = SQLitePersistenceLedger(tmp_path / "phase1.sqlite3")
    ledger.initialize()
    ledger.create_topic(topic_id="topic_001", slug="ai-drug-discovery", title="AI drug discovery")
    return ledger


def make_queue_job(*, queue_item_id: str = "queue_001", idempotency_key: str = "idem_001") -> QueueJob:
    return QueueJob.model_validate(
        {
            "queue_item_id": queue_item_id,
            "kind": "run.execute",
            "state": "queued",
            "topic_id": "topic_001",
            "run_id": f"seed_{queue_item_id}",
            "dedupe_key": f"dedupe_{queue_item_id}",
            "idempotency_key": idempotency_key,
            "priority": 10,
            "attempts": 0,
            "max_attempts": 5,
            "available_at": "2026-04-19T00:00:00Z",
            "payload": {
                "initiator": "scheduler",
                "objective": "Challenge the current best hypothesis",
                "selected_queue_item_ids": [queue_item_id],
            },
            "last_failure": None,
        }
    )


def test_happy_path_migration_creates_phase1_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "phase1.sqlite3"
    ledger = SQLitePersistenceLedger(db_path)

    applied = ledger.initialize()

    assert applied == ["0001_phase1_relational_ledger"]
    with ledger.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {
        "schema_migrations",
        "topics",
        "runs",
        "run_events",
        "queue_items",
        "idempotency_keys",
        "session_ledger",
        "session_leases",
        "session_events",
        "scheduler_policies",
    } <= tables


def test_migration_rerun_is_idempotent(tmp_path: Path) -> None:
    ledger = SQLitePersistenceLedger(tmp_path / "phase1.sqlite3")

    first = ledger.initialize()
    second = ledger.initialize()

    assert first == ["0001_phase1_relational_ledger"]
    assert second == []


def test_duplicate_idempotency_key_rejected(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)

    ledger.reserve_idempotency_key(
        idempotency_key="idem_001",
        scope="run.execute",
        request_digest="sha256:first",
    )

    with pytest.raises(DuplicateIdempotencyKeyError):
        ledger.reserve_idempotency_key(
            idempotency_key="idem_001",
            scope="run.execute",
            request_digest="sha256:second",
        )


def test_append_only_run_event_immutability(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    ledger.reserve_idempotency_key(
        idempotency_key="idem_001",
        scope="run.execute",
        request_digest="sha256:queue_001",
    )
    ledger.enqueue_job(make_queue_job())
    claimed = ledger.claim_next_queue_item_for_run(
        worker_id="worker-a",
        run_id="run_001",
        mode="scheduled",
    )
    assert claimed is not None

    ledger.append_run_event(
        RuntimeEvent(
            run_id="run_001",
            seq=0,
            event_type=RuntimeEventType.RUN_FAILED,
            turn_index=0,
            timestamp=datetime(2026, 4, 19, tzinfo=timezone.utc),
            payload=RunFailedPayload(
                failure_code="queue_mutation_mismatch",
                detail="synthetic failure for append-only trigger coverage",
            ),
        )
    )

    with ledger.connect() as connection, pytest.raises(
        sqlite3.IntegrityError, match="append-only"
    ):
        connection.execute(
            "UPDATE run_events SET event_type = ? WHERE run_id = ? AND seq = ?",
            ("run.completed", "run_001", 0),
        )

    with ledger.connect() as connection, pytest.raises(
        sqlite3.IntegrityError, match="append-only"
    ):
        connection.execute(
            "DELETE FROM run_events WHERE run_id = ? AND seq = ?",
            ("run_001", 0),
        )


def test_append_only_session_event_immutability(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    ledger.create_session_record(
        session_id="session_001",
        principal_id="principal_001",
        workspace_id="ws_001",
        state="active",
        credential_locator="~/.codex/auth.json",
    )
    ledger.append_session_event(
        session_id="session_001",
        event_type="session.started",
        payload={"source": "test"},
    )

    with ledger.connect() as connection, pytest.raises(
        sqlite3.IntegrityError, match="append-only"
    ):
        connection.execute(
            "UPDATE session_events SET event_type = ? WHERE session_id = ?",
            ("session.ended", "session_001"),
        )

    with ledger.connect() as connection, pytest.raises(
        sqlite3.IntegrityError, match="append-only"
    ):
        connection.execute(
            "DELETE FROM session_events WHERE session_id = ?",
            ("session_001",),
        )


def test_concurrent_dequeue_claim_race_allows_single_winner(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    ledger.reserve_idempotency_key(
        idempotency_key="idem_001",
        scope="run.execute",
        request_digest="sha256:queue_001",
    )
    ledger.enqueue_job(make_queue_job())

    def attempt(worker_id: str, run_id: str) -> str | None:
        claimed = ledger.claim_next_queue_item_for_run(
            worker_id=worker_id,
            run_id=run_id,
            mode="scheduled",
        )
        return None if claimed is None else claimed.worker_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        winner_a = executor.submit(attempt, "worker-a", "run_a")
        winner_b = executor.submit(attempt, "worker-b", "run_b")

    winners = {result for result in (winner_a.result(), winner_b.result()) if result is not None}
    assert len(winners) == 1

    queue_row = ledger.fetch_queue_item("queue_001")
    assert queue_row is not None
    assert queue_row["state"] == QueueJobState.CLAIMED.value
    assert queue_row["claimed_by"] in winners

    idempotency_record = ledger.get_idempotency_record("idem_001")
    assert idempotency_record is not None
    assert idempotency_record["run_id"] in {"run_a", "run_b"}


def test_claim_links_idempotency_record_to_run(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    ledger.reserve_idempotency_key(
        idempotency_key="idem_001",
        scope="run.execute",
        request_digest="sha256:queue_001",
    )
    ledger.enqueue_job(make_queue_job())

    claimed = ledger.claim_next_queue_item_for_run(
        worker_id="worker-a",
        run_id="run_001",
        mode="scheduled",
    )

    assert claimed is not None
    idempotency_record = ledger.get_idempotency_record("idem_001")
    assert idempotency_record is not None
    assert idempotency_record["run_id"] == "run_001"


def test_queue_retry_counter_update(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    ledger.reserve_idempotency_key(
        idempotency_key="idem_001",
        scope="run.execute",
        request_digest="sha256:queue_001",
    )
    ledger.enqueue_job(make_queue_job())

    ledger.record_queue_retry(
        queue_item_id="queue_001",
        failure_code="output_schema_validation_failed",
        detail="repair budget exhausted",
        next_available_at=datetime(2026, 4, 19, 0, 5, tzinfo=timezone.utc),
    )

    queue_row = ledger.fetch_queue_item("queue_001")
    assert queue_row is not None
    assert queue_row["state"] == QueueJobState.QUEUED.value
    assert queue_row["attempts"] == 1
    assert queue_row["last_failure_code"] == "output_schema_validation_failed"
    assert queue_row["last_failure_detail"] == "repair budget exhausted"


def test_stale_lease_release(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    ledger.create_session_record(
        session_id="session_001",
        principal_id="principal_001",
        workspace_id="ws_001",
        state="active",
        credential_locator="~/.codex/auth.json",
    )
    ledger.acquire_session_lease(
        session_id="session_001",
        holder="worker-a",
        ttl_seconds=5,
        now=datetime(2026, 4, 19, 0, 0, tzinfo=timezone.utc),
    )

    released = ledger.release_stale_session_leases(
        now=datetime(2026, 4, 19, 0, 0, 10, tzinfo=timezone.utc)
    )

    assert released == 1
    with ledger.connect() as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM session_leases"
        ).fetchone()[0]
    assert remaining == 0


def test_persistence_transaction_rolls_back_claim_on_run_insert_failure(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    ledger.reserve_idempotency_key(
        idempotency_key="idem_001",
        scope="run.execute",
        request_digest="sha256:queue_001",
    )
    ledger.reserve_idempotency_key(
        idempotency_key="idem_002",
        scope="run.execute",
        request_digest="sha256:queue_002",
    )
    ledger.enqueue_job(make_queue_job(queue_item_id="queue_001", idempotency_key="idem_001"))
    ledger.enqueue_job(make_queue_job(queue_item_id="queue_002", idempotency_key="idem_002"))

    first_claim = ledger.claim_next_queue_item_for_run(
        worker_id="worker-a",
        run_id="run_duplicate",
        mode="scheduled",
    )
    assert first_claim is not None

    with pytest.raises(sqlite3.IntegrityError):
        ledger.claim_next_queue_item_for_run(
            worker_id="worker-b",
            run_id="run_duplicate",
            mode="scheduled",
        )

    rolled_back_queue = ledger.fetch_queue_item("queue_002")
    assert rolled_back_queue is not None
    assert rolled_back_queue["state"] == QueueJobState.QUEUED.value
    assert rolled_back_queue["claimed_by"] is None
    assert ledger.fetch_run("run_duplicate") is not None


def test_claim_rolls_back_when_idempotency_run_link_is_not_writable(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    ledger.reserve_idempotency_key(
        idempotency_key="idem_001",
        scope="run.execute",
        request_digest="sha256:queue_001",
        run_id="run_existing",
    )
    ledger.enqueue_job(make_queue_job())

    with pytest.raises(sqlite3.IntegrityError, match="already linked to a run"):
        ledger.claim_next_queue_item_for_run(
            worker_id="worker-a",
            run_id="run_001",
            mode="scheduled",
        )

    queue_row = ledger.fetch_queue_item("queue_001")
    assert queue_row is not None
    assert queue_row["state"] == QueueJobState.QUEUED.value
    assert queue_row["claimed_by"] is None
    assert ledger.fetch_run("run_001") is None

    idempotency_record = ledger.get_idempotency_record("idem_001")
    assert idempotency_record is not None
    assert idempotency_record["run_id"] == "run_existing"
