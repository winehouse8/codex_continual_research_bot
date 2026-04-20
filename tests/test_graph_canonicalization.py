from __future__ import annotations

import json
from pathlib import Path

from codex_continual_research_bot.contracts import ProposalBundle
from codex_continual_research_bot.graph_canonicalization import (
    CanonicalGraphService,
    CanonicalizationContext,
    HypothesisSnapshot,
    canonical_mapping_spec,
    neo4j_constraints,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"


def load_proposal() -> ProposalBundle:
    return ProposalBundle.model_validate(
        json.loads((FIXTURES_DIR / "proposal_bundle.json").read_text())
    )


def make_context() -> CanonicalizationContext:
    return CanonicalizationContext(
        topic_id="topic_001",
        run_id="run_001",
        proposal_id="proposal_001",
        current_best_hypothesis_id="hyp_001",
        existing_hypotheses=[
            HypothesisSnapshot(
                hypothesis_id="hyp_001",
                title="Block scheduled run on session ambiguity",
                statement="A scheduled run must stop when principal or workspace inspection is ambiguous.",
                version=2,
            )
        ],
    )


def test_happy_path_canonicalization_builds_layered_graph() -> None:
    result = CanonicalGraphService().canonicalize(
        proposal=load_proposal(),
        context=make_context(),
    )

    assert not result.quarantined
    labels = {(node.label, node.layer.value) for node in result.graph.nodes}
    assert ("Evidence", "world") in labels
    assert ("Claim", "world") in labels
    assert ("Hypothesis", "epistemic") in labels
    assert ("ProvenanceRecord", "provenance") in labels

    edge_types = {edge.type for edge in result.graph.edges}
    assert "DERIVED_FROM" in edge_types
    assert "SUPPORTS" in edge_types
    assert "CHALLENGES" in edge_types
    assert any(
        edge.type == "CHALLENGES"
        and edge.source == "hypothesis:hyp_002:v1"
        and edge.target == "hypothesis:hyp_001:v2"
        for edge in result.graph.edges
    )


def test_malformed_argument_reference_is_quarantined() -> None:
    payload = load_proposal().model_dump(mode="json")
    payload["arguments"][0]["claim_ids"] = ["claim_missing"]
    proposal = ProposalBundle.model_validate(payload)

    result = CanonicalGraphService().canonicalize(
        proposal=proposal,
        context=make_context(),
    )

    assert result.quarantined
    assert any("missing claim references" in reason for reason in result.quarantine_reasons)


def test_duplicate_evidence_and_claims_are_deduped() -> None:
    payload = load_proposal().model_dump(mode="json")
    payload["evidence_candidates"].append(dict(payload["evidence_candidates"][0], artifact_id="src_999"))
    payload["claims"].append(dict(payload["claims"][0], claim_id="claim_999", artifact_ids=["src_999"]))
    proposal = ProposalBundle.model_validate(payload)

    result = CanonicalGraphService().canonicalize(
        proposal=proposal,
        context=make_context(),
    )

    assert not result.quarantined
    assert result.dedupe.duplicate_evidence_collapsed == 1
    assert result.dedupe.duplicate_claims_collapsed == 1
    assert sum(node.label == "Evidence" for node in result.graph.nodes) == 1
    assert sum(node.label == "Claim" for node in result.graph.nodes) == 1


def test_temporal_scope_missing_rejected() -> None:
    payload = load_proposal().model_dump(mode="json")
    payload["claims"][0]["temporal_scope"] = "unknown"
    proposal = ProposalBundle.model_validate(payload)

    result = CanonicalGraphService().canonicalize(
        proposal=proposal,
        context=make_context(),
    )

    assert result.quarantined
    assert any("temporal scope" in reason for reason in result.quarantine_reasons)


def test_stale_hypothesis_version_supersession_creates_new_version_edge() -> None:
    payload = load_proposal().model_dump(mode="json")
    payload["challenger_hypotheses"] = [
        {
            "hypothesis_id": "hyp_001",
            "title": "Require explicit healthcheck before scheduled runs",
            "statement": "Scheduled runs must pass a dedicated healthcheck before using an existing session.",
            "status": "proposed",
        }
    ]
    payload["revision_proposals"] = [
        {
            "hypothesis_id": "hyp_001",
            "action": "supersede",
            "rationale": "The older hypothesis is stale because it lacks the dedicated healthcheck requirement.",
            "supersedes_hypothesis_id": "hyp_001",
        }
    ]
    proposal = ProposalBundle.model_validate(payload)

    result = CanonicalGraphService().canonicalize(
        proposal=proposal,
        context=make_context(),
    )

    assert not result.quarantined
    assert any(node.id == "hypothesis:hyp_001:v3" for node in result.graph.nodes)
    assert any(
        edge.type == "SUPERSEDES"
        and edge.source == "hypothesis:hyp_001:v3"
        and edge.target == "hypothesis:hyp_001:v2"
        for edge in result.graph.edges
    )


def test_missing_provenance_reference_is_quarantined() -> None:
    payload = load_proposal().model_dump(mode="json")
    payload["claims"][0]["artifact_ids"] = ["src_missing"]
    proposal = ProposalBundle.model_validate(payload)

    result = CanonicalGraphService().canonicalize(
        proposal=proposal,
        context=make_context(),
    )

    assert result.quarantined
    assert any("missing provenance evidence" in reason for reason in result.quarantine_reasons)


def test_repeated_canonicalization_is_idempotent() -> None:
    proposal = load_proposal()
    service = CanonicalGraphService()
    context = make_context()

    first = service.canonicalize(proposal=proposal, context=context)
    second = service.canonicalize(proposal=proposal, context=context)

    assert first.digest == second.digest
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_same_proposal_replay_is_order_independent() -> None:
    payload = load_proposal().model_dump(mode="json")
    payload["evidence_candidates"] = list(reversed(payload["evidence_candidates"]))
    payload["claims"] = list(reversed(payload["claims"]))
    payload["arguments"] = list(reversed(payload["arguments"]))
    replayed = ProposalBundle.model_validate(payload)
    service = CanonicalGraphService()

    first = service.canonicalize(proposal=load_proposal(), context=make_context())
    second = service.canonicalize(proposal=replayed, context=make_context())

    assert first.digest == second.digest
    assert first.graph.model_dump(mode="json") == second.graph.model_dump(mode="json")


def test_support_only_repetition_gets_stagnation_flag() -> None:
    result = CanonicalGraphService().canonicalize(
        proposal=load_proposal(),
        context=make_context(),
    )

    assert any(flag.code == "stagnation_risk_support_only" for flag in result.review_flags)


def test_challenger_links_to_existing_best_hypothesis() -> None:
    result = CanonicalGraphService().canonicalize(
        proposal=load_proposal(),
        context=make_context(),
    )

    challenge_edges = [
        edge for edge in result.graph.edges if edge.type == "CHALLENGES"
    ]
    assert any(edge.source == "hypothesis:hyp_002:v1" for edge in challenge_edges)
    assert any(edge.target == "hypothesis:hyp_001:v2" for edge in challenge_edges)


def test_missing_current_best_snapshot_quarantines_challenger_linkage() -> None:
    result = CanonicalGraphService().canonicalize(
        proposal=load_proposal(),
        context=CanonicalizationContext(
            topic_id="topic_001",
            run_id="run_001",
            proposal_id="proposal_001",
            current_best_hypothesis_id="hyp_missing",
            existing_hypotheses=[],
        ),
    )

    assert result.quarantined
    assert any(
        "current best hypothesis hyp_missing is missing" in reason
        for reason in result.quarantine_reasons
    )
    assert not any(
        edge.type == "CHALLENGES"
        and edge.target == "hypothesis:hyp_missing:v1"
        for edge in result.graph.edges
    )


def test_neo4j_schema_constraints_cover_phase2_labels() -> None:
    constraints = neo4j_constraints()

    assert any("Evidence" in statement for statement in constraints)
    assert any("Claim" in statement for statement in constraints)
    assert any("Hypothesis" in statement for statement in constraints)
    assert any("ProvenanceRecord" in statement for statement in constraints)


def test_mapping_spec_covers_world_epistemic_and_provenance_layers() -> None:
    mapping = canonical_mapping_spec()

    assert "Evidence" in mapping["nodes"]
    assert "Claim" in mapping["nodes"]
    assert "Hypothesis" in mapping["nodes"]
    assert "ProvenanceRecord" in mapping["nodes"]
    assert "SUPERSEDES" in mapping["edges"]
