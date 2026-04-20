"""Codex exec runtime client and JSONL event ingestion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import selectors
import subprocess
import time
from typing import Protocol

from pydantic import ValidationError

from codex_continual_research_bot.contracts import (
    CodexRawEventPayload,
    ExecutionBudgets,
    FailureCode,
    OutputValidatedPayload,
    ProposalBundle,
    RunCompletedPayload,
    RunExecutionRequest,
    RunFailedPayload,
    RunIntent,
    RunMode,
    RunStartedPayload,
    RuntimeEvent,
    RuntimeEventType,
)
from codex_continual_research_bot.persistence import SQLitePersistenceLedger
from codex_continual_research_bot.tools import (
    ToolPolicyViolation,
    ToolRegistry,
    ToolPolicyValidator,
    build_default_tool_registry,
)


TURN_START_EVENTS = frozenset({"turn.started", "turn_started"})
TOOL_CALL_START_EVENTS = frozenset(
    {
        "tool.started",
        "tool_started",
        "tool_call.started",
        "tool_call_started",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest_text(text: str) -> str:
    return f"sha256:{sha256(text.encode('utf-8')).hexdigest()}"


class CodexRuntimeError(RuntimeError):
    """Base error for fail-closed runtime execution failures."""

    def __init__(
        self,
        *,
        failure_code: FailureCode,
        detail: str,
        retryable: bool,
    ) -> None:
        super().__init__(detail)
        self.failure_code = failure_code
        self.detail = detail
        self.retryable = retryable


class WorkspaceRootMismatchError(CodexRuntimeError):
    """Raised when the invocation workspace does not match backend policy."""


class BudgetExceededError(CodexRuntimeError):
    """Raised before launch when the request exceeds backend-owned budgets."""


class ExecutionPolicyError(CodexRuntimeError):
    """Raised when backend policy cannot be enforced by the runtime."""


class MalformedJSONLEventError(CodexRuntimeError):
    """Raised when `codex exec --json` emits an invalid event line."""


class CodexTransportTimeoutError(CodexRuntimeError):
    """Raised when the Codex process exceeds its runtime deadline."""


class CodexProcessCrashError(CodexRuntimeError):
    """Raised when the Codex process exits without a successful final artifact."""


class OutputSchemaValidationError(CodexRuntimeError):
    """Raised when the final message cannot be parsed as a proposal bundle."""


@dataclass(frozen=True)
class CodexRuntimeConfig:
    workspace_root: Path
    artifact_root: Path
    output_schema_path: Path
    codex_binary: str = "codex"
    attempt: int = 1


@dataclass(frozen=True)
class CodexExecInvocation:
    command: tuple[str, ...]
    cwd: Path
    timeout_seconds: int
    final_message_path: Path
    prompt: str


@dataclass(frozen=True)
class CodexExecProcessResult:
    stdout_lines: tuple[str, ...]
    exit_code: int
    timed_out: bool = False
    stderr: str = ""


@dataclass(frozen=True)
class RuntimeMetrics:
    raw_event_count: int
    normalized_event_count: int
    artifact_count: int
    exit_code: int
    timed_out: bool


@dataclass(frozen=True)
class RuntimeExecutionResult:
    run_id: str
    proposal: ProposalBundle
    metrics: RuntimeMetrics
    artifacts_dir: Path


StdoutHandler = Callable[[str], None]


class CodexExecLauncher(Protocol):
    def run(
        self,
        invocation: CodexExecInvocation,
        stdout_handler: StdoutHandler | None = None,
    ) -> CodexExecProcessResult:
        """Run a prepared Codex exec invocation and return captured JSONL lines."""


class SubprocessCodexExecLauncher:
    """Production launcher for `codex exec --json`."""

    def run(
        self,
        invocation: CodexExecInvocation,
        stdout_handler: StdoutHandler | None = None,
    ) -> CodexExecProcessResult:
        stdout_lines: list[str] = []
        deadline = time.monotonic() + invocation.timeout_seconds
        timed_out = False
        process = subprocess.Popen(
            invocation.command,
            cwd=invocation.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        selector = selectors.DefaultSelector()
        try:
            if process.stdout is None:
                raise CodexProcessCrashError(
                    failure_code=FailureCode.CODEX_PROCESS_CRASH,
                    detail="codex exec stdout pipe was not available",
                    retryable=True,
                )
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 and process.poll() is None:
                    timed_out = True
                    process.kill()
                    break

                events = selector.select(timeout=max(0.0, min(0.1, remaining)))
                if events:
                    line = process.stdout.readline()
                    if line:
                        self._handle_stdout_line(
                            line=line,
                            stdout_lines=stdout_lines,
                            stdout_handler=stdout_handler,
                        )
                        continue

                if process.poll() is not None:
                    for line in process.stdout:
                        self._handle_stdout_line(
                            line=line,
                            stdout_lines=stdout_lines,
                            stdout_handler=stdout_handler,
                        )
                    break
        except Exception:
            if process.poll() is None:
                process.kill()
                process.wait()
            raise
        finally:
            selector.close()

        if timed_out:
            process.wait()
            exit_code = -1
        else:
            exit_code = process.wait()
        stderr = "" if process.stderr is None else process.stderr.read()
        return CodexExecProcessResult(
            stdout_lines=() if stdout_handler is not None else tuple(stdout_lines),
            exit_code=exit_code,
            timed_out=timed_out,
            stderr=stderr,
        )

    def _handle_stdout_line(
        self,
        *,
        line: str,
        stdout_lines: list[str],
        stdout_handler: StdoutHandler | None,
    ) -> None:
        stripped = line.rstrip("\n")
        stdout_lines.append(stripped)
        if stdout_handler is not None:
            stdout_handler(stripped)


class RuntimePromptBuilder:
    """Assembles the stable prompt envelope sent to Codex exec."""

    def build(self, request: RunExecutionRequest) -> str:
        request_json = json.dumps(
            request.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        return (
            "You are executing a backend-controlled continual research run.\n"
            "Return only a JSON object that satisfies the configured output schema.\n"
            "The event stream is observed by the backend and must not be treated as "
            "the source of truth for graph writes.\n\n"
            "RunExecutionRequest:\n"
            f"{request_json}\n"
        )


class RuntimeArtifactStore:
    """Stores raw JSONL events, normalized events, final output, and metrics."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def attempt_dir(self, *, run_id: str, attempt: int) -> Path:
        path = self._root / run_id / f"attempt_{attempt:03d}"
        path.mkdir(parents=True, exist_ok=True)
        (path / "raw_events").mkdir(exist_ok=True)
        return path

    def store_raw_event(
        self,
        *,
        attempt_dir: Path,
        seq: int,
        canonical_event: str,
    ) -> str:
        artifact_id = f"codex_raw_event_{seq:06d}"
        (attempt_dir / "raw_events" / f"{artifact_id}.json").write_text(
            canonical_event + "\n",
            encoding="utf-8",
        )
        with (attempt_dir / "raw_events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(canonical_event + "\n")
        return artifact_id

    def store_malformed_event(
        self,
        *,
        attempt_dir: Path,
        seq: int,
        line: str,
    ) -> None:
        (attempt_dir / f"malformed_event_{seq:06d}.txt").write_text(
            line,
            encoding="utf-8",
        )

    def append_normalized_event(self, *, attempt_dir: Path, event: RuntimeEvent) -> None:
        with (attempt_dir / "normalized_events.jsonl").open(
            "a",
            encoding="utf-8",
        ) as handle:
            handle.write(_canonical_json(event.model_dump(mode="json")) + "\n")

    def store_json(self, *, attempt_dir: Path, name: str, payload: object) -> Path:
        path = attempt_dir / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def replay_events(self, *, run_id: str, attempt: int) -> list[RuntimeEvent]:
        path = self._root / run_id / f"attempt_{attempt:03d}" / "normalized_events.jsonl"
        events: list[RuntimeEvent] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    events.append(RuntimeEvent.model_validate_json(line))
        return events

    def artifact_count(self, *, attempt_dir: Path) -> int:
        return sum(1 for path in attempt_dir.rglob("*") if path.is_file())


class CodexJSONLEventNormalizer:
    """Converts Codex JSONL stdout events into backend canonical ledger events."""

    def __init__(self, artifact_store: RuntimeArtifactStore) -> None:
        self._artifact_store = artifact_store

    def normalize_line(
        self,
        *,
        line: str,
        run_id: str,
        seq: int,
        attempt_dir: Path,
        timestamp: datetime | None = None,
    ) -> RuntimeEvent:
        try:
            raw_event = json.loads(line)
        except json.JSONDecodeError as exc:
            self._artifact_store.store_malformed_event(
                attempt_dir=attempt_dir,
                seq=seq,
                line=line,
            )
            raise MalformedJSONLEventError(
                failure_code=FailureCode.MALFORMED_CODEX_EVENT,
                detail=f"malformed JSONL event at sequence {seq}: {exc.msg}",
                retryable=False,
            ) from exc

        if not isinstance(raw_event, dict):
            self._artifact_store.store_malformed_event(
                attempt_dir=attempt_dir,
                seq=seq,
                line=line,
            )
            raise MalformedJSONLEventError(
                failure_code=FailureCode.MALFORMED_CODEX_EVENT,
                detail=f"JSONL event at sequence {seq} is not an object",
                retryable=False,
            )

        raw_event_type = raw_event.get("type") or raw_event.get("event_type") or raw_event.get("event")
        if not isinstance(raw_event_type, str) or not raw_event_type:
            self._artifact_store.store_malformed_event(
                attempt_dir=attempt_dir,
                seq=seq,
                line=line,
            )
            raise MalformedJSONLEventError(
                failure_code=FailureCode.MALFORMED_CODEX_EVENT,
                detail=f"JSONL event at sequence {seq} has no event type",
                retryable=False,
            )

        canonical_event = _canonical_json(raw_event)
        artifact_id = self._artifact_store.store_raw_event(
            attempt_dir=attempt_dir,
            seq=seq,
            canonical_event=canonical_event,
        )
        turn_index = raw_event.get("turn_index", 0)
        if not isinstance(turn_index, int) or turn_index < 0:
            turn_index = 0
        return RuntimeEvent(
            run_id=run_id,
            seq=seq,
            event_type=RuntimeEventType.CODEX_EVENT,
            turn_index=turn_index,
            timestamp=timestamp or _utcnow(),
            payload=CodexRawEventPayload(
                raw_event_type=raw_event_type,
                raw_event_digest=_digest_text(canonical_event),
                artifact_id=artifact_id,
            ),
        )


class CodexRuntimeCoordinator:
    """Coordinates prompt assembly, exec launch, event ingestion, and artifacts."""

    def __init__(
        self,
        ledger: SQLitePersistenceLedger,
        config: CodexRuntimeConfig,
        *,
        launcher: CodexExecLauncher | None = None,
        prompt_builder: RuntimePromptBuilder | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._ledger = ledger
        self._config = config
        self._launcher = launcher or SubprocessCodexExecLauncher()
        self._prompt_builder = prompt_builder or RuntimePromptBuilder()
        self._artifact_store = RuntimeArtifactStore(config.artifact_root)
        self._normalizer = CodexJSONLEventNormalizer(self._artifact_store)
        self._tool_registry = tool_registry or build_default_tool_registry()
        self._tool_policy_validator = ToolPolicyValidator(
            self._tool_registry,
            workspace_root=config.workspace_root,
        )

    def execute(self, intent: RunIntent) -> RuntimeExecutionResult:
        attempt_dir = self._artifact_store.attempt_dir(
            run_id=intent.run_id,
            attempt=self._config.attempt,
        )
        seq = self._next_event_seq(intent.run_id)
        self._append_event(
            attempt_dir=attempt_dir,
            event=RuntimeEvent(
                run_id=intent.run_id,
                seq=seq,
                event_type=RuntimeEventType.RUN_STARTED,
                turn_index=0,
                timestamp=_utcnow(),
                payload=RunStartedPayload(
                    objective=intent.execution_request.objective,
                    mode=RunMode(intent.execution_request.mode),
                ),
            ),
        )
        seq += 1

        try:
            invocation = self._prepare_invocation(
                intent.execution_request,
                attempt_dir=attempt_dir,
            )
        except CodexRuntimeError as exc:
            self._record_failure(
                attempt_dir=attempt_dir,
                run_id=intent.run_id,
                seq=seq,
                failure_code=exc.failure_code,
                detail=exc.detail,
            )
            raise

        raw_event_count = 0
        observed_turn_count = 0
        observed_tool_call_count = 0

        def handle_stdout_line(line: str) -> None:
            nonlocal seq, raw_event_count, observed_turn_count, observed_tool_call_count
            if not line.strip():
                return
            try:
                event = self._normalizer.normalize_line(
                    line=line,
                    run_id=intent.run_id,
                    seq=seq,
                    attempt_dir=attempt_dir,
                )
            except CodexRuntimeError as exc:
                self._record_failure(
                    attempt_dir=attempt_dir,
                    run_id=intent.run_id,
                    seq=seq,
                    failure_code=exc.failure_code,
                    detail=exc.detail,
                )
                raise
            self._append_event(attempt_dir=attempt_dir, event=event)
            seq += 1
            raw_event_count += 1
            raw_event_type = event.payload.raw_event_type
            if _is_turn_start_event(raw_event_type):
                observed_turn_count += 1
            if _is_tool_call_start_event(raw_event_type):
                self._enforce_observed_tool_policy(
                    request=intent.execution_request,
                    raw_line=line,
                    attempt_dir=attempt_dir,
                    run_id=intent.run_id,
                    seq=seq,
                )
                observed_tool_call_count += 1
            self._enforce_observed_runtime_budgets(
                budgets=intent.execution_request.budgets,
                observed_turn_count=observed_turn_count,
                observed_tool_call_count=observed_tool_call_count,
                attempt_dir=attempt_dir,
                run_id=intent.run_id,
                seq=seq,
            )

        result = self._launcher.run(invocation, stdout_handler=handle_stdout_line)
        for line in result.stdout_lines:
            handle_stdout_line(line)

        if result.timed_out:
            detail = (
                "codex exec exceeded "
                f"{intent.execution_request.budgets.max_runtime_seconds}s runtime budget"
            )
            self._record_failure(
                attempt_dir=attempt_dir,
                run_id=intent.run_id,
                seq=seq,
                failure_code=FailureCode.CODEX_TRANSPORT_TIMEOUT,
                detail=detail,
            )
            raise CodexTransportTimeoutError(
                failure_code=FailureCode.CODEX_TRANSPORT_TIMEOUT,
                detail=detail,
                retryable=True,
            )

        if raw_event_count == 0:
            detail = "codex exec produced no JSONL events; refusing final-output-only result"
            self._record_failure(
                attempt_dir=attempt_dir,
                run_id=intent.run_id,
                seq=seq,
                failure_code=FailureCode.CODEX_PROCESS_CRASH,
                detail=detail,
            )
            raise CodexProcessCrashError(
                failure_code=FailureCode.CODEX_PROCESS_CRASH,
                detail=detail,
                retryable=True,
            )

        if result.exit_code != 0:
            detail = f"codex exec exited with status {result.exit_code}"
            if result.stderr.strip():
                detail = f"{detail}: {result.stderr.strip()}"
            self._record_failure(
                attempt_dir=attempt_dir,
                run_id=intent.run_id,
                seq=seq,
                failure_code=FailureCode.CODEX_PROCESS_CRASH,
                detail=detail,
            )
            raise CodexProcessCrashError(
                failure_code=FailureCode.CODEX_PROCESS_CRASH,
                detail=detail,
                retryable=True,
            )

        proposal = self._load_proposal(
            path=invocation.final_message_path,
            attempt_dir=attempt_dir,
            run_id=intent.run_id,
            seq=seq,
        )
        self._enforce_proposal_runtime_budgets(
            proposal=proposal,
            budgets=intent.execution_request.budgets,
            attempt_dir=attempt_dir,
            run_id=intent.run_id,
            seq=seq,
        )
        proposal_json = proposal.model_dump(mode="json")
        proposal_digest = _digest_text(_canonical_json(proposal_json))
        summary_digest = _digest_text(proposal.summary_draft)
        self._artifact_store.store_json(
            attempt_dir=attempt_dir,
            name="proposal_bundle.json",
            payload=proposal_json,
        )

        self._append_event(
            attempt_dir=attempt_dir,
            event=RuntimeEvent(
                run_id=intent.run_id,
                seq=seq,
                event_type=RuntimeEventType.OUTPUT_VALIDATED,
                turn_index=0,
                timestamp=_utcnow(),
                payload=OutputValidatedPayload(
                    schema_id=intent.execution_request.output_contract.schema_id,
                    repair_attempts=0,
                ),
            ),
        )
        seq += 1
        self._append_event(
            attempt_dir=attempt_dir,
            event=RuntimeEvent(
                run_id=intent.run_id,
                seq=seq,
                event_type=RuntimeEventType.RUN_COMPLETED,
                turn_index=0,
                timestamp=_utcnow(),
                payload=RunCompletedPayload(
                    proposal_bundle_digest=proposal_digest,
                    summary_digest=summary_digest,
                ),
            ),
        )

        metrics_artifact_count = self._artifact_store.artifact_count(
            attempt_dir=attempt_dir
        ) + 1
        metrics = RuntimeMetrics(
            raw_event_count=raw_event_count,
            normalized_event_count=raw_event_count + 3,
            artifact_count=metrics_artifact_count,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
        )
        self._artifact_store.store_json(
            attempt_dir=attempt_dir,
            name="metrics.json",
            payload={
                "artifact_count": metrics.artifact_count,
                "exit_code": metrics.exit_code,
                "normalized_event_count": metrics.normalized_event_count,
                "raw_event_count": metrics.raw_event_count,
                "timed_out": metrics.timed_out,
            },
        )
        return RuntimeExecutionResult(
            run_id=intent.run_id,
            proposal=proposal,
            metrics=metrics,
            artifacts_dir=attempt_dir,
        )

    def replay_events(self, *, run_id: str, attempt: int | None = None) -> list[RuntimeEvent]:
        return self._ledger.list_run_events(run_id)

    def _next_event_seq(self, run_id: str) -> int:
        events = self._ledger.list_run_events(run_id)
        if not events:
            return 0
        return max(event.seq for event in events) + 1

    def _prepare_invocation(
        self,
        request: RunExecutionRequest,
        *,
        attempt_dir: Path,
    ) -> CodexExecInvocation:
        workspace_root = self._config.workspace_root.resolve()
        if not workspace_root.exists() or not workspace_root.is_dir():
            raise WorkspaceRootMismatchError(
                failure_code=FailureCode.WORKSPACE_MISMATCH,
                detail=f"workspace root does not exist: {workspace_root}",
                retryable=False,
            )
        if Path.cwd().resolve() != workspace_root:
            raise WorkspaceRootMismatchError(
                failure_code=FailureCode.WORKSPACE_MISMATCH,
                detail=(
                    "runtime invocation cwd must match configured workspace root: "
                    f"{Path.cwd().resolve()} != {workspace_root}"
                ),
                retryable=False,
            )
        self._enforce_tool_policy(request)
        if not self._config.output_schema_path.exists():
            raise OutputSchemaValidationError(
                failure_code=FailureCode.OUTPUT_SCHEMA_VALIDATION_FAILED,
                detail=f"output schema path does not exist: {self._config.output_schema_path}",
                retryable=False,
            )

        prompt = self._prompt_builder.build(request)
        self._enforce_budgets(prompt=prompt, budgets=request.budgets)
        final_message_path = attempt_dir / "final_message.json"
        command = (
            self._config.codex_binary,
            "exec",
            "--json",
            "--output-schema",
            str(self._config.output_schema_path),
            "-C",
            str(workspace_root),
            "--sandbox",
            request.tool_policy.sandbox_mode.value,
            "-o",
            str(final_message_path),
            prompt,
        )
        self._artifact_store.store_json(
            attempt_dir=attempt_dir,
            name="invocation.json",
            payload={
                "command": list(command[:-1]) + ["<runtime prompt>"],
                "cwd": str(workspace_root),
                "registered_tools": sorted(self._tool_registry.names()),
                "tool_policy": request.tool_policy.model_dump(mode="json"),
                "timeout_seconds": request.budgets.max_runtime_seconds,
                "writable_roots": [str(workspace_root)],
            },
        )
        return CodexExecInvocation(
            command=command,
            cwd=workspace_root,
            timeout_seconds=request.budgets.max_runtime_seconds,
            final_message_path=final_message_path,
            prompt=prompt,
        )

    def _enforce_tool_policy(self, request: RunExecutionRequest) -> None:
        try:
            self._tool_policy_validator.validate_runtime_policy(request.tool_policy)
        except ToolPolicyViolation as exc:
            raise ExecutionPolicyError(
                failure_code=FailureCode.EXECUTION_POLICY_REJECTED,
                detail=str(exc),
                retryable=False,
            ) from exc

    def _enforce_observed_tool_policy(
        self,
        *,
        request: RunExecutionRequest,
        raw_line: str,
        attempt_dir: Path,
        run_id: str,
        seq: int,
    ) -> None:
        tool_name = _extract_tool_name_from_raw_event(raw_line)
        if tool_name is None:
            detail = "observed tool event did not include a tool name"
            self._record_failure(
                attempt_dir=attempt_dir,
                run_id=run_id,
                seq=seq,
                failure_code=FailureCode.EXECUTION_POLICY_REJECTED,
                detail=detail,
            )
            raise ExecutionPolicyError(
                failure_code=FailureCode.EXECUTION_POLICY_REJECTED,
                detail=detail,
                retryable=False,
            )
        try:
            self._tool_policy_validator.validate_observed_tool_call(
                policy=request.tool_policy,
                tool_name=tool_name,
            )
        except ToolPolicyViolation as exc:
            detail = f"observed tool call rejected by registry policy: {exc}"
            self._record_failure(
                attempt_dir=attempt_dir,
                run_id=run_id,
                seq=seq,
                failure_code=FailureCode.EXECUTION_POLICY_REJECTED,
                detail=detail,
            )
            raise ExecutionPolicyError(
                failure_code=FailureCode.EXECUTION_POLICY_REJECTED,
                detail=detail,
                retryable=False,
            ) from exc

    def _enforce_budgets(
        self,
        *,
        prompt: str,
        budgets: ExecutionBudgets,
    ) -> None:
        estimated_tokens = max(1, len(prompt) // 4)
        if estimated_tokens > budgets.hard_input_tokens:
            raise BudgetExceededError(
                failure_code=FailureCode.BUDGET_EXCEEDED,
                detail=(
                    "runtime prompt exceeds hard input budget: "
                    f"estimated {estimated_tokens}, limit {budgets.hard_input_tokens}"
                ),
                retryable=False,
            )

    def _enforce_observed_runtime_budgets(
        self,
        *,
        budgets: ExecutionBudgets,
        observed_turn_count: int,
        observed_tool_call_count: int,
        attempt_dir: Path,
        run_id: str,
        seq: int,
    ) -> None:
        if observed_turn_count > budgets.max_turns:
            detail = (
                "observed turn count exceeded runtime budget: "
                f"{observed_turn_count} > {budgets.max_turns}"
            )
            self._record_budget_failure(
                attempt_dir=attempt_dir,
                run_id=run_id,
                seq=seq,
                detail=detail,
            )
        if observed_tool_call_count > budgets.max_tool_calls:
            detail = (
                "observed tool call count exceeded runtime budget: "
                f"{observed_tool_call_count} > {budgets.max_tool_calls}"
            )
            self._record_budget_failure(
                attempt_dir=attempt_dir,
                run_id=run_id,
                seq=seq,
                detail=detail,
            )

    def _enforce_proposal_runtime_budgets(
        self,
        *,
        proposal: ProposalBundle,
        budgets: ExecutionBudgets,
        attempt_dir: Path,
        run_id: str,
        seq: int,
    ) -> None:
        if proposal.execution_meta.turn_count > budgets.max_turns:
            detail = (
                "proposal turn count exceeded runtime budget: "
                f"{proposal.execution_meta.turn_count} > {budgets.max_turns}"
            )
            self._record_budget_failure(
                attempt_dir=attempt_dir,
                run_id=run_id,
                seq=seq,
                detail=detail,
            )
        if proposal.execution_meta.tool_call_count > budgets.max_tool_calls:
            detail = (
                "proposal tool call count exceeded runtime budget: "
                f"{proposal.execution_meta.tool_call_count} > {budgets.max_tool_calls}"
            )
            self._record_budget_failure(
                attempt_dir=attempt_dir,
                run_id=run_id,
                seq=seq,
                detail=detail,
            )

    def _record_budget_failure(
        self,
        *,
        attempt_dir: Path,
        run_id: str,
        seq: int,
        detail: str,
    ) -> None:
        self._record_failure(
            attempt_dir=attempt_dir,
            run_id=run_id,
            seq=seq,
            failure_code=FailureCode.BUDGET_EXCEEDED,
            detail=detail,
        )
        raise BudgetExceededError(
            failure_code=FailureCode.BUDGET_EXCEEDED,
            detail=detail,
            retryable=False,
        )

    def _load_proposal(
        self,
        *,
        path: Path,
        attempt_dir: Path,
        run_id: str,
        seq: int,
    ) -> ProposalBundle:
        if not path.exists():
            detail = f"codex exec did not write final message artifact: {path}"
            self._record_failure(
                attempt_dir=attempt_dir,
                run_id=run_id,
                seq=seq,
                failure_code=FailureCode.CODEX_PROCESS_CRASH,
                detail=detail,
            )
            raise CodexProcessCrashError(
                failure_code=FailureCode.CODEX_PROCESS_CRASH,
                detail=detail,
                retryable=True,
            )
        final_text = path.read_text(encoding="utf-8")
        try:
            return ProposalBundle.model_validate_json(final_text)
        except ValidationError as exc:
            detail = "final output did not match ProposalBundle schema"
            self._record_failure(
                attempt_dir=attempt_dir,
                run_id=run_id,
                seq=seq,
                failure_code=FailureCode.OUTPUT_SCHEMA_VALIDATION_FAILED,
                detail=detail,
            )
            raise OutputSchemaValidationError(
                failure_code=FailureCode.OUTPUT_SCHEMA_VALIDATION_FAILED,
                detail=detail,
                retryable=True,
            ) from exc

    def _record_failure(
        self,
        *,
        attempt_dir: Path,
        run_id: str,
        seq: int,
        failure_code: FailureCode,
        detail: str,
    ) -> None:
        self._append_event(
            attempt_dir=attempt_dir,
            event=RuntimeEvent(
                run_id=run_id,
                seq=seq,
                event_type=RuntimeEventType.RUN_FAILED,
                turn_index=0,
                timestamp=_utcnow(),
                payload=RunFailedPayload(
                    failure_code=failure_code,
                    detail=detail,
                ),
            ),
        )

    def _append_event(self, *, attempt_dir: Path, event: RuntimeEvent) -> None:
        self._ledger.append_run_event(event)
        self._artifact_store.append_normalized_event(attempt_dir=attempt_dir, event=event)


def _is_turn_start_event(raw_event_type: str) -> bool:
    return raw_event_type.lower() in TURN_START_EVENTS


def _is_tool_call_start_event(raw_event_type: str) -> bool:
    return raw_event_type.lower() in TOOL_CALL_START_EVENTS


def _extract_tool_name_from_raw_event(raw_line: str) -> str | None:
    raw_event = json.loads(raw_line)
    candidates = (
        raw_event.get("tool_name"),
        raw_event.get("name"),
        raw_event.get("tool"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate

    tool_call = raw_event.get("tool_call")
    if isinstance(tool_call, dict):
        candidate = tool_call.get("name") or tool_call.get("tool_name")
        if isinstance(candidate, str) and candidate:
            return candidate

    item = raw_event.get("item")
    if isinstance(item, dict):
        candidate = item.get("name") or item.get("tool_name")
        if isinstance(candidate, str) and candidate:
            return candidate
    return None
