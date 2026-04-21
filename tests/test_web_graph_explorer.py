from __future__ import annotations

import json
from pathlib import Path

from codex_continual_research_bot.ux_contracts import GraphExportArtifact
from codex_continual_research_bot.web_graph_explorer import build_graph_explorer_view


ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"


def graph_artifact() -> GraphExportArtifact:
    return GraphExportArtifact.model_validate(
        json.loads((FIXTURES_DIR / "graph_export.json").read_text())
    )


def test_graph_explorer_data_adapter_keeps_backend_projection_metadata() -> None:
    view = build_graph_explorer_view(graph_artifact(), scope="latest")

    assert view["schema_id"] == "crb.web.graph_explorer.runtime.v1"
    assert view["scope"] == "latest"
    assert view["projection_source"] == "canonical_graph_write"
    assert "not a source of truth" in str(view["authority_notice"]).lower()
    assert view["renderer"]["kind"] == "local_svg_graph_renderer"
    assert "CDN" in view["renderer"]["asset_policy"]
    assert {item["filter_id"] for item in view["filters"]} == {
        "current_best",
        "challenger",
        "evidence",
        "conflict",
        "provenance",
    }
    assert {"hyp_001", "hyp_002", "conf_001"} <= set(view["focus_node_ids"])


def test_graph_explorer_assigns_node_and_edge_style_classes() -> None:
    view = build_graph_explorer_view(graph_artifact(), scope="latest")
    nodes = {node["node_id"]: node for node in view["nodes"]}
    edges = {edge["edge_id"]: edge for edge in view["edges"]}

    assert nodes["hyp_001"]["group"] == "current_best"
    assert nodes["hyp_001"]["style_classes"] == [
        "graph-node",
        "graph-node--hypothesis",
        "graph-group--current-best",
        "graph-severity--normal",
    ]
    assert nodes["conf_001"]["severity"] == "critical"
    assert "graph-edge--challenges" in edges["edge_002"]["style_classes"]
    assert "graph-edge--conflicts-with" in edges["edge_006"]["style_classes"]


def test_graph_explorer_filter_state_hides_filtered_nodes_and_attached_edges() -> None:
    view = build_graph_explorer_view(
        graph_artifact(),
        scope="latest",
        enabled_filter_ids={"current_best", "challenger", "evidence"},
    )
    nodes = {node["node_id"]: node for node in view["nodes"]}
    edges = {edge["edge_id"]: edge for edge in view["edges"]}

    assert nodes["conf_001"]["visible"] is False
    assert nodes["prov_001"]["visible"] is False
    assert edges["edge_006"]["visible"] is False
    assert edges["edge_002"]["visible"] is True


def test_graph_explorer_selected_node_detail_lists_relations() -> None:
    view = build_graph_explorer_view(
        graph_artifact(),
        scope="latest",
        selected_node_id="conf_001",
    )
    detail = view["selected_node"]

    assert detail["node_id"] == "conf_001"
    assert detail["group"] == "conflict"
    assert detail["provenance_ids"] == ["prov_002", "prov_003"]
    assert {
        relation["relation"] for relation in detail["outgoing_relations"]
    } == {"conflicts_with"}


def test_graph_explorer_sample_html_snapshot_is_local_and_covers_key_roles() -> None:
    html = (ROOT / "docs" / "graph-explorer-sample.html").read_text()

    assert "Graph Explorer Sample" in html
    assert "current best" in html
    assert "challenger" in html
    assert "evidence" in html
    assert "conflict" in html
    assert "provenance" in html
    assert "graphCanvas" in html
    assert "cdn" not in html.lower()
