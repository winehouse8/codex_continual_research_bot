"""Read-only graph explorer adapter for the local web dashboard."""

from __future__ import annotations

from typing import Any

from codex_continual_research_bot.ux_contracts import (
    GraphExportArtifact,
    GraphExportEdge,
    GraphExportNode,
)


GRAPH_EXPLORER_SCHEMA_ID = "crb.web.graph_explorer.runtime.v1"
FILTER_LABELS = {
    "current_best": "Current best",
    "challenger": "Challengers",
    "evidence": "Evidence",
    "conflict": "Conflicts",
    "provenance": "Provenance",
}
FILTER_ORDER = tuple(FILTER_LABELS)
FILTERED_GROUPS = set(FILTER_ORDER)


def build_graph_explorer_view(
    artifact: GraphExportArtifact,
    *,
    scope: str,
    enabled_filter_ids: set[str] | None = None,
    selected_node_id: str | None = None,
) -> dict[str, object]:
    """Adapt a backend graph artifact into the browser graph explorer view."""

    enabled = set(FILTER_ORDER if enabled_filter_ids is None else enabled_filter_ids)
    nodes_by_id = {node.node_id: node for node in artifact.nodes}
    node_groups = {
        node.node_id: _node_group(node=node, artifact=artifact)
        for node in artifact.nodes
    }
    visible_node_ids = {
        node.node_id
        for node in artifact.nodes
        if _node_visible(node_groups[node.node_id], enabled)
    }
    adapted_nodes = [
        _adapt_node(
            node=node,
            group=node_groups[node.node_id],
            visible=node.node_id in visible_node_ids,
        )
        for node in artifact.nodes
    ]
    adapted_edges = [
        _adapt_edge(
            edge=edge,
            visible=(
                edge.source_node_id in visible_node_ids
                and edge.target_node_id in visible_node_ids
            ),
        )
        for edge in artifact.edges
    ]
    selected = _selected_node_id(
        requested=selected_node_id,
        visible_node_ids=visible_node_ids,
        artifact=artifact,
    )

    return {
        "schema_id": GRAPH_EXPLORER_SCHEMA_ID,
        "topic_id": artifact.topic_id,
        "snapshot_version": artifact.snapshot_version,
        "scope": scope,
        "projection_source": artifact.projection_source,
        "graph_digest": artifact.graph_digest,
        "authority_notice": artifact.authority_notice,
        "renderer": {
            "kind": "local_svg_graph_renderer",
            "asset_policy": (
                "Graph explorer uses packaged local JavaScript and CSS only; "
                "it does not load CDN assets."
            ),
        },
        "filters": [
            {
                "filter_id": filter_id,
                "label": FILTER_LABELS[filter_id],
                "enabled": filter_id in enabled,
                "visible_count": sum(
                    1
                    for node in adapted_nodes
                    if node["group"] == filter_id and node["visible"]
                ),
                "total_count": sum(1 for node in adapted_nodes if node["group"] == filter_id),
            }
            for filter_id in FILTER_ORDER
        ],
        "provenance_options": _provenance_options(artifact),
        "nodes": adapted_nodes,
        "edges": adapted_edges,
        "focus_node_ids": [
            node_id
            for node_id in (
                artifact.memory_explorer.current_best_node_ids
                + artifact.memory_explorer.challenger_node_ids
                + artifact.memory_explorer.conflict_node_ids
            )
            if node_id in nodes_by_id
        ],
        "selected_node": _node_detail(
            node_id=selected,
            artifact=artifact,
            group=node_groups.get(selected, "support"),
        ),
        "states": {
            "empty": len(artifact.nodes) == 0,
            "filtered_empty": not visible_node_ids and len(artifact.nodes) > 0,
            "error": False,
        },
        "summary": artifact.memory_explorer.summary,
        "unresolved_conflict_count": artifact.memory_explorer.unresolved_conflict_count,
    }


def _node_group(*, node: GraphExportNode, artifact: GraphExportArtifact) -> str:
    explorer = artifact.memory_explorer
    if node.node_id in explorer.current_best_node_ids:
        return "current_best"
    if node.node_id in explorer.challenger_node_ids:
        return "challenger"
    if node.node_id in explorer.conflict_node_ids:
        return "conflict"
    if node.node_id in explorer.evidence_node_ids:
        return "evidence"
    if node.node_id in explorer.provenance_node_ids or node.node_type.value == "provenance":
        return "provenance"
    return "support"


def _node_visible(group: str, enabled_filter_ids: set[str]) -> bool:
    return group not in FILTERED_GROUPS or group in enabled_filter_ids


def _adapt_node(
    *,
    node: GraphExportNode,
    group: str,
    visible: bool,
) -> dict[str, object]:
    return {
        "node_id": node.node_id,
        "node_type": node.node_type.value,
        "group": group,
        "label": node.label,
        "summary": node.summary,
        "temporal_scope": node.temporal_scope,
        "provenance_ids": node.provenance_ids,
        "severity": _node_severity(group),
        "style_classes": _style_classes(
            "node",
            node.node_type.value,
            group,
            _node_severity(group),
        ),
        "visible": visible,
    }


def _adapt_edge(*, edge: GraphExportEdge, visible: bool) -> dict[str, object]:
    severity = _edge_severity(edge.edge_type.value)
    return {
        "edge_id": edge.edge_id,
        "edge_type": edge.edge_type.value,
        "source_node_id": edge.source_node_id,
        "target_node_id": edge.target_node_id,
        "label": edge.edge_type.value,
        "summary": edge.summary,
        "provenance_ids": edge.provenance_ids,
        "severity": severity,
        "style_classes": _style_classes(
            "edge",
            edge.edge_type.value,
            edge.edge_type.value,
            severity,
        ),
        "visible": visible,
    }


def _style_classes(kind: str, object_type: str, group: str, severity: str) -> list[str]:
    return [
        f"graph-{kind}",
        f"graph-{kind}--{_css_token(object_type)}",
        f"graph-group--{_css_token(group)}",
        f"graph-severity--{_css_token(severity)}",
    ]


def _css_token(value: str) -> str:
    return value.replace("_", "-").replace(":", "-").lower()


def _node_severity(group: str) -> str:
    if group == "conflict":
        return "critical"
    if group == "challenger":
        return "warning"
    if group in {"evidence", "provenance"}:
        return "info"
    return "normal"


def _edge_severity(edge_type: str) -> str:
    if edge_type == "conflicts_with":
        return "critical"
    if edge_type in {"challenges", "weakens", "retires"}:
        return "warning"
    if edge_type in {"derived_from", "visualizes"}:
        return "info"
    return "normal"


def _selected_node_id(
    *,
    requested: str | None,
    visible_node_ids: set[str],
    artifact: GraphExportArtifact,
) -> str:
    if requested in visible_node_ids:
        return str(requested)
    preferred = (
        artifact.memory_explorer.current_best_node_ids
        + artifact.memory_explorer.conflict_node_ids
        + artifact.memory_explorer.challenger_node_ids
    )
    for node_id in preferred:
        if node_id in visible_node_ids:
            return node_id
    return sorted(visible_node_ids)[0] if visible_node_ids else ""


def _node_detail(
    *,
    node_id: str,
    artifact: GraphExportArtifact,
    group: str,
) -> dict[str, object] | None:
    if not node_id:
        return None
    nodes = {node.node_id: node for node in artifact.nodes}
    node = nodes.get(node_id)
    if node is None:
        return None
    incoming = [
        _relation_detail(edge=edge, nodes=nodes)
        for edge in artifact.edges
        if edge.target_node_id == node_id
    ]
    outgoing = [
        _relation_detail(edge=edge, nodes=nodes)
        for edge in artifact.edges
        if edge.source_node_id == node_id
    ]
    return {
        "node_id": node.node_id,
        "node_type": node.node_type.value,
        "group": group,
        "label": node.label,
        "summary": node.summary,
        "temporal_scope": node.temporal_scope,
        "provenance_ids": node.provenance_ids,
        "incoming_relations": incoming,
        "outgoing_relations": outgoing,
    }


def _relation_detail(
    *,
    edge: GraphExportEdge,
    nodes: dict[str, GraphExportNode],
) -> dict[str, object]:
    source = nodes[edge.source_node_id]
    target = nodes[edge.target_node_id]
    return {
        "edge_id": edge.edge_id,
        "relation": edge.edge_type.value,
        "source_node_id": edge.source_node_id,
        "source_label": source.label,
        "target_node_id": edge.target_node_id,
        "target_label": target.label,
        "summary": edge.summary,
        "provenance_ids": edge.provenance_ids,
    }


def _provenance_options(artifact: GraphExportArtifact) -> list[dict[str, Any]]:
    nodes = {node.node_id: node for node in artifact.nodes}
    options = []
    for node_id in artifact.memory_explorer.provenance_node_ids:
        node = nodes.get(node_id)
        if node is None:
            continue
        options.append(
            {
                "provenance_id": node.node_id,
                "label": node.label,
                "run_id": node.temporal_scope,
                "summary": node.summary,
            }
        )
    return options
