from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from codex_continual_research_bot.cli import build_parser
from codex_continual_research_bot.cli_backend import LocalBackendGateway
from codex_continual_research_bot.contracts import (
    ConflictRef,
    FailureCode,
    HypothesisRef,
    RunLifecycleState,
    TopicSnapshot,
)
from codex_continual_research_bot.persistence import SQLitePersistenceLedger
from codex_continual_research_bot.web import (
    DEFAULT_WEB_HOST,
    create_web_server,
)


NOW = datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)


def seed_backend(tmp_path: Path) -> LocalBackendGateway:
    backend = LocalBackendGateway(db_path=tmp_path / "crb.sqlite3", workspace_root=tmp_path)
    backend.init()
    backend.topic_create(
        title="Codex auth boundary",
        objective="Track session ownership risk",
    )
    started = backend.run_start(
        topic_id="topic_codex_auth_boundary",
        user_input="counterargument: warning-only stale sessions may be safe",
    )
    SQLitePersistenceLedger(tmp_path / "crb.sqlite3").claim_queue_item_for_run(
        queue_item_id=str(started["queue_item_id"]),
        worker_id="web-test",
        run_id=str(started["run_id"]),
        mode="interactive",
    )
    return backend


def seed_run_state_backend(tmp_path: Path) -> LocalBackendGateway:
    backend = LocalBackendGateway(db_path=tmp_path / "crb.sqlite3", workspace_root=tmp_path)
    backend.init()
    backend.topic_create(
        title="Codex auth boundary",
        objective="Track session ownership risk",
    )
    ledger = SQLitePersistenceLedger(tmp_path / "crb.sqlite3")
    snapshot = TopicSnapshot(
        topic_id="topic_codex_auth_boundary",
        snapshot_version=2,
        topic_summary="Track session ownership risk.",
        current_best_hypotheses=[
            HypothesisRef(
                hypothesis_id="hyp_auth_boundary_current_best",
                title="Fail closed on workspace drift",
                summary="Stop scheduled execution when workspace verification is ambiguous.",
            )
        ],
        challenger_targets=[
            HypothesisRef(
                hypothesis_id="hyp_warning_only_stale",
                title="Warning-only stale session path",
                summary="Treat stale but verified sessions as warning-only until contradicted.",
            )
        ],
        active_conflicts=[
            ConflictRef(
                conflict_id="conf_stale_vs_identity_drift",
                summary="Staleness can mean recoverable age or identity drift.",
            )
        ],
        open_questions=["Can stale sessions be retried without hiding identity drift?"],
        recent_provenance_digest="prov_snapshot_auth_boundary",
        queued_user_inputs=[],
    )
    ledger.store_topic_snapshot(snapshot, created_at=NOW)

    queued = backend.run_start(
        topic_id="topic_codex_auth_boundary",
        user_input="queued challenger",
    )
    running = backend.run_start(
        topic_id="topic_codex_auth_boundary",
        user_input="running challenger",
    )
    completed = backend.run_start(
        topic_id="topic_codex_auth_boundary",
        user_input="completed challenger",
    )
    stale = backend.run_start(
        topic_id="topic_codex_auth_boundary",
        user_input="stale challenger",
    )
    dead = backend.run_start(
        topic_id="topic_codex_auth_boundary",
        user_input="dead letter challenger",
    )

    ledger.claim_queue_item_for_run(
        queue_item_id=str(running["queue_item_id"]),
        worker_id="worker-running",
        run_id=str(running["run_id"]),
        mode="interactive",
    )
    ledger.transition_run_state(
        run_id=str(running["run_id"]),
        state=RunLifecycleState.CODEX_EXECUTING,
        snapshot_version=2,
    )
    seed_canonical_graph_write(
        ledger=ledger,
        topic_id="topic_codex_auth_boundary",
        run_id=str(running["run_id"]),
        proposal_id="proposal_running",
        created_at=NOW.isoformat(),
    )

    ledger.claim_queue_item_for_run(
        queue_item_id=str(completed["queue_item_id"]),
        worker_id="worker-completed",
        run_id=str(completed["run_id"]),
        mode="interactive",
    )
    ledger.transition_run_state(
        run_id=str(completed["run_id"]),
        state=RunLifecycleState.COMPLETED,
        snapshot_version=2,
    )
    ledger.complete_queue_item(
        queue_item_id=str(completed["queue_item_id"]),
        run_id=str(completed["run_id"]),
        worker_id="worker-completed",
    )

    ledger.claim_queue_item_for_run(
        queue_item_id=str(stale["queue_item_id"]),
        worker_id="worker-stale",
        run_id=str(stale["run_id"]),
        mode="interactive",
    )
    with ledger.connect() as connection, connection:
        connection.execute(
            """
            UPDATE queue_items
            SET claimed_at = ?
            WHERE id = ?
            """,
            ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), str(stale["queue_item_id"])),
        )
    ledger.transition_run_state(
        run_id=str(stale["run_id"]),
        state=RunLifecycleState.CODEX_EXECUTING,
        snapshot_version=2,
    )

    ledger.claim_queue_item_for_run(
        queue_item_id=str(dead["queue_item_id"]),
        worker_id="worker-dead",
        run_id=str(dead["run_id"]),
        mode="interactive",
    )
    ledger.record_queue_dead_letter(
        queue_item_id=str(dead["queue_item_id"]),
        run_id=str(dead["run_id"]),
        worker_id="worker-dead",
        failure_code=FailureCode.MALFORMED_PROPOSAL.value,
        detail="proposal missing required challenger_hypotheses field",
        retryable=False,
        human_review_required=True,
    )

    assert queued["queue_item_id"]
    return backend


def seed_canonical_graph_write(
    *,
    ledger: SQLitePersistenceLedger,
    topic_id: str,
    run_id: str,
    proposal_id: str,
    created_at: str,
) -> None:
    current_best_id = "hypothesis:hyp_auth_boundary_current_best:v1"
    challenger_id = "hypothesis:hyp_warning_only_stale:v1"
    evidence_id = "evidence:auth_boundary_001"
    provenance_id = f"provenance:{proposal_id}"
    graph_payload = {
        "nodes": [
            {
                "id": current_best_id,
                "label": "Hypothesis",
                "layer": "epistemic",
                "key": "hyp_auth_boundary_current_best:v1",
                "properties": {
                    "hypothesis_id": "hyp_auth_boundary_current_best",
                    "title": "Fail closed on workspace drift",
                    "statement": "Stop scheduled execution when workspace verification is ambiguous.",
                    "version": 1,
                    "is_current_best": True,
                },
            },
            {
                "id": challenger_id,
                "label": "Hypothesis",
                "layer": "epistemic",
                "key": "hyp_warning_only_stale:v1",
                "properties": {
                    "hypothesis_id": "hyp_warning_only_stale",
                    "title": "Warning-only stale session path",
                    "statement": "A stale but verified session may be safe to defer.",
                    "version": 1,
                    "status": "challenger",
                },
            },
            {
                "id": evidence_id,
                "label": "Evidence",
                "layer": "world",
                "key": "auth_boundary_001",
                "properties": {
                    "title": "Session inspection result",
                    "source_url": "https://example.com/session-inspection",
                    "extraction_note": "Inspection ties a run to principal and workspace evidence.",
                    "accessed_at": created_at,
                },
            },
            {
                "id": provenance_id,
                "label": "ProvenanceRecord",
                "layer": "provenance",
                "key": proposal_id,
                "properties": {
                    "proposal_id": proposal_id,
                    "run_id": run_id,
                    "summary_draft": "Running run produced dashboard graph context.",
                },
            },
        ],
        "edges": [
            {
                "id": "edge:stale_challenges_current_best",
                "type": "CHALLENGES",
                "layer": "epistemic",
                "source": challenger_id,
                "target": current_best_id,
                "properties": {},
            },
            {
                "id": "edge:evidence_supports_current_best",
                "type": "SUPPORTS",
                "layer": "epistemic",
                "source": evidence_id,
                "target": current_best_id,
                "properties": {},
            },
            {
                "id": "edge:challenger_recorded",
                "type": "RECORDED_IN",
                "layer": "provenance",
                "source": challenger_id,
                "target": provenance_id,
                "properties": {},
            },
            {
                "id": "edge:evidence_recorded",
                "type": "RECORDED_IN",
                "layer": "provenance",
                "source": evidence_id,
                "target": provenance_id,
                "properties": {},
            },
        ],
    }
    with ledger.connect() as connection, connection:
        connection.execute(
            """
            INSERT INTO canonical_graph_writes(
                run_id, topic_id, proposal_id, graph_digest, node_count,
                edge_count, graph_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                topic_id,
                proposal_id,
                f"sha256:{proposal_id}",
                len(graph_payload["nodes"]),
                len(graph_payload["edges"]),
                json.dumps(graph_payload, sort_keys=True),
                created_at,
            ),
        )


class RunningServer:
    def __init__(self, backend: LocalBackendGateway) -> None:
        self.server = create_web_server(backend=backend, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> RunningServer:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def fetch_json(base_url: str, path: str) -> dict[str, object]:
    with urlopen(f"{base_url}{path}", timeout=5) as response:
        assert response.headers["Content-Type"].startswith("application/json")
        return json.loads(response.read().decode("utf-8"))


def fetch_text(base_url: str, path: str) -> str:
    with urlopen(f"{base_url}{path}", timeout=5) as response:
        return response.read().decode("utf-8")


def test_web_server_routes_smoke(tmp_path: Path) -> None:
    backend = seed_backend(tmp_path)

    with RunningServer(backend) as server:
        html = fetch_text(server.base_url, "/")
        topics = fetch_json(server.base_url, "/api/topics")
        topic = fetch_json(server.base_url, "/api/topics/topic_codex_auth_boundary")
        runs = fetch_json(server.base_url, "/api/topics/topic_codex_auth_boundary/runs")
        queue = fetch_json(server.base_url, "/api/topics/topic_codex_auth_boundary/queue")
        memory = fetch_json(server.base_url, "/api/topics/topic_codex_auth_boundary/memory")
        graph = fetch_json(server.base_url, "/api/topics/topic_codex_auth_boundary/graph/latest")

    assert "Research Dashboard" in html
    assert topics["schema_id"] == "crb.web.topics.v1"
    assert topic["schema_id"] == "crb.web.topic.v1"
    assert runs["schema_id"] == "crb.web.topic.runs.v1"
    assert queue["schema_id"] == "crb.web.topic.queue.v1"
    assert memory["schema_id"] == "crb.web.topic.memory.v1"
    assert graph["schema_id"] == "crb.web.topic.graph.v1"
    assert graph["graph"]["scope"] == "latest"


def test_web_api_json_response_schema(tmp_path: Path) -> None:
    backend = seed_backend(tmp_path)

    with RunningServer(backend) as server:
        topics = fetch_json(server.base_url, "/api/topics")
        dashboard = fetch_json(
            server.base_url,
            "/api/web/topics/topic_codex_auth_boundary/dashboard",
        )
        history = fetch_json(
            server.base_url,
            "/api/web/topics/topic_codex_auth_boundary/graph/history",
        )

    assert topics["read_only"] is True
    assert isinstance(topics["topics"], list)
    assert set(topics["topics"][0]) >= {
        "topic_id",
        "title",
        "status",
        "snapshot_version",
    }
    assert dashboard["schema_id"] == "crb.web.topic.dashboard.v1"
    assert dashboard["read_only"] is True
    assert {"topic", "runs", "queue", "memory", "graph"} <= set(dashboard)
    assert history["graph"]["scope"] == "history"
    assert history["graph"]["renderer"]["kind"] == "local_svg_graph_renderer"


def test_web_dashboard_run_state_view_model_links_queue_run_and_graph(tmp_path: Path) -> None:
    backend = seed_run_state_backend(tmp_path)

    with RunningServer(backend) as server:
        dashboard = fetch_json(
            server.base_url,
            "/api/web/topics/topic_codex_auth_boundary/dashboard",
        )

    run_state = dashboard["run_state"]
    assert run_state["schema_id"] == "crb.web.run_state.v1"
    assert run_state["status_counts"] == {
        "running": 1,
        "queued": 1,
        "completed": 1,
        "dead_letter": 1,
        "stale": 1,
        "total": 5,
    }
    assert run_state["running_now"]["state"] == "running"
    assert run_state["running_now"]["run_id"].startswith("run_")
    assert run_state["running_now"]["queue_item_id"].startswith("queue_")
    assert "running challenger" in run_state["running_now"]["objective"]
    assert run_state["running_now"]["latest_event"]["event_type"].startswith("run.")

    relation_counts = run_state["running_now"]["graph_context"]["relation_counts"]
    assert relation_counts["current_best"] == 1
    assert relation_counts["challenger"] == 1
    assert relation_counts["evidence"] == 1
    assert relation_counts["provenance"] == 1
    assert any(
        group["group"] == "dead_letter" and group["count"] == 1
        for group in run_state["queue_groups"]
    )
    assert any(
        group["group"] == "stale" and group["items"][0]["claim"]["stale"] is True
        for group in run_state["queue_groups"]
    )


def test_web_server_default_bind_is_loopback(tmp_path: Path) -> None:
    backend = seed_backend(tmp_path)
    server = create_web_server(backend=backend, port=0)
    try:
        assert server.server_address[0] == DEFAULT_WEB_HOST
        assert server.server_address[0] != "0.0.0.0"
    finally:
        server.server_close()


def test_crb_web_serve_help_lists_localhost_defaults(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["web", "serve", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--host" in help_text
    assert "--port" in help_text
    assert "Serve the localhost dashboard" in help_text


def test_web_surface_rejects_direct_writes(tmp_path: Path) -> None:
    backend = seed_backend(tmp_path)
    before_topics = backend.topic_list()["topics"]
    before_queue = backend.queue_list(topic_id="topic_codex_auth_boundary")["items"]

    with RunningServer(backend) as server:
        request = Request(
            f"{server.base_url}/api/topics",
            data=b'{"title":"Direct write"}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as exc:
            urlopen(request, timeout=5)

        body = json.loads(exc.value.read().decode("utf-8"))

    after_topics = backend.topic_list()["topics"]
    after_queue = backend.queue_list(topic_id="topic_codex_auth_boundary")["items"]
    assert exc.value.code == HTTPStatus.METHOD_NOT_ALLOWED
    assert body["failure_code"] == "read_only_web_surface"
    assert after_topics == before_topics
    assert after_queue == before_queue


def test_html_shell_smoke(tmp_path: Path) -> None:
    backend = seed_backend(tmp_path)

    with RunningServer(backend) as server:
        html = fetch_text(server.base_url, "/dashboard")
        css = fetch_text(server.base_url, "/styles.css")
        js = fetch_text(server.base_url, "/app.js")
        renderer = fetch_text(server.base_url, "/graph-renderer.js")

    assert 'id="topicSelect"' in html
    assert 'id="graphCanvas"' in html
    assert 'src="/graph-renderer.js"' in html
    assert 'id="runsList"' in html
    assert 'id="queueList"' in html
    assert 'id="memoryList"' in html
    assert 'id="runningNowCard"' in html
    assert 'id="deadLetterCount"' in html
    assert 'id="staleCount"' in html
    assert ".graph-canvas" in css
    assert ".summary-band" in css
    assert ".current-work" in css
    assert "/graph/" in js
    assert "/dashboard" in js
    assert "CRBGraphRenderer" in renderer
