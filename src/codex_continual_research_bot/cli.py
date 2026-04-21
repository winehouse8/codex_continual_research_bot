"""Executable CLI for Phase 13 operator workflows."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Protocol, TextIO

from codex_continual_research_bot.cli_backend import LocalBackendGateway
from codex_continual_research_bot.cli_contracts import (
    CliBackendError,
    CliFailure,
    CliResult,
    cli_result_json,
)


class CliBackend(Protocol):
    def init(self) -> dict[str, object]: ...

    def doctor(self) -> dict[str, object]: ...

    def topic_create(self, *, title: str, objective: str) -> dict[str, object]: ...

    def topic_list(self) -> dict[str, object]: ...

    def topic_show(self, *, topic_id: str) -> dict[str, object]: ...

    def run_start(self, *, topic_id: str, user_input: str) -> dict[str, object]: ...

    def run_status(self, *, run_id: str) -> dict[str, object]: ...

    def run_resume(self, *, run_id: str) -> dict[str, object]: ...

    def queue_list(self, *, topic_id: str | None) -> dict[str, object]: ...

    def queue_retry(self, *, queue_item_id: str, reason: str) -> dict[str, object]: ...

    def queue_dead_letter(self, *, queue_item_id: str) -> dict[str, object]: ...

    def memory_snapshot(self, *, topic_id: str) -> dict[str, object]: ...

    def memory_conflicts(self, *, topic_id: str) -> dict[str, object]: ...

    def memory_hypotheses(self, *, topic_id: str) -> dict[str, object]: ...

    def graph_export(
        self,
        *,
        topic_id: str,
        output_format: str,
        output_path: str,
    ) -> dict[str, object]: ...

    def graph_view(
        self,
        *,
        topic_id: str,
        output_format: str,
        output_path: str,
    ) -> dict[str, object]: ...

    def ops_health(self) -> dict[str, object]: ...

    def ops_audit(self, *, run_id: str) -> dict[str, object]: ...

    def ops_replay(self, *, run_id: str, reason: str) -> dict[str, object]: ...


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crb",
        description="Continual Research Bot operator CLI.",
    )
    parser.add_argument("--json", action="store_true", help="Emit stable JSON output.")
    subparsers = parser.add_subparsers(dest="group", required=True)

    _add_leaf(subparsers, "init", "Bootstrap local operator storage.", "init")
    _add_leaf(subparsers, "doctor", "Inspect local operator configuration.", "doctor")

    topic = subparsers.add_parser("topic", help="Create, list, and inspect topics.")
    topic_sub = topic.add_subparsers(dest="action", required=True)
    topic_create = _add_leaf(topic_sub, "create", "Create a backend-owned topic.", "topic.create")
    topic_create.add_argument("title")
    topic_create.add_argument("--objective", required=True)
    _add_leaf(topic_sub, "list", "List backend-owned topics.", "topic.list")
    topic_show = _add_leaf(topic_sub, "show", "Show topic state.", "topic.show")
    topic_show.add_argument("topic_id")

    run = subparsers.add_parser("run", help="Start, inspect, and resume runs.")
    run_sub = run.add_subparsers(dest="action", required=True)
    run_start = _add_leaf(run_sub, "start", "Enqueue an interactive run.", "run.start")
    run_start.add_argument("topic_id")
    run_start.add_argument("--input", required=True)
    run_status = _add_leaf(run_sub, "status", "Show run lifecycle state.", "run.status")
    run_status.add_argument("run_id")
    run_resume = _add_leaf(run_sub, "resume", "Request backend-mediated run resume.", "run.resume")
    run_resume.add_argument("run_id")

    queue = subparsers.add_parser("queue", help="Inspect and request queue actions.")
    queue_sub = queue.add_subparsers(dest="action", required=True)
    queue_list = _add_leaf(queue_sub, "list", "List queue items.", "queue.list")
    queue_list.add_argument("--topic", dest="topic_id")
    queue_retry = _add_leaf(queue_sub, "retry", "Recover a dead-lettered queue item.", "queue.retry")
    queue_retry.add_argument("queue_item_id")
    queue_retry.add_argument("--reason", required=True)
    queue_dead = _add_leaf(queue_sub, "dead-letter", "Inspect a dead-letter item.", "queue.dead-letter")
    queue_dead.add_argument("queue_item_id")

    memory = subparsers.add_parser("memory", help="Inspect backend-owned memory state.")
    memory_sub = memory.add_subparsers(dest="action", required=True)
    for name, command_id, help_text in (
        ("snapshot", "memory.snapshot", "Show memory counts and digests."),
        ("conflicts", "memory.conflicts", "List active conflicts."),
        ("hypotheses", "memory.hypotheses", "List current and challenger hypotheses."),
    ):
        command = _add_leaf(memory_sub, name, help_text, command_id)
        command.add_argument("topic_id")

    graph = subparsers.add_parser("graph", help="Export visualization artifacts.")
    graph_sub = graph.add_subparsers(dest="action", required=True)
    graph_export = _add_leaf(graph_sub, "export", "Export a graph visualization artifact.", "graph.export")
    graph_export.add_argument("topic_id")
    graph_export.add_argument("--format", choices=("json",), default="json")
    graph_export.add_argument("--output", required=True)
    graph_view = _add_leaf(graph_sub, "view", "Render a graph visualization artifact.", "graph.view")
    graph_view.add_argument("topic_id")
    graph_view.add_argument("--format", choices=("html",), default="html")
    graph_view.add_argument("--output", required=True)

    ops = subparsers.add_parser("ops", help="Inspect health, audit, and replay.")
    ops_sub = ops.add_subparsers(dest="action", required=True)
    _add_leaf(ops_sub, "health", "Show backend health.", "ops.health")
    ops_audit = _add_leaf(ops_sub, "audit", "Show run audit trail.", "ops.audit")
    ops_audit.add_argument("run_id")
    ops_replay = _add_leaf(ops_sub, "replay", "Replay run artifacts for audit.", "ops.replay")
    ops_replay.add_argument("run_id")
    ops_replay.add_argument("--reason", required=True)
    return parser


def _add_leaf(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
    command_id: str,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, help=help_text)
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(command_id=command_id)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    backend: CliBackend | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    backend = backend or LocalBackendGateway.from_environment()

    try:
        data = dispatch(args, backend)
        result = CliResult(
            command_id=args.command_id,
            ok=True,
            summary=_success_summary(args.command_id, data),
            data=data,
        )
        _emit(result, json_mode=args.json, stdout=output)
        return 0
    except CliBackendError as exc:
        result = CliResult(
            command_id=getattr(args, "command_id", "unknown"),
            ok=False,
            summary=f"{exc.failure_code}: {exc.detail}",
            failure=CliFailure(
                failure_code=exc.failure_code,
                retryable=exc.retryable,
                human_review_required=exc.human_review_required,
                detail=exc.detail,
            ),
        )
        _emit(result, json_mode=args.json, stdout=output if args.json else error_output)
        return 1


def dispatch(args: argparse.Namespace, backend: CliBackend) -> dict[str, object]:
    command_id = args.command_id
    if command_id == "init":
        return backend.init()
    if command_id == "doctor":
        return backend.doctor()
    if command_id == "topic.create":
        return backend.topic_create(title=args.title, objective=args.objective)
    if command_id == "topic.list":
        return backend.topic_list()
    if command_id == "topic.show":
        return backend.topic_show(topic_id=args.topic_id)
    if command_id == "run.start":
        return backend.run_start(topic_id=args.topic_id, user_input=args.input)
    if command_id == "run.status":
        return backend.run_status(run_id=args.run_id)
    if command_id == "run.resume":
        return backend.run_resume(run_id=args.run_id)
    if command_id == "queue.list":
        return backend.queue_list(topic_id=args.topic_id)
    if command_id == "queue.retry":
        return backend.queue_retry(queue_item_id=args.queue_item_id, reason=args.reason)
    if command_id == "queue.dead-letter":
        return backend.queue_dead_letter(queue_item_id=args.queue_item_id)
    if command_id == "memory.snapshot":
        return backend.memory_snapshot(topic_id=args.topic_id)
    if command_id == "memory.conflicts":
        return backend.memory_conflicts(topic_id=args.topic_id)
    if command_id == "memory.hypotheses":
        return backend.memory_hypotheses(topic_id=args.topic_id)
    if command_id == "graph.export":
        return backend.graph_export(
            topic_id=args.topic_id,
            output_format=args.format,
            output_path=args.output,
        )
    if command_id == "graph.view":
        return backend.graph_view(
            topic_id=args.topic_id,
            output_format=args.format,
            output_path=args.output,
        )
    if command_id == "ops.health":
        return backend.ops_health()
    if command_id == "ops.audit":
        return backend.ops_audit(run_id=args.run_id)
    if command_id == "ops.replay":
        return backend.ops_replay(run_id=args.run_id, reason=args.reason)
    raise CliBackendError(
        failure_code="unknown_command",
        detail=f"no handler registered for {command_id}",
        retryable=False,
        human_review_required=False,
    )


def _success_summary(command_id: str, data: dict[str, object]) -> str:
    summary = data.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    return f"{command_id} completed"


def _emit(result: CliResult, *, json_mode: bool, stdout: TextIO) -> None:
    if json_mode:
        stdout.write(cli_result_json(result))
        return
    stdout.write(_human_result(result))


def _human_result(result: CliResult) -> str:
    lines = [result.summary]
    if result.failure is not None:
        lines.extend(
            [
                f"Failure code: {result.failure.failure_code}",
                f"Retryable: {'yes' if result.failure.retryable else 'no'}",
                (
                    "Human review required: "
                    f"{'yes' if result.failure.human_review_required else 'no'}"
                ),
            ]
        )
        return "\n".join(lines) + "\n"

    details = result.data.get("human")
    if isinstance(details, list):
        lines.extend(str(item) for item in details)
    elif isinstance(details, str):
        lines.append(details)
    else:
        for key in ("topic_id", "run_id", "queue_item_id", "db_path"):
            value = result.data.get(key)
            if value is not None:
                lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
