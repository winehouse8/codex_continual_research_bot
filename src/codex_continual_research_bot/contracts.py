"""Strict contract models for Phase 0 fixtures."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr


class StrictModel(BaseModel):
    """Base model that rejects undeclared fields throughout the contract tree."""

    model_config = ConfigDict(extra="forbid")


class RunMode(str, Enum):
    INTERACTIVE = "interactive"
    SCHEDULED = "scheduled"


class NetworkMode(str, Enum):
    RESTRICTED = "restricted"
    ALLOWLIST = "allowlist"
    OPEN = "open"


class SandboxMode(str, Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DISABLED = "disabled"


class EvidenceKind(str, Enum):
    WEB_PAGE = "web_page"
    WEB_RESULT = "web_result"
    INTERNAL_NOTE = "internal_note"


class ArgumentStance(str, Enum):
    SUPPORT = "support"
    CHALLENGE = "challenge"


class ChallengerStatus(str, Enum):
    PROPOSED = "proposed"
    CARRIED_FORWARD = "carried_forward"


class ConflictStatus(str, Enum):
    UNRESOLVED = "unresolved"
    RECONCILED = "reconciled"
    ESCALATED = "escalated"


class RevisionAction(str, Enum):
    STRENGTHEN = "strengthen"
    WEAKEN = "weaken"
    RETIRE = "retire"
    SUPERSEDE = "supersede"


class NextActionKind(str, Enum):
    ATTACK_CURRENT_BEST = "attack_current_best"
    GATHER_EVIDENCE = "gather_evidence"
    VALIDATE_SOURCE = "validate_source"
    PROCESS_USER_INPUT = "process_user_input"


class RuntimeEventType(str, Enum):
    RUN_STARTED = "run.started"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    OUTPUT_VALIDATED = "output.validated"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class SessionState(str, Enum):
    BOOTSTRAPPING = "bootstrapping"
    ACTIVE = "active"
    REFRESHING = "refreshing"
    EXPIRED = "expired"
    REAUTH_REQUIRED = "reauth_required"
    REVOKED = "revoked"
    RENEWAL_SESSION = "renewal_session"


class VerificationLevel(str, Enum):
    ACCOUNT_AND_CONFIG = "account-read+config-read"
    AUTH_JSON_ONLY = "auth-json-continuity-only"


class LoginMethod(str, Enum):
    CHATGPT = "chatgpt"


class TrustLevel(str, Enum):
    TRUSTED = "trusted"


class QueueJobKind(str, Enum):
    TOPIC_BOOTSTRAP = "topic.bootstrap"
    RUN_EXECUTE = "run.execute"
    RUN_RESUME = "run.resume"
    USER_INPUT_PROCESS = "user_input.process"
    TOPIC_REFRESH_SCHEDULE = "topic.refresh_schedule"
    GRAPH_REPAIR = "graph.repair"
    SESSION_HEALTHCHECK = "session.healthcheck"


class QueueJobState(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    RETRYABLE_FAILED = "retryable_failed"
    TERMINAL_FAILED = "terminal_failed"
    DEAD_LETTER = "dead_letter"


class FailureCode(str, Enum):
    AUTH_MATERIAL_MISSING = "auth_material_missing"
    REFRESH_FAILED = "refresh_failed"
    PRINCIPAL_MISMATCH = "principal_mismatch"
    WORKSPACE_MISMATCH = "workspace_mismatch"
    STALE_SESSION = "stale_session"
    RUNNER_HOST_UNAVAILABLE = "runner_host_unavailable"
    CONCURRENT_SESSION_MUTATION = "concurrent_session_mutation"
    CLI_LOGIN_PATH_BLOCKED = "cli_login_path_blocked"
    OUTPUT_SCHEMA_VALIDATION_FAILED = "output_schema_validation_failed"
    MALFORMED_PROPOSAL = "malformed_proposal"
    QUEUE_MUTATION_MISMATCH = "queue_mutation_mismatch"
    DUPLICATE_QUEUE_DELIVERY = "duplicate_queue_delivery"


class RunPlan(StrictModel):
    must_attack_current_best: StrictBool
    must_generate_challenger: StrictBool
    must_collect_support_and_challenge: StrictBool


class HypothesisRef(StrictModel):
    hypothesis_id: StrictStr = Field(min_length=1)
    title: StrictStr = Field(min_length=1)
    summary: StrictStr = Field(min_length=1)


class ConflictRef(StrictModel):
    conflict_id: StrictStr = Field(min_length=1)
    summary: StrictStr = Field(min_length=1)


class QueueSelection(StrictModel):
    queue_item_id: StrictStr = Field(min_length=1)
    kind: QueueJobKind
    summary: StrictStr = Field(min_length=1)


class QueuedUserInput(StrictModel):
    user_input_id: StrictStr = Field(min_length=1)
    input_type: StrictStr = Field(min_length=1)
    summary: StrictStr = Field(min_length=1)
    submitted_at: datetime


class ContextSnapshot(StrictModel):
    topic_summary: StrictStr = Field(min_length=1)
    current_best_hypotheses: list[HypothesisRef]
    challenger_targets: list[HypothesisRef]
    active_conflicts: list[ConflictRef]
    open_questions: list[StrictStr]
    recent_provenance_digest: StrictStr = Field(min_length=1)
    selected_queue_items: list[QueueSelection]
    queued_user_inputs: list[QueuedUserInput]


class ToolPolicy(StrictModel):
    allowed_tools: list[StrictStr]
    network_mode: NetworkMode
    sandbox_mode: SandboxMode


class OutputContract(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    max_repair_attempts: StrictInt = Field(ge=0)


class ExecutionBudgets(StrictModel):
    max_turns: StrictInt = Field(ge=1)
    max_tool_calls: StrictInt = Field(ge=1)
    max_runtime_seconds: StrictInt = Field(ge=1)
    soft_input_tokens: StrictInt = Field(ge=1)
    hard_input_tokens: StrictInt = Field(ge=1)


class RunExecutionRequest(StrictModel):
    run_id: StrictStr = Field(min_length=1)
    topic_id: StrictStr = Field(min_length=1)
    mode: RunMode
    objective: StrictStr = Field(min_length=1)
    plan: RunPlan
    context_snapshot: ContextSnapshot
    tool_policy: ToolPolicy
    output_contract: OutputContract
    budgets: ExecutionBudgets
    idempotency_key: StrictStr = Field(min_length=1)


class EvidenceCandidate(StrictModel):
    artifact_id: StrictStr = Field(min_length=1)
    kind: EvidenceKind
    source_url: StrictStr = Field(min_length=1)
    title: StrictStr = Field(min_length=1)
    accessed_at: datetime
    extraction_note: StrictStr = Field(min_length=1)


class Claim(StrictModel):
    claim_id: StrictStr = Field(min_length=1)
    text: StrictStr = Field(min_length=1)
    artifact_ids: list[StrictStr] = Field(min_length=1)
    temporal_scope: StrictStr = Field(min_length=1)


class Argument(StrictModel):
    argument_id: StrictStr = Field(min_length=1)
    stance: ArgumentStance
    target_hypothesis_id: StrictStr = Field(min_length=1)
    claim_ids: list[StrictStr] = Field(min_length=1)
    rationale: StrictStr = Field(min_length=1)


class ChallengerHypothesis(StrictModel):
    hypothesis_id: StrictStr = Field(min_length=1)
    title: StrictStr = Field(min_length=1)
    statement: StrictStr = Field(min_length=1)
    status: ChallengerStatus


class ConflictAssessment(StrictModel):
    conflict_id: StrictStr = Field(min_length=1)
    status: ConflictStatus
    summary: StrictStr = Field(min_length=1)


class RevisionProposal(StrictModel):
    hypothesis_id: StrictStr = Field(min_length=1)
    action: RevisionAction
    rationale: StrictStr = Field(min_length=1)
    supersedes_hypothesis_id: StrictStr | None = None


class NextAction(StrictModel):
    action_id: StrictStr = Field(min_length=1)
    kind: NextActionKind
    description: StrictStr = Field(min_length=1)


class ExecutionMeta(StrictModel):
    turn_count: StrictInt = Field(ge=0)
    tool_call_count: StrictInt = Field(ge=0)
    compactions: StrictInt = Field(ge=0)
    repair_attempts: StrictInt = Field(ge=0)


class ProposalBundle(StrictModel):
    summary_draft: StrictStr = Field(min_length=1)
    evidence_candidates: list[EvidenceCandidate]
    claims: list[Claim]
    arguments: list[Argument]
    challenger_hypotheses: list[ChallengerHypothesis]
    conflict_assessments: list[ConflictAssessment]
    revision_proposals: list[RevisionProposal]
    next_actions: list[NextAction]
    execution_meta: ExecutionMeta


class RuntimeEvent(StrictModel):
    run_id: StrictStr = Field(min_length=1)
    seq: StrictInt = Field(ge=0)
    event_type: RuntimeEventType
    turn_index: StrictInt = Field(ge=0)
    timestamp: datetime
    payload: dict[StrictStr, Any]


class AccountSnapshot(StrictModel):
    email: StrictStr = Field(min_length=1)
    type: StrictStr = Field(min_length=1)
    plan_type: StrictStr = Field(min_length=1)
    requires_openai_auth: StrictBool


class ConfigSnapshot(StrictModel):
    forced_login_method: LoginMethod
    forced_chatgpt_workspace_id: StrictStr = Field(min_length=1)
    trusted_project_paths: list[StrictStr] = Field(min_length=1)


class AuthJsonSnapshot(StrictModel):
    auth_mode: LoginMethod
    last_refresh: datetime
    has_access_token: StrictBool
    has_id_token: StrictBool
    has_refresh_token: StrictBool


class SessionChecks(StrictModel):
    principal_match: StrictBool
    workspace_match: StrictBool
    trust_configured: StrictBool
    session_fresh: StrictBool


class SessionInspectResult(StrictModel):
    session_id: StrictStr = Field(min_length=1)
    principal_id: StrictStr = Field(min_length=1)
    purpose: StrictStr = Field(min_length=1)
    host_id: StrictStr = Field(min_length=1)
    credential_locator: StrictStr = Field(min_length=1)
    state: SessionState
    workspace_id: StrictStr = Field(min_length=1)
    workspace_root: StrictStr = Field(min_length=1)
    verification_level: VerificationLevel
    login_method: LoginMethod
    principal_fingerprint: StrictStr = Field(min_length=1)
    account: AccountSnapshot
    config: ConfigSnapshot
    auth_json: AuthJsonSnapshot
    checks: SessionChecks
    inspected_at: datetime
    last_validated_at: datetime
    last_refreshed_at: datetime


class QueueFailure(StrictModel):
    code: FailureCode
    retryable: StrictBool
    human_review_required: StrictBool
    detail: StrictStr = Field(min_length=1)


class QueuePayload(StrictModel):
    initiator: StrictStr = Field(min_length=1)
    objective: StrictStr = Field(min_length=1)
    selected_queue_item_ids: list[StrictStr]


class QueueJob(StrictModel):
    queue_item_id: StrictStr = Field(min_length=1)
    kind: QueueJobKind
    state: QueueJobState
    topic_id: StrictStr = Field(min_length=1)
    run_id: StrictStr = Field(min_length=1)
    dedupe_key: StrictStr = Field(min_length=1)
    idempotency_key: StrictStr = Field(min_length=1)
    priority: StrictInt = Field(ge=0)
    attempts: StrictInt = Field(ge=0)
    max_attempts: StrictInt = Field(ge=1)
    available_at: datetime
    payload: QueuePayload
    last_failure: QueueFailure | None = None
