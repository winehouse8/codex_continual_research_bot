from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from codex_continual_research_bot.contracts import (
    ConflictRef,
    HypothesisRef,
    ProposalBundle,
    TopicSnapshot,
)
from codex_continual_research_bot.graph_canonicalization import (
    CanonicalGraphService,
    CanonicalizationContext,
    HypothesisSnapshot,
)
from codex_continual_research_bot.graph_visualization import (
    build_graph_export_artifact,
    render_graph_artifact,
)
from codex_continual_research_bot.ux_contracts import GraphExportArtifact


ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
GENERATED_AT = datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)


def load_proposal() -> ProposalBundle:
    return ProposalBundle.model_validate(
        json.loads((FIXTURES_DIR / "proposal_bundle.json").read_text())
    )


def make_snapshot() -> TopicSnapshot:
    return TopicSnapshot(
        topic_id="topic_001",
        snapshot_version=7,
        topic_summary="Track scheduled run session ambiguity.",
        current_best_hypotheses=[
            HypothesisRef(
                hypothesis_id="hyp_001",
                title="Block scheduled run on session ambiguity",
                summary=(
                    "A scheduled run must stop when principal or workspace inspection "
                    "is ambiguous."
                ),
            )
        ],
        challenger_targets=[
            HypothesisRef(
                hypothesis_id="hyp_002",
                title="Warning-only stale session path",
                summary=(
                    "A stale but account-verified session might be safe to defer "
                    "without blocking."
                ),
            )
        ],
        active_conflicts=[
            ConflictRef(
                conflict_id="conf_001",
                summary="Freshness failure can mean recoverable token age or identity drift.",
            )
        ],
        open_questions=[],
        recent_provenance_digest="prov_snapshot_001",
        queued_user_inputs=[],
    )


def make_graph_write() -> dict[str, object]:
    return make_graph_write_for(
        run_id="run_001",
        proposal_id="proposal_001",
        created_at="2026-04-21T12:00:00+00:00",
        artifact_id="src_001",
        source_url="https://example.com/openai-codex-auth-doc",
        claim_id="claim_001",
        claim_text=(
            "A scheduled run must not proceed when session inspection cannot verify "
            "both principal identity and workspace policy."
        ),
        argument_id="arg_001",
        challenger_id="hyp_002",
    )


def make_graph_write_for(
    *,
    run_id: str,
    proposal_id: str,
    created_at: str,
    artifact_id: str,
    source_url: str,
    claim_id: str,
    claim_text: str,
    argument_id: str,
    challenger_id: str,
) -> dict[str, object]:
    payload = load_proposal().model_dump(mode="json")
    payload["evidence_candidates"][0].update(
        {
            "artifact_id": artifact_id,
            "source_url": source_url,
            "title": f"Evidence for {proposal_id}",
        }
    )
    payload["claims"][0].update(
        {
            "claim_id": claim_id,
            "text": claim_text,
            "artifact_ids": [artifact_id],
        }
    )
    payload["arguments"][0].update(
        {
            "argument_id": argument_id,
            "claim_ids": [claim_id],
            "rationale": f"{proposal_id} rationale",
        }
    )
    payload["challenger_hypotheses"][0].update(
        {
            "hypothesis_id": challenger_id,
            "title": f"Challenger from {proposal_id}",
            "statement": f"{proposal_id} should remain visible in history projection.",
        }
    )
    proposal = ProposalBundle.model_validate(payload)
    result = CanonicalGraphService().canonicalize(
        proposal=proposal,
        context=CanonicalizationContext(
            topic_id="topic_001",
            run_id=run_id,
            proposal_id=proposal_id,
            current_best_hypothesis_id="hyp_001",
            existing_hypotheses=[
                HypothesisSnapshot(
                    hypothesis_id="hyp_001",
                    title="Block scheduled run on session ambiguity",
                    statement=(
                        "A scheduled run must stop when principal or workspace "
                        "inspection is ambiguous."
                    ),
                    version=2,
                )
            ],
        ),
    )
    assert not result.quarantined
    return {
        "run_id": run_id,
        "proposal_id": proposal_id,
        "created_at": created_at,
        "graph_json": result.graph.model_dump(mode="json"),
        "graph_digest": result.digest,
    }


def test_canonical_graph_export_projects_hypotheses_evidence_conflicts_and_provenance() -> None:
    artifact = build_graph_export_artifact(
        topic_id="topic_001",
        snapshot=make_snapshot(),
        graph_write=make_graph_write(),
        generated_at=GENERATED_AT,
    )

    node_types = {node.node_type.value for node in artifact.nodes}
    edge_types = {edge.edge_type.value for edge in artifact.edges}

    assert artifact.projection_source == "canonical_graph_write"
    assert {"hypothesis", "evidence", "claim", "conflict", "provenance"} <= node_types
    assert {"supports", "challenges", "derived_from", "conflicts_with"} <= edge_types
    assert artifact.memory_explorer.current_best_node_ids == ["hypothesis:hyp_001:v2"]
    assert artifact.memory_explorer.challenger_node_ids == ["hypothesis:hyp_002:v1"]
    assert artifact.memory_explorer.conflict_node_ids == ["conf_001"]
    assert artifact.memory_explorer.evidence_node_ids
    assert artifact.memory_explorer.provenance_node_ids == ["provenance:proposal_001"]


def test_canonical_graph_export_accepts_persisted_graph_json_string() -> None:
    graph_write = make_graph_write()
    graph_write["graph_json"] = json.dumps(graph_write["graph_json"], sort_keys=True)

    artifact = build_graph_export_artifact(
        topic_id="topic_001",
        snapshot=make_snapshot(),
        graph_write=graph_write,
        generated_at=GENERATED_AT,
    )

    assert artifact.projection_source == "canonical_graph_write"
    assert artifact.memory_explorer.provenance_node_ids == ["provenance:proposal_001"]


def test_multiple_canonical_graph_writes_accumulate_history_provenance() -> None:
    first = make_graph_write()
    second = make_graph_write_for(
        run_id="run_002",
        proposal_id="proposal_002",
        created_at="2026-04-21T13:00:00+00:00",
        artifact_id="src_002",
        source_url="https://example.com/codex-healthcheck-path",
        claim_id="claim_002",
        claim_text="A dedicated healthcheck path preserves scheduled run auditability.",
        argument_id="arg_002",
        challenger_id="hyp_003",
    )

    artifact = build_graph_export_artifact(
        topic_id="topic_001",
        snapshot=make_snapshot(),
        graph_writes=[first, second],
        generated_at=GENERATED_AT,
    )

    assert artifact.projection_source == "canonical_graph_history"
    assert artifact.memory_explorer.provenance_node_ids == [
        "provenance:proposal_001",
        "provenance:proposal_002",
    ]
    assert len(artifact.memory_explorer.evidence_node_ids) == 2
    run_scopes = {
        node.temporal_scope
        for node in artifact.nodes
        if node.node_type.value == "provenance"
    }
    assert {"run_001", "run_002"} <= run_scopes


def test_latest_and_history_graph_exports_are_distinct() -> None:
    first = make_graph_write()
    second = make_graph_write_for(
        run_id="run_002",
        proposal_id="proposal_002",
        created_at="2026-04-21T13:00:00+00:00",
        artifact_id="src_002",
        source_url="https://example.com/codex-healthcheck-path",
        claim_id="claim_002",
        claim_text="A dedicated healthcheck path preserves scheduled run auditability.",
        argument_id="arg_002",
        challenger_id="hyp_003",
    )

    latest = build_graph_export_artifact(
        topic_id="topic_001",
        snapshot=make_snapshot(),
        graph_write=second,
        generated_at=GENERATED_AT,
    )
    history = build_graph_export_artifact(
        topic_id="topic_001",
        snapshot=make_snapshot(),
        graph_writes=[first, second],
        generated_at=GENERATED_AT,
    )

    assert latest.projection_source == "canonical_graph_write"
    assert latest.memory_explorer.provenance_node_ids == ["provenance:proposal_002"]
    assert history.projection_source == "canonical_graph_history"
    assert history.memory_explorer.provenance_node_ids == [
        "provenance:proposal_001",
        "provenance:proposal_002",
    ]
    assert latest.graph_digest != history.graph_digest


def test_graph_export_is_deterministic_for_same_inputs() -> None:
    graph_write = make_graph_write()

    first = build_graph_export_artifact(
        topic_id="topic_001",
        snapshot=make_snapshot(),
        graph_write=graph_write,
        generated_at=GENERATED_AT,
    )
    second = build_graph_export_artifact(
        topic_id="topic_001",
        snapshot=make_snapshot(),
        graph_write=graph_write,
        generated_at=GENERATED_AT,
    )

    assert first.graph_digest == second.graph_digest
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_history_graph_export_is_deterministic_for_reordered_writes() -> None:
    first_write = make_graph_write()
    second_write = make_graph_write_for(
        run_id="run_002",
        proposal_id="proposal_002",
        created_at="2026-04-21T13:00:00+00:00",
        artifact_id="src_002",
        source_url="https://example.com/codex-healthcheck-path",
        claim_id="claim_002",
        claim_text="A dedicated healthcheck path preserves scheduled run auditability.",
        argument_id="arg_002",
        challenger_id="hyp_003",
    )

    first = build_graph_export_artifact(
        topic_id="topic_001",
        snapshot=make_snapshot(),
        graph_writes=[second_write, first_write],
        generated_at=GENERATED_AT,
    )
    second = build_graph_export_artifact(
        topic_id="topic_001",
        snapshot=make_snapshot(),
        graph_writes=[first_write, second_write],
        generated_at=GENERATED_AT,
    )

    assert first.graph_digest == second.graph_digest
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_hypothesis_revision_relations_render_in_visualization() -> None:
    graph_write = make_graph_write()
    graph = graph_write["graph_json"]
    assert isinstance(graph, dict)
    graph["edges"].extend(
        [
            {
                "id": "edge:revision:supersedes",
                "type": "SUPERSEDES",
                "layer": "epistemic",
                "source": "hypothesis:hyp_002:v1",
                "target": "hypothesis:hyp_001:v2",
                "properties": {"rationale": "A challenger supersedes the older version."},
            },
            {
                "id": "edge:revision:weakens",
                "type": "WEAKENS",
                "layer": "epistemic",
                "source": "hypothesis:hyp_002:v1",
                "target": "hypothesis:hyp_001:v2",
                "properties": {"rationale": "Contrary evidence weakens the older version."},
            },
            {
                "id": "edge:revision:retires",
                "type": "RETIRES",
                "layer": "epistemic",
                "source": "hypothesis:hyp_002:v1",
                "target": "hypothesis:hyp_001:v2",
                "properties": {"rationale": "The older version is retired."},
            },
        ]
    )

    artifact = build_graph_export_artifact(
        topic_id="topic_001",
        snapshot=make_snapshot(),
        graph_write=graph_write,
        generated_at=GENERATED_AT,
    )

    edge_types = {edge.edge_type.value for edge in artifact.edges}
    assert {"supersedes", "weakens", "retires"} <= edge_types
    assert "weakens" in render_graph_artifact(artifact, output_format="dot")
    assert "-->|retires|" in render_graph_artifact(artifact, output_format="mermaid")


def test_snapshot_conflict_subgraph_export_links_conflict_to_beliefs() -> None:
    artifact = build_graph_export_artifact(
        topic_id="topic_001",
        snapshot=make_snapshot(),
        graph_write=None,
        generated_at=GENERATED_AT,
    )

    assert artifact.projection_source == "topic_snapshot"
    assert any(node.node_id == "conf_001" for node in artifact.nodes)
    assert any(
        edge.edge_type.value == "conflicts_with"
        and edge.source_node_id == "conf_001"
        and edge.target_node_id == "hyp_001"
        for edge in artifact.edges
    )
    assert artifact.memory_explorer.unresolved_conflict_count == 1


def test_graph_artifact_rejects_missing_memory_explorer_reference() -> None:
    payload = build_graph_export_artifact(
        topic_id="topic_001",
        snapshot=make_snapshot(),
        generated_at=GENERATED_AT,
    ).model_dump(mode="json")
    payload["memory_explorer"]["conflict_node_ids"] = ["conf_missing"]

    with pytest.raises(ValidationError):
        GraphExportArtifact.model_validate(payload)


def test_dot_mermaid_json_and_html_renderers_are_stable_smoke_outputs() -> None:
    artifact = build_graph_export_artifact(
        topic_id="topic_001",
        snapshot=make_snapshot(),
        graph_write=make_graph_write(),
        generated_at=GENERATED_AT,
    )

    json_output = render_graph_artifact(artifact, output_format="json")
    dot_output = render_graph_artifact(artifact, output_format="dot")
    mermaid_output = render_graph_artifact(artifact, output_format="mermaid")
    html_output = render_graph_artifact(artifact, output_format="html")

    assert json.loads(json_output)["graph_digest"] == artifact.graph_digest
    assert dot_output.startswith("digraph crb_graph")
    assert "->" in dot_output
    assert mermaid_output.startswith("graph LR")
    assert "-->|challenges|" in mermaid_output
    assert html_output.startswith("<!doctype html>")
    assert "Memory Explorer" in html_output
    assert "Unresolved conflicts: 1" in html_output
