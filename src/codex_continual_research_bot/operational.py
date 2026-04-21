"""Phase 11 operational dashboards, replay, repair, and alert controls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any

from codex_continual_research_bot.contracts import (
    FailureCode,
    QueueJob,
    QueueJobKind,
    QueueJobState,
)
from codex_continual_research_bot.failure_analysis import (
    summarize_malformed_proposal_failures,
)
from codex_continual_research_bot.persistence import (
    DuplicateIdempotencyKeyError,
    SQLitePersistenceLedger,
)
from codex_continual_research_bot.scheduler import TopicScheduleCandidate


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_timestamp(value: datetime | None = None) -> str:
    current = value or _utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(start: datetime | None, now: datetime) -> int | None:
    if start is None:
        return None
    return max(0, int((now - start).total_seconds()))


def _canonical_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256_json(data: object) -> str:
    return sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def _canonical_graph_digest(data: object) -> str:
    return sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


class OperationalControlError(RuntimeError):
    """Base error for Phase 11 operational control failures."""


class ReplayArtifactMissingError(OperationalControlError):
    """Raised when replay cannot prove consistency from stored artifacts."""


class ReplayDeterminismError(OperationalControlError):
    """Raised when replayed artifacts drift from the original ledger."""


class RepairJobRejectedError(OperationalControlError):
    """Raised when repair would not be explicit and auditable."""


class StaleClaimRecoveryRejectedError(OperationalControlError):
    """Raised when a claimed queue item is not safe to recover."""


DEFAULT_STALE_CLAIM_SECONDS = 15 * 60


@dataclass(frozen=True)
class ReplayResult:
    run_id: str
    topic_id: str
    graph_digest: str
    report_digest: str
    event_count: int
    audit_event_id: int


@dataclass(frozen=True)
class RepairJobResult:
    source_queue_item_id: str
    repair_queue_item_id: str
    repair_run_id: str
    audit_event_id: int


@dataclass(frozen=True)
class StaleClaimRecoveryResult:
    queue_item_id: str
    action: str
    state: str
    stale: bool
    idempotent: bool
    audit_event_id: int | None


@dataclass(frozen=True)
class OperatorAlertResult:
    alert_id: str
    alert_type: str
    severity: str
    detail: str
    emitted: bool


class OperationalControlService:
    """Operator-facing admin path for safe replay, repair, alerts, and audits."""

    _REPAIRABLE_FAILURES = frozenset(
        {
            FailureCode.MALFORMED_PROPOSAL.value,
            FailureCode.OUTPUT_SCHEMA_VALIDATION_FAILED.value,
        }
    )
    _AUTH_FAILURES = frozenset(
        {
            FailureCode.AUTH_MATERIAL_MISSING.value,
            FailureCode.REFRESH_FAILED.value,
            FailureCode.PRINCIPAL_MISMATCH.value,
            FailureCode.WORKSPACE_MISMATCH.value,
            FailureCode.STALE_SESSION.value,
            FailureCode.CLI_LOGIN_PATH_BLOCKED.value,
        }
    )

    def __init__(self, ledger: SQLitePersistenceLedger) -> None:
        self._ledger = ledger

    def run_dashboard(self, *, topic_id: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if topic_id is not None:
            clauses.append("runs.topic_id = ?")
            params.append(topic_id)
        where = "" if not clauses else f"WHERE {' AND '.join(clauses)}"
        with self._ledger.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    runs.id AS run_id,
                    runs.topic_id,
                    runs.queue_item_id,
                    runs.mode,
                    runs.status,
                    runs.snapshot_version,
                    runs.created_at,
                    runs.updated_at,
                    queue_items.state AS queue_state,
                    queue_items.last_failure_code,
                    canonical_graph_writes.graph_digest
                FROM runs
                LEFT JOIN queue_items ON queue_items.id = runs.queue_item_id
                LEFT JOIN canonical_graph_writes ON canonical_graph_writes.run_id = runs.id
                {where}
                ORDER BY runs.updated_at DESC, runs.id ASC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def event_dashboard(self, *, run_id: str) -> dict[str, Any]:
        events = [
            event.model_dump(mode="json")
            for event in self._ledger.list_run_events(run_id)
        ]
        audit = self._ledger.list_operation_audit_events(scope="run", subject_id=run_id)
        return {
            "run_id": run_id,
            "runtime_events": events,
            "operation_audit_events": audit,
        }

    def queue_dashboard(self, *, topic_id: str | None = None) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if topic_id is not None:
            clauses.append("topic_id = ?")
            params.append(topic_id)
        where = "" if not clauses else f"WHERE {' AND '.join(clauses)}"
        with self._ledger.connect() as connection:
            counts = connection.execute(
                f"""
                SELECT state, COUNT(*) AS count
                FROM queue_items
                {where}
                GROUP BY state
                ORDER BY state ASC
                """,
                params,
            ).fetchall()
        dead_letters = self._ledger.list_dead_letter_queue(topic_id=topic_id)
        stale_claims = self.list_claimed_queue_items(topic_id=topic_id)
        return {
            "state_counts": {row["state"]: row["count"] for row in counts},
            "dead_letters": dead_letters,
            "malformed_proposal_failure_summary": summarize_malformed_proposal_failures(
                dead_letters
            ),
            "claimed": stale_claims,
            "stale_claimed": [item for item in stale_claims if item["stale"]],
        }

    def list_claimed_queue_items(
        self,
        *,
        topic_id: str | None = None,
        now: datetime | None = None,
        stale_after_seconds: int = DEFAULT_STALE_CLAIM_SECONDS,
    ) -> list[dict[str, Any]]:
        clauses = ["queue_items.state = ?"]
        params: list[Any] = [QueueJobState.CLAIMED.value]
        if topic_id is not None:
            clauses.append("queue_items.topic_id = ?")
            params.append(topic_id)
        where = " AND ".join(clauses)
        with self._ledger.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    queue_items.*,
                    runs.id AS run_id
                FROM queue_items
                LEFT JOIN runs ON runs.queue_item_id = queue_items.id
                WHERE {where}
                ORDER BY queue_items.claimed_at ASC, queue_items.id ASC
                """,
                params,
            ).fetchall()
            return [
                self._claimed_queue_item_view(
                    connection=connection,
                    row=dict(row),
                    now=now,
                    stale_after_seconds=stale_after_seconds,
                )
                for row in rows
            ]

    def recover_stale_claimed_item(
        self,
        *,
        queue_item_id: str,
        actor: str,
        reason: str,
        action: str = "retry",
        now: datetime | None = None,
        stale_after_seconds: int = DEFAULT_STALE_CLAIM_SECONDS,
    ) -> StaleClaimRecoveryResult:
        if action not in {"retry", "dead_letter"}:
            raise StaleClaimRecoveryRejectedError(
                f"unsupported stale claim recovery action {action}"
            )

        current_time = _normalize_timestamp(now)
        with self._ledger.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT
                        queue_items.*,
                        runs.id AS run_id
                    FROM queue_items
                    LEFT JOIN runs ON runs.queue_item_id = queue_items.id
                    WHERE queue_items.id = ?
                    """,
                    (queue_item_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"queue item {queue_item_id} does not exist")

                current = dict(row)
                if current["state"] != QueueJobState.CLAIMED.value:
                    previous = self._previous_stale_recovery(
                        connection=connection,
                        queue_item_id=queue_item_id,
                        action=action,
                    )
                    if previous is not None:
                        connection.execute("COMMIT")
                        return StaleClaimRecoveryResult(
                            queue_item_id=queue_item_id,
                            action=action,
                            state=str(current["state"]),
                            stale=False,
                            idempotent=True,
                            audit_event_id=int(previous["id"]),
                        )
                    raise StaleClaimRecoveryRejectedError(
                        f"queue item {queue_item_id} is {current['state']}, not claimed"
                    )

                claim = self._claimed_queue_item_view(
                    connection=connection,
                    row=current,
                    now=now,
                    stale_after_seconds=stale_after_seconds,
                )
                if not claim["stale"]:
                    raise StaleClaimRecoveryRejectedError(
                        f"queue item {queue_item_id} is claimed but not stale"
                    )

                target_state = (
                    QueueJobState.QUEUED.value
                    if action == "retry"
                    else QueueJobState.DEAD_LETTER.value
                )
                failure_detail = (
                    "stale claimed queue item recovered by operator: "
                    f"{reason}"
                )
                cursor = connection.execute(
                    """
                    UPDATE queue_items
                    SET state = ?,
                        available_at = ?,
                        claimed_by = NULL,
                        claimed_at = NULL,
                        last_failure_code = ?,
                        last_failure_detail = ?,
                        last_failure_retryable = ?,
                        last_failure_human_review = ?,
                        updated_at = ?
                    WHERE id = ? AND state = ?
                    """,
                    (
                        target_state,
                        current_time,
                        FailureCode.RUNNER_HOST_UNAVAILABLE.value,
                        failure_detail,
                        1,
                        1 if action == "dead_letter" else 0,
                        current_time,
                        queue_item_id,
                        QueueJobState.CLAIMED.value,
                    ),
                )
                if cursor.rowcount == 0:
                    raise StaleClaimRecoveryRejectedError(
                        f"queue item {queue_item_id} moved before stale recovery"
                    )
                if current.get("run_id") is not None:
                    run_status = "queued" if action == "retry" else "dead_letter"
                    connection.execute(
                        """
                        UPDATE runs
                        SET status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (run_status, current_time, current["run_id"]),
                    )
                event_type = (
                    "stale_claim.recovered"
                    if action == "retry"
                    else "stale_claim.dead_lettered"
                )
                audit = connection.execute(
                    """
                    INSERT INTO operation_audit_events(
                        scope,
                        subject_id,
                        event_type,
                        actor,
                        payload_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        "queue",
                        queue_item_id,
                        event_type,
                        actor,
                        json.dumps(
                            {
                                "action": action,
                                "reason": reason,
                                "run_id": current.get("run_id"),
                                "topic_id": current["topic_id"],
                                "previous_claimed_by": current["claimed_by"],
                                "previous_claimed_at": current["claimed_at"],
                                "stale_after_seconds": stale_after_seconds,
                                "stale_basis": claim["stale_basis"],
                                "stale_age_seconds": claim["stale_age_seconds"],
                            },
                            sort_keys=True,
                        ),
                        current_time,
                    ),
                ).fetchone()
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

        return StaleClaimRecoveryResult(
            queue_item_id=queue_item_id,
            action=action,
            state=target_state,
            stale=True,
            idempotent=False,
            audit_event_id=int(audit["id"]),
        )

    def session_dashboard(self, *, session_id: str | None = None) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_ledger.session_id = ?")
            params.append(session_id)
        where = "" if not clauses else f"WHERE {' AND '.join(clauses)}"
        with self._ledger.connect() as connection:
            sessions = connection.execute(
                f"""
                SELECT *
                FROM session_ledger
                {where}
                ORDER BY updated_at DESC, session_id ASC
                """,
                params,
            ).fetchall()
            event_rows = connection.execute(
                f"""
                SELECT session_events.*
                FROM session_events
                JOIN session_ledger ON session_ledger.session_id = session_events.session_id
                {where}
                ORDER BY session_events.created_at DESC, session_events.id DESC
                """,
                params,
            ).fetchall()
        events = [dict(row) for row in event_rows]
        for event in events:
            event["payload_json"] = json.loads(event["payload_json"])
        return {
            "sessions": [dict(row) for row in sessions],
            "events": events,
        }

    def replay_run(
        self,
        *,
        run_id: str,
        actor: str = "operator",
        created_at: datetime | None = None,
    ) -> ReplayResult:
        run = self._ledger.fetch_run(run_id)
        if run is None:
            raise ReplayArtifactMissingError(f"run {run_id} does not exist")
        report = self._ledger.fetch_interactive_run_report(run_id=run_id)
        if report is None:
            raise ReplayArtifactMissingError(f"run {run_id} has no report artifact")
        graph_write = self._ledger.fetch_canonical_graph_write(run_id)
        if graph_write is None:
            raise ReplayArtifactMissingError(f"run {run_id} has no canonical graph artifact")

        recomputed_graph_digest = _canonical_graph_digest(graph_write["graph_json"])
        if recomputed_graph_digest != graph_write["graph_digest"]:
            raise ReplayDeterminismError(
                f"run {run_id} graph artifact digest drifted from stored ledger digest"
            )
        if report.backend_state_update is not None:
            state_update = report.backend_state_update
            if (
                state_update.graph_digest != graph_write["graph_digest"]
                or state_update.node_count != graph_write["node_count"]
                or state_update.edge_count != graph_write["edge_count"]
            ):
                raise ReplayDeterminismError(
                    f"run {run_id} report state update does not match canonical graph write"
                )

        report_digest = _sha256_json(report.model_dump(mode="json"))
        events = self._ledger.list_run_events(run_id)
        audit_event_id = self._ledger.append_operation_audit_event(
            scope="run",
            subject_id=run_id,
            event_type="run.replayed",
            actor=actor,
            payload={
                "topic_id": run["topic_id"],
                "queue_item_id": run["queue_item_id"],
                "graph_digest": graph_write["graph_digest"],
                "report_digest": report_digest,
                "event_count": len(events),
            },
            created_at=created_at,
        )
        return ReplayResult(
            run_id=run_id,
            topic_id=str(run["topic_id"]),
            graph_digest=str(graph_write["graph_digest"]),
            report_digest=report_digest,
            event_count=len(events),
            audit_event_id=audit_event_id,
        )

    def submit_repair_job(
        self,
        *,
        source_queue_item_id: str,
        actor: str,
        reason: str,
        created_at: datetime | None = None,
    ) -> RepairJobResult:
        source = self._ledger.fetch_queue_item(source_queue_item_id)
        if source is None:
            raise RepairJobRejectedError(f"queue item {source_queue_item_id} does not exist")
        if source["state"] != QueueJobState.DEAD_LETTER.value:
            raise RepairJobRejectedError(
                f"queue item {source_queue_item_id} is {source['state']}, not dead_letter"
            )
        if source["last_failure_code"] not in self._REPAIRABLE_FAILURES:
            raise RepairJobRejectedError(
                f"queue item {source_queue_item_id} failure is not repairable"
            )

        suffix = _sha256_json(
            {
                "source_queue_item_id": source_queue_item_id,
                "failure_code": source["last_failure_code"],
                "reason": reason,
            }
        )[:12]
        repair_queue_item_id = f"repair_{suffix}"
        repair_run_id = f"repair_run_{suffix}"
        idempotency_key = f"graph.repair:{source_queue_item_id}:{source['last_failure_code']}"
        dedupe_key = idempotency_key
        try:
            self._ledger.reserve_idempotency_key(
                idempotency_key=idempotency_key,
                scope=QueueJobKind.GRAPH_REPAIR.value,
                request_digest=f"sha256:{suffix}",
            )
        except DuplicateIdempotencyKeyError as exc:
            raise RepairJobRejectedError(
                f"repair job for queue item {source_queue_item_id} already exists"
            ) from exc
        self._ledger.enqueue_job(
            QueueJob(
                queue_item_id=repair_queue_item_id,
                kind=QueueJobKind.GRAPH_REPAIR,
                state=QueueJobState.QUEUED,
                topic_id=str(source["topic_id"]),
                requested_run_id=repair_run_id,
                dedupe_key=dedupe_key,
                idempotency_key=idempotency_key,
                priority=max(0, int(source["priority"])),
                attempts=0,
                max_attempts=1,
                available_at=created_at or _utcnow(),
                payload={
                    "initiator": "operator",
                    "objective": f"Repair failed graph write without mutating source evidence: {reason}",
                    "selected_queue_item_ids": [source_queue_item_id],
                },
                last_failure=None,
            )
        )
        audit_event_id = self._ledger.append_operation_audit_event(
            scope="queue",
            subject_id=source_queue_item_id,
            event_type="graph_repair.queued",
            actor=actor,
            payload={
                "repair_queue_item_id": repair_queue_item_id,
                "repair_run_id": repair_run_id,
                "failure_code": source["last_failure_code"],
                "failure_detail": source["last_failure_detail"],
                "source_state": source["state"],
            },
            created_at=created_at,
        )
        return RepairJobResult(
            source_queue_item_id=source_queue_item_id,
            repair_queue_item_id=repair_queue_item_id,
            repair_run_id=repair_run_id,
            audit_event_id=audit_event_id,
        )

    def recover_dead_letter(
        self,
        *,
        queue_item_id: str,
        actor: str,
        reason: str,
        available_at: datetime | None = None,
    ) -> None:
        self._ledger.recover_dead_letter_queue_item(
            queue_item_id=queue_item_id,
            actor=actor,
            reason=reason,
            available_at=available_at,
        )

    def _claimed_queue_item_view(
        self,
        *,
        connection: Any,
        row: dict[str, Any],
        now: datetime | None,
        stale_after_seconds: int,
    ) -> dict[str, Any]:
        current_time = now or _utcnow()
        run_id = row.get("run_id") or row.get("requested_run_id")
        lease = None
        if row.get("claimed_by") is not None and run_id is not None:
            lease_row = connection.execute(
                """
                SELECT *
                FROM session_leases
                WHERE holder = ? AND run_id = ?
                ORDER BY heartbeat_at DESC, leased_at DESC
                LIMIT 1
                """,
                (row["claimed_by"], run_id),
            ).fetchone()
            lease = None if lease_row is None else dict(lease_row)

        claimed_at = _parse_timestamp(row.get("claimed_at"))
        claim_age = _age_seconds(claimed_at, current_time)
        heartbeat_at = _parse_timestamp(None if lease is None else lease.get("heartbeat_at"))
        heartbeat_age = _age_seconds(heartbeat_at, current_time)
        leased_at = _parse_timestamp(None if lease is None else lease.get("leased_at"))
        lease_age = _age_seconds(leased_at, current_time)
        lease_expires_at = _parse_timestamp(None if lease is None else lease.get("expires_at"))
        lease_expired = lease_expires_at is not None and lease_expires_at <= current_time

        if lease is not None and not lease_expired:
            stale_basis = "worker_heartbeat"
            basis_age = heartbeat_age if heartbeat_age is not None else lease_age
            stale = basis_age is not None and basis_age >= stale_after_seconds
        elif claim_age is None:
            stale_basis = "claimed_at"
            basis_age = None
            stale = True
        else:
            stale_basis = "claimed_at"
            basis_age = claim_age
            stale = claim_age >= stale_after_seconds

        return {
            "queue_item_id": row["id"],
            "topic_id": row["topic_id"],
            "requested_run_id": row["requested_run_id"],
            "run_id": row.get("run_id"),
            "claimed_by": row.get("claimed_by"),
            "claimed_at": row.get("claimed_at"),
            "claim_age_seconds": claim_age,
            "worker_heartbeat_at": None if lease is None else lease.get("heartbeat_at"),
            "worker_heartbeat_age_seconds": heartbeat_age,
            "worker_lease_age_seconds": lease_age,
            "worker_lease_expires_at": None if lease is None else lease.get("expires_at"),
            "has_active_worker_lease": lease is not None and not lease_expired,
            "stale_after_seconds": stale_after_seconds,
            "stale_basis": stale_basis,
            "stale_age_seconds": basis_age,
            "stale": stale,
        }

    def _previous_stale_recovery(
        self,
        *,
        connection: Any,
        queue_item_id: str,
        action: str,
    ) -> dict[str, Any] | None:
        rows = connection.execute(
            """
            SELECT *
            FROM operation_audit_events
            WHERE scope = ?
              AND subject_id = ?
              AND event_type IN (?, ?)
            ORDER BY id DESC
            """,
            (
                "queue",
                queue_item_id,
                "stale_claim.recovered",
                "stale_claim.dead_lettered",
            ),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("action") == action:
                return dict(row)
        return None

    def emit_repeated_auth_failure_alert(
        self,
        *,
        session_id: str,
        threshold: int,
        actor: str = "system",
        created_at: datetime | None = None,
    ) -> OperatorAlertResult | None:
        failures = self._auth_failure_events(session_id)
        if len(failures) < threshold:
            return None
        last_failure = failures[-1]
        return self._record_alert(
            alert_type="repeated_auth_failure",
            severity="critical",
            detail=f"session {session_id} has {len(failures)} auth failures",
            actor=actor,
            payload={
                "session_id": session_id,
                "threshold": threshold,
                "observed_failures": len(failures),
                "last_event_id": last_failure["id"],
                "last_failure_code": last_failure["payload_json"].get("failure_code"),
            },
            session_id=session_id,
            created_at=created_at,
        )

    def emit_stagnation_threshold_alert(
        self,
        *,
        candidate: TopicScheduleCandidate,
        threshold: int,
        actor: str = "system",
        created_at: datetime | None = None,
    ) -> OperatorAlertResult | None:
        if candidate.consecutive_stagnant_runs < threshold:
            return None
        return self._record_alert(
            alert_type="stagnation_threshold_breach",
            severity="warning",
            detail=(
                f"topic {candidate.topic_id} reached "
                f"{candidate.consecutive_stagnant_runs} stagnant runs"
            ),
            actor=actor,
            payload={
                "topic_id": candidate.topic_id,
                "threshold": threshold,
                "consecutive_stagnant_runs": candidate.consecutive_stagnant_runs,
                "support_challenge_imbalance": candidate.support_challenge_imbalance,
                "unresolved_conflict_count": candidate.unresolved_conflict_count,
            },
            topic_id=candidate.topic_id,
            created_at=created_at,
        )

    def _auth_failure_events(self, session_id: str) -> list[dict[str, Any]]:
        with self._ledger.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM session_events
                WHERE session_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (session_id,),
            ).fetchall()
        events = [dict(row) for row in rows]
        failures: list[dict[str, Any]] = []
        for event in events:
            event["payload_json"] = json.loads(event["payload_json"])
            failure_code = event["payload_json"].get("failure_code")
            if failure_code in self._AUTH_FAILURES:
                failures.append(event)
        return failures

    def _record_alert(
        self,
        *,
        alert_type: str,
        severity: str,
        detail: str,
        actor: str,
        payload: dict[str, Any],
        topic_id: str | None = None,
        queue_item_id: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        created_at: datetime | None = None,
    ) -> OperatorAlertResult:
        alert_id = f"{alert_type}:{_sha256_json(payload)[:16]}"
        emitted = self._ledger.record_operator_alert(
            alert_id=alert_id,
            alert_type=alert_type,
            severity=severity,
            topic_id=topic_id,
            queue_item_id=queue_item_id,
            run_id=run_id,
            session_id=session_id,
            detail=detail,
            payload=payload,
            created_at=created_at,
        )
        if emitted:
            subject_id = (
                topic_id
                or queue_item_id
                or run_id
                or session_id
                or alert_id
            )
            self._ledger.append_operation_audit_event(
                scope="alert",
                subject_id=subject_id,
                event_type="alert.emitted",
                actor=actor,
                payload={
                    "alert_id": alert_id,
                    "alert_type": alert_type,
                    "severity": severity,
                    "detail": detail,
                },
                created_at=created_at,
            )
        return OperatorAlertResult(
            alert_id=alert_id,
            alert_type=alert_type,
            severity=severity,
            detail=detail,
            emitted=emitted,
        )
