"""SQLite-backed relational authority for Phase 1 persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from pydantic import ValidationError

from codex_continual_research_bot.contracts import (
    QueueJob,
    QueueJobState,
    RunReportViewModel,
    RunLifecycleState,
    RuntimeEvent,
    SessionInspectResult,
    SessionState,
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


class StaleRunStateError(RuntimeError):
    """Raised when a persisted run moved before a guarded state transition."""


class QueueMutationMismatchError(RuntimeError):
    """Raised when ack/nack/dead-letter does not match the claimed queue item."""


class MalformedTopicSnapshotError(RuntimeError):
    """Raised when persisted topic snapshot JSON cannot be decoded as a contract."""


class DuplicateSessionLeaseError(RuntimeError):
    """Raised when a non-expired session lease is already active."""


@dataclass(frozen=True)
class ClaimedQueueItem:
    queue_item_id: str
    run_id: str
    topic_id: str
    dedupe_key: str
    idempotency_key: str
    attempts: int
    worker_id: str


@dataclass(frozen=True)
class SessionLeaseRecord:
    lease_id: str
    session_id: str
    principal_id: str
    purpose: str
    holder: str
    host_id: str
    run_id: str | None
    leased_at: str
    expires_at: str


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
                SELECT topic_id, snapshot_version, snapshot_json
                FROM topic_snapshots
                WHERE topic_id = ?
                ORDER BY snapshot_version DESC
                LIMIT 1
            """
            params = (topic_id,)
        else:
            query = """
                SELECT topic_id, snapshot_version, snapshot_json
                FROM topic_snapshots
                WHERE topic_id = ? AND snapshot_version = ?
            """
            params = (topic_id, snapshot_version)

        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()

        if row is None:
            return None
        try:
            snapshot = TopicSnapshot.model_validate(json.loads(row["snapshot_json"]))
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            raise MalformedTopicSnapshotError(
                f"topic {topic_id} has malformed snapshot payload"
            ) from exc
        if (
            snapshot.topic_id != row["topic_id"]
            or snapshot.snapshot_version != row["snapshot_version"]
        ):
            raise MalformedTopicSnapshotError(
                f"topic {topic_id} snapshot row does not match payload authority"
            )
        return snapshot

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
                    SELECT id, topic_id, dedupe_key, idempotency_key, attempts, state
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
                    if row["state"] == QueueJobState.QUEUED.value:
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

    def fetch_next_claimable_queue_item(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM queue_items
                WHERE state = ? AND available_at <= ?
                ORDER BY priority DESC, available_at ASC, created_at ASC
                LIMIT 1
                """,
                (QueueJobState.QUEUED.value, _normalize_timestamp(now)),
            ).fetchone()
        return None if row is None else dict(row)

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

    def list_run_events(self, run_id: str) -> list[RuntimeEvent]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, seq, event_type, turn_index, timestamp, payload_json
                FROM run_events
                WHERE run_id = ?
                ORDER BY seq ASC
                """,
                (run_id,),
            ).fetchall()
        return [
            RuntimeEvent.model_validate(
                {
                    "run_id": row["run_id"],
                    "seq": row["seq"],
                    "event_type": row["event_type"],
                    "turn_index": row["turn_index"],
                    "timestamp": row["timestamp"],
                    "payload": json.loads(row["payload_json"]),
                }
            )
            for row in rows
        ]

    def record_interactive_run_success(
        self,
        *,
        report: RunReportViewModel,
        proposal_id: str,
        graph_payload: dict[str, Any],
        graph_digest: str,
        node_count: int,
        edge_count: int,
    ) -> None:
        report_json = json.dumps(report.model_dump(mode="json"), sort_keys=True)
        graph_json = json.dumps(graph_payload, sort_keys=True)
        with self.connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO canonical_graph_writes(
                    run_id,
                    topic_id,
                    proposal_id,
                    graph_digest,
                    node_count,
                    edge_count,
                    graph_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.run_id,
                    report.topic_id,
                    proposal_id,
                    graph_digest,
                    node_count,
                    edge_count,
                    graph_json,
                    _normalize_timestamp(report.created_at),
                ),
            )
            self._insert_interactive_run_report(
                connection=connection,
                report=report,
                report_json=report_json,
            )

    def record_interactive_run_failure(self, report: RunReportViewModel) -> None:
        report_json = json.dumps(report.model_dump(mode="json"), sort_keys=True)
        with self.connect() as connection, connection:
            self._insert_interactive_run_report(
                connection=connection,
                report=report,
                report_json=report_json,
            )

    def fetch_interactive_run_report(
        self,
        *,
        run_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> RunReportViewModel | None:
        if (run_id is None) == (idempotency_key is None):
            raise ValueError("provide exactly one of run_id or idempotency_key")
        if run_id is not None:
            where = "run_id = ?"
            value = run_id
        else:
            where = "idempotency_key = ?"
            value = idempotency_key
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT report_json
                FROM interactive_run_reports
                WHERE {where}
                """,
                (value,),
            ).fetchone()
        if row is None:
            return None
        return RunReportViewModel.model_validate(json.loads(row["report_json"]))

    def fetch_canonical_graph_write(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM canonical_graph_writes
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["graph_json"] = json.loads(data["graph_json"])
        return data

    def _insert_interactive_run_report(
        self,
        *,
        connection: sqlite3.Connection,
        report: RunReportViewModel,
        report_json: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO interactive_run_reports(
                report_id,
                run_id,
                topic_id,
                trigger_id,
                idempotency_key,
                snapshot_version,
                status,
                report_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.report_id,
                report.run_id,
                report.topic_id,
                report.trigger_id,
                report.idempotency_key,
                report.snapshot_version,
                report.status.value,
                report_json,
                _normalize_timestamp(report.created_at),
            ),
        )

    def transition_run_state(
        self,
        *,
        run_id: str,
        state: RunLifecycleState,
        snapshot_version: int | None = None,
        expected_state: RunLifecycleState | None = None,
    ) -> None:
        assignments = ["status = ?", "updated_at = ?"]
        params: list[Any] = [state.value, _normalize_timestamp()]
        if snapshot_version is not None:
            assignments.append("snapshot_version = ?")
            params.append(snapshot_version)
        params.append(run_id)
        where_clause = "id = ?"
        if expected_state is not None:
            where_clause += " AND status = ?"
            params.append(expected_state.value)

        with self.connect() as connection, connection:
            cursor = connection.execute(
                f"""
                UPDATE runs
                SET {", ".join(assignments)}
                WHERE {where_clause}
                """,
                params,
            )
            if cursor.rowcount == 0:
                current = connection.execute(
                    "SELECT status FROM runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                if current is None:
                    raise KeyError(f"run {run_id} does not exist")
                if expected_state is not None:
                    raise StaleRunStateError(
                        f"run {run_id} moved from {expected_state.value} to {current['status']}"
                    )
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

    def record_session_inspection(
        self,
        inspection: SessionInspectResult,
        *,
        provider: str = "openai-codex-chatgpt",
        codex_home: str = "",
        created_at: datetime | None = None,
    ) -> None:
        with self.connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO session_ledger(
                    session_id,
                    principal_id,
                    provider,
                    host_id,
                    workspace_id,
                    workspace_root,
                    state,
                    credential_locator,
                    account_fingerprint,
                    plan_type,
                    verification_level,
                    last_validated_at,
                    last_refreshed_at,
                    codex_home,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    principal_id = excluded.principal_id,
                    provider = excluded.provider,
                    host_id = excluded.host_id,
                    workspace_id = excluded.workspace_id,
                    workspace_root = excluded.workspace_root,
                    state = excluded.state,
                    credential_locator = excluded.credential_locator,
                    account_fingerprint = excluded.account_fingerprint,
                    plan_type = excluded.plan_type,
                    verification_level = excluded.verification_level,
                    last_validated_at = excluded.last_validated_at,
                    last_refreshed_at = excluded.last_refreshed_at,
                    codex_home = excluded.codex_home,
                    updated_at = excluded.updated_at
                """,
                (
                    inspection.session_id,
                    inspection.principal_id,
                    provider,
                    inspection.host_id,
                    inspection.workspace_id,
                    inspection.workspace_root,
                    inspection.state.value,
                    inspection.credential_locator,
                    inspection.principal_fingerprint,
                    inspection.account.plan_type,
                    inspection.verification_level.value,
                    _normalize_timestamp(inspection.last_validated_at),
                    _normalize_timestamp(inspection.last_refreshed_at),
                    codex_home,
                    _normalize_timestamp(created_at),
                    _normalize_timestamp(created_at),
                ),
            )
            connection.execute(
                """
                INSERT INTO session_host_bindings(
                    session_id,
                    host_id,
                    workspace_root,
                    codex_home,
                    created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id, host_id) DO UPDATE SET
                    workspace_root = excluded.workspace_root,
                    codex_home = excluded.codex_home
                """,
                (
                    inspection.session_id,
                    inspection.host_id,
                    inspection.workspace_root,
                    codex_home,
                    _normalize_timestamp(created_at),
                ),
            )

    def fetch_session_record(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM session_ledger
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def transition_session_state(
        self,
        *,
        session_id: str,
        state: SessionState,
        failure_code: str | None = None,
        now: datetime | None = None,
    ) -> None:
        failure_at = None if failure_code is None else _normalize_timestamp(now)
        with self.connect() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE session_ledger
                SET state = ?,
                    last_failure_code = ?,
                    last_failure_at = ?,
                    updated_at = ?
                WHERE session_id = ?
                """,
                (
                    state.value,
                    failure_code,
                    failure_at,
                    _normalize_timestamp(now),
                    session_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"session {session_id} does not exist")

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
        lease_id: str | None = None,
        principal_id: str | None = None,
        purpose: str | None = None,
        run_id: str | None = None,
        host_id: str | None = None,
        now: datetime | None = None,
    ) -> SessionLeaseRecord:
        current = now or _utcnow()
        expires_at = current + timedelta(seconds=ttl_seconds)
        lease_id = lease_id or f"{session_id}:{holder}:{int(current.timestamp())}"
        current_value = _normalize_timestamp(current)
        expires_value = _normalize_timestamp(expires_at)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM session_leases WHERE session_id = ? AND expires_at < ?",
                    (session_id, current_value),
                )
                try:
                    connection.execute(
                        """
                        INSERT INTO session_leases(
                            session_id,
                            holder,
                            leased_at,
                            expires_at,
                            lease_id,
                            principal_id,
                            purpose,
                            run_id,
                            host_id,
                            heartbeat_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            holder,
                            current_value,
                            expires_value,
                            lease_id,
                            principal_id,
                            purpose,
                            run_id,
                            host_id,
                            current_value,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise DuplicateSessionLeaseError(session_id) from exc
                connection.execute(
                    """
                    UPDATE session_ledger
                    SET lease_count = lease_count + 1,
                        updated_at = ?
                    WHERE session_id = ?
                    """,
                    (current_value, session_id),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return SessionLeaseRecord(
            lease_id=lease_id,
            session_id=session_id,
            principal_id=principal_id or "",
            purpose=purpose or "",
            holder=holder,
            host_id=host_id or "",
            run_id=run_id,
            leased_at=current_value,
            expires_at=expires_value,
        )

    def release_session_lease(self, *, session_id: str, lease_id: str | None = None) -> bool:
        where = "session_id = ?"
        params: list[Any] = [session_id]
        if lease_id is not None:
            where += " AND lease_id = ?"
            params.append(lease_id)
        with self.connect() as connection, connection:
            cursor = connection.execute(
                f"DELETE FROM session_leases WHERE {where}",
                params,
            )
            if cursor.rowcount:
                connection.execute(
                    """
                    UPDATE session_ledger
                    SET lease_count = CASE WHEN lease_count > 0 THEN lease_count - 1 ELSE 0 END,
                        updated_at = ?
                    WHERE session_id = ?
                    """,
                    (_normalize_timestamp(), session_id),
                )
            return cursor.rowcount > 0

    def release_stale_session_leases(self, *, now: datetime | None = None) -> int:
        cutoff = _normalize_timestamp(now)
        with self.connect() as connection, connection:
            stale_rows = connection.execute(
                "SELECT session_id FROM session_leases WHERE expires_at < ?",
                (cutoff,),
            ).fetchall()
            cursor = connection.execute(
                "DELETE FROM session_leases WHERE expires_at < ?",
                (cutoff,),
            )
            for row in stale_rows:
                connection.execute(
                    """
                    UPDATE session_ledger
                    SET lease_count = CASE WHEN lease_count > 0 THEN lease_count - 1 ELSE 0 END,
                        updated_at = ?
                    WHERE session_id = ?
                    """,
                    (cutoff, row["session_id"]),
                )
            return cursor.rowcount

    def record_queue_retry(
        self,
        *,
        queue_item_id: str,
        failure_code: str,
        detail: str,
        next_available_at: datetime,
        run_id: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        next_time = _normalize_timestamp(next_available_at)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if run_id is not None or worker_id is not None:
                    self._validate_claim_for_mutation(
                        connection=connection,
                        queue_item_id=queue_item_id,
                        run_id=run_id,
                        worker_id=worker_id,
                    )
                cursor = connection.execute(
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
                if cursor.rowcount == 0:
                    raise QueueMutationMismatchError(
                        f"queue item {queue_item_id} is not writable for retry"
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def complete_queue_item(
        self,
        *,
        queue_item_id: str,
        run_id: str,
        worker_id: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._validate_claim_for_mutation(
                    connection=connection,
                    queue_item_id=queue_item_id,
                    run_id=run_id,
                    worker_id=worker_id,
                )
                connection.execute(
                    """
                    UPDATE queue_items
                    SET state = ?,
                        claimed_by = NULL,
                        claimed_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        QueueJobState.COMPLETED.value,
                        _normalize_timestamp(),
                        queue_item_id,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def record_queue_dead_letter(
        self,
        *,
        queue_item_id: str,
        failure_code: str,
        detail: str,
        retryable: bool,
        human_review_required: bool,
        run_id: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if run_id is not None or worker_id is not None:
                    self._validate_claim_for_mutation(
                        connection=connection,
                        queue_item_id=queue_item_id,
                        run_id=run_id,
                        worker_id=worker_id,
                    )
                cursor = connection.execute(
                    """
                    UPDATE queue_items
                    SET state = ?,
                        attempts = attempts + 1,
                        claimed_by = NULL,
                        claimed_at = NULL,
                        last_failure_code = ?,
                        last_failure_detail = ?,
                        last_failure_retryable = ?,
                        last_failure_human_review = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        QueueJobState.DEAD_LETTER.value,
                        failure_code,
                        detail,
                        int(retryable),
                        int(human_review_required),
                        _normalize_timestamp(),
                        queue_item_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise QueueMutationMismatchError(
                        f"queue item {queue_item_id} is not writable for dead-letter"
                    )
                if run_id is not None:
                    connection.execute(
                        """
                        UPDATE runs
                        SET status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            RunLifecycleState.DEAD_LETTER.value,
                            _normalize_timestamp(),
                            run_id,
                        ),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def list_dead_letter_queue(
        self,
        *,
        topic_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [QueueJobState.DEAD_LETTER.value]
        where_clause = "state = ?"
        if topic_id is not None:
            where_clause += " AND topic_id = ?"
            params.append(topic_id)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM queue_items
                WHERE {where_clause}
                ORDER BY updated_at DESC, created_at ASC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def _validate_claim_for_mutation(
        self,
        *,
        connection: sqlite3.Connection,
        queue_item_id: str,
        run_id: str | None,
        worker_id: str | None,
    ) -> None:
        row = connection.execute(
            """
            SELECT
                queue_items.id AS queue_item_id,
                queue_items.state AS queue_state,
                queue_items.claimed_by AS claimed_by,
                runs.id AS run_id,
                runs.queue_item_id AS run_queue_item_id
            FROM queue_items
            LEFT JOIN runs ON runs.queue_item_id = queue_items.id
            WHERE queue_items.id = ?
            """,
            (queue_item_id,),
        ).fetchone()
        if row is None:
            raise QueueMutationMismatchError(f"queue item {queue_item_id} does not exist")
        if row["queue_state"] != QueueJobState.CLAIMED.value:
            raise QueueMutationMismatchError(
                f"queue item {queue_item_id} is {row['queue_state']}, not claimed"
            )
        if worker_id is not None and row["claimed_by"] != worker_id:
            raise QueueMutationMismatchError(
                f"queue item {queue_item_id} is claimed by {row['claimed_by']}, not {worker_id}"
            )
        if run_id is not None and row["run_id"] != run_id:
            raise QueueMutationMismatchError(
                f"queue item {queue_item_id} is linked to run {row['run_id']}, not {run_id}"
            )
        if row["run_queue_item_id"] != queue_item_id:
            raise QueueMutationMismatchError(
                f"run {row['run_id']} does not point back to queue item {queue_item_id}"
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
