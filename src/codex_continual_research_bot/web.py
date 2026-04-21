"""Loopback-only local web dashboard for backend-owned read models."""

from __future__ import annotations

import json
import mimetypes
import posixpath
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from codex_continual_research_bot.cli_backend import LocalBackendGateway
from codex_continual_research_bot.cli_contracts import CliBackendError
from codex_continual_research_bot.operational import OperationalControlService
from codex_continual_research_bot.web_graph_explorer import build_graph_explorer_view


DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8765
READ_ONLY_NOTICE = (
    "Local web dashboard is read-only; backend state, graph, queue, and "
    "provenance ledgers remain authoritative."
)


class ReadOnlyWebApi:
    """Read-only adapter over the existing backend gateway.

    The web surface deliberately reuses CLI/backend read models and does not
    expose topic creation, run enqueue, queue retry, or graph write paths.
    """

    def __init__(self, backend: LocalBackendGateway) -> None:
        self._backend = backend

    def topics(self) -> dict[str, object]:
        data = self._backend.topic_list()
        return {
            "schema_id": "crb.web.topics.v1",
            "read_only": True,
            "authority_notice": READ_ONLY_NOTICE,
            "topics": data["topics"],
        }

    def topic(self, topic_id: str) -> dict[str, object]:
        data = self._backend.topic_show(topic_id=topic_id)
        return {
            "schema_id": "crb.web.topic.v1",
            "read_only": True,
            "authority_notice": READ_ONLY_NOTICE,
            "topic_id": topic_id,
            "topic": data,
        }

    def runs(self, topic_id: str) -> dict[str, object]:
        ledger = self._backend._initialized_ledger()
        runs = OperationalControlService(ledger).run_dashboard(topic_id=topic_id)
        timeline_items = self._run_timeline_items(topic_id=topic_id, runs=runs)
        return {
            "schema_id": "crb.web.topic.runs.v1",
            "read_only": True,
            "authority_notice": READ_ONLY_NOTICE,
            "topic_id": topic_id,
            "runs": runs,
            "timeline_items": timeline_items,
        }

    def run_timeline(self, run_id: str) -> dict[str, object]:
        status = self._backend.run_status(run_id=run_id)
        audit = self._backend.ops_audit(run_id=run_id)["audit"]
        return {
            "schema_id": "crb.web.run.timeline.v1",
            "read_only": True,
            "authority_notice": READ_ONLY_NOTICE,
            "run_id": run_id,
            "status": status,
            "audit": audit,
        }

    def queue(self, topic_id: str) -> dict[str, object]:
        data = self._backend.queue_list(topic_id=topic_id)
        return {
            "schema_id": "crb.web.topic.queue.v1",
            "read_only": True,
            "authority_notice": READ_ONLY_NOTICE,
            "topic_id": topic_id,
            "queue": data,
        }

    def memory(self, topic_id: str) -> dict[str, object]:
        data = self._backend.memory_snapshot(topic_id=topic_id)
        return {
            "schema_id": "crb.web.topic.memory.v1",
            "read_only": True,
            "authority_notice": READ_ONLY_NOTICE,
            "topic_id": topic_id,
            "memory": data,
        }

    def graph(self, topic_id: str, *, scope: str = "latest") -> dict[str, object]:
        if scope not in {"latest", "history"}:
            raise KeyError(f"graph scope {scope} does not exist")
        artifact = self._backend.graph_artifact(topic_id=topic_id, scope=scope)
        return {
            "schema_id": "crb.web.topic.graph.v1",
            "read_only": True,
            "authority_notice": READ_ONLY_NOTICE,
            "topic_id": topic_id,
            "graph": build_graph_explorer_view(artifact, scope=scope),
        }

    def dashboard(self, topic_id: str) -> dict[str, object]:
        return {
            "schema_id": "crb.web.topic.dashboard.v1",
            "read_only": True,
            "authority_notice": READ_ONLY_NOTICE,
            "topic_id": topic_id,
            "topic": self.topic(topic_id)["topic"],
            "runs": self.runs(topic_id)["runs"],
            "queue": self.queue(topic_id)["queue"],
            "memory": self.memory(topic_id)["memory"],
            "graph": self.graph(topic_id)["graph"],
        }

    def _run_timeline_items(
        self,
        *,
        topic_id: str,
        runs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        run_ids = {str(run["run_id"]) for run in runs}
        ledger_items = [
            {**run, "timeline_source": "run_ledger", "objective": None}
            for run in runs
        ]
        queued_items = []
        for item in self._backend.queue_list(topic_id=topic_id)["items"]:
            requested_run_id = item.get("requested_run_id")
            if not requested_run_id or str(requested_run_id) in run_ids:
                continue
            queued_items.append(
                {
                    "run_id": requested_run_id,
                    "topic_id": item["topic_id"],
                    "queue_item_id": item["queue_item_id"],
                    "mode": item["kind"],
                    "status": item["state"],
                    "snapshot_version": None,
                    "created_at": item["available_at"],
                    "updated_at": item["available_at"],
                    "queue_state": item["state"],
                    "last_failure_code": item["failure"].get("failure_code"),
                    "graph_digest": None,
                    "timeline_source": "queue_request",
                    "objective": item["objective"],
                }
            )
        return ledger_items + queued_items


class LocalWebRequestHandler(BaseHTTPRequestHandler):
    server_version = "CRBLocalWeb/1.0"

    def __init__(
        self,
        *args: Any,
        api: ReadOnlyWebApi,
        **kwargs: Any,
    ) -> None:
        self._api = api
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path.startswith("/api/"):
            self._handle_api(path)
            return
        self._handle_static(path)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path.startswith("/api/"):
            self._send_json(self._route_api(path), status=HTTPStatus.OK, include_body=False)
            return
        self._handle_static(path, include_body=False)

    def do_POST(self) -> None:
        self._reject_write()

    def do_PUT(self) -> None:
        self._reject_write()

    def do_PATCH(self) -> None:
        self._reject_write()

    def do_DELETE(self) -> None:
        self._reject_write()

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_api(self, path: str) -> None:
        try:
            self._send_json(self._route_api(path), status=HTTPStatus.OK)
        except CliBackendError as exc:
            status = (
                HTTPStatus.NOT_FOUND
                if exc.failure_code in {"topic_not_found", "run_not_found"}
                else HTTPStatus.SERVICE_UNAVAILABLE
            )
            self._send_json(
                {
                    "schema_id": "crb.web.error.v1",
                    "ok": False,
                    "failure_code": exc.failure_code,
                    "detail": exc.detail,
                    "read_only": True,
                },
                status=status,
            )
        except KeyError as exc:
            self._send_json(
                {
                    "schema_id": "crb.web.error.v1",
                    "ok": False,
                    "failure_code": "not_found",
                    "detail": str(exc),
                    "read_only": True,
                },
                status=HTTPStatus.NOT_FOUND,
            )

    def _route_api(self, raw_path: str) -> dict[str, object]:
        path = PurePosixPath(raw_path)
        parts = [part for part in path.parts if part != "/"]
        if parts == ["api", "topics"]:
            return self._api.topics()
        if len(parts) >= 3 and parts[:2] == ["api", "topics"]:
            return self._route_topic_api(parts[2], parts[3:])
        if (
            len(parts) == 5
            and parts[:3] == ["api", "web", "runs"]
            and parts[4] == "timeline"
        ):
            return self._api.run_timeline(parts[3])
        if len(parts) >= 4 and parts[:3] == ["api", "web", "topics"]:
            return self._route_topic_api(parts[3], parts[4:])
        raise KeyError(f"route {raw_path} does not exist")

    def _route_topic_api(self, topic_id: str, suffix: list[str]) -> dict[str, object]:
        if not suffix:
            return self._api.topic(topic_id)
        if suffix == ["runs"]:
            return self._api.runs(topic_id)
        if suffix == ["queue"]:
            return self._api.queue(topic_id)
        if suffix == ["memory"]:
            return self._api.memory(topic_id)
        if suffix == ["graph"]:
            return self._api.graph(topic_id)
        if len(suffix) == 2 and suffix[0] == "graph":
            return self._api.graph(topic_id, scope=suffix[1])
        if suffix == ["dashboard"]:
            return self._api.dashboard(topic_id)
        raise KeyError(f"topic route /{topic_id}/{'/'.join(suffix)} does not exist")

    def _handle_static(self, path: str, *, include_body: bool = True) -> None:
        if path in {"", "/", "/dashboard"}:
            resource_name = "index.html"
        else:
            normalized = posixpath.normpath(path).lstrip("/")
            if normalized.startswith("../") or normalized == "..":
                self._send_json(
                    {
                        "schema_id": "crb.web.error.v1",
                        "ok": False,
                        "failure_code": "invalid_static_path",
                        "detail": "static path escapes dashboard root",
                    },
                    status=HTTPStatus.BAD_REQUEST,
                    include_body=include_body,
                )
                return
            resource_name = normalized

        try:
            payload = (
                resources.files("codex_continual_research_bot.web_static")
                .joinpath(resource_name)
                .read_bytes()
            )
        except FileNotFoundError:
            self._send_json(
                {
                    "schema_id": "crb.web.error.v1",
                    "ok": False,
                    "failure_code": "static_not_found",
                    "detail": f"static asset {resource_name} does not exist",
                },
                status=HTTPStatus.NOT_FOUND,
                include_body=include_body,
            )
            return

        content_type = mimetypes.guess_type(resource_name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if include_body:
            self.wfile.write(payload)

    def _reject_write(self) -> None:
        self._send_json(
            {
                "schema_id": "crb.web.error.v1",
                "ok": False,
                "failure_code": "read_only_web_surface",
                "detail": READ_ONLY_NOTICE,
                "read_only": True,
            },
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )

    def _send_json(
        self,
        data: dict[str, object],
        *,
        status: HTTPStatus,
        include_body: bool = True,
    ) -> None:
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if include_body:
            self.wfile.write(payload)


def create_web_server(
    *,
    backend: LocalBackendGateway,
    host: str = DEFAULT_WEB_HOST,
    port: int = DEFAULT_WEB_PORT,
) -> ThreadingHTTPServer:
    handler = partial(LocalWebRequestHandler, api=ReadOnlyWebApi(backend))
    return ThreadingHTTPServer((host, port), handler)


def serve_local_dashboard(
    *,
    backend: LocalBackendGateway,
    host: str = DEFAULT_WEB_HOST,
    port: int = DEFAULT_WEB_PORT,
) -> dict[str, object]:
    server = create_web_server(backend=backend, host=host, port=port)
    url = f"http://{server.server_address[0]}:{server.server_address[1]}/"
    print(f"Serving CRB dashboard at {url}")
    print(READ_ONLY_NOTICE)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return {
        "summary": "Local web dashboard stopped.",
        "url": url,
        "host": server.server_address[0],
        "port": server.server_address[1],
        "read_only": True,
        "authority_notice": READ_ONLY_NOTICE,
    }
