"""Phase 12 CLI and UX read-model contracts."""

from __future__ import annotations

import json
import shlex
from datetime import datetime
from enum import Enum

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from codex_continual_research_bot.contracts import (
    BackendStateUpdateSummary,
    ConflictStatus,
    InteractiveRunStatus,
    QueueJobKind,
    QueueJobState,
    StrictModel,
)


class CliCommandCategory(str, Enum):
    TOPIC = "topic"
    RUN = "run"
    QUEUE = "queue"
    MEMORY = "memory"
    GRAPH = "graph"
    OPS = "ops"


class CliOutputMode(str, Enum):
    HUMAN = "human"
    JSON = "json"


class CliStateMutation(str, Enum):
    NONE = "none"
    CREATE_TOPIC = "create_topic"
    ENQUEUE_RUN = "enqueue_run"
    ENQUEUE_USER_INPUT = "enqueue_user_input"
    ENQUEUE_REPAIR_JOB = "enqueue_repair_job"


class ConfidenceBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CONTESTED = "contested"


class GraphExportNodeType(str, Enum):
    TOPIC = "topic"
    HYPOTHESIS = "hypothesis"
    CLAIM = "claim"
    EVIDENCE = "evidence"
    CONFLICT = "conflict"
    PROVENANCE = "provenance"


class GraphExportEdgeType(str, Enum):
    SUPPORTS = "supports"
    CHALLENGES = "challenges"
    SUPERSEDES = "supersedes"
    CONFLICTS_WITH = "conflicts_with"
    DERIVED_FROM = "derived_from"
    VISUALIZES = "visualizes"


class UserFacingTerm(StrictModel):
    term: StrictStr = Field(min_length=1)
    meaning: StrictStr = Field(min_length=1)
    avoid: StrictStr = Field(min_length=1)


class CliCommandContract(StrictModel):
    command_id: StrictStr = Field(min_length=1)
    category: CliCommandCategory
    invocation: StrictStr = Field(min_length=1)
    summary: StrictStr = Field(min_length=1)
    reads: list[StrictStr]
    state_mutation: CliStateMutation
    authority_boundary: StrictStr = Field(min_length=1)
    output_modes: list[CliOutputMode] = Field(min_length=1)
    json_schema_id: StrictStr | None = None
    human_summary_shape: StrictStr | None = None
    examples: list[StrictStr] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_backend_authority_for_mutations(self) -> CliCommandContract:
        if self.state_mutation is not CliStateMutation.NONE:
            boundary = self.authority_boundary.lower()
            if "backend" not in boundary or "direct graph write" in boundary:
                raise ValueError(
                    "mutating CLI commands must route through backend authority"
                )
        return self


class CliCommandSpec(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    commands: list[CliCommandContract] = Field(min_length=1)
    terminology: list[UserFacingTerm] = Field(min_length=1)
    global_guards: list[StrictStr] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_command_ids(self) -> CliCommandSpec:
        command_ids = [command.command_id for command in self.commands]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("command_id values must be unique")
        return self


class HypothesisUXView(StrictModel):
    hypothesis_id: StrictStr = Field(min_length=1)
    title: StrictStr = Field(min_length=1)
    summary: StrictStr = Field(min_length=1)
    confidence: ConfidenceBand
    why_currently_best: StrictStr = Field(min_length=1)
    provenance_ids: list[StrictStr] = Field(min_length=1)


class ConflictUXView(StrictModel):
    conflict_id: StrictStr = Field(min_length=1)
    status: ConflictStatus
    summary: StrictStr = Field(min_length=1)
    why_it_matters: StrictStr = Field(min_length=1)


class QueueItemUXView(StrictModel):
    queue_item_id: StrictStr = Field(min_length=1)
    kind: QueueJobKind
    state: QueueJobState
    objective: StrictStr = Field(min_length=1)
    priority: StrictInt = Field(ge=0)
    attempts: StrictInt = Field(ge=0)
    next_visible_action: StrictStr = Field(min_length=1)


class TopicUXReadModel(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    topic_id: StrictStr = Field(min_length=1)
    title: StrictStr = Field(min_length=1)
    snapshot_version: StrictInt = Field(ge=1)
    topic_summary: StrictStr = Field(min_length=1)
    current_best_hypotheses: list[HypothesisUXView] = Field(min_length=1)
    challenger_targets: list[HypothesisUXView]
    active_conflicts: list[ConflictUXView]
    open_questions: list[StrictStr]
    queue_overview: list[QueueItemUXView]
    uncertainty: StrictStr = Field(min_length=1)
    provenance_digest: StrictStr = Field(min_length=1)
    generated_at: datetime


class RunUXReadModel(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    run_id: StrictStr = Field(min_length=1)
    topic_id: StrictStr = Field(min_length=1)
    status: InteractiveRunStatus
    objective: StrictStr = Field(min_length=1)
    summary: StrictStr = Field(min_length=1)
    uncertainty: StrictStr = Field(min_length=1)
    conflict_delta: StrictStr = Field(min_length=1)
    backend_state_update_required: StrictBool
    backend_state_update_applied: StrictBool
    backend_state_update: BackendStateUpdateSummary | None = None
    next_actions: list[StrictStr]
    created_at: datetime

    @model_validator(mode="after")
    def validate_state_update_visibility(self) -> RunUXReadModel:
        if self.status is InteractiveRunStatus.COMPLETED:
            if self.backend_state_update_required and self.backend_state_update is None:
                raise ValueError("completed runs must expose the backend state update")
        return self


class QueueUXReadModel(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    topic_id: StrictStr = Field(min_length=1)
    queued_count: StrictInt = Field(ge=0)
    blocked_count: StrictInt = Field(ge=0)
    items: list[QueueItemUXView]
    operator_note: StrictStr = Field(min_length=1)


class MemoryUXReadModel(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    topic_id: StrictStr = Field(min_length=1)
    graph_digest: StrictStr = Field(min_length=1)
    hypothesis_count: StrictInt = Field(ge=0)
    evidence_count: StrictInt = Field(ge=0)
    conflict_count: StrictInt = Field(ge=0)
    provenance_notice: StrictStr = Field(min_length=1)
    visualization_notice: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def validate_visualization_notice(self) -> MemoryUXReadModel:
        notice = self.visualization_notice.lower()
        if "not a source of truth" not in notice or "backend" not in notice:
            raise ValueError(
                "memory visualization notice must say it is not a source of truth"
            )
        return self


class UXReadModelBundle(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    topic: TopicUXReadModel
    run: RunUXReadModel
    queue: QueueUXReadModel
    memory: MemoryUXReadModel


class GraphExportNode(StrictModel):
    node_id: StrictStr = Field(min_length=1)
    node_type: GraphExportNodeType
    label: StrictStr = Field(min_length=1)
    summary: StrictStr = Field(min_length=1)
    temporal_scope: StrictStr = Field(min_length=1)
    provenance_ids: list[StrictStr]


class GraphExportEdge(StrictModel):
    edge_id: StrictStr = Field(min_length=1)
    edge_type: GraphExportEdgeType
    source_node_id: StrictStr = Field(min_length=1)
    target_node_id: StrictStr = Field(min_length=1)
    summary: StrictStr = Field(min_length=1)
    provenance_ids: list[StrictStr]


class GraphExportArtifact(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    export_id: StrictStr = Field(min_length=1)
    topic_id: StrictStr = Field(min_length=1)
    snapshot_version: StrictInt = Field(ge=1)
    graph_digest: StrictStr = Field(min_length=1)
    generated_at: datetime
    authority_notice: StrictStr = Field(min_length=1)
    nodes: list[GraphExportNode] = Field(min_length=1)
    edges: list[GraphExportEdge]

    @model_validator(mode="after")
    def validate_authority_notice(self) -> GraphExportArtifact:
        notice = self.authority_notice.lower()
        if "not a source of truth" not in notice or "backend" not in notice:
            raise ValueError(
                "graph export must declare that backend state remains authoritative"
            )
        node_ids = {node.node_id for node in self.nodes}
        missing = [
            edge.edge_id
            for edge in self.edges
            if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids
        ]
        if missing:
            raise ValueError(f"graph export edges reference missing nodes: {missing}")
        return self


def render_human_topic_summary(bundle: UXReadModelBundle) -> str:
    """Render the stable human summary shape used by the CLI contract."""

    topic = bundle.topic
    run = bundle.run
    memory = bundle.memory

    lines: list[str] = [
        f"Topic: {topic.title}",
        f"Snapshot: v{topic.snapshot_version}",
        f"Status: latest run {run.status.value} ({run.run_id})",
        "",
        "Current best hypotheses:",
    ]
    lines.extend(
        f"- {hypothesis.title} [{hypothesis.confidence.value}]: {hypothesis.summary}"
        for hypothesis in topic.current_best_hypotheses
    )
    lines.append("")
    lines.append("Challenger targets:")
    if topic.challenger_targets:
        lines.extend(
            f"- {hypothesis.title}: {hypothesis.summary}"
            for hypothesis in topic.challenger_targets
        )
    else:
        lines.append("- None selected yet.")
    lines.append("")
    lines.append("Active conflicts:")
    if topic.active_conflicts:
        lines.extend(
            f"- {conflict.conflict_id} ({conflict.status.value}): {conflict.summary}"
            for conflict in topic.active_conflicts
        )
    else:
        lines.append("- None recorded.")
    lines.extend(
        [
            "",
            "Uncertainty:",
            topic.uncertainty,
            "",
            "Run result:",
            run.summary,
            f"Conflict delta: {run.conflict_delta}",
            "",
            "Queue:",
        ]
    )
    if topic.queue_overview:
        lines.extend(
            (
                f"- {item.queue_item_id} {item.kind.value} "
                f"({item.state.value}): {item.objective}"
            )
            for item in topic.queue_overview
        )
    else:
        lines.append("- No queued work.")
    lines.extend(
        [
            "",
            "Memory:",
            f"- Graph digest: {memory.graph_digest}",
            f"- Hypotheses: {memory.hypothesis_count}; conflicts: {memory.conflict_count}",
            f"- Visualization note: {memory.visualization_notice}",
            "",
            "Next actions:",
        ]
    )
    if run.next_actions:
        lines.extend(f"- {action}" for action in run.next_actions)
    else:
        lines.append("- None.")
    return "\n".join(lines) + "\n"


def canonical_json(data: object) -> str:
    """Return the fixture snapshot encoding used by Phase 12 tests."""

    return json.dumps(data, indent=2, sort_keys=False) + "\n"


def extract_crb_examples(markdown: str) -> list[str]:
    """Extract shell examples that are part of the CLI contract docs."""

    examples: list[str] = []
    in_fence = False
    in_shell_block = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            if in_fence:
                in_fence = False
                in_shell_block = False
                continue
            language = line.removeprefix("```").strip()
            in_fence = True
            in_shell_block = language in {"bash", "sh", ""}
            continue
        if in_shell_block and line.startswith("crb "):
            examples.append(line)
    return examples


def command_prefix(example: str) -> tuple[str, ...]:
    """Return the stable command prefix from a documented `crb` example."""

    tokens = shlex.split(example)
    if len(tokens) < 3 or tokens[0] != "crb":
        raise ValueError(f"not a crb command example: {example}")
    return tuple(tokens[:3])
