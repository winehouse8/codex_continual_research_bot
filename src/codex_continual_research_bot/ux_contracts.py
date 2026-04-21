"""CLI, graph, and local web UX read-model contracts."""

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
    RunLifecycleState,
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
    WEAKENS = "weakens"
    RETIRES = "retires"
    CONFLICTS_WITH = "conflicts_with"
    DERIVED_FROM = "derived_from"
    VISUALIZES = "visualizes"


class WebDashboardViewId(str, Enum):
    OVERVIEW = "overview"
    HYPOTHESIS_BOARD = "hypothesis_board"
    GRAPH_EXPLORER = "graph_explorer"
    RUN_TIMELINE = "run_timeline"


class WebSurfaceState(str, Enum):
    READY = "ready"
    LOADING = "loading"
    EMPTY = "empty"
    ERROR = "error"
    DEAD_LETTER = "dead_letter"
    STALE_CLAIM = "stale_claim"


class WebSeverity(str, Enum):
    NORMAL = "normal"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class WebHypothesisLane(str, Enum):
    CURRENT_BEST = "current_best"
    CHALLENGER = "challenger"
    RETIRED = "retired"


class WebApiMethod(str, Enum):
    GET = "GET"


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


class MemoryExplorerSummary(StrictModel):
    summary: StrictStr = Field(min_length=1)
    current_best_node_ids: list[StrictStr]
    challenger_node_ids: list[StrictStr]
    conflict_node_ids: list[StrictStr]
    evidence_node_ids: list[StrictStr]
    provenance_node_ids: list[StrictStr]
    unresolved_conflict_count: StrictInt = Field(ge=0)


class GraphExportArtifact(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    export_id: StrictStr = Field(min_length=1)
    topic_id: StrictStr = Field(min_length=1)
    snapshot_version: StrictInt = Field(ge=1)
    projection_source: StrictStr = Field(min_length=1)
    graph_digest: StrictStr = Field(min_length=1)
    generated_at: datetime
    authority_notice: StrictStr = Field(min_length=1)
    memory_explorer: MemoryExplorerSummary
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
        missing_explorer_nodes = [
            node_id
            for node_id in (
                self.memory_explorer.current_best_node_ids
                + self.memory_explorer.challenger_node_ids
                + self.memory_explorer.conflict_node_ids
                + self.memory_explorer.evidence_node_ids
                + self.memory_explorer.provenance_node_ids
            )
            if node_id not in node_ids
        ]
        if missing_explorer_nodes:
            raise ValueError(
                "memory explorer references missing nodes: "
                f"{sorted(set(missing_explorer_nodes))}"
            )
        return self


class WebAuthorityNotice(StrictModel):
    notice_id: StrictStr = Field(min_length=1)
    text: StrictStr = Field(min_length=1)
    source_of_truth: StrictStr = Field(min_length=1)
    projection_only: StrictBool

    @model_validator(mode="after")
    def validate_backend_authority_notice(self) -> WebAuthorityNotice:
        notice = self.text.lower()
        if "not a source of truth" not in notice or "backend" not in notice:
            raise ValueError(
                "web UI authority notice must say projections are not a source of truth"
            )
        if self.source_of_truth.lower() != "backend":
            raise ValueError("web UI source_of_truth must be backend")
        if not self.projection_only:
            raise ValueError("web UI projections must be marked projection_only")
        return self


class WebNavigationItem(StrictModel):
    view_id: WebDashboardViewId
    label: StrictStr = Field(min_length=1)
    summary: StrictStr = Field(min_length=1)
    primary_metric: StrictStr = Field(min_length=1)


class WebDashboardInformationArchitecture(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    title: StrictStr = Field(min_length=1)
    default_view: WebDashboardViewId
    authority_notice: WebAuthorityNotice
    navigation: list[WebNavigationItem] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_required_dashboard_views(self) -> WebDashboardInformationArchitecture:
        required = set(WebDashboardViewId)
        present = {item.view_id for item in self.navigation}
        missing = required - present
        if missing:
            names = ", ".join(sorted(view.value for view in missing))
            raise ValueError(f"web dashboard navigation missing required views: {names}")
        return self


class WebStatusIndicator(StrictModel):
    indicator_id: StrictStr = Field(min_length=1)
    label: StrictStr = Field(min_length=1)
    severity: WebSeverity
    summary: StrictStr = Field(min_length=1)
    next_visible_action: StrictStr = Field(min_length=1)


class WebNextResearchAction(StrictModel):
    action_id: StrictStr = Field(min_length=1)
    label: StrictStr = Field(min_length=1)
    reason: StrictStr = Field(min_length=1)
    queue_item_id: StrictStr | None = None
    command_hint: StrictStr | None = None


class WebOverviewViewModel(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    topic_id: StrictStr = Field(min_length=1)
    title: StrictStr = Field(min_length=1)
    snapshot_version: StrictInt = Field(ge=1)
    state: WebSurfaceState
    generated_at: datetime
    authority_notice: WebAuthorityNotice
    topic_summary: StrictStr = Field(min_length=1)
    current_best_hypotheses: list[HypothesisUXView] = Field(min_length=1)
    active_conflicts: list[ConflictUXView]
    status_indicators: list[WebStatusIndicator] = Field(min_length=1)
    next_research_actions: list[WebNextResearchAction] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_overview_makes_work_visible(self) -> WebOverviewViewModel:
        if self.state is WebSurfaceState.READY:
            severe = {
                indicator.indicator_id
                for indicator in self.status_indicators
                if indicator.severity in {WebSeverity.WARNING, WebSeverity.CRITICAL}
            }
            if self.active_conflicts and "active_conflicts" not in severe:
                raise ValueError("overview must surface active conflicts as a warning")
        return self


class WebHypothesisCard(StrictModel):
    hypothesis_id: StrictStr = Field(min_length=1)
    lane: WebHypothesisLane
    title: StrictStr = Field(min_length=1)
    summary: StrictStr = Field(min_length=1)
    confidence: ConfidenceBand
    support_count: StrictInt = Field(ge=0)
    challenge_count: StrictInt = Field(ge=0)
    conflict_ids: list[StrictStr]
    provenance_ids: list[StrictStr]
    stale: StrictBool
    next_visible_action: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def validate_stale_cards_are_not_current_best(self) -> WebHypothesisCard:
        if self.stale and self.lane is WebHypothesisLane.CURRENT_BEST:
            raise ValueError("stale hypotheses must not appear in the current best lane")
        return self


class WebHypothesisBoardColumn(StrictModel):
    lane: WebHypothesisLane
    title: StrictStr = Field(min_length=1)
    cards: list[WebHypothesisCard]


class WebHypothesisBoardViewModel(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    topic_id: StrictStr = Field(min_length=1)
    snapshot_version: StrictInt = Field(ge=1)
    state: WebSurfaceState
    authority_notice: WebAuthorityNotice
    columns: list[WebHypothesisBoardColumn] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_required_hypothesis_lanes(self) -> WebHypothesisBoardViewModel:
        lanes = {column.lane for column in self.columns}
        required = {WebHypothesisLane.CURRENT_BEST, WebHypothesisLane.CHALLENGER}
        if not required <= lanes:
            raise ValueError("hypothesis board must include current best and challenger lanes")
        return self


class WebGraphExplorerFilter(StrictModel):
    filter_id: StrictStr = Field(min_length=1)
    label: StrictStr = Field(min_length=1)
    enabled: StrictBool


class WebGraphExplorerNode(StrictModel):
    node_id: StrictStr = Field(min_length=1)
    node_type: GraphExportNodeType
    label: StrictStr = Field(min_length=1)
    summary: StrictStr = Field(min_length=1)
    severity: WebSeverity
    provenance_ids: list[StrictStr]


class WebGraphExplorerEdge(StrictModel):
    edge_id: StrictStr = Field(min_length=1)
    edge_type: GraphExportEdgeType
    source_node_id: StrictStr = Field(min_length=1)
    target_node_id: StrictStr = Field(min_length=1)
    label: StrictStr = Field(min_length=1)


class WebGraphExplorerViewModel(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    topic_id: StrictStr = Field(min_length=1)
    snapshot_version: StrictInt = Field(ge=1)
    state: WebSurfaceState
    authority_notice: WebAuthorityNotice
    projection_source: StrictStr = Field(min_length=1)
    json_schema_id: StrictStr = Field(min_length=1)
    nodes: list[WebGraphExplorerNode] = Field(min_length=1)
    edges: list[WebGraphExplorerEdge]
    focus_node_ids: list[StrictStr]
    filters: list[WebGraphExplorerFilter] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph_projection_references(self) -> WebGraphExplorerViewModel:
        if "projection" not in self.projection_source.lower():
            raise ValueError("graph explorer must declare a projection source")
        node_ids = {node.node_id for node in self.nodes}
        missing_edges = [
            edge.edge_id
            for edge in self.edges
            if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids
        ]
        if missing_edges:
            raise ValueError(f"graph explorer edges reference missing nodes: {missing_edges}")
        missing_focus = [node_id for node_id in self.focus_node_ids if node_id not in node_ids]
        if missing_focus:
            raise ValueError(f"graph explorer focus references missing nodes: {missing_focus}")
        return self


class WebTimelineEvent(StrictModel):
    event_id: StrictStr = Field(min_length=1)
    timestamp: datetime
    lifecycle_state: RunLifecycleState
    title: StrictStr = Field(min_length=1)
    detail: StrictStr = Field(min_length=1)
    severity: WebSeverity


class WebRunTimelineViewModel(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    topic_id: StrictStr = Field(min_length=1)
    run_id: StrictStr = Field(min_length=1)
    state: WebSurfaceState
    authority_notice: WebAuthorityNotice
    current_lifecycle_state: RunLifecycleState
    backend_state_update_applied: StrictBool
    events: list[WebTimelineEvent] = Field(min_length=1)
    next_actions: list[WebNextResearchAction] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_current_lifecycle_state_is_visible(self) -> WebRunTimelineViewModel:
        if self.current_lifecycle_state not in {event.lifecycle_state for event in self.events}:
            raise ValueError("run timeline must include the current lifecycle state")
        return self


class WebStateSnapshot(StrictModel):
    snapshot_id: StrictStr = Field(min_length=1)
    view_id: WebDashboardViewId
    state: WebSurfaceState
    title: StrictStr = Field(min_length=1)
    message: StrictStr = Field(min_length=1)
    severity: WebSeverity
    next_visible_action: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def validate_failure_states_are_not_normal(self) -> WebStateSnapshot:
        if self.state in {WebSurfaceState.DEAD_LETTER, WebSurfaceState.STALE_CLAIM}:
            if self.severity not in {WebSeverity.WARNING, WebSeverity.CRITICAL}:
                raise ValueError("dead-letter and stale-claim states must not look normal")
        if self.state is WebSurfaceState.ERROR and self.severity is WebSeverity.NORMAL:
            raise ValueError("error states must not use normal severity")
        return self


class WebApiEndpointSchema(StrictModel):
    route: StrictStr = Field(min_length=1)
    method: WebApiMethod
    response_schema_id: StrictStr = Field(min_length=1)
    fixture: StrictStr = Field(min_length=1)


class WebApiSchemaCatalog(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    endpoints: list[WebApiEndpointSchema] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_routes(self) -> WebApiSchemaCatalog:
        routes = [(endpoint.method, endpoint.route) for endpoint in self.endpoints]
        if len(routes) != len(set(routes)):
            raise ValueError("web API endpoint routes must be unique per method")
        return self


class WebDashboardViewModelBundle(StrictModel):
    schema_id: StrictStr = Field(min_length=1)
    information_architecture: WebDashboardInformationArchitecture
    overview: WebOverviewViewModel
    hypothesis_board: WebHypothesisBoardViewModel
    graph_explorer: WebGraphExplorerViewModel
    run_timeline: WebRunTimelineViewModel
    state_snapshots: list[WebStateSnapshot] = Field(min_length=1)
    api_schema_catalog: WebApiSchemaCatalog

    @model_validator(mode="after")
    def validate_bundle_consistency(self) -> WebDashboardViewModelBundle:
        topic_ids = {
            self.overview.topic_id,
            self.hypothesis_board.topic_id,
            self.graph_explorer.topic_id,
            self.run_timeline.topic_id,
        }
        if len(topic_ids) != 1:
            raise ValueError("web dashboard view models must describe one topic")
        versions = {
            self.overview.snapshot_version,
            self.hypothesis_board.snapshot_version,
            self.graph_explorer.snapshot_version,
        }
        if len(versions) != 1:
            raise ValueError("web dashboard view models must share one snapshot version")
        required_states = {
            WebSurfaceState.LOADING,
            WebSurfaceState.EMPTY,
            WebSurfaceState.ERROR,
            WebSurfaceState.DEAD_LETTER,
            WebSurfaceState.STALE_CLAIM,
        }
        present_states = {snapshot.state for snapshot in self.state_snapshots}
        missing = required_states - present_states
        if missing:
            names = ", ".join(sorted(state.value for state in missing))
            raise ValueError(f"web dashboard missing required state snapshots: {names}")
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
            "Backend state update:",
            f"- Required: {'yes' if run.backend_state_update_required else 'no'}",
            f"- Applied: {'yes' if run.backend_state_update_applied else 'no'}",
        ]
    )
    if run.backend_state_update is not None:
        lines.extend(
            [
                f"- Graph digest: {run.backend_state_update.graph_digest}",
                (
                    "- Review flags: "
                    + (
                        ", ".join(run.backend_state_update.review_flags)
                        if run.backend_state_update.review_flags
                        else "none"
                    )
                ),
            ]
        )
    else:
        lines.append("- Graph digest: none")
    lines.extend(
        [
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
    if len(tokens) < 2 or tokens[0] != "crb":
        raise ValueError(f"not a crb command example: {example}")
    if tokens[1] in {"init", "doctor"}:
        return tuple(tokens[:2])
    if len(tokens) < 3:
        raise ValueError(f"not a grouped crb command example: {example}")
    return tuple(tokens[:3])
