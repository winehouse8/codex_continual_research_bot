"""Backend-owned tool registry, policy validation, and execution wrapper."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from codex_continual_research_bot.contracts import (
    NetworkMode,
    SandboxMode,
    StrictModel,
    ToolPolicy,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest_payload(data: object) -> str:
    return f"sha256:{sha256(_canonical_json(data).encode('utf-8')).hexdigest()}"


class ToolSideEffectLevel(str, Enum):
    READ_ONLY = "read_only"
    DETERMINISTIC_WRITE = "deterministic_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"


class ToolIdempotencyMode(str, Enum):
    PURE = "pure"
    CACHEABLE = "cacheable"
    IDEMPOTENT = "idempotent"
    NON_IDEMPOTENT = "non_idempotent"


class ToolRetryBackoff(str, Enum):
    NONE = "none"
    FIXED = "fixed"
    EXPONENTIAL = "exponential"


class ToolCallStatus(str, Enum):
    OK = "ok"
    ERROR = "error"


class ToolValidationError(ValueError):
    """Raised when a tool manifest, input, or output violates its schema."""


class ToolPolicyViolation(RuntimeError):
    """Raised when backend policy rejects a tool before execution."""


class ToolOutputValidationError(ToolValidationError):
    """Raised when a tool result cannot be normalized to its manifest schema."""


class ToolTimeoutError(RuntimeError):
    """Raised by handlers when the underlying tool exceeded its timeout."""


class ToolRetryPolicy(StrictModel):
    max_attempts: StrictInt = Field(ge=1)
    backoff: ToolRetryBackoff


class ToolPermissions(StrictModel):
    network: StrictBool = False
    filesystem: StrictBool = False
    writable_roots: list[StrictStr] = Field(default_factory=list)
    secrets: list[StrictStr] = Field(default_factory=list)


class ToolManifest(StrictModel):
    name: StrictStr = Field(min_length=1)
    version: StrictStr = Field(min_length=1)
    kind: StrictStr = Field(min_length=1)
    description: StrictStr = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    timeout_seconds: StrictInt = Field(ge=1)
    side_effect_level: ToolSideEffectLevel
    idempotency_mode: ToolIdempotencyMode
    retry_policy: ToolRetryPolicy
    permissions: ToolPermissions

    @model_validator(mode="after")
    def validate_policy_pairing(self) -> ToolManifest:
        if (
            self.side_effect_level == ToolSideEffectLevel.NON_IDEMPOTENT_WRITE
            and self.idempotency_mode != ToolIdempotencyMode.NON_IDEMPOTENT
        ):
            raise ValueError("non-idempotent writes must declare non_idempotent mode")
        if (
            self.side_effect_level == ToolSideEffectLevel.DETERMINISTIC_WRITE
            and self.idempotency_mode != ToolIdempotencyMode.IDEMPOTENT
        ):
            raise ValueError("deterministic writes must declare idempotent mode")
        if (
            self.side_effect_level == ToolSideEffectLevel.READ_ONLY
            and self.idempotency_mode
            not in {ToolIdempotencyMode.PURE, ToolIdempotencyMode.CACHEABLE}
        ):
            raise ValueError("read-only tools must be pure or cacheable")
        _validate_json_schema(self.input_schema, path="input_schema")
        _validate_json_schema(self.output_schema, path="output_schema")
        return self


class ToolErrorEnvelope(StrictModel):
    error_class: StrictStr = Field(min_length=1)
    message: StrictStr = Field(min_length=1)
    retryable: StrictBool
    hint: StrictStr | None = None


class NormalizedToolResult(StrictModel):
    tool_call_id: StrictStr = Field(min_length=1)
    tool_name: StrictStr = Field(min_length=1)
    status: ToolCallStatus
    result: dict[str, Any] | None = None
    error: ToolErrorEnvelope | None = None
    retryable: StrictBool
    result_digest: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def validate_status_payload(self) -> NormalizedToolResult:
        if self.status == ToolCallStatus.OK and (self.result is None or self.error is not None):
            raise ValueError("ok tool results must include result and omit error")
        if self.status == ToolCallStatus.ERROR and (self.error is None or self.result is not None):
            raise ValueError("error tool results must include error and omit result")
        return self


class DeniedToolCallRecord(StrictModel):
    run_id: StrictStr = Field(min_length=1)
    tool_call_id: StrictStr = Field(min_length=1)
    tool_name: StrictStr = Field(min_length=1)
    reason: StrictStr = Field(min_length=1)
    timestamp: datetime


@dataclass
class DeniedCallAuditLog:
    """In-memory denied-call audit sink used by the runtime and tests."""

    records: list[DeniedToolCallRecord] = field(default_factory=list)

    def append(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        reason: str,
    ) -> None:
        self.records.append(
            DeniedToolCallRecord(
                run_id=run_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                reason=reason,
                timestamp=_utcnow(),
            )
        )


@dataclass(frozen=True)
class ToolExecutionContext:
    run_id: str
    workspace_root: Path
    network_mode: NetworkMode = NetworkMode.RESTRICTED
    sandbox_mode: SandboxMode = SandboxMode.WORKSPACE_WRITE
    writable_roots: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ToolCall:
    tool_call_id: str
    tool_name: str
    args: Mapping[str, Any]
    idempotency_key: str | None = None


ToolHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class ToolRegistry:
    """Authoritative manifest registry for every backend-executable tool."""

    def __init__(self, manifests: list[ToolManifest] | None = None) -> None:
        self._manifests: dict[str, ToolManifest] = {}
        for manifest in manifests or []:
            self.register(manifest)

    def register(self, manifest: ToolManifest) -> None:
        if manifest.name in self._manifests:
            raise ToolValidationError(f"duplicate tool manifest: {manifest.name}")
        self._manifests[manifest.name] = manifest

    def resolve(self, tool_name: str) -> ToolManifest:
        try:
            return self._manifests[tool_name]
        except KeyError as exc:
            raise ToolPolicyViolation(f"unknown tool: {tool_name}") from exc

    def names(self) -> frozenset[str]:
        return frozenset(self._manifests)

    def manifests(self) -> tuple[ToolManifest, ...]:
        return tuple(self._manifests[name] for name in sorted(self._manifests))

    def manifest_schema(self) -> dict[str, Any]:
        return ToolManifest.model_json_schema()


class ToolPolicyValidator:
    """Fail-closed policy validator for manifests, allowed tools, and roots."""

    def __init__(self, registry: ToolRegistry, *, workspace_root: Path) -> None:
        self._registry = registry
        self._workspace_root = workspace_root.resolve()

    def validate_runtime_policy(self, policy: ToolPolicy) -> None:
        if policy.sandbox_mode == SandboxMode.DISABLED:
            raise ToolPolicyViolation("runtime policy rejected disabled sandbox mode")
        if policy.network_mode != NetworkMode.RESTRICTED:
            raise ToolPolicyViolation(
                f"unsupported network mode for codex exec runtime: {policy.network_mode.value}"
            )
        allowed_tools = frozenset(policy.allowed_tools)
        for tool_name in allowed_tools:
            self.validate_tool_allowed(
                tool_name=tool_name,
                policy=policy,
                writable_roots=(self._workspace_root,),
            )

    def validate_observed_tool_call(self, *, policy: ToolPolicy, tool_name: str) -> None:
        self.validate_tool_allowed(
            tool_name=tool_name,
            policy=policy,
            writable_roots=(self._workspace_root,),
        )

    def validate_tool_allowed(
        self,
        *,
        tool_name: str,
        policy: ToolPolicy,
        writable_roots: tuple[Path, ...],
    ) -> ToolManifest:
        if tool_name not in set(policy.allowed_tools):
            raise ToolPolicyViolation(f"tool is not allowed by runtime policy: {tool_name}")
        manifest = self._registry.resolve(tool_name)
        self.validate_manifest_for_runtime(
            manifest,
            network_mode=policy.network_mode,
            sandbox_mode=policy.sandbox_mode,
            writable_roots=writable_roots,
        )
        return manifest

    def validate_manifest_for_runtime(
        self,
        manifest: ToolManifest,
        *,
        network_mode: NetworkMode,
        sandbox_mode: SandboxMode,
        writable_roots: tuple[Path, ...],
    ) -> None:
        if manifest.side_effect_level == ToolSideEffectLevel.NON_IDEMPOTENT_WRITE:
            raise ToolPolicyViolation(
                f"non-idempotent write tools are forbidden in v1 runtime: {manifest.name}"
            )
        if manifest.idempotency_mode == ToolIdempotencyMode.NON_IDEMPOTENT:
            raise ToolPolicyViolation(
                f"non-idempotent tool mode is forbidden in v1 runtime: {manifest.name}"
            )
        if network_mode != NetworkMode.RESTRICTED:
            raise ToolPolicyViolation(f"unsupported network mode: {network_mode.value}")
        if sandbox_mode == SandboxMode.DISABLED:
            raise ToolPolicyViolation("disabled sandbox mode is forbidden")
        manifest_roots = tuple(Path(root) for root in manifest.permissions.writable_roots)
        for root in writable_roots + manifest_roots:
            _ensure_within_workspace(root=root, workspace_root=self._workspace_root)
        if (
            manifest.side_effect_level == ToolSideEffectLevel.DETERMINISTIC_WRITE
            and sandbox_mode != SandboxMode.WORKSPACE_WRITE
        ):
            raise ToolPolicyViolation(
                f"deterministic write tool requires workspace-write sandbox: {manifest.name}"
            )


class ToolExecutorWrapper:
    """Executor gate that validates, dispatches, normalizes, and audits tools."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        workspace_root: Path,
        audit_log: DeniedCallAuditLog | None = None,
    ) -> None:
        self._registry = registry
        self._policy_validator = ToolPolicyValidator(
            registry,
            workspace_root=workspace_root,
        )
        self._audit_log = audit_log or DeniedCallAuditLog()
        self._handlers: dict[str, ToolHandler] = {}

    @property
    def audit_log(self) -> DeniedCallAuditLog:
        return self._audit_log

    def register_handler(self, tool_name: str, handler: ToolHandler) -> None:
        self._registry.resolve(tool_name)
        self._handlers[tool_name] = handler

    def dispatch(
        self,
        call: ToolCall,
        *,
        context: ToolExecutionContext,
        policy: ToolPolicy,
    ) -> NormalizedToolResult:
        try:
            _validate_context_matches_policy(context=context, policy=policy)
            manifest = self._policy_validator.validate_tool_allowed(
                tool_name=call.tool_name,
                policy=policy,
                writable_roots=context.writable_roots or (context.workspace_root,),
            )
            _validate_json_value(
                manifest.input_schema,
                dict(call.args),
                path=f"{call.tool_name}.args",
            )
            if manifest.side_effect_level == ToolSideEffectLevel.DETERMINISTIC_WRITE:
                if not call.idempotency_key:
                    raise ToolPolicyViolation(
                        f"deterministic write tool requires idempotency key: {call.tool_name}"
                    )
            handler = self._handlers.get(call.tool_name)
            if handler is None:
                raise ToolPolicyViolation(f"no executor handler registered for tool: {call.tool_name}")
        except (ToolPolicyViolation, ToolValidationError) as exc:
            self._audit_denial(call=call, context=context, reason=str(exc))
            raise

        try:
            raw_result = handler(call.args)
        except ToolTimeoutError as exc:
            error = ToolErrorEnvelope(
                error_class="timeout",
                message=str(exc) or f"tool timed out after {manifest.timeout_seconds}s",
                retryable=manifest.retry_policy.max_attempts > 1,
                hint="retry according to the manifest retry policy",
            )
            return _error_result(call=call, error=error)

        normalized_result = _normalize_json_value(
            manifest.output_schema,
            dict(raw_result),
            path=f"{call.tool_name}.result",
        )
        return NormalizedToolResult(
            tool_call_id=call.tool_call_id,
            tool_name=call.tool_name,
            status=ToolCallStatus.OK,
            result=normalized_result,
            retryable=False,
            result_digest=_digest_payload(normalized_result),
        )

    def _audit_denial(
        self,
        *,
        call: ToolCall,
        context: ToolExecutionContext,
        reason: str,
    ) -> None:
        self._audit_log.append(
            run_id=context.run_id,
            tool_call_id=call.tool_call_id,
            tool_name=call.tool_name,
            reason=reason,
        )


def build_default_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolManifest(
                name="web.search",
                version="1",
                kind="external_io",
                description="Search the public web for evidence gathering.",
                input_schema={
                    "type": "object",
                    "required": ["query"],
                    "additionalProperties": False,
                    "properties": {"query": {"type": "string", "minLength": 1}},
                },
                output_schema={
                    "type": "object",
                    "required": ["summary", "artifacts"],
                    "additionalProperties": False,
                    "properties": {
                        "summary": {"type": "string", "minLength": 1},
                        "artifacts": {"type": "array", "items": {"type": "object"}},
                    },
                },
                timeout_seconds=30,
                side_effect_level=ToolSideEffectLevel.READ_ONLY,
                idempotency_mode=ToolIdempotencyMode.CACHEABLE,
                retry_policy=ToolRetryPolicy(
                    max_attempts=2,
                    backoff=ToolRetryBackoff.EXPONENTIAL,
                ),
                permissions=ToolPermissions(network=True),
            ),
            ToolManifest(
                name="web.fetch",
                version="1",
                kind="external_io",
                description="Fetch a known public web resource for evidence gathering.",
                input_schema={
                    "type": "object",
                    "required": ["url"],
                    "additionalProperties": False,
                    "properties": {"url": {"type": "string", "minLength": 1}},
                },
                output_schema={
                    "type": "object",
                    "required": ["summary", "artifact_id"],
                    "additionalProperties": False,
                    "properties": {
                        "summary": {"type": "string", "minLength": 1},
                        "artifact_id": {"type": "string", "minLength": 1},
                    },
                },
                timeout_seconds=30,
                side_effect_level=ToolSideEffectLevel.READ_ONLY,
                idempotency_mode=ToolIdempotencyMode.CACHEABLE,
                retry_policy=ToolRetryPolicy(
                    max_attempts=2,
                    backoff=ToolRetryBackoff.EXPONENTIAL,
                ),
                permissions=ToolPermissions(network=True),
            ),
            ToolManifest(
                name="internal.graph_query",
                version="1",
                kind="internal_service",
                description="Read the backend-owned research graph snapshot.",
                input_schema={
                    "type": "object",
                    "required": ["topic_id"],
                    "additionalProperties": False,
                    "properties": {"topic_id": {"type": "string", "minLength": 1}},
                },
                output_schema={
                    "type": "object",
                    "required": ["summary"],
                    "additionalProperties": False,
                    "properties": {"summary": {"type": "string", "minLength": 1}},
                },
                timeout_seconds=10,
                side_effect_level=ToolSideEffectLevel.READ_ONLY,
                idempotency_mode=ToolIdempotencyMode.PURE,
                retry_policy=ToolRetryPolicy(max_attempts=1, backoff=ToolRetryBackoff.NONE),
                permissions=ToolPermissions(network=False),
            ),
        ]
    )


def _error_result(*, call: ToolCall, error: ToolErrorEnvelope) -> NormalizedToolResult:
    payload = error.model_dump(mode="json")
    return NormalizedToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=call.tool_name,
        status=ToolCallStatus.ERROR,
        error=error,
        retryable=error.retryable,
        result_digest=_digest_payload(payload),
    )


def _ensure_within_workspace(*, root: Path, workspace_root: Path) -> None:
    resolved = root.resolve()
    workspace = workspace_root.resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise ToolPolicyViolation(
            f"writable root escapes workspace boundary: {resolved} not under {workspace}"
        )


def _validate_context_matches_policy(
    *,
    context: ToolExecutionContext,
    policy: ToolPolicy,
) -> None:
    if context.network_mode != policy.network_mode:
        raise ToolPolicyViolation(
            "execution context network mode does not match runtime policy: "
            f"{context.network_mode.value} != {policy.network_mode.value}"
        )
    if context.sandbox_mode != policy.sandbox_mode:
        raise ToolPolicyViolation(
            "execution context sandbox mode does not match runtime policy: "
            f"{context.sandbox_mode.value} != {policy.sandbox_mode.value}"
        )


def _validate_json_schema(schema: Mapping[str, Any], *, path: str) -> None:
    if not isinstance(schema, Mapping):
        raise ToolValidationError(f"{path} must be an object schema")
    schema_type = schema.get("type")
    if schema_type not in {"object", "array", "string", "integer", "number", "boolean", "null"}:
        raise ToolValidationError(f"{path}.type is unsupported: {schema_type!r}")
    if schema_type == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ToolValidationError(f"{path}.properties must be an object")
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise ToolValidationError(f"{path}.required must be a string list")
        for key, child_schema in properties.items():
            if not isinstance(key, str):
                raise ToolValidationError(f"{path}.properties keys must be strings")
            _validate_json_schema(child_schema, path=f"{path}.properties.{key}")
    if schema_type == "array" and "items" in schema:
        _validate_json_schema(schema["items"], path=f"{path}.items")


def _validate_json_value(schema: Mapping[str, Any], value: Any, *, path: str) -> None:
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            raise ToolValidationError(f"{path} must be an object")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise ToolValidationError(f"{path}.{key} is required")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise ToolValidationError(f"{path} has unsupported properties: {', '.join(extra)}")
        for key, child_schema in properties.items():
            if key in value:
                _validate_json_value(child_schema, value[key], path=f"{path}.{key}")
        return
    if schema_type == "array":
        if not isinstance(value, list):
            raise ToolValidationError(f"{path} must be an array")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_json_value(item_schema, item, path=f"{path}[{index}]")
        return
    if schema_type == "string":
        if not isinstance(value, str):
            raise ToolValidationError(f"{path} must be a string")
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            raise ToolValidationError(f"{path} is shorter than minLength {min_length}")
        return
    if schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ToolValidationError(f"{path} must be an integer")
        return
    if schema_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ToolValidationError(f"{path} must be a number")
        return
    if schema_type == "boolean":
        if not isinstance(value, bool):
            raise ToolValidationError(f"{path} must be a boolean")
        return
    if schema_type == "null" and value is not None:
        raise ToolValidationError(f"{path} must be null")


def _normalize_json_value(schema: Mapping[str, Any], value: Any, *, path: str) -> Any:
    schema_type = schema.get("type")
    if schema_type == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        if not properties and schema.get("additionalProperties") is not False:
            _validate_json_value(schema, value, path=path)
            return dict(value)
        normalized = {
            key: _normalize_json_value(child_schema, value[key], path=f"{path}.{key}")
            for key, child_schema in properties.items()
            if key in value
        }
        try:
            _validate_json_value(schema, normalized, path=path)
        except ToolValidationError as exc:
            raise ToolOutputValidationError(str(exc)) from exc
        return normalized
    if schema_type == "array" and isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        normalized_items = [
            _normalize_json_value(schema["items"], item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
        _validate_json_value(schema, normalized_items, path=path)
        return normalized_items
    _validate_json_value(schema, value, path=path)
    return value
