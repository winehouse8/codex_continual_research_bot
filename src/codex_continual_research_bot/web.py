"""Loopback-only local web dashboard for backend-owned read models."""

from __future__ import annotations

import json
import mimetypes
import posixpath
from datetime import datetime, timezone
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
    "이 dashboard는 read-only projection이며 not a source of truth입니다. "
    "Source of truth는 backend state, canonical graph, queue, provenance ledger입니다."
)

DASHBOARD_GLOSSARY: tuple[dict[str, str], ...] = (
    {
        "term": "TOP",
        "short_label": "TOP",
        "korean_label": "토픽",
        "plain_explanation": "연구가 다루는 주제 단위입니다.",
        "why_it_matters": "모든 run, queue item, graph projection이 특정 topic 아래에 묶입니다.",
        "example": "topic_codex_auth_boundary",
    },
    {
        "term": "HYP",
        "short_label": "HYP",
        "korean_label": "가설",
        "plain_explanation": "현재 최선 설명이나 그 설명에 도전하는 후보입니다.",
        "why_it_matters": "CRB는 fact를 쌓는 대신 hypothesis를 경쟁시키며 수정합니다.",
        "example": "Current best 또는 challenger hypothesis",
    },
    {
        "term": "CLA",
        "short_label": "CLA",
        "korean_label": "주장",
        "plain_explanation": "가설을 구성하거나 검증하는 개별 claim입니다.",
        "why_it_matters": "claim은 evidence와 연결되어 hypothesis를 지지하거나 흔듭니다.",
        "example": "stale session은 identity drift 신호일 수 있음",
    },
    {
        "term": "EVI",
        "short_label": "EVI",
        "korean_label": "근거",
        "plain_explanation": "claim이나 hypothesis를 판단하는 데 쓰인 evidence입니다.",
        "why_it_matters": "근거 없는 결론은 current best로 유지되지 않아야 합니다.",
        "example": "session inspection result",
    },
    {
        "term": "PRO",
        "short_label": "PRO",
        "korean_label": "출처 기록",
        "plain_explanation": "어떤 run/proposal이 이 graph node를 만들었는지 남기는 provenance입니다.",
        "why_it_matters": "나중에 결론이 바뀌어도 어떤 실행에서 비롯됐는지 추적할 수 있습니다.",
        "example": "provenance:proposal_running",
    },
    {
        "term": "CON",
        "short_label": "CON",
        "korean_label": "충돌",
        "plain_explanation": "서로 긴장하거나 동시에 참이라고 보기 어려운 설명입니다.",
        "why_it_matters": "unresolved conflict는 다음 run이 다시 다뤄야 하는 frontier입니다.",
        "example": "stale claim이 recoverable age인지 identity drift인지 불명확함",
    },
    {
        "term": "supports",
        "short_label": "supports",
        "korean_label": "지지",
        "plain_explanation": "한 node가 다른 claim/hypothesis를 강화하는 edge입니다.",
        "why_it_matters": "가설이 왜 유지되는지 확인하는 연결입니다.",
        "example": "evidence supports current best",
    },
    {
        "term": "challenges",
        "short_label": "challenges",
        "korean_label": "도전",
        "plain_explanation": "한 node가 기존 설명을 공격하거나 대안을 제시하는 edge입니다.",
        "why_it_matters": "반복 연구가 더 좋아지려면 challenge pressure가 보여야 합니다.",
        "example": "challenger challenges current best",
    },
    {
        "term": "visualizes",
        "short_label": "visualizes",
        "korean_label": "시각화",
        "plain_explanation": "backend state를 사람이 읽기 쉬운 projection으로 보여주는 edge입니다.",
        "why_it_matters": "시각화는 검토 화면일 뿐 source of truth가 아닙니다.",
        "example": "graph export visualizes canonical graph write",
    },
    {
        "term": "dead-letter",
        "short_label": "Dead-letter",
        "korean_label": "사람 확인이 필요한 실패",
        "plain_explanation": "자동 처리로 계속 진행하면 위험해서 격리된 queue item입니다.",
        "why_it_matters": "failure code, retry 가능 여부, human review 필요 여부를 보고 다음 행동을 정해야 합니다.",
        "example": "malformed_proposal",
    },
    {
        "term": "Queue",
        "short_label": "Queue",
        "korean_label": "작업 대기열",
        "plain_explanation": "worker가 처리할 research run 또는 repair 작업 목록입니다.",
        "why_it_matters": "총 queue 수와 현재 실행 중 worker 수를 구분해야 합니다.",
        "example": "queued, claimed, completed, dead_letter",
    },
    {
        "term": "Worker loop",
        "short_label": "Worker loop",
        "korean_label": "자동 실행 루프",
        "plain_explanation": "queue item을 claim하고 Codex runtime을 실행하는 backend loop입니다.",
        "why_it_matters": "멈춤 사유와 no-yield streak를 보면 자동 연구가 계속 진행 가능한지 판단할 수 있습니다.",
        "example": "max_consecutive_no_yield",
    },
)

DASHBOARD_HELP_SECTIONS: tuple[dict[str, object], ...] = (
    {
        "view_id": "overview",
        "title": "개요를 읽는 법",
        "summary": "현재 실행 상태, 대기열 수, current best hypothesis, active conflict를 먼저 확인합니다.",
        "checkpoints": [
            "Running은 실제 worker가 claim한 작업 수이고 Queue total은 전체 작업 수입니다.",
            "Dead-letter 또는 Stale이 0이 아니면 자동 진행이 막혔는지 확인합니다.",
            "Current best와 Active conflict가 함께 보이는지 확인합니다.",
        ],
    },
    {
        "view_id": "graph",
        "title": "그래프를 읽는 법",
        "summary": "badge 약어와 edge 의미를 보고 hypothesis가 어떤 근거로 지지되거나 도전받는지 봅니다.",
        "checkpoints": [
            "HYP는 가설, EVI는 근거, PRO는 출처 기록, CON은 unresolved conflict입니다.",
            "supports는 지지, challenges는 도전 관계입니다.",
            "Latest는 최신 graph write, History는 누적 projection입니다.",
        ],
    },
    {
        "view_id": "runs",
        "title": "실행 시간을 읽는 법",
        "summary": "요청, claim, 시작, 완료/실패 시각과 duration을 같이 봅니다.",
        "checkpoints": [
            "요청만 있고 시작이 없으면 worker가 아직 claim하지 않은 상태입니다.",
            "완료 시각이 없고 status가 진행 중이면 아직 완료 전입니다.",
            "duration은 terminal timestamp가 있을 때 시작/요청 시각과 비교해 계산합니다.",
        ],
    },
    {
        "view_id": "queue",
        "title": "Queue 상태를 읽는 법",
        "summary": "Queued, Running/Claimed, Completed, Dead-letter, Stale을 나눠 다음 행동을 정합니다.",
        "checkpoints": [
            "Dead-letter는 실패를 숨기지 않고 operator 판단을 요구합니다.",
            "retryable=false이면 같은 입력을 자동 재시도하기보다 원인을 먼저 봅니다.",
            "human_review_required=true이면 사람이 proposal 또는 failure detail을 확인해야 합니다.",
        ],
    },
    {
        "view_id": "memory",
        "title": "Memory projection을 읽는 법",
        "summary": "backend-owned hypothesis graph의 요약 projection으로 현재 믿음 상태를 확인합니다.",
        "checkpoints": [
            "Graph digest는 projection이 바라본 canonical graph write를 식별합니다.",
            "Challenger가 없으면 다음 run에서 공격 가설을 더 만들어야 할 수 있습니다.",
            "Conflict가 계속 남으면 후속 run frontier로 되돌려야 합니다.",
        ],
    },
)

QUEUE_STATE_HELP: tuple[dict[str, str], ...] = (
    {
        "state": "queued",
        "label": "Queued",
        "korean_label": "대기",
        "plain_explanation": "아직 worker가 claim하지 않은 작업입니다.",
        "next_action": "worker loop가 실행 중인지 확인하거나 우선순위를 조정합니다.",
    },
    {
        "state": "running",
        "label": "Running/Claimed",
        "korean_label": "실행 중",
        "plain_explanation": "worker가 claim했고 run 실행 또는 처리를 진행 중인 작업입니다.",
        "next_action": "claimed_at과 latest event를 보고 정상 진행인지 확인합니다.",
    },
    {
        "state": "completed",
        "label": "Completed",
        "korean_label": "완료",
        "plain_explanation": "queue item 처리가 끝났고 backend state update가 반영된 상태입니다.",
        "next_action": "graph digest와 memory projection 변화가 기대와 맞는지 확인합니다.",
    },
    {
        "state": "dead_letter",
        "label": "Dead-letter",
        "korean_label": "격리된 실패",
        "plain_explanation": "자동 재처리하면 위험하거나 반복 실패할 수 있어 멈춘 작업입니다.",
        "next_action": "failure code, retryable, human-review-required를 보고 repair/retry/설계 보완을 결정합니다.",
    },
    {
        "state": "stale",
        "label": "Stale",
        "korean_label": "오래된 claim",
        "plain_explanation": "worker가 claim한 뒤 heartbeat/진행이 오래 확인되지 않은 작업입니다.",
        "next_action": "실행이 살아 있는지 확인한 뒤 stale recovery 또는 dead-letter 처리를 선택합니다.",
    },
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
            "dashboard_help": {
                "schema_id": "crb.web.dashboard_help.ko.v1",
                "sections": list(DASHBOARD_HELP_SECTIONS),
            },
            "glossary": {
                "schema_id": "crb.web.dashboard_glossary.ko.v1",
                "entries": list(DASHBOARD_GLOSSARY),
            },
            "graph_legend": self._graph_legend_view(),
            "queue_state_help": {
                "schema_id": "crb.web.queue_state_help.ko.v1",
                "states": list(QUEUE_STATE_HELP),
                "retryable_guidance": (
                    "retryable=true이면 원인 수정 뒤 재시도 후보입니다. "
                    "retryable=false이면 같은 입력을 자동 반복하지 말고 failure detail을 먼저 검토합니다."
                ),
                "human_review_required_guidance": (
                    "human-review-required=true이면 operator가 proposal, evidence, failure code를 확인해야 합니다."
                ),
            },
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
                "queue_created_at": queue_by_id.get(str(run.get("queue_item_id")), {}).get(
                    "created_at"
                ),
                "available_at": queue_by_id.get(str(run.get("queue_item_id")), {}).get(
                    "available_at"
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
                    "queue_created_at": item.get("created_at"),
                    "available_at": item.get("available_at"),
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
                "executor_kind": worker_loop.get("executor_kind"),
                "iteration_count": worker_loop.get("iteration_count", 0),
                "consecutive_no_yield": worker_loop.get("consecutive_no_yield", 0),
                "stop_reason": worker_loop.get("stop_reason"),
                "last_error": worker_loop.get("last_error"),
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
            "run_timeline_items": self._run_timeline_view(timeline_items, graph=graph),
        }

    def _run_timeline_view(
        self,
        timeline_items: list[dict[str, Any]],
        *,
        graph: dict[str, Any],
    ) -> list[dict[str, object]]:
        view = []
        for item in timeline_items:
            latest_event = self._latest_event_for_run(
                run_id=item.get("run_id"),
                fallback_status=str(item.get("status") or item.get("queue_state")),
            )
            enriched = {
                **item,
                "latest_event": latest_event,
                "graph_context": self._graph_context_for_item(item, graph=graph),
            }
            enriched["timing"] = self._run_timing_view(enriched)
            view.append(enriched)
        return view

    def _run_timing_view(self, item: dict[str, Any]) -> dict[str, object]:
        status = str(item.get("status") or item.get("queue_state") or "")
        queue_state = str(item.get("queue_state") or "")
        failure = item.get("failure", {})
        requested_at = _first_timestamp(
            item.get("queue_created_at"),
            item.get("available_at"),
            item.get("created_at"),
        )
        claimed_at = _first_timestamp(item.get("claim", {}).get("claimed_at"))
        started_at = (
            _first_timestamp(item.get("created_at"))
            if item.get("timeline_source") == "run_ledger"
            else None
        )
        latest_event_at = _first_timestamp(item.get("latest_event", {}).get("timestamp"))
        completed_at = (
            _first_timestamp(item.get("updated_at"))
            if status == "completed" or queue_state == "completed"
            else None
        )
        failed_at = (
            _first_timestamp(item.get("updated_at"))
            if status in {"failed", "dead_letter"}
            or queue_state == "dead_letter"
            or bool(failure.get("failure_code"))
            else None
        )
        stopped_at = (
            _first_timestamp(item.get("updated_at"))
            if status in {"stopped", "cancelled", "canceled"}
            else None
        )
        anchor_start = started_at or claimed_at or requested_at
        anchor_end = completed_at or failed_at or stopped_at
        duration_seconds = _duration_seconds(anchor_start, anchor_end)
        terminal = bool(anchor_end)
        if duration_seconds is not None:
            duration_label = _duration_label(duration_seconds)
        elif not anchor_start:
            duration_label = "기록 없음"
        elif terminal:
            duration_label = "계산 불가"
        elif started_at or claimed_at:
            duration_label = "아직 완료 전"
        else:
            duration_label = "아직 시작 전"
        return {
            "schema_id": "crb.web.run_timing.ko.v1",
            "requested_at": requested_at,
            "claimed_at": claimed_at,
            "started_at": started_at,
            "completed_at": completed_at,
            "failed_at": failed_at,
            "stopped_at": stopped_at,
            "latest_event_at": latest_event_at,
            "duration_seconds": duration_seconds,
            "duration_label": duration_label,
            "labels": {
                "requested": _timestamp_state_label(requested_at, "요청 시각 기록 없음"),
                "claimed": _timestamp_state_label(claimed_at, "아직 worker claim 전"),
                "started": _timestamp_state_label(started_at, "아직 시작 전"),
                "completed": _terminal_label(
                    value=completed_at,
                    status=status,
                    terminal_status="completed",
                    pending="아직 완료 전",
                    missing="완료 시각 기록 없음",
                ),
                "failed": _terminal_label(
                    value=failed_at,
                    status=status if status else queue_state,
                    terminal_status="dead_letter",
                    pending="실패 기록 없음",
                    missing="실패 시각 기록 없음",
                ),
                "stopped": _timestamp_state_label(stopped_at, "중단 기록 없음"),
                "latest_event": _timestamp_state_label(latest_event_at, "event 기록 없음"),
            },
            "raw_timestamps": {
                "run_created_at": item.get("created_at"),
                "run_updated_at": item.get("updated_at"),
                "queue_created_at": item.get("queue_created_at"),
                "available_at": item.get("available_at"),
                "claimed_at": item.get("claim", {}).get("claimed_at"),
                "latest_event_at": item.get("latest_event", {}).get("timestamp"),
            },
        }

    def _graph_legend_view(self) -> dict[str, object]:
        entries = {entry["term"]: entry for entry in DASHBOARD_GLOSSARY}
        return {
            "schema_id": "crb.web.graph_legend.ko.v1",
            "node_badges": [
                entries[term]
                for term in ("TOP", "HYP", "CLA", "EVI", "PRO", "CON")
            ],
            "edge_types": [
                entries[term]
                for term in ("supports", "challenges", "visualizes")
            ],
            "projection_notice": READ_ONLY_NOTICE,
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


def _first_timestamp(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _duration_seconds(start: str | None, end: str | None) -> int | None:
    started = _parse_timestamp(start)
    ended = _parse_timestamp(end)
    if started is None or ended is None:
        return None
    return max(0, int((ended - started).total_seconds()))


def _duration_label(seconds: int) -> str:
    minutes, remaining_seconds = divmod(seconds, 60)
    hours, remaining_minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}시간 {remaining_minutes}분"
    if remaining_minutes:
        return f"{remaining_minutes}분 {remaining_seconds}초"
    return f"{remaining_seconds}초"


def _timestamp_state_label(value: str | None, fallback: str) -> str:
    return value if value else fallback


def _terminal_label(
    *,
    value: str | None,
    status: str,
    terminal_status: str,
    pending: str,
    missing: str,
) -> str:
    if value:
        return value
    if status == terminal_status:
        return missing
    return pending


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
