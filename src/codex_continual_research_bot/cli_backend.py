"""Local backend gateway used by the Phase 13 CLI."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from codex_continual_research_bot.cli_contracts import CliBackendError
from codex_continual_research_bot.contracts import (
    HypothesisRef,
    QueueJob,
    QueueJobKind,
    QueueJobState,
    QueuePayload,
    TopicSnapshot,
)
from codex_continual_research_bot.operational import (
    OperationalControlError,
    OperationalControlService,
)
from codex_continual_research_bot.graph_visualization import (
    build_graph_export_artifact,
    render_graph_artifact,
)
from codex_continual_research_bot.persistence import (
    DuplicateIdempotencyKeyError,
    QueueMutationMismatchError,
    SQLitePersistenceLedger,
)
from codex_continual_research_bot.ux_contracts import GraphExportArtifact


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(data: object) -> str:
    return f"sha256:{sha256(_canonical_json(data).encode('utf-8')).hexdigest()}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "topic"


def _row_json(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        return {}
    return json.loads(value)


class LocalBackendGateway:
    """Backend-owned local gateway for the executable CLI.

    The CLI command module depends only on this gateway interface. Persistence
    writes stay behind this adapter so command handlers cannot bypass backend
    boundaries.
    """

    def __init__(self, *, db_path: Path, workspace_root: Path | None = None) -> None:
        self.db_path = db_path
        self.workspace_root = (workspace_root or Path.cwd()).resolve()

    @classmethod
    def from_environment(cls) -> LocalBackendGateway:
        db_path = Path(os.environ.get("CRB_DB_PATH", ".crb/crb.sqlite3"))
        return cls(db_path=db_path)

    def init(self) -> dict[str, object]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        migrations = self._ledger().initialize()
        return {
            "summary": "Local CRB backend storage is initialized.",
            "db_path": str(self.db_path),
            "migrations": migrations,
            "human": [
                f"Database: {self.db_path}",
                f"Migrations applied: {len(migrations)}",
            ],
        }

    def doctor(self) -> dict[str, object]:
        exists = self.db_path.exists()
        migration_count = 0
        if exists:
            try:
                with sqlite3.connect(self.db_path) as connection:
                    migration_count = connection.execute(
                        "SELECT COUNT(*) FROM schema_migrations"
                    ).fetchone()[0]
            except sqlite3.Error:
                migration_count = 0
        return {
            "summary": "Local CRB operator configuration inspected.",
            "db_path": str(self.db_path),
            "database_exists": exists,
            "migration_count": migration_count,
            "workspace_root": str(self.workspace_root),
            "human": [
                f"Database: {self.db_path}",
                f"Exists: {'yes' if exists else 'no'}",
                f"Migrations: {migration_count}",
                f"Workspace: {self.workspace_root}",
            ],
        }

    def topic_create(self, *, title: str, objective: str) -> dict[str, object]:
        ledger = self._initialized_ledger()
        slug = _slugify(title)
        topic_id = f"topic_{slug}"
        now = _utcnow()
        snapshot = TopicSnapshot(
            topic_id=topic_id,
            snapshot_version=1,
            topic_summary=objective,
            current_best_hypotheses=[
                HypothesisRef(
                    hypothesis_id=f"hyp_{slug}_current_best",
                    title="Initial objective hypothesis",
                    summary=objective,
                )
            ],
            challenger_targets=[],
            active_conflicts=[],
            open_questions=[
                "What evidence would most strongly challenge the initial objective?"
            ],
            recent_provenance_digest=_digest(
                {"topic_id": topic_id, "title": title, "objective": objective}
            ),
            queued_user_inputs=[],
        )
        try:
            ledger.create_topic(topic_id=topic_id, slug=slug, title=title, created_at=now)
            ledger.store_topic_snapshot(snapshot, created_at=now)
        except sqlite3.IntegrityError as exc:
            raise CliBackendError(
                failure_code="topic_already_exists",
                detail=f"topic {topic_id} already exists",
                retryable=False,
                human_review_required=False,
            ) from exc
        return {
            "summary": f"Created topic {topic_id}.",
            "topic_id": topic_id,
            "snapshot_version": snapshot.snapshot_version,
            "topic": snapshot.model_dump(mode="json"),
            "human": [
                f"Topic: {title}",
                f"Topic id: {topic_id}",
                "Current best hypothesis initialized from the objective.",
            ],
        }

    def topic_list(self) -> dict[str, object]:
        rows = self._topic_rows()
        topics = [
            {
                "topic_id": row["id"],
                "slug": row["slug"],
                "title": row["title"],
                "status": row["status"],
                "snapshot_version": row["snapshot_version"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
        return {
            "summary": f"Found {len(topics)} topic(s).",
            "topics": topics,
            "human": [
                (
                    f"- {topic['topic_id']} v{topic['snapshot_version']}: "
                    f"{topic['title']} ({topic['status']})"
                )
                for topic in topics
            ]
            or ["No topics found."],
        }

    def topic_show(self, *, topic_id: str) -> dict[str, object]:
        snapshot = self._snapshot(topic_id)
        queue = self._queue_items(topic_id=topic_id)
        human = [
            f"Topic: {topic_id}",
            f"Snapshot: v{snapshot.snapshot_version}",
            "Current best hypotheses:",
            *[f"- {item.title}: {item.summary}" for item in snapshot.current_best_hypotheses],
            "Challenger targets:",
            *(
                [f"- {item.title}: {item.summary}" for item in snapshot.challenger_targets]
                or ["- None selected yet."]
            ),
            "Active conflicts:",
            *(
                [f"- {item.conflict_id}: {item.summary}" for item in snapshot.active_conflicts]
                or ["- None recorded."]
            ),
            "Queue:",
            *(
                [
                    f"- {item['id']} {item['kind']} ({item['state']}): "
                    f"{_row_json(item, 'payload_json').get('objective', '')}"
                    for item in queue
                ]
                or ["- No queued work."]
            ),
        ]
        return {
            "summary": f"Loaded topic {topic_id}.",
            "topic": snapshot.model_dump(mode="json"),
            "queue": [self._queue_view(row) for row in queue],
            "human": human,
        }

    def run_start(self, *, topic_id: str, user_input: str) -> dict[str, object]:
        snapshot = self._snapshot(topic_id)
        suffix = sha256(
            _canonical_json(
                {
                    "topic_id": topic_id,
                    "snapshot_version": snapshot.snapshot_version,
                    "input": user_input,
                }
            ).encode("utf-8")
        ).hexdigest()[:12]
        queue_item_id = f"queue_{suffix}"
        run_id = f"run_{suffix}"
        idempotency_key = f"run.start:{topic_id}:{suffix}"
        return self._enqueue_run_request(
            topic_id=topic_id,
            queue_item_id=queue_item_id,
            run_id=run_id,
            kind=QueueJobKind.RUN_EXECUTE,
            idempotency_key=idempotency_key,
            objective=f"Run interactive research for CLI input: {user_input}",
            priority=100,
            max_attempts=1,
            summary=f"Enqueued run {run_id}.",
        )

    def run_status(self, *, run_id: str) -> dict[str, object]:
        ledger = self._initialized_ledger()
        run = ledger.fetch_run(run_id)
        queue = self._queue_for_run_id(run_id)
        report = ledger.fetch_interactive_run_report(run_id=run_id) if run is not None else None
        if run is None and queue is None:
            raise CliBackendError(
                failure_code="run_not_found",
                detail=f"run {run_id} does not exist",
                retryable=False,
                human_review_required=False,
            )
        status = run["status"] if run is not None else queue["state"]
        failure = None if queue is None else self._failure_view(queue)
        data = {
            "summary": f"Run {run_id} is {status}.",
            "run_id": run_id,
            "run": None if run is None else dict(run),
            "queue": None if queue is None else self._queue_view(queue),
            "report": None if report is None else report.model_dump(mode="json"),
            "failure": failure,
            "human": [
                f"Run: {run_id}",
                f"Status: {status}",
                (
                    "Failure: none"
                    if failure is None
                    else (
                        f"Failure: {failure['failure_code']} "
                        f"(retryable={failure['retryable']}, "
                        f"human_review_required={failure['human_review_required']})"
                    )
                ),
            ],
        }
        return data

    def run_resume(self, *, run_id: str) -> dict[str, object]:
        run = self._initialized_ledger().fetch_run(run_id)
        queue = self._queue_for_run_id(run_id)
        if run is None and queue is None:
            raise CliBackendError(
                failure_code="run_not_found",
                detail=f"run {run_id} does not exist",
                retryable=False,
                human_review_required=False,
            )
        topic_id = str(run["topic_id"] if run is not None else queue["topic_id"])
        suffix = sha256(f"resume:{run_id}".encode("utf-8")).hexdigest()[:12]
        return self._enqueue_run_request(
            topic_id=topic_id,
            queue_item_id=f"queue_resume_{suffix}",
            run_id=run_id,
            kind=QueueJobKind.RUN_RESUME,
            idempotency_key=f"run.resume:{run_id}",
            objective=f"Resume interrupted run {run_id} through backend idempotency state.",
            priority=90,
            max_attempts=1,
            summary=f"Submitted resume request for {run_id}.",
        )

    def queue_list(self, *, topic_id: str | None) -> dict[str, object]:
        rows = self._queue_items(topic_id=topic_id)
        items = [self._queue_view(row) for row in rows]
        retryable = sum(1 for item in items if item["failure"].get("retryable") is True)
        human_review = sum(
            1 for item in items if item["failure"].get("human_review_required") is True
        )
        return {
            "summary": f"Found {len(items)} queue item(s).",
            "topic_id": topic_id,
            "items": items,
            "retryable_failure_count": retryable,
            "human_review_failure_count": human_review,
            "human": [
                (
                    f"- {item['queue_item_id']} {item['kind']} ({item['state']}): "
                    f"retryable={item['failure'].get('retryable', False)} "
                    f"human_review={item['failure'].get('human_review_required', False)}"
                )
                for item in items
            ]
            or ["No queue items found."],
        }

    def queue_retry(self, *, queue_item_id: str, reason: str) -> dict[str, object]:
        service = OperationalControlService(self._initialized_ledger())
        try:
            service.recover_dead_letter(
                queue_item_id=queue_item_id,
                actor="cli",
                reason=reason,
                available_at=_utcnow(),
            )
        except (KeyError, QueueMutationMismatchError) as exc:
            raise CliBackendError(
                failure_code="queue_retry_rejected",
                detail=str(exc),
                retryable=False,
                human_review_required=True,
            ) from exc
        item = self._initialized_ledger().fetch_queue_item(queue_item_id)
        return {
            "summary": f"Recovered dead-letter queue item {queue_item_id}.",
            "queue_item_id": queue_item_id,
            "queue": None if item is None else self._queue_view(item),
            "human": [
                f"Queue item: {queue_item_id}",
                "State: queued",
                f"Reason: {reason}",
            ],
        }

    def queue_dead_letter(self, *, queue_item_id: str) -> dict[str, object]:
        row = self._initialized_ledger().fetch_queue_item(queue_item_id)
        if row is None:
            raise CliBackendError(
                failure_code="queue_item_not_found",
                detail=f"queue item {queue_item_id} does not exist",
                retryable=False,
                human_review_required=False,
            )
        view = self._queue_view(row)
        return {
            "summary": f"Queue item {queue_item_id} is {view['state']}.",
            "queue_item_id": queue_item_id,
            "queue": view,
            "human": [
                f"Queue item: {queue_item_id}",
                f"State: {view['state']}",
                f"Failure code: {view['failure'].get('failure_code', 'none')}",
                f"Retryable: {view['failure'].get('retryable', False)}",
                f"Human review required: {view['failure'].get('human_review_required', False)}",
            ],
        }

    def memory_snapshot(self, *, topic_id: str) -> dict[str, object]:
        snapshot = self._snapshot(topic_id)
        graph = self._latest_graph(topic_id)
        return {
            "summary": f"Loaded memory snapshot for {topic_id}.",
            "topic_id": topic_id,
            "graph_digest": (
                graph["graph_digest"] if graph is not None else snapshot.recent_provenance_digest
            ),
            "hypothesis_count": len(snapshot.current_best_hypotheses)
            + len(snapshot.challenger_targets),
            "conflict_count": len(snapshot.active_conflicts),
            "provenance_digest": snapshot.recent_provenance_digest,
            "visualization_notice": (
                "Graph visualization is not a source of truth; backend graph and "
                "provenance ledgers remain authoritative."
            ),
        }

    def memory_conflicts(self, *, topic_id: str) -> dict[str, object]:
        snapshot = self._snapshot(topic_id)
        conflicts = [conflict.model_dump(mode="json") for conflict in snapshot.active_conflicts]
        return {
            "summary": f"Found {len(conflicts)} active conflict(s) for {topic_id}.",
            "topic_id": topic_id,
            "conflicts": conflicts,
            "human": [
                f"- {conflict['conflict_id']}: {conflict['summary']}"
                for conflict in conflicts
            ]
            or ["No active conflicts recorded."],
        }

    def memory_hypotheses(self, *, topic_id: str) -> dict[str, object]:
        snapshot = self._snapshot(topic_id)
        hypotheses = [
            {"role": "current_best", **item.model_dump(mode="json")}
            for item in snapshot.current_best_hypotheses
        ] + [
            {"role": "challenger", **item.model_dump(mode="json")}
            for item in snapshot.challenger_targets
        ]
        return {
            "summary": f"Found {len(hypotheses)} hypothesis view(s) for {topic_id}.",
            "topic_id": topic_id,
            "hypotheses": hypotheses,
            "human": [
                f"- {item['role']}: {item['title']} ({item['hypothesis_id']})"
                for item in hypotheses
            ],
        }

    def graph_export(
        self,
        *,
        topic_id: str,
        output_format: str,
        output_path: str,
    ) -> dict[str, object]:
        if output_format not in {"json", "dot", "mermaid"}:
            raise CliBackendError(
                failure_code="unsupported_graph_format",
                detail=f"graph export format {output_format} is not supported",
                retryable=False,
                human_review_required=False,
            )
        artifact = self._graph_artifact(topic_id)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_graph_artifact(artifact, output_format=output_format))
        return {
            "summary": f"Exported graph artifact for {topic_id}.",
            "topic_id": topic_id,
            "output_path": str(path),
            "format": output_format,
            "graph_digest": artifact.graph_digest,
            "human": [
                f"Output: {path}",
                artifact.authority_notice,
            ],
        }

    def graph_view(
        self,
        *,
        topic_id: str,
        output_format: str,
        output_path: str,
    ) -> dict[str, object]:
        if output_format != "html":
            raise CliBackendError(
                failure_code="unsupported_graph_format",
                detail=f"graph view format {output_format} is not supported",
                retryable=False,
                human_review_required=False,
            )
        artifact = self._graph_artifact(topic_id)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_graph_artifact(artifact, output_format=output_format))
        return {
            "summary": f"Rendered graph view for {topic_id}.",
            "topic_id": topic_id,
            "output_path": str(path),
            "format": output_format,
            "graph_digest": artifact.graph_digest,
            "human": [
                f"Output: {path}",
                artifact.authority_notice,
            ],
        }

    def ops_health(self) -> dict[str, object]:
        ledger = self._initialized_ledger()
        queue = OperationalControlService(ledger).queue_dashboard()
        rows = self._topic_rows()
        return {
            "summary": "Backend health inspected.",
            "db_path": str(self.db_path),
            "topic_count": len(rows),
            "queue": queue,
            "human": [
                f"Database: {self.db_path}",
                f"Topics: {len(rows)}",
                f"Queue states: {queue['state_counts']}",
                f"Dead letters: {len(queue['dead_letters'])}",
            ],
        }

    def ops_audit(self, *, run_id: str) -> dict[str, object]:
        audit = OperationalControlService(self._initialized_ledger()).event_dashboard(
            run_id=run_id
        )
        return {
            "summary": f"Loaded audit trail for {run_id}.",
            "run_id": run_id,
            "audit": audit,
            "human": [
                f"Runtime events: {len(audit['runtime_events'])}",
                f"Operation audit events: {len(audit['operation_audit_events'])}",
            ],
        }

    def ops_replay(self, *, run_id: str, reason: str) -> dict[str, object]:
        try:
            replay = OperationalControlService(self._initialized_ledger()).replay_run(
                run_id=run_id,
                actor="cli",
            )
        except OperationalControlError as exc:
            raise CliBackendError(
                failure_code="replay_rejected",
                detail=str(exc),
                retryable=False,
                human_review_required=True,
            ) from exc
        return {
            "summary": f"Replayed run {run_id}.",
            "run_id": run_id,
            "reason": reason,
            "replay": {
                "topic_id": replay.topic_id,
                "graph_digest": replay.graph_digest,
                "report_digest": replay.report_digest,
                "event_count": replay.event_count,
                "audit_event_id": replay.audit_event_id,
            },
            "human": [
                f"Graph digest: {replay.graph_digest}",
                f"Report digest: {replay.report_digest}",
                f"Runtime events: {replay.event_count}",
            ],
        }

    def _ledger(self) -> SQLitePersistenceLedger:
        return SQLitePersistenceLedger(self.db_path)

    def _initialized_ledger(self) -> SQLitePersistenceLedger:
        if not self.db_path.exists():
            raise CliBackendError(
                failure_code="backend_not_initialized",
                detail="run `crb init` before using backend-owned state commands",
                retryable=False,
                human_review_required=False,
            )
        ledger = self._ledger()
        ledger.initialize()
        return ledger

    def _snapshot(self, topic_id: str) -> TopicSnapshot:
        snapshot = self._initialized_ledger().fetch_topic_snapshot(topic_id=topic_id)
        if snapshot is None:
            raise CliBackendError(
                failure_code="topic_not_found",
                detail=f"topic {topic_id} does not exist",
                retryable=False,
                human_review_required=False,
            )
        return snapshot

    def _enqueue_run_request(
        self,
        *,
        topic_id: str,
        queue_item_id: str,
        run_id: str,
        kind: QueueJobKind,
        idempotency_key: str,
        objective: str,
        priority: int,
        max_attempts: int,
        summary: str,
    ) -> dict[str, object]:
        ledger = self._initialized_ledger()
        request_digest = _digest(
            {
                "topic_id": topic_id,
                "queue_item_id": queue_item_id,
                "run_id": run_id,
                "kind": kind.value,
                "objective": objective,
            }
        )
        existing = ledger.get_idempotency_record(idempotency_key)
        if existing is None:
            try:
                ledger.reserve_idempotency_key(
                    idempotency_key=idempotency_key,
                    scope=kind.value,
                    request_digest=request_digest,
                )
                ledger.enqueue_job(
                    QueueJob(
                        queue_item_id=queue_item_id,
                        kind=kind,
                        state=QueueJobState.QUEUED,
                        topic_id=topic_id,
                        requested_run_id=run_id,
                        dedupe_key=idempotency_key,
                        idempotency_key=idempotency_key,
                        priority=priority,
                        attempts=0,
                        max_attempts=max_attempts,
                        available_at=_utcnow(),
                        payload=QueuePayload(
                            initiator="cli",
                            objective=objective,
                            selected_queue_item_ids=[queue_item_id],
                        ),
                    )
                )
            except DuplicateIdempotencyKeyError:
                existing = ledger.get_idempotency_record(idempotency_key)
        queue = self._queue_for_run_id(run_id)
        return {
            "summary": summary,
            "topic_id": topic_id,
            "run_id": run_id,
            "queue_item_id": queue_item_id,
            "duplicate": existing is not None,
            "queue": None if queue is None else self._queue_view(queue),
            "human": [
                f"Run: {run_id}",
                f"Queue item: {queue_item_id}",
                f"Action: {kind.value}",
            ],
        }

    def _topic_rows(self) -> list[dict[str, Any]]:
        ledger = self._initialized_ledger()
        with ledger.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    topics.*,
                    MAX(topic_snapshots.snapshot_version) AS snapshot_version
                FROM topics
                LEFT JOIN topic_snapshots ON topic_snapshots.topic_id = topics.id
                GROUP BY topics.id
                ORDER BY topics.updated_at DESC, topics.id ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def _queue_items(self, *, topic_id: str | None = None) -> list[dict[str, Any]]:
        ledger = self._initialized_ledger()
        clauses: list[str] = []
        params: list[Any] = []
        if topic_id is not None:
            clauses.append("topic_id = ?")
            params.append(topic_id)
        where = "" if not clauses else f"WHERE {' AND '.join(clauses)}"
        with ledger.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM queue_items
                {where}
                ORDER BY updated_at DESC, priority DESC, id ASC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def _queue_for_run_id(self, run_id: str) -> dict[str, Any] | None:
        ledger = self._initialized_ledger()
        with ledger.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM queue_items
                WHERE requested_run_id = ?
                ORDER BY updated_at DESC, id ASC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def _queue_view(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = _row_json(row, "payload_json")
        failure = self._failure_view(row)
        return {
            "queue_item_id": row["id"],
            "topic_id": row["topic_id"],
            "kind": row["kind"],
            "state": row["state"],
            "requested_run_id": row["requested_run_id"],
            "priority": row["priority"],
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
            "available_at": row["available_at"],
            "objective": payload.get("objective", ""),
            "failure": {} if failure is None else failure,
        }

    def _failure_view(self, row: dict[str, Any]) -> dict[str, object] | None:
        if row.get("last_failure_code") is None:
            return None
        return {
            "failure_code": row["last_failure_code"],
            "detail": row["last_failure_detail"],
            "retryable": bool(row["last_failure_retryable"]),
            "human_review_required": bool(row["last_failure_human_review"]),
        }

    def _latest_graph(self, topic_id: str) -> dict[str, Any] | None:
        ledger = self._initialized_ledger()
        with ledger.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM canonical_graph_writes
                WHERE topic_id = ?
                ORDER BY created_at DESC, run_id ASC
                LIMIT 1
                """,
                (topic_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def _graph_artifact(self, topic_id: str) -> GraphExportArtifact:
        snapshot = self._snapshot(topic_id)
        return build_graph_export_artifact(
            topic_id=topic_id,
            snapshot=snapshot,
            graph_write=self._latest_graph(topic_id),
            generated_at=_utcnow(),
        )
