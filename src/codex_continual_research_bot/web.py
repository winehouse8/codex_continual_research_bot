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
from codex_continual_research_bot.worker_loop import WorkerLoopService
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

    def worker_loop(self, topic_id: str) -> dict[str, object]:
        ledger = self._backend._initialized_ledger()
        status = WorkerLoopService(ledger, worker_id="web-readonly").status(
            topic_id=topic_id
        )
        return {
            "schema_id": "crb.web.topic.worker_loop.v1",
            "read_only": True,
            "authority_notice": READ_ONLY_NOTICE,
            "topic_id": topic_id,
            "worker_loop": status,
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
        topic = self.topic(topic_id)["topic"]
        runs = self.runs(topic_id)
        queue = self.queue(topic_id)["queue"]
        memory = self.memory(topic_id)["memory"]
        graph = self.graph(topic_id)["graph"]
        worker_loop = self.worker_loop(topic_id)["worker_loop"]
        return {
            "schema_id": "crb.web.topic.dashboard.v1",
            "read_only": True,
            "authority_notice": READ_ONLY_NOTICE,
            "topic_id": topic_id,
            "topic": topic,
            "runs": runs["runs"],
            "run_timeline_items": runs["timeline_items"],
            "queue": queue,
            "memory": memory,
            "graph": graph,
            "worker_loop": worker_loop,
            "run_state": self._run_state_view(
                topic_id=topic_id,
                timeline_items=runs["timeline_items"],
                queue=queue,
                graph=graph,
                worker_loop=worker_loop,
            ),
        }

    def _run_timeline_items(
        self,
        *,
        topic_id: str,
        runs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        queue_items = self._backend.queue_list(topic_id=topic_id)["items"]
        queue_by_id = {str(item["queue_item_id"]): item for item in queue_items}
        run_ids = {str(run["run_id"]) for run in runs}
        ledger_items = [
            {
                **run,
                "timeline_source": "run_ledger",
                "objective": queue_by_id.get(str(run.get("queue_item_id")), {}).get(
                    "objective"
                ),
                "claim": queue_by_id.get(str(run.get("queue_item_id")), {}).get(
                    "claim", {}
                ),
                "failure": queue_by_id.get(str(run.get("queue_item_id")), {}).get(
                    "failure", {}
                ),
            }
            for run in runs
        ]
        queued_items = []
        for item in queue_items:
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
                    "claim": item["claim"],
                    "failure": item["failure"],
                }
            )
        return ledger_items + queued_items

    def _run_state_view(
        self,
        *,
        topic_id: str,
        timeline_items: list[dict[str, Any]],
        queue: dict[str, Any],
        graph: dict[str, Any],
        worker_loop: dict[str, Any],
    ) -> dict[str, object]:
        items = list(queue.get("items", []))
        counts = {
            "running": 0,
            "queued": 0,
            "completed": 0,
            "dead_letter": 0,
            "stale": 0,
        }
        for item in items:
            state = str(item.get("state", ""))
            stale = item.get("claim", {}).get("stale") is True
            if stale:
                counts["stale"] += 1
            elif state == "claimed":
                counts["running"] += 1
            elif state == "queued":
                counts["queued"] += 1
            elif state == "completed":
                counts["completed"] += 1
            elif state == "dead_letter":
                counts["dead_letter"] += 1

        current_item = self._current_queue_item(items)
        running_now = self._running_now_view(
            topic_id=topic_id,
            item=current_item,
            graph=graph,
        )
        return {
            "schema_id": "crb.web.run_state.v1",
            "topic_id": topic_id,
            "worker_loop": {
                "state": worker_loop.get("state", "idle"),
                "active": worker_loop.get("active", False),
                "iteration_count": worker_loop.get("iteration_count", 0),
                "consecutive_no_yield": worker_loop.get("consecutive_no_yield", 0),
                "stop_reason": worker_loop.get("stop_reason"),
                "last_meaningful_graph_change": worker_loop.get(
                    "last_meaningful_graph_change"
                ),
            },
            "status_counts": {
                **counts,
                "total": len(items),
            },
            "running_now": running_now,
            "queue_groups": [
                {
                    "group": key,
                    "label": label,
                    "count": counts[key],
                    "items": [
                        self._queue_status_card(item, graph=graph)
                        for item in items
                        if self._queue_state_group(item) == key
                    ],
                }
                for key, label in [
                    ("running", "Running"),
                    ("queued", "Queued"),
                    ("completed", "Completed"),
                    ("dead_letter", "Dead-letter"),
                    ("stale", "Stale claim"),
                ]
            ],
            "run_timeline_items": [
                {
                    **item,
                    "latest_event": self._latest_event_for_run(
                        run_id=item.get("run_id"),
                        fallback_status=str(item.get("status") or item.get("queue_state")),
                    ),
                    "graph_context": self._graph_context_for_item(item, graph=graph),
                }
                for item in timeline_items
            ],
        }

    def _current_queue_item(self, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        for group in ("running", "stale", "queued", "dead_letter"):
            for item in items:
                if self._queue_state_group(item) == group:
                    return item
        return None

    def _queue_state_group(self, item: dict[str, Any]) -> str:
        if item.get("claim", {}).get("stale") is True:
            return "stale"
        state = str(item.get("state", ""))
        if state == "claimed":
            return "running"
        if state == "dead_letter":
            return "dead_letter"
        if state in {"queued", "completed"}:
            return state
        return "queued"

    def _running_now_view(
        self,
        *,
        topic_id: str,
        item: dict[str, Any] | None,
        graph: dict[str, Any],
    ) -> dict[str, object]:
        if item is None:
            return {
                "state": "idle",
                "topic_id": topic_id,
                "title": "No active or queued work",
                "objective": "No queued, running, stale, or dead-letter work is projected.",
                "run_id": None,
                "queue_item_id": None,
                "latest_event": {
                    "event_type": "idle",
                    "timestamp": None,
                    "detail": "No queue item is waiting for operator or worker action.",
                },
                "graph_context": self._graph_context_for_item({}, graph=graph),
            }
        group = self._queue_state_group(item)
        run_id = item.get("requested_run_id")
        return {
            "state": group,
            "topic_id": topic_id,
            "title": {
                "running": "Running now",
                "queued": "Queued next",
                "stale": "Stale claimed work",
                "dead_letter": "Dead-lettered work",
                "completed": "Recently completed work",
            }.get(group, "Current work"),
            "objective": item.get("objective") or "No objective projected.",
            "run_id": run_id,
            "queue_item_id": item.get("queue_item_id"),
            "latest_event": self._latest_event_for_run(
                run_id=run_id,
                fallback_status=str(item.get("state")),
            ),
            "graph_context": self._graph_context_for_item(item, graph=graph),
        }

    def _queue_status_card(
        self,
        item: dict[str, Any],
        *,
        graph: dict[str, Any],
    ) -> dict[str, object]:
        return {
            "queue_item_id": item.get("queue_item_id"),
            "run_id": item.get("requested_run_id"),
            "state": self._queue_state_group(item),
            "raw_state": item.get("state"),
            "objective": item.get("objective") or "No objective projected.",
            "claim": item.get("claim", {}),
            "failure": item.get("failure", {}),
            "graph_context": self._graph_context_for_item(item, graph=graph),
        }

    def _latest_event_for_run(
        self,
        *,
        run_id: object,
        fallback_status: str,
    ) -> dict[str, object]:
        if not run_id:
            return {
                "event_type": f"queue.{fallback_status}",
                "timestamp": None,
                "detail": f"Queue item is {fallback_status}.",
            }
        try:
            audit = self._backend.ops_audit(run_id=str(run_id))["audit"]
        except (CliBackendError, KeyError):
            return {
                "event_type": f"run.{fallback_status}",
                "timestamp": None,
                "detail": "Run row exists but no runtime event has been recorded yet.",
            }
        events: list[dict[str, Any]] = []
        for event in audit.get("runtime_events", []):
            events.append(
                {
                    "event_type": event.get("event_type"),
                    "timestamp": event.get("timestamp"),
                    "detail": json.dumps(event.get("payload", {}), sort_keys=True),
                }
            )
        for event in audit.get("operation_audit_events", []):
            events.append(
                {
                    "event_type": event.get("event_type"),
                    "timestamp": event.get("created_at"),
                    "detail": json.dumps(event.get("payload", {}), sort_keys=True),
                }
            )
        if not events:
            return {
                "event_type": f"run.{fallback_status}",
                "timestamp": None,
                "detail": "No runtime or operation audit event has been recorded yet.",
            }
        return sorted(events, key=lambda event: str(event.get("timestamp") or ""))[-1]

    def _graph_context_for_item(
        self,
        item: dict[str, Any],
        *,
        graph: dict[str, Any],
    ) -> dict[str, object]:
        run_id = item.get("requested_run_id") or item.get("run_id")
        nodes = list(graph.get("nodes", []))
        selected_ids: set[str] = set()
        if run_id:
            for option in graph.get("provenance_options", []):
                if option.get("run_id") == run_id:
                    selected_ids.add(str(option["provenance_id"]))
            for node in nodes:
                if node.get("temporal_scope") == run_id:
                    selected_ids.add(str(node["node_id"]))
            provenance_ids = set(selected_ids)
            for node in nodes:
                if provenance_ids.intersection(
                    str(value) for value in node.get("provenance_ids", [])
                ):
                    selected_ids.add(str(node["node_id"]))
        if not selected_ids:
            selected_ids.update(str(node_id) for node_id in graph.get("focus_node_ids", []))
        if not selected_ids and graph.get("selected_node") is not None:
            selected_ids.add(str(graph["selected_node"]["node_id"]))
        for _ in range(2):
            before = set(selected_ids)
            for edge in graph.get("edges", []):
                source = str(edge.get("source_node_id"))
                target = str(edge.get("target_node_id"))
                if source in selected_ids or target in selected_ids:
                    selected_ids.update({source, target})
            if selected_ids == before:
                break

        linked = [
            {
                "node_id": node["node_id"],
                "node_type": node["node_type"],
                "group": node["group"],
                "label": node["label"],
            }
            for node in nodes
            if str(node.get("node_id")) in selected_ids
        ]
        relation_counts: dict[str, int] = {}
        for node in linked:
            group = str(node["group"])
            relation_counts[group] = relation_counts.get(group, 0) + 1
        summary = (
            "No graph relation projected for this queue item."
            if not linked
            else ", ".join(
                f"{group}={count}" for group, count in sorted(relation_counts.items())
            )
        )
        return {
            "node_ids": [node["node_id"] for node in linked],
            "nodes": linked,
            "relation_counts": relation_counts,
            "summary": summary,
        }


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
        if suffix == ["worker-loop"]:
            return self._api.worker_loop(topic_id)
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
