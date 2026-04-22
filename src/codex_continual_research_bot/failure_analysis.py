"""Operator-facing failure classification helpers."""

from __future__ import annotations

from collections import Counter
from typing import Any

from codex_continual_research_bot.contracts import FailureCode


def classify_malformed_proposal_failure(detail: str | None) -> str:
    normalized = " ".join((detail or "").lower().split())
    if not normalized:
        return "malformed_proposal_other"
    if "supersede action requires supersedes_hypothesis_id" in normalized:
        return "supersede_missing_predecessor"
    if "supersede proposal must target the attack frontier" in normalized:
        return "supersede_contract_mismatch"
    if "supersedes_hypothesis_id" in normalized or "stale predecessor" in normalized:
        return "supersede_invalid_predecessor"
    if "temporal scope" in normalized or "temporal_scope" in normalized:
        return "temporal_scope_not_canonical"
    if "missing claim references" in normalized or "references unknown claims" in normalized:
        return "argument_claim_reference_missing"
    if "must reference declared claims" in normalized:
        return "argument_claim_reference_missing"
    if "missing provenance evidence" in normalized:
        return "claim_evidence_reference_missing"
    if "references unknown evidence artifacts" in normalized:
        return "claim_evidence_reference_missing"
    if "declared evidence artifacts" in normalized:
        return "claim_evidence_reference_missing"
    if "outside current snapshot" in normalized or "proposal challengers" in normalized:
        return "hypothesis_reference_contract"
    if "current best hypothesis" in normalized and "missing" in normalized:
        return "hypothesis_reference_contract"
    if "challenger" in normalized and "missing" in normalized:
        return "hypothesis_reference_contract"
    if "support-only" in normalized or "stagnation" in normalized:
        return "stagnation_or_quality_drift"
    return "malformed_proposal_other"


def summarize_malformed_proposal_failures(
    rows: list[dict[str, Any]],
) -> dict[str, object]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("last_failure_code") != FailureCode.MALFORMED_PROPOSAL.value:
            continue
        counts[classify_malformed_proposal_failure(row.get("last_failure_detail"))] += 1
    return {
        "total": sum(counts.values()),
        "by_type": dict(sorted(counts.items())),
    }
