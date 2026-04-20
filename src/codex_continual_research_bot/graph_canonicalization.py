"""Phase 2 graph canonicalization and validation boundary."""

from __future__ import annotations

from enum import Enum
from hashlib import sha256
import json
import re

from pydantic import Field, StrictInt, StrictStr

from .contracts import (
    ArgumentStance,
    ChallengerHypothesis,
    ProposalBundle,
    RevisionAction,
    StrictModel,
)


class GraphLayer(str, Enum):
    WORLD = "world"
    EPISTEMIC = "epistemic"
    PROVENANCE = "provenance"


class CanonicalNode(StrictModel):
    id: StrictStr = Field(min_length=1)
    label: StrictStr = Field(min_length=1)
    layer: GraphLayer
    key: StrictStr = Field(min_length=1)
    properties: dict[str, object]


class CanonicalEdge(StrictModel):
    id: StrictStr = Field(min_length=1)
    type: StrictStr = Field(min_length=1)
    layer: GraphLayer
    source: StrictStr = Field(min_length=1)
    target: StrictStr = Field(min_length=1)
    properties: dict[str, object]


class HypothesisSnapshot(StrictModel):
    hypothesis_id: StrictStr = Field(min_length=1)
    title: StrictStr = Field(min_length=1)
    statement: StrictStr = Field(min_length=1)
    version: StrictInt = Field(ge=1, default=1)


class CanonicalizationContext(StrictModel):
    topic_id: StrictStr = Field(min_length=1)
    run_id: StrictStr = Field(min_length=1)
    proposal_id: StrictStr = Field(min_length=1)
    current_best_hypothesis_id: StrictStr | None = None
    existing_hypotheses: list[HypothesisSnapshot] = Field(default_factory=list)


class DeduplicationSummary(StrictModel):
    evidence_candidates_in: StrictInt = Field(ge=0)
    evidence_nodes_out: StrictInt = Field(ge=0)
    duplicate_evidence_collapsed: StrictInt = Field(ge=0)
    claims_in: StrictInt = Field(ge=0)
    claim_nodes_out: StrictInt = Field(ge=0)
    duplicate_claims_collapsed: StrictInt = Field(ge=0)


class ReviewFlag(StrictModel):
    code: StrictStr = Field(min_length=1)
    message: StrictStr = Field(min_length=1)


class CanonicalGraph(StrictModel):
    nodes: list[CanonicalNode]
    edges: list[CanonicalEdge]


class CanonicalizationResult(StrictModel):
    graph: CanonicalGraph
    dedupe: DeduplicationSummary
    review_flags: list[ReviewFlag]
    quarantine_reasons: list[StrictStr]
    digest: StrictStr = Field(min_length=1)

    @property
    def quarantined(self) -> bool:
        return bool(self.quarantine_reasons)


def neo4j_constraints() -> list[str]:
    """Return the Phase 2 schema constraints for canonical nodes."""

    return [
        "CREATE CONSTRAINT evidence_id IF NOT EXISTS FOR (n:Evidence) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (n:Claim) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT hypothesis_id IF NOT EXISTS FOR (n:Hypothesis) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT provenance_id IF NOT EXISTS FOR (n:ProvenanceRecord) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT evidence_key IF NOT EXISTS FOR (n:Evidence) REQUIRE n.key IS UNIQUE",
        "CREATE CONSTRAINT claim_key IF NOT EXISTS FOR (n:Claim) REQUIRE n.key IS UNIQUE",
        "CREATE CONSTRAINT hypothesis_key IF NOT EXISTS FOR (n:Hypothesis) REQUIRE n.key IS UNIQUE",
    ]


class CanonicalGraphService:
    """Normalize runtime proposal output into canonical graph nodes and edges."""

    _TEMPORAL_SCOPE_RE = re.compile(
        r"(\d{4}-\d{2}-\d{2}|\d{4}|\bonward\b|\bas of\b|\bcurrent\b|\bongoing\b)",
        re.IGNORECASE,
    )

    def canonicalize(
        self,
        *,
        proposal: ProposalBundle,
        context: CanonicalizationContext,
    ) -> CanonicalizationResult:
        quarantine_reasons: list[str] = []
        review_flags: list[ReviewFlag] = []
        nodes: list[CanonicalNode] = []
        edges: list[CanonicalEdge] = []

        provenance_node = CanonicalNode(
            id=f"provenance:{context.proposal_id}",
            label="ProvenanceRecord",
            layer=GraphLayer.PROVENANCE,
            key=context.proposal_id,
            properties={
                "topic_id": context.topic_id,
                "run_id": context.run_id,
                "proposal_id": context.proposal_id,
                "summary_draft": proposal.summary_draft,
                "turn_count": proposal.execution_meta.turn_count,
                "tool_call_count": proposal.execution_meta.tool_call_count,
                "repair_attempts": proposal.execution_meta.repair_attempts,
            },
        )
        nodes.append(provenance_node)

        existing_by_id = {
            hypothesis.hypothesis_id: hypothesis for hypothesis in context.existing_hypotheses
        }

        for hypothesis in context.existing_hypotheses:
            node = self._make_hypothesis_node(hypothesis, is_current_best=(
                hypothesis.hypothesis_id == context.current_best_hypothesis_id
            ))
            nodes.append(node)

        evidence_by_key: dict[str, CanonicalNode] = {}
        evidence_aliases: dict[str, str] = {}
        for evidence in proposal.evidence_candidates:
            key = self._evidence_key(evidence.kind.value, evidence.source_url)
            node = evidence_by_key.get(key)
            if node is None:
                node = CanonicalNode(
                    id=f"evidence:{key}",
                    label="Evidence",
                    layer=GraphLayer.WORLD,
                    key=key,
                    properties={
                        "kind": evidence.kind.value,
                        "source_url": evidence.source_url,
                        "title": evidence.title,
                        "accessed_at": evidence.accessed_at.isoformat(),
                        "extraction_note": evidence.extraction_note,
                    },
                )
                evidence_by_key[key] = node
                nodes.append(node)
                edges.append(
                    self._edge(
                        edge_type="RECORDED_IN",
                        layer=GraphLayer.PROVENANCE,
                        source=node.id,
                        target=provenance_node.id,
                    )
                )
            evidence_aliases[evidence.artifact_id] = node.id

        claim_by_key: dict[str, CanonicalNode] = {}
        claim_aliases: dict[str, str] = {}
        for claim in proposal.claims:
            try:
                normalized_scope = self._normalize_temporal_scope(claim.temporal_scope)
            except ValueError as exc:
                quarantine_reasons.append(f"claim {claim.claim_id}: {exc}")
                continue

            canonical_artifacts: list[str] = []
            missing_artifacts: list[str] = []
            for artifact_id in claim.artifact_ids:
                canonical_artifact = evidence_aliases.get(artifact_id)
                if canonical_artifact is None:
                    missing_artifacts.append(artifact_id)
                else:
                    canonical_artifacts.append(canonical_artifact)
            if missing_artifacts:
                quarantine_reasons.append(
                    f"claim {claim.claim_id}: missing provenance evidence {', '.join(sorted(missing_artifacts))}"
                )
                continue

            key = self._claim_key(
                text=claim.text,
                temporal_scope=normalized_scope,
                artifact_ids=canonical_artifacts,
            )
            node = claim_by_key.get(key)
            if node is None:
                node = CanonicalNode(
                    id=f"claim:{key}",
                    label="Claim",
                    layer=GraphLayer.WORLD,
                    key=key,
                    properties={
                        "text": self._normalize_text(claim.text),
                        "temporal_scope": normalized_scope,
                    },
                )
                claim_by_key[key] = node
                nodes.append(node)
                edges.append(
                    self._edge(
                        edge_type="RECORDED_IN",
                        layer=GraphLayer.PROVENANCE,
                        source=node.id,
                        target=provenance_node.id,
                    )
                )
                for artifact_id in sorted(set(canonical_artifacts)):
                    edges.append(
                        self._edge(
                            edge_type="DERIVED_FROM",
                            layer=GraphLayer.PROVENANCE,
                            source=node.id,
                            target=artifact_id,
                        )
                    )
            claim_aliases[claim.claim_id] = node.id

        hypothesis_nodes: dict[str, CanonicalNode] = {
            node.properties["hypothesis_id"]: node
            for node in nodes
            if node.label == "Hypothesis"
        }

        for challenger in proposal.challenger_hypotheses:
            if context.current_best_hypothesis_id is None:
                quarantine_reasons.append(
                    f"challenger {challenger.hypothesis_id}: current best hypothesis context is required"
                )
                continue
            current_best = existing_by_id.get(context.current_best_hypothesis_id)
            if current_best is None:
                quarantine_reasons.append(
                    f"challenger {challenger.hypothesis_id}: current best hypothesis "
                    f"{context.current_best_hypothesis_id} is missing from context"
                )
                continue
            node = self._make_candidate_hypothesis_node(
                challenger=challenger,
                existing=existing_by_id.get(challenger.hypothesis_id),
                is_current_best=False,
            )
            hypothesis_nodes[challenger.hypothesis_id] = node
            nodes = self._replace_or_append_node(nodes, node)
            edges.append(
                self._edge(
                    edge_type="RECORDED_IN",
                    layer=GraphLayer.PROVENANCE,
                    source=node.id,
                    target=provenance_node.id,
                )
            )
            edges.append(
                self._edge(
                    edge_type="CHALLENGES",
                    layer=GraphLayer.EPISTEMIC,
                    source=node.id,
                    target=self._hypothesis_canonical_id(
                        context.current_best_hypothesis_id,
                        current_best.version,
                    ),
                )
            )

        challenge_argument_count = 0
        for argument in proposal.arguments:
            target_node = hypothesis_nodes.get(argument.target_hypothesis_id)
            if target_node is None:
                quarantine_reasons.append(
                    f"argument {argument.argument_id}: unknown target hypothesis {argument.target_hypothesis_id}"
                )
                continue
            missing_claims = [
                claim_id for claim_id in argument.claim_ids if claim_id not in claim_aliases
            ]
            if missing_claims:
                quarantine_reasons.append(
                    f"argument {argument.argument_id}: missing claim references {', '.join(sorted(missing_claims))}"
                )
                continue
            if argument.stance == ArgumentStance.CHALLENGE:
                challenge_argument_count += 1
            for claim_id in sorted(set(argument.claim_ids)):
                edge_type = "SUPPORTS" if argument.stance == ArgumentStance.SUPPORT else "CHALLENGES"
                edges.append(
                    self._edge(
                        edge_type=edge_type,
                        layer=GraphLayer.EPISTEMIC,
                        source=claim_aliases[claim_id],
                        target=target_node.id,
                        properties={"argument_id": argument.argument_id, "rationale": argument.rationale},
                    )
                )

        for revision in proposal.revision_proposals:
            if revision.action != RevisionAction.SUPERSEDE:
                continue
            if revision.supersedes_hypothesis_id is None:
                quarantine_reasons.append(
                    f"revision for {revision.hypothesis_id}: supersede action requires supersedes_hypothesis_id"
                )
                continue
            predecessor = existing_by_id.get(revision.supersedes_hypothesis_id)
            if predecessor is None:
                quarantine_reasons.append(
                    f"revision for {revision.hypothesis_id}: stale predecessor {revision.supersedes_hypothesis_id} is missing"
                )
                continue
            current_node = hypothesis_nodes.get(revision.hypothesis_id)
            if current_node is None:
                quarantine_reasons.append(
                    f"revision for {revision.hypothesis_id}: superseding hypothesis node is missing"
                )
                continue
            versioned_node = CanonicalNode(
                id=self._hypothesis_canonical_id(revision.hypothesis_id, predecessor.version + 1),
                label="Hypothesis",
                layer=GraphLayer.EPISTEMIC,
                key=f"{revision.hypothesis_id}:v{predecessor.version + 1}",
                properties={
                    **current_node.properties,
                    "hypothesis_id": revision.hypothesis_id,
                    "version": predecessor.version + 1,
                    "supersedes_hypothesis_id": revision.supersedes_hypothesis_id,
                },
            )
            hypothesis_nodes[revision.hypothesis_id] = versioned_node
            nodes = self._replace_or_append_node(nodes, versioned_node)
            edges.append(
                self._edge(
                    edge_type="SUPERSEDES",
                    layer=GraphLayer.EPISTEMIC,
                    source=versioned_node.id,
                    target=self._hypothesis_canonical_id(
                        revision.supersedes_hypothesis_id,
                        predecessor.version,
                    ),
                    properties={"rationale": revision.rationale},
                )
            )

        if proposal.arguments and challenge_argument_count == 0:
            review_flags.append(
                ReviewFlag(
                    code="stagnation_risk_support_only",
                    message="Proposal contains support-only arguments and should be reviewed for competition drift.",
                )
            )

        dedupe = DeduplicationSummary(
            evidence_candidates_in=len(proposal.evidence_candidates),
            evidence_nodes_out=len(evidence_by_key),
            duplicate_evidence_collapsed=max(0, len(proposal.evidence_candidates) - len(evidence_by_key)),
            claims_in=len(proposal.claims),
            claim_nodes_out=len(claim_by_key),
            duplicate_claims_collapsed=max(0, len(proposal.claims) - len(claim_by_key)),
        )

        canonical_graph = CanonicalGraph(
            nodes=sorted(nodes, key=lambda node: node.id),
            edges=sorted(edges, key=lambda edge: edge.id),
        )
        quarantine_reasons.extend(self._edge_integrity_reasons(canonical_graph))
        digest = sha256(
            json.dumps(canonical_graph.model_dump(mode="json"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        return CanonicalizationResult(
            graph=canonical_graph,
            dedupe=dedupe,
            review_flags=review_flags,
            quarantine_reasons=sorted(set(quarantine_reasons)),
            digest=digest,
        )

    def _normalize_temporal_scope(self, value: str) -> str:
        normalized = self._normalize_text(value)
        if not normalized:
            raise ValueError("temporal scope is required")
        if self._TEMPORAL_SCOPE_RE.search(normalized) is None:
            raise ValueError(
                f"temporal scope '{value}' is not canonical enough for contradiction-safe storage"
            )
        return normalized.lower()

    def _normalize_text(self, value: str) -> str:
        return " ".join(value.split())

    def _evidence_key(self, kind: str, source_url: str) -> str:
        return sha256(f"{kind}|{source_url.strip().lower()}".encode("utf-8")).hexdigest()

    def _claim_key(self, *, text: str, temporal_scope: str, artifact_ids: list[str]) -> str:
        payload = {
            "artifacts": sorted(set(artifact_ids)),
            "temporal_scope": temporal_scope,
            "text": self._normalize_text(text).lower(),
        }
        return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _make_hypothesis_node(
        self,
        hypothesis: HypothesisSnapshot,
        *,
        is_current_best: bool,
    ) -> CanonicalNode:
        return CanonicalNode(
            id=self._hypothesis_canonical_id(hypothesis.hypothesis_id, hypothesis.version),
            label="Hypothesis",
            layer=GraphLayer.EPISTEMIC,
            key=f"{hypothesis.hypothesis_id}:v{hypothesis.version}",
            properties={
                "hypothesis_id": hypothesis.hypothesis_id,
                "title": hypothesis.title,
                "statement": hypothesis.statement,
                "version": hypothesis.version,
                "is_current_best": is_current_best,
            },
        )

    def _make_candidate_hypothesis_node(
        self,
        *,
        challenger: ChallengerHypothesis,
        existing: HypothesisSnapshot | None,
        is_current_best: bool,
    ) -> CanonicalNode:
        version = 1 if existing is None else existing.version
        return CanonicalNode(
            id=self._hypothesis_canonical_id(challenger.hypothesis_id, version),
            label="Hypothesis",
            layer=GraphLayer.EPISTEMIC,
            key=f"{challenger.hypothesis_id}:v{version}",
            properties={
                "hypothesis_id": challenger.hypothesis_id,
                "title": challenger.title,
                "statement": challenger.statement,
                "status": challenger.status.value,
                "version": version,
                "is_current_best": is_current_best,
            },
        )

    def _hypothesis_canonical_id(self, hypothesis_id: str, version: int) -> str:
        return f"hypothesis:{hypothesis_id}:v{version}"

    def _edge(
        self,
        *,
        edge_type: str,
        layer: GraphLayer,
        source: str,
        target: str,
        properties: dict[str, object] | None = None,
    ) -> CanonicalEdge:
        edge_key = {
            "edge_type": edge_type,
            "layer": layer.value,
            "properties": properties or {},
            "source": source,
            "target": target,
        }
        edge_id = sha256(json.dumps(edge_key, sort_keys=True).encode("utf-8")).hexdigest()
        return CanonicalEdge(
            id=f"edge:{edge_id}",
            type=edge_type,
            layer=layer,
            source=source,
            target=target,
            properties=properties or {},
        )

    def _replace_or_append_node(
        self,
        nodes: list[CanonicalNode],
        new_node: CanonicalNode,
    ) -> list[CanonicalNode]:
        replaced = False
        updated_nodes: list[CanonicalNode] = []
        for node in nodes:
            if node.id == new_node.id:
                updated_nodes.append(new_node)
                replaced = True
            else:
                updated_nodes.append(node)
        if not replaced:
            updated_nodes.append(new_node)
        return updated_nodes

    def _edge_integrity_reasons(self, graph: CanonicalGraph) -> list[str]:
        node_ids = {node.id for node in graph.nodes}
        reasons: list[str] = []
        for edge in graph.edges:
            if edge.source not in node_ids:
                reasons.append(f"edge {edge.id}: missing source node {edge.source}")
            if edge.target not in node_ids:
                reasons.append(f"edge {edge.id}: missing target node {edge.target}")
        return reasons


def canonical_mapping_spec() -> dict[str, dict[str, str]]:
    """Machine-readable summary of the Phase 2 node and edge mapping."""

    return {
        "nodes": {
            "Evidence": "world-layer source material deduped by kind + source_url",
            "Claim": "world-layer statement deduped by text + temporal_scope + evidence lineage",
            "Hypothesis": "epistemic-layer revisable belief node versioned per canonical hypothesis id",
            "ProvenanceRecord": "provenance-layer record for a single proposal canonicalization pass",
        },
        "edges": {
            "DERIVED_FROM": "claim to evidence provenance lineage",
            "SUPPORTS": "claim to hypothesis support relation",
            "CHALLENGES": "claim-to-hypothesis or challenger-to-current-best adversarial relation",
            "SUPERSEDES": "new hypothesis version replaces a stale predecessor",
            "RECORDED_IN": "canonical node was produced from a concrete proposal record",
        },
    }
