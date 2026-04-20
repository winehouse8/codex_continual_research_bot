from __future__ import annotations

from pathlib import Path

import pytest

from codex_continual_research_bot.contracts import NetworkMode, SandboxMode, ToolPolicy
from codex_continual_research_bot.tools import (
    DeniedCallAuditLog,
    NormalizedToolResult,
    ToolCall,
    ToolCallStatus,
    ToolExecutorWrapper,
    ToolExecutionContext,
    ToolIdempotencyMode,
    ToolManifest,
    ToolOutputValidationError,
    ToolPermissions,
    ToolPolicyValidator,
    ToolPolicyViolation,
    ToolRegistry,
    ToolRetryBackoff,
    ToolRetryPolicy,
    ToolSideEffectLevel,
    ToolTimeoutError,
    ToolValidationError,
    build_default_tool_registry,
)


def runtime_policy(*, allowed_tools: list[str] | None = None) -> ToolPolicy:
    return ToolPolicy(
        allowed_tools=allowed_tools or ["web.search", "web.fetch", "internal.graph_query"],
        network_mode=NetworkMode.RESTRICTED,
        sandbox_mode=SandboxMode.WORKSPACE_WRITE,
    )


def context(tmp_path: Path, *, writable_roots: tuple[Path, ...] = ()) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id="run_001",
        workspace_root=tmp_path,
        writable_roots=writable_roots,
    )


def deterministic_write_manifest(tmp_path: Path) -> ToolManifest:
    return ToolManifest(
        name="internal.note_write",
        version="1",
        kind="internal_service",
        description="Write a deterministic note under the workspace.",
        input_schema={
            "type": "object",
            "required": ["note"],
            "additionalProperties": False,
            "properties": {"note": {"type": "string", "minLength": 1}},
        },
        output_schema={
            "type": "object",
            "required": ["path"],
            "additionalProperties": False,
            "properties": {"path": {"type": "string", "minLength": 1}},
        },
        timeout_seconds=5,
        side_effect_level=ToolSideEffectLevel.DETERMINISTIC_WRITE,
        idempotency_mode=ToolIdempotencyMode.IDEMPOTENT,
        retry_policy=ToolRetryPolicy(max_attempts=1, backoff=ToolRetryBackoff.NONE),
        permissions=ToolPermissions(
            filesystem=True,
            writable_roots=[str(tmp_path / "notes")],
        ),
    )


def non_idempotent_manifest() -> ToolManifest:
    return ToolManifest(
        name="external.publish",
        version="1",
        kind="external_io",
        description="Publish irreversible external state.",
        input_schema={
            "type": "object",
            "required": ["body"],
            "additionalProperties": False,
            "properties": {"body": {"type": "string", "minLength": 1}},
        },
        output_schema={
            "type": "object",
            "required": ["published_id"],
            "additionalProperties": False,
            "properties": {"published_id": {"type": "string", "minLength": 1}},
        },
        timeout_seconds=5,
        side_effect_level=ToolSideEffectLevel.NON_IDEMPOTENT_WRITE,
        idempotency_mode=ToolIdempotencyMode.NON_IDEMPOTENT,
        retry_policy=ToolRetryPolicy(max_attempts=1, backoff=ToolRetryBackoff.NONE),
        permissions=ToolPermissions(network=True),
    )


def test_tool_manifest_schema_is_available_and_validates_required_fields() -> None:
    registry = build_default_tool_registry()
    schema = registry.manifest_schema()

    assert schema["title"] == "ToolManifest"
    assert {"name", "input_schema", "output_schema", "permissions"}.issubset(
        set(schema["required"])
    )


def test_happy_path_tool_dispatch_validates_and_normalizes_output(tmp_path: Path) -> None:
    registry = build_default_tool_registry()
    executor = ToolExecutorWrapper(registry, workspace_root=tmp_path)
    executor.register_handler(
        "web.search",
        lambda args: {
            "summary": f"result for {args['query']}",
            "artifacts": [{"artifact_id": "src_001"}],
        },
    )

    result = executor.dispatch(
        ToolCall(
            tool_call_id="call_001",
            tool_name="web.search",
            args={"query": "belief revision"},
        ),
        context=context(tmp_path),
        policy=runtime_policy(),
    )

    assert isinstance(result, NormalizedToolResult)
    assert result.status == ToolCallStatus.OK
    assert result.result == {
        "summary": "result for belief revision",
        "artifacts": [{"artifact_id": "src_001"}],
    }
    assert result.result_digest.startswith("sha256:")


def test_malformed_tool_args_rejected_before_handler_and_audited(tmp_path: Path) -> None:
    registry = build_default_tool_registry()
    audit = DeniedCallAuditLog()
    executor = ToolExecutorWrapper(registry, workspace_root=tmp_path, audit_log=audit)
    executor.register_handler("web.search", lambda args: {"summary": "x", "artifacts": []})

    with pytest.raises(ToolValidationError) as excinfo:
        executor.dispatch(
            ToolCall(
                tool_call_id="call_001",
                tool_name="web.search",
                args={"unexpected": "drift"},
            ),
            context=context(tmp_path),
            policy=runtime_policy(),
        )

    assert "query is required" in str(excinfo.value)
    assert len(audit.records) == 1
    assert audit.records[0].tool_name == "web.search"


def test_unknown_tool_rejected_and_audited(tmp_path: Path) -> None:
    audit = DeniedCallAuditLog()
    executor = ToolExecutorWrapper(
        build_default_tool_registry(),
        workspace_root=tmp_path,
        audit_log=audit,
    )

    with pytest.raises(ToolPolicyViolation, match="tool is not allowed"):
        executor.dispatch(
            ToolCall(tool_call_id="call_001", tool_name="shell.exec", args={}),
            context=context(tmp_path),
            policy=runtime_policy(),
        )

    assert len(audit.records) == 1
    assert audit.records[0].tool_name == "shell.exec"


def test_forbidden_tool_class_rejected_by_policy_validator(tmp_path: Path) -> None:
    registry = build_default_tool_registry()
    registry.register(non_idempotent_manifest())
    validator = ToolPolicyValidator(registry, workspace_root=tmp_path)

    with pytest.raises(ToolPolicyViolation, match="non-idempotent write tools are forbidden"):
        validator.validate_tool_allowed(
            tool_name="external.publish",
            policy=runtime_policy(
                allowed_tools=[
                    "web.search",
                    "web.fetch",
                    "internal.graph_query",
                    "external.publish",
                ]
            ),
            writable_roots=(tmp_path,),
        )


def test_non_idempotent_write_blocked_by_executor_and_audited(tmp_path: Path) -> None:
    registry = build_default_tool_registry()
    registry.register(non_idempotent_manifest())
    audit = DeniedCallAuditLog()
    executor = ToolExecutorWrapper(registry, workspace_root=tmp_path, audit_log=audit)
    executor.register_handler("external.publish", lambda args: {"published_id": "pub_001"})

    with pytest.raises(ToolPolicyViolation, match="non-idempotent write tools are forbidden"):
        executor.dispatch(
            ToolCall(
                tool_call_id="call_001",
                tool_name="external.publish",
                args={"body": "publish this"},
            ),
            context=context(tmp_path),
            policy=runtime_policy(
                allowed_tools=[
                    "web.search",
                    "web.fetch",
                    "internal.graph_query",
                    "external.publish",
                ]
            ),
        )

    assert len(audit.records) == 1
    assert "non-idempotent write tools are forbidden" in audit.records[0].reason


def test_permission_boundary_blocks_writable_roots_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    registry = ToolRegistry([deterministic_write_manifest(workspace)])
    validator = ToolPolicyValidator(registry, workspace_root=workspace)

    with pytest.raises(ToolPolicyViolation, match="writable root escapes workspace boundary"):
        validator.validate_tool_allowed(
            tool_name="internal.note_write",
            policy=runtime_policy(allowed_tools=["internal.note_write"]),
            writable_roots=(outside,),
        )


def test_tool_output_drift_is_normalized_before_downstream(tmp_path: Path) -> None:
    registry = build_default_tool_registry()
    executor = ToolExecutorWrapper(registry, workspace_root=tmp_path)
    executor.register_handler(
        "web.fetch",
        lambda args: {
            "summary": "canonical summary",
            "artifact_id": "src_001",
            "raw_html": "<html>large drift</html>",
        },
    )

    result = executor.dispatch(
        ToolCall(
            tool_call_id="call_001",
            tool_name="web.fetch",
            args={"url": "https://example.com"},
        ),
        context=context(tmp_path),
        policy=runtime_policy(),
    )

    assert result.status == ToolCallStatus.OK
    assert result.result == {"summary": "canonical summary", "artifact_id": "src_001"}
    assert "raw_html" not in result.model_dump(mode="json")["result"]


def test_tool_output_missing_required_field_is_rejected(tmp_path: Path) -> None:
    registry = build_default_tool_registry()
    executor = ToolExecutorWrapper(registry, workspace_root=tmp_path)
    executor.register_handler("web.fetch", lambda args: {"summary": "missing artifact"})

    with pytest.raises(ToolOutputValidationError, match="artifact_id is required"):
        executor.dispatch(
            ToolCall(
                tool_call_id="call_001",
                tool_name="web.fetch",
                args={"url": "https://example.com"},
            ),
            context=context(tmp_path),
            policy=runtime_policy(),
        )


def test_tool_timeout_result_classifies_retryability_from_manifest(tmp_path: Path) -> None:
    registry = build_default_tool_registry()
    executor = ToolExecutorWrapper(registry, workspace_root=tmp_path)

    def timeout_handler(args):
        raise ToolTimeoutError("upstream search timeout")

    executor.register_handler("web.search", timeout_handler)

    result = executor.dispatch(
        ToolCall(
            tool_call_id="call_001",
            tool_name="web.search",
            args={"query": "slow source"},
        ),
        context=context(tmp_path),
        policy=runtime_policy(),
    )

    assert result.status == ToolCallStatus.ERROR
    assert result.error is not None
    assert result.error.error_class == "timeout"
    assert result.retryable is True
