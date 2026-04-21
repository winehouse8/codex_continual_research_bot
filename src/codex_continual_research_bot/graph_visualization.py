"""Phase 14 graph visualization and memory explorer projections."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from codex_continual_research_bot.contracts import TopicSnapshot
from codex_continual_research_bot.ux_contracts import (
    GraphExportArtifact,
    GraphExportEdge,
    GraphExportNode,
    MemoryExplorerSummary,
    canonical_json,
)


AUTHORITY_NOTICE = (
    "Graph export is not a source of truth; backend graph and provenance "
    "ledgers remain authoritative."
)


def build_graph_export_artifact(
    *,
    topic_id: str,
    snapshot: TopicSnapshot,
    graph_write: dict[str, Any] | None = None,
    graph_writes: list[dict[str, Any]] | None = None,
    generated_at: datetime | None = None,
) -> GraphExportArtifact:
    """Build a deterministic visualization artifact from backend-owned state."""

    generated = generated_at or datetime.now(timezone.utc)
    if graph_write is not None and graph_writes is not None:
        raise ValueError("provide either graph_write or graph_writes, not both")
    if graph_writes is not None:
        if not graph_writes:
            projection = _snapshot_projection(topic_id=topic_id, snapshot=snapshot)
            projection_source = "topic_snapshot"
        else:
            projection = _canonical_graph_history_projection(
                topic_id=topic_id,
                snapshot=snapshot,
                graph_writes=graph_writes,
            )
            projection_source = "canonical_graph_history"
    elif graph_write is None:
        projection = _snapshot_projection(topic_id=topic_id, snapshot=snapshot)
        projection_source = "topic_snapshot"
    else:
        projection = _canonical_graph_projection(
            topic_id=topic_id,
            snapshot=snapshot,
            canonical_graph=_graph_json_payload(graph_write["graph_json"]),
        )
        projection_source = "canonical_graph_write"

    nodes = sorted(projection.nodes, key=lambda node: node.node_id)
    edges = sorted(projection.edges, key=lambda edge: edge.edge_id)
    digest_payload = {
        "topic_id": topic_id,
        "snapshot_version": snapshot.snapshot_version,
        "projection_source": projection_source,
        "nodes": [node.model_dump(mode="json") for node in nodes],
        "edges": [edge.model_dump(mode="json") for edge in edges],
        "memory_explorer": projection.memory_explorer.model_dump(mode="json"),
    }

    return GraphExportArtifact(
        schema_id="crb.graph_export.v1",
        export_id=f"graph_export_{topic_id}_v{snapshot.snapshot_version}",
        topic_id=topic_id,
        snapshot_version=snapshot.snapshot_version,
        projection_source=projection_source,
        graph_digest=_digest(digest_payload),
        generated_at=generated,
        authority_notice=AUTHORITY_NOTICE,
        memory_explorer=projection.memory_explorer,
        nodes=nodes,
        edges=edges,
    )


def render_graph_artifact(artifact: GraphExportArtifact, *, output_format: str) -> str:
    if output_format == "json":
        return canonical_json(artifact.model_dump(mode="json"))
    if output_format == "dot":
        return render_dot(artifact)
    if output_format == "mermaid":
        return render_mermaid(artifact)
    if output_format == "html":
        return render_html(artifact)
    raise ValueError(f"unsupported graph artifact format: {output_format}")


def render_dot(artifact: GraphExportArtifact) -> str:
    lines = [
        "digraph crb_graph {",
        "  rankdir=LR;",
        f"  label={_dot_quote(artifact.topic_id)};",
        "  labelloc=t;",
    ]
    for node in artifact.nodes:
        label = f"{node.node_type.value}: {node.label}"
        lines.append(f"  {_dot_id(node.node_id)} [label={_dot_quote(label)}];")
    for edge in artifact.edges:
        lines.append(
            "  "
            f"{_dot_id(edge.source_node_id)} -> {_dot_id(edge.target_node_id)} "
            f"[label={_dot_quote(edge.edge_type.value)}];"
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_mermaid(artifact: GraphExportArtifact) -> str:
    node_aliases = {
        node.node_id: f"n{index}"
        for index, node in enumerate(sorted(artifact.nodes, key=lambda item: item.node_id), start=1)
    }
    lines = ["graph LR"]
    for node in artifact.nodes:
        label = _mermaid_label(f"{node.node_type.value}: {node.label}")
        lines.append(f"  {node_aliases[node.node_id]}[\"{label}\"]")
    for edge in artifact.edges:
        source = node_aliases[edge.source_node_id]
        target = node_aliases[edge.target_node_id]
        lines.append(f"  {source} -->|{_mermaid_label(edge.edge_type.value)}| {target}")
    return "\n".join(lines) + "\n"


def render_html(artifact: GraphExportArtifact) -> str:
    nodes = "\n".join(
        "<li>"
        f"<strong>{html.escape(node.node_type.value)}</strong> "
        f"{html.escape(node.label)}"
        f"<p>{html.escape(node.summary)}</p>"
        "</li>"
        for node in artifact.nodes
    )
    edges = "\n".join(
        "<li>"
        f"{html.escape(edge.source_node_id)} "
        f"<code>{html.escape(edge.edge_type.value)}</code> "
        f"{html.escape(edge.target_node_id)}"
        f"<p>{html.escape(edge.summary)}</p>"
        "</li>"
        for edge in artifact.edges
    )
    explorer = artifact.memory_explorer
    return "\n".join(
        [
            "<!doctype html>",
            "<html lang=\"en\">",
            "<meta charset=\"utf-8\">",
            f"<title>CRB graph {html.escape(artifact.topic_id)}</title>",
            "<style>",
            "body{font-family:system-ui,-apple-system,sans-serif;margin:2rem;line-height:1.45}",
            "section{max-width:960px;margin-bottom:2rem}",
            "li{margin:.5rem 0}",
            "p{margin:.25rem 0;color:#3f3f46}",
            "code{background:#f4f4f5;padding:.1rem .25rem;border-radius:4px}",
            "</style>",
            f"<h1>{html.escape(artifact.topic_id)}</h1>",
            f"<p>{html.escape(artifact.authority_notice)}</p>",
            "<section>",
            "<h2>Memory Explorer</h2>",
            f"<p>{html.escape(explorer.summary)}</p>",
            f"<p>Unresolved conflicts: {explorer.unresolved_conflict_count}</p>",
            "</section>",
            "<section>",
            "<h2>Nodes</h2>",
            "<ul>",
            nodes,
            "</ul>",
            "</section>",
            "<section>",
            "<h2>Edges</h2>",
            "<ul>",
            edges,
            "</ul>",
            "</section>",
            "</html>",
            "",
        ]
    )


class _Projection:
    def __init__(
        self,
        *,
        nodes: list[GraphExportNode],
        edges: list[GraphExportEdge],
        memory_explorer: MemoryExplorerSummary,
    ) -> None:
        self.nodes = nodes
        self.edges = edges
        self.memory_explorer = memory_explorer


def _graph_json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("canonical graph write graph_json must be an object payload")


def _snapshot_projection(*, topic_id: str, snapshot: TopicSnapshot) -> _Projection:
    nodes: list[GraphExportNode] = [
        GraphExportNode(
            node_id=topic_id,
            node_type="topic",
            label=topic_id,
            summary=snapshot.topic_summary,
            temporal_scope="current snapshot",
            provenance_ids=[snapshot.recent_provenance_digest],
        ),
        GraphExportNode(
            node_id=snapshot.recent_provenance_digest,
            node_type="provenance",
            label="Topic snapshot provenance",
            summary="Latest backend-owned topic snapshot provenance digest.",
            temporal_scope="current snapshot",
            provenance_ids=[],
        ),
    ]
    edges: list[GraphExportEdge] = []
    current_best_ids: list[str] = []
    challenger_ids: list[str] = []
    conflict_ids: list[str] = []

    for hypothesis in snapshot.current_best_hypotheses:
        current_best_ids.append(hypothesis.hypothesis_id)
        nodes.append(
            _hypothesis_node(
                node_id=hypothesis.hypothesis_id,
                label=hypothesis.title,
                summary=f"current best: {hypothesis.summary}",
                provenance_ids=[snapshot.recent_provenance_digest],
            )
        )
        edges.append(
            _edge(
                edge_id=f"edge_{topic_id}_{hypothesis.hypothesis_id}",
                edge_type="visualizes",
                source=topic_id,
                target=hypothesis.hypothesis_id,
                summary="Topic snapshot visualizes this current-best hypothesis.",
                provenance_ids=[snapshot.recent_provenance_digest],
            )
        )

    first_current_best = current_best_ids[0] if current_best_ids else None
    for hypothesis in snapshot.challenger_targets:
        challenger_ids.append(hypothesis.hypothesis_id)
        nodes.append(
            _hypothesis_node(
                node_id=hypothesis.hypothesis_id,
                label=hypothesis.title,
                summary=f"challenger: {hypothesis.summary}",
                provenance_ids=[snapshot.recent_provenance_digest],
            )
        )
        if first_current_best is not None:
            edges.append(
                _edge(
                    edge_id=f"edge_{hypothesis.hypothesis_id}_challenges_{first_current_best}",
                    edge_type="challenges",
                    source=hypothesis.hypothesis_id,
                    target=first_current_best,
                    summary="Challenger target is retained for adversarial verification.",
                    provenance_ids=[snapshot.recent_provenance_digest],
                )
            )

    for conflict in snapshot.active_conflicts:
        conflict_ids.append(conflict.conflict_id)
        nodes.append(
            GraphExportNode(
                node_id=conflict.conflict_id,
                node_type="conflict",
                label=conflict.conflict_id,
                summary=conflict.summary,
                temporal_scope="current snapshot",
                provenance_ids=[snapshot.recent_provenance_digest],
            )
        )
        for target in current_best_ids + challenger_ids:
            edges.append(
                _edge(
                    edge_id=f"edge_{conflict.conflict_id}_conflicts_with_{target}",
                    edge_type="conflicts_with",
                    source=conflict.conflict_id,
                    target=target,
                    summary="Active conflict explains why this belief remains contested.",
                    provenance_ids=[snapshot.recent_provenance_digest],
                )
            )

    return _Projection(
        nodes=nodes,
        edges=edges,
        memory_explorer=_memory_explorer(
            current_best_ids=current_best_ids,
            challenger_ids=challenger_ids,
            conflict_ids=conflict_ids,
            evidence_ids=[],
            provenance_ids=[snapshot.recent_provenance_digest],
            unresolved_conflict_count=len(snapshot.active_conflicts),
        ),
    )


def _canonical_graph_projection(
    *,
    topic_id: str,
    snapshot: TopicSnapshot,
    canonical_graph: dict[str, Any],
) -> _Projection:
    canonical_nodes = list(canonical_graph.get("nodes", []))
    canonical_edges = list(canonical_graph.get("edges", []))
    recorded_in: dict[str, list[str]] = {}
    for edge in canonical_edges:
        if edge.get("type") == "RECORDED_IN":
            recorded_in.setdefault(str(edge["source"]), []).append(str(edge["target"]))

    nodes: list[GraphExportNode] = [
        GraphExportNode(
            node_id=topic_id,
            node_type="topic",
            label=topic_id,
            summary=snapshot.topic_summary,
            temporal_scope="current snapshot",
            provenance_ids=[snapshot.recent_provenance_digest],
        )
    ]
    current_best_ids: list[str] = []
    challenger_ids: list[str] = []
    evidence_ids: list[str] = []
    provenance_ids: list[str] = []

    for node in sorted(canonical_nodes, key=lambda item: str(item["id"])):
        export_node = _canonical_node_to_export(node, recorded_in.get(str(node["id"]), []))
        nodes.append(export_node)
        if export_node.node_type.value == "hypothesis":
            properties = node.get("properties", {})
            if properties.get("is_current_best") is True:
                current_best_ids.append(export_node.node_id)
            elif properties.get("status") is not None:
                challenger_ids.append(export_node.node_id)
        elif export_node.node_type.value == "evidence":
            evidence_ids.append(export_node.node_id)
        elif export_node.node_type.value == "provenance":
            provenance_ids.append(export_node.node_id)

    edges: list[GraphExportEdge] = []
    for hypothesis_id in current_best_ids:
        edges.append(
            _edge(
                edge_id=f"edge_{topic_id}_{hypothesis_id}",
                edge_type="visualizes",
                source=topic_id,
                target=hypothesis_id,
                summary="Topic snapshot visualizes this current-best hypothesis.",
                provenance_ids=[snapshot.recent_provenance_digest],
            )
        )
    for edge in sorted(canonical_edges, key=lambda item: str(item["id"])):
        export_edge = _canonical_edge_to_export(edge, recorded_in)
        if export_edge is not None:
            edges.append(export_edge)

    conflict_ids = _append_snapshot_conflicts(
        nodes=nodes,
        edges=edges,
        snapshot=snapshot,
        current_best_ids=current_best_ids,
        challenger_ids=challenger_ids,
    )
    return _Projection(
        nodes=nodes,
        edges=edges,
        memory_explorer=_memory_explorer(
            current_best_ids=current_best_ids,
            challenger_ids=challenger_ids,
            conflict_ids=conflict_ids,
            evidence_ids=evidence_ids,
            provenance_ids=provenance_ids,
            unresolved_conflict_count=len(snapshot.active_conflicts),
        ),
    )


def _canonical_graph_history_projection(
    *,
    topic_id: str,
    snapshot: TopicSnapshot,
    graph_writes: list[dict[str, Any]],
) -> _Projection:
    return _canonical_graph_projection(
        topic_id=topic_id,
        snapshot=snapshot,
        canonical_graph=_merge_canonical_graph_writes(graph_writes),
    )


def _merge_canonical_graph_writes(graph_writes: list[dict[str, Any]]) -> dict[str, Any]:
    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges_by_id: dict[str, dict[str, Any]] = {}
    for graph_write in sorted(graph_writes, key=_graph_write_sort_key):
        canonical_graph = _graph_json_payload(graph_write["graph_json"])
        for node in sorted(canonical_graph.get("nodes", []), key=lambda item: str(item["id"])):
            nodes_by_id[str(node["id"])] = node
        for edge in sorted(canonical_graph.get("edges", []), key=lambda item: str(item["id"])):
            edges_by_id[str(edge["id"])] = edge
    return {
        "nodes": [nodes_by_id[node_id] for node_id in sorted(nodes_by_id)],
        "edges": [edges_by_id[edge_id] for edge_id in sorted(edges_by_id)],
    }


def _graph_write_sort_key(graph_write: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(graph_write.get("created_at", "")),
        str(graph_write.get("run_id", "")),
        str(graph_write.get("proposal_id", "")),
        str(graph_write.get("graph_digest", "")),
    )


def _canonical_node_to_export(
    node: dict[str, Any],
    provenance_ids: list[str],
) -> GraphExportNode:
    label = str(node["label"])
    properties = node.get("properties", {})
    if label == "Evidence":
        return GraphExportNode(
            node_id=str(node["id"]),
            node_type="evidence",
            label=str(properties.get("title") or node["key"]),
            summary=str(
                properties.get("extraction_note")
                or properties.get("source_url")
                or node["id"]
            ),
            temporal_scope=str(properties.get("accessed_at") or "current snapshot"),
            provenance_ids=sorted(set(provenance_ids)),
        )
    if label == "Claim":
        return GraphExportNode(
            node_id=str(node["id"]),
            node_type="claim",
            label=str(properties.get("text") or node["key"]),
            summary=str(properties.get("text") or node["id"]),
            temporal_scope=str(properties.get("temporal_scope") or "current snapshot"),
            provenance_ids=sorted(set(provenance_ids)),
        )
    if label == "Hypothesis":
        version = properties.get("version")
        return GraphExportNode(
            node_id=str(node["id"]),
            node_type="hypothesis",
            label=str(properties.get("title") or properties.get("hypothesis_id") or node["key"]),
            summary=str(properties.get("statement") or node["id"]),
            temporal_scope=f"v{version}" if version is not None else "current snapshot",
            provenance_ids=sorted(set(provenance_ids)),
        )
    if label == "ProvenanceRecord":
        proposal_id = str(properties.get("proposal_id") or node["key"])
        return GraphExportNode(
            node_id=str(node["id"]),
            node_type="provenance",
            label=f"Proposal {proposal_id}",
            summary=str(properties.get("summary_draft") or node["id"]),
            temporal_scope=str(properties.get("run_id") or "current snapshot"),
            provenance_ids=[],
        )
    return GraphExportNode(
        node_id=str(node["id"]),
        node_type="claim",
        label=label,
        summary=str(properties or node["id"]),
        temporal_scope="current snapshot",
        provenance_ids=sorted(set(provenance_ids)),
    )


def _canonical_edge_to_export(
    edge: dict[str, Any],
    recorded_in: dict[str, list[str]],
) -> GraphExportEdge | None:
    edge_type = str(edge["type"])
    export_type = {
        "SUPPORTS": "supports",
        "CHALLENGES": "challenges",
        "SUPERSEDES": "supersedes",
        "WEAKENS": "weakens",
        "RETIRES": "retires",
        "DERIVED_FROM": "derived_from",
        "RECORDED_IN": "derived_from",
    }.get(edge_type)
    if export_type is None:
        return None
    source = str(edge["source"])
    target = str(edge["target"])
    provenance_ids = sorted(
        set(recorded_in.get(source, []) + recorded_in.get(target, []))
    )
    return _edge(
        edge_id=str(edge["id"]),
        edge_type=export_type,
        source=source,
        target=target,
        summary=_edge_summary(edge_type=edge_type, properties=edge.get("properties", {})),
        provenance_ids=provenance_ids,
    )


def _append_snapshot_conflicts(
    *,
    nodes: list[GraphExportNode],
    edges: list[GraphExportEdge],
    snapshot: TopicSnapshot,
    current_best_ids: list[str],
    challenger_ids: list[str],
) -> list[str]:
    conflict_ids: list[str] = []
    targets = current_best_ids + challenger_ids
    for conflict in snapshot.active_conflicts:
        conflict_ids.append(conflict.conflict_id)
        nodes.append(
            GraphExportNode(
                node_id=conflict.conflict_id,
                node_type="conflict",
                label=conflict.conflict_id,
                summary=conflict.summary,
                temporal_scope="current snapshot",
                provenance_ids=[snapshot.recent_provenance_digest],
            )
        )
        for target in targets:
            edges.append(
                _edge(
                    edge_id=f"edge_{conflict.conflict_id}_conflicts_with_{target}",
                    edge_type="conflicts_with",
                    source=conflict.conflict_id,
                    target=target,
                    summary="Active conflict explains why this belief remains contested.",
                    provenance_ids=[snapshot.recent_provenance_digest],
                )
            )
    return conflict_ids


def _hypothesis_node(
    *,
    node_id: str,
    label: str,
    summary: str,
    provenance_ids: list[str],
) -> GraphExportNode:
    return GraphExportNode(
        node_id=node_id,
        node_type="hypothesis",
        label=label,
        summary=summary,
        temporal_scope="current snapshot",
        provenance_ids=provenance_ids,
    )


def _edge(
    *,
    edge_id: str,
    edge_type: str,
    source: str,
    target: str,
    summary: str,
    provenance_ids: list[str],
) -> GraphExportEdge:
    return GraphExportEdge(
        edge_id=edge_id,
        edge_type=edge_type,
        source_node_id=source,
        target_node_id=target,
        summary=summary,
        provenance_ids=sorted(set(provenance_ids)),
    )


def _memory_explorer(
    *,
    current_best_ids: list[str],
    challenger_ids: list[str],
    conflict_ids: list[str],
    evidence_ids: list[str],
    provenance_ids: list[str],
    unresolved_conflict_count: int,
) -> MemoryExplorerSummary:
    return MemoryExplorerSummary(
        summary=(
            "Conflict-focused memory explorer projection with current best, "
            "challenger, evidence, conflict, and provenance node groups."
        ),
        current_best_node_ids=sorted(set(current_best_ids)),
        challenger_node_ids=sorted(set(challenger_ids)),
        conflict_node_ids=sorted(set(conflict_ids)),
        evidence_node_ids=sorted(set(evidence_ids)),
        provenance_node_ids=sorted(set(provenance_ids)),
        unresolved_conflict_count=unresolved_conflict_count,
    )


def _edge_summary(*, edge_type: str, properties: dict[str, Any]) -> str:
    rationale = properties.get("rationale")
    if isinstance(rationale, str) and rationale:
        return rationale
    return {
        "SUPPORTS": "Canonical support relation projected from backend graph.",
        "CHALLENGES": "Canonical challenge relation projected from backend graph.",
        "SUPERSEDES": "Canonical supersession relation projected from backend graph.",
        "WEAKENS": "Canonical weakening relation projected from backend graph.",
        "RETIRES": "Canonical retirement relation projected from backend graph.",
        "DERIVED_FROM": "Claim or graph node is derived from provenance evidence.",
        "RECORDED_IN": "Canonical graph node was recorded in this provenance record.",
    }.get(edge_type, f"Canonical {edge_type} relation projected from backend graph.")


def _digest(data: object) -> str:
    return "sha256:" + sha256(
        json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest()


def _dot_id(value: str) -> str:
    return "n_" + re.sub(r"[^a-zA-Z0-9_]", "_", value)


def _dot_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _mermaid_label(value: str) -> str:
    return value.replace("\"", "'").replace("|", "/")
