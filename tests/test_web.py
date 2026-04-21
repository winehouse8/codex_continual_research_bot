from __future__ import annotations

import json
import threading
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from codex_continual_research_bot.cli import build_parser
from codex_continual_research_bot.cli_backend import LocalBackendGateway
from codex_continual_research_bot.persistence import SQLitePersistenceLedger
from codex_continual_research_bot.web import (
    DEFAULT_WEB_HOST,
    create_web_server,
)


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
    assert ".graph-canvas" in css
    assert ".summary-band" in css
    assert "/graph/" in js
    assert "CRBGraphRenderer" in renderer
