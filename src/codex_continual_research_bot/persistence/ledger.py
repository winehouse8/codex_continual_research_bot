"""SQLite-backed relational authority for Phase 1 persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from codex_continual_research_bot.contracts import (
    QueueJob,
    QueueJobState,
    RunLifecycleState,
    RuntimeEvent,
    TopicSnapshot,
)

from .migrations import apply_migrations


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_timestamp(value: datetime | None = None) -> str:
    current = value or _utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


class DuplicateIdempotencyKeyError(RuntimeError):
    """Raised when an idempotency key is reserved twice."""


class DuplicateRunEventError(RuntimeError):
    """Raised when a run event sequence is reused."""


class DuplicateRunStartError(RuntimeError):
    """Raised when a queue item is already linked to a different run."""


@dataclass(frozen=True)
class ClaimedQueueItem:
    queue_item_id: str
    run_id: str
    topic_id: str
    dedupe_key: str
    idempotency_key: str
    attempts: int
    worker_id: str


class SQLitePersistenceLedger:
    """Repository facade that keeps queue, run, and session writes transactional."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._db_path,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> list[str]:
        with self.connect() as connection:
            return apply_migrations(connection)

    def create_topic(
        self,
        *,
        topic_id: str,
        slug: str,
        title: str,
        created_at: datetime | None = None,
    ) -> None:
        with self.connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO topics(id, slug, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    topic_id,
                    slug,
                    title,
                    _normalize_timestamp(created_at),
                    _normalize_timestamp(created_at),
                ),
            )

    def set_scheduler_policy(
        self,
        *,
        topic_id: str,
        cadence_minutes: int,
        jitter_minutes: int,
        policy_kind: str = "periodic",
        next_run_after: datetime | None = None,
    ) -> None:
        with self.connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO scheduler_policies(
                    topic_id,
                    policy_kind,
                    cadence_minutes,
                    jitter_minutes,
                    next_run_after,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id) DO UPDATE SET
                    policy_kind = excluded.policy_kind,
                    cadence_minutes = excluded.cadence_minutes,
                    jitter_minutes = excluded.jitter_minutes,
                    next_run_after = excluded.next_run_after,
                    updated_at = excluded.updated_at
                """,
                (
                    topic_id,
                    policy_kind,
                    cadence_minutes,
                    jitter_minutes,
                    _normalize_timestamp(next_run_after),
                    _normalize_timestamp(),
                ),
            )

    def enqueue_job(self, job: QueueJob) -> None:
        payload_json = json.dumps(job.payload.model_dump(mode="json"), sort_keys=True)
        last_failure_code = None if job.last_failure is None else job.last_failure.code.value
        last_failure_detail = None if job.last_failure is None else job.last_failure.detail
        last_failure_retryable = None if job.last_failure is None else int(job.last_failure.retryable)
        last_failure_human_review = None if job.last_failure is None else int(job.last_failure.human_review_required)

        with self.connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO queue_items(
                    id,
                    topic_id,
                    kind,
                    state,
                    requested_run_id,
                    dedupe_key,
                    idempotency_key,
                    priority,
                    attempts,
                    max_attempts,
                    available_at,
                    payload_json,
                    last_failure_code,
                    last_failure_detail,
                    last_failure_retryable,
                    last_failure_human_review,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.queue_item_id,
                    job.topic_id,
                    job.kind.value,
                    job.state.value,
                    job.requested_run_id,
                    job.dedupe_key,
                    job.idempotency_key,
                    job.priority,
                    job.attempts,
                    job.max_attempts,
                    _normalize_timestamp(job.available_at),
                    payload_json,
                    last_failure_code,
                    last_failure_detail,
                    last_failure_retryable,
                    last_failure_human_review,
                    _normalize_timestamp(),
                    _normalize_timestamp(),
                ),
            )

    def store_topic_snapshot(
        self,
        snapshot: TopicSnapshot,
        *,
        created_at: datetime | None = None,
    ) -> None:
        snapshot_json = json.dumps(snapshot.model_dump(mode="json"), sort_keys=True)
        with self.connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO topic_snapshots(
                    topic_id,
                    snapshot_version,
                    snapshot_json,
                    created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    snapshot.topic_id,
                    snapshot.snapshot_version,
                    snapshot_json,
                    _normalize_timestamp(created_at),
                ),
            )

    def fetch_topic_snapshot(
        self,
        *,
        topic_id: str,
        snapshot_version: int | None = None,
    ) -> TopicSnapshot | None:
        if snapshot_version is None:
            query = """
                SELECT snapshot_json
                FROM topic_snapshots
                WHERE topic_id = ?
                ORDER BY snapshot_version DESC
                LIMIT 1
            """
            params = (topic_id,)
        else:
            query = """
                SELECT snapshot_json
                FROM topic_snapshots
                WHERE topic_id = ? AND snapshot_version = ?
            """
            params = (topic_id, snapshot_version)

        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()

        if row is None:
            return None
        return TopicSnapshot.model_validate(json.loads(row["snapshot_json"]))

    def reserve_idempotency_key(
        self,
        *,
        idempotency_key: str,
        scope: str,
        request_digest: str,
        run_id: str | None = None,
    ) -> None:
        try:
            with self.connect() as connection, connection:
                connection.execute(
                    """
                    INSERT INTO idempotency_keys(
                        idempotency_key,
                        scope,
                        request_digest,
                        run_id,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        idempotency_key,
                        scope,
                        request_digest,
                        run_id,
                        _normalize_timestamp(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateIdempotencyKeyError(idempotency_key) from exc

    def get_idempotency_record(self, idempotency_key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT idempotency_key, scope, request_digest, run_id, created_at
                FROM idempotency_keys
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        return None if row is None else dict(row)

    def claim_queue_item_for_run(
        self,
        *,
        queue_item_id: str,
        worker_id: str,
        run_id: str,
        mode: str,
        now: datetime | None = None,
    ) -> ClaimedQueueItem | None:
        now_value = _normalize_timestamp(now)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT id, topic_id, dedupe_key, idempotency_key, attempts
                    FROM queue_items
                    WHERE id = ? AND available_at <= ?
                    LIMIT 1
                    """,
                    (queue_item_id, now_value),
                ).fetchone()
                if row is None:
                    connection.execute("ROLLBACK")
                    return None

                existing_run = connection.execute(
                    """
                    SELECT id
                    FROM runs
                    WHERE queue_item_id = ?
                    """,
                    (row["id"],),
                ).fetchone()
                if existing_run is not None:
                    if existing_run["id"] != run_id:
                        raise DuplicateRunStartError(
                            f"{row['id']} is already linked to {existing_run['id']}"
                        )
                    connection.execute("COMMIT")
                    return ClaimedQueueItem(
                        queue_item_id=row["id"],
                        run_id=run_id,
                        topic_id=row["topic_id"],
                        dedupe_key=row["dedupe_key"],
                        idempotency_key=row["idempotency_key"],
                        attempts=row["attempts"],
                        worker_id=worker_id,
                    )

                cursor = connection.execute(
                    """
                    UPDATE queue_items
                    SET state = ?, claimed_by = ?, claimed_at = ?, updated_at = ?
                    WHERE id = ? AND state = ?
                    """,
                    (
                        QueueJobState.CLAIMED.value,
                        worker_id,
                        now_value,
                        now_value,
                        row["id"],
                        QueueJobState.QUEUED.value,
                    ),
                )
                if cursor.rowcount == 0:
                    connection.execute("ROLLBACK")
                    return None

                connection.execute(
                    """
                    INSERT INTO runs(
                        id,
                        topic_id,
                        queue_item_id,
                        idempotency_key,
                        mode,
                        status,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        row["topic_id"],
                        row["id"],
                        row["idempotency_key"],
                        mode,
                        RunLifecycleState.QUEUED.value,
                        now_value,
                        now_value,
                    ),
                )
                cursor = connection.execute(
                    """
                    UPDATE idempotency_keys
                    SET run_id = ?
                    WHERE idempotency_key = ? AND run_id IS NULL
                    """,
                    (
                        run_id,
                        row["idempotency_key"],
                    ),
                )
                if cursor.rowcount == 0:
                    raise sqlite3.IntegrityError(
                        "idempotency key is missing or already linked to a run"
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

        return ClaimedQueueItem(
            queue_item_id=row["id"],
            run_id=run_id,
            topic_id=row["topic_id"],
            dedupe_key=row["dedupe_key"],
            idempotency_key=row["idempotency_key"],
            attempts=row["attempts"],
            worker_id=worker_id,
        )

    def claim_next_queue_item_for_run(
        self,
        *,
        worker_id: str,
        run_id: str,
        mode: str,
        now: datetime | None = None,
    ) -> ClaimedQueueItem | None:
        now_value = _normalize_timestamp(now)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id
                FROM queue_items
                WHERE state = ? AND available_at <= ?
                ORDER BY priority DESC, available_at ASC, created_at ASC
                LIMIT 1
                """,
                (QueueJobState.QUEUED.value, now_value),
            ).fetchone()

        if row is None:
            return None
        try:
            return self.claim_queue_item_for_run(
                queue_item_id=row["id"],
                worker_id=worker_id,
                run_id=run_id,
                mode=mode,
                now=now,
            )
        except DuplicateRunStartError:
            return None

    def append_run_event(self, event: RuntimeEvent) -> None:
        payload_json = json.dumps(event.payload.model_dump(mode="json"), sort_keys=True)
        try:
            with self.connect() as connection, connection:
                connection.execute(
                    """
                    INSERT INTO run_events(
                        run_id,
                        seq,
                        event_type,
                        turn_index,
                        timestamp,
                        payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.run_id,
                        event.seq,
                        event.event_type.value,
                        event.turn_index,
                        _normalize_timestamp(event.timestamp),
                        payload_json,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRunEventError(f"{event.run_id}:{event.seq}") from exc

    def transition_run_state(
        self,
        *,
        run_id: str,
        state: RunLifecycleState,
        snapshot_version: int | None = None,
    ) -> None:
        assignments = ["status = ?", "updated_at = ?"]
        params: list[Any] = [state.value, _normalize_timestamp()]
        if snapshot_version is not None:
            assignments.append("snapshot_version = ?")
            params.append(snapshot_version)
        params.append(run_id)

        with self.connect() as connection, connection:
            cursor = connection.execute(
                f"""
                UPDATE runs
                SET {", ".join(assignments)}
                WHERE id = ?
                """,
                params,
            )
            if cursor.rowcount == 0:
                raise KeyError(f"run {run_id} does not exist")

    def create_session_record(
        self,
        *,
        session_id: str,
        principal_id: str,
        workspace_id: str,
        state: str,
        credential_locator: str,
        created_at: datetime | None = None,
    ) -> None:
        with self.connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO session_ledger(
                    session_id,
                    principal_id,
                    workspace_id,
                    state,
                    credential_locator,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    principal_id,
                    workspace_id,
                    state,
                    credential_locator,
                    _normalize_timestamp(created_at),
                    _normalize_timestamp(created_at),
                ),
            )

    def append_session_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        created_at: datetime | None = None,
    ) -> None:
        with self.connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO session_events(session_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    session_id,
                    event_type,
                    json.dumps(payload, sort_keys=True),
                    _normalize_timestamp(created_at),
                ),
            )

    def acquire_session_lease(
        self,
        *,
        session_id: str,
        holder: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> None:
        current = now or _utcnow()
        expires_at = current + timedelta(seconds=ttl_seconds)
        with self.connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO session_leases(session_id, holder, leased_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    holder = excluded.holder,
                    leased_at = excluded.leased_at,
                    expires_at = excluded.expires_at
                """,
                (
                    session_id,
                    holder,
                    _normalize_timestamp(current),
                    _normalize_timestamp(expires_at),
                ),
            )

    def release_stale_session_leases(self, *, now: datetime | None = None) -> int:
        with self.connect() as connection, connection:
            cursor = connection.execute(
                "DELETE FROM session_leases WHERE expires_at < ?",
                (_normalize_timestamp(now),),
            )
            return cursor.rowcount

    def record_queue_retry(
        self,
        *,
        queue_item_id: str,
        failure_code: str,
        detail: str,
        next_available_at: datetime,
    ) -> None:
        next_time = _normalize_timestamp(next_available_at)
        with self.connect() as connection, connection:
            connection.execute(
                """
                UPDATE queue_items
                SET state = ?,
                    attempts = attempts + 1,
                    available_at = ?,
                    claimed_by = NULL,
                    claimed_at = NULL,
                    last_failure_code = ?,
                    last_failure_detail = ?,
                    last_failure_retryable = 1,
                    last_failure_human_review = 0,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    QueueJobState.QUEUED.value,
                    next_time,
                    failure_code,
                    detail,
                    _normalize_timestamp(),
                    queue_item_id,
                ),
            )

    def fetch_queue_item(self, queue_item_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM queue_items WHERE id = ?",
                (queue_item_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def fetch_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def fetch_run_by_queue_item(self, queue_item_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE queue_item_id = ?",
                (queue_item_id,),
            ).fetchone()
        return None if row is None else dict(row)
