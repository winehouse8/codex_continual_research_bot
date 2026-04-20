"""Final proposal validation, repair prompts, and quarantine artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import re

from pydantic import ValidationError

from codex_continual_research_bot.contracts import ProposalBundle, RunExecutionRequest


class ValidationLayer(str, Enum):
    SYNTAX = "syntax"
    SCHEMA = "schema"
    SEMANTIC = "semantic"
    POLICY = "policy"


@dataclass(frozen=True)
class ProposalValidationViolation:
    layer: ValidationLayer
    location: str
    message: str
    repairable: bool

    def format(self) -> str:
        return f"{self.layer.value}:{self.location}: {self.message}"


@dataclass(frozen=True)
class ProposalValidationResult:
    proposal: ProposalBundle | None
    violations: tuple[ProposalValidationViolation, ...]

    @property
    def valid(self) -> bool:
        return self.proposal is not None and not self.violations

    @property
    def repairable(self) -> bool:
        return bool(self.violations) and all(
            violation.repairable for violation in self.violations
        )


@dataclass(frozen=True)
class ProposalValidationContext:
    request: RunExecutionRequest
    retained_artifact_ids_after_compaction: frozenset[str] | None = None


_CITATION_PLACEHOLDER_PATTERN = re.compile(
    r"(\[citation needed\]|\{\{\s*citation|citation[_ -]?needed|source[_ -]?needed|"
    r"\bTODO[_ -]?CITATION\b|<citation>)",
    re.IGNORECASE,
)


class ProposalValidator:
    """Layered final-output validator used before persistence can observe proposals."""

    def validate_text(
        self,
        final_text: str,
        *,
        context: ProposalValidationContext,
    ) -> ProposalValidationResult:
        try:
            raw_payload = json.loads(final_text)
        except json.JSONDecodeError as exc:
            return ProposalValidationResult(
                proposal=None,
                violations=(
                    ProposalValidationViolation(
                        layer=ValidationLayer.SYNTAX,
                        location=f"line {exc.lineno} column {exc.colno}",
                        message=exc.msg,
                        repairable=True,
                    ),
                ),
            )

        try:
            proposal = ProposalBundle.model_validate(raw_payload)
        except ValidationError as exc:
            violations = tuple(
                ProposalValidationViolation(
                    layer=ValidationLayer.SCHEMA,
                    location=_format_location(error.get("loc", ())),
                    message=str(error.get("msg", "schema validation failed")),
                    repairable=True,
                )
                for error in exc.errors()
            )
            return ProposalValidationResult(proposal=None, violations=violations)

        violations = [
            *self._semantic_violations(proposal, context=context),
            *self._policy_violations(proposal, context=context),
        ]
        return ProposalValidationResult(
            proposal=None if violations else proposal,
            violations=tuple(violations),
        )

    def _semantic_violations(
        self,
        proposal: ProposalBundle,
        *,
        context: ProposalValidationContext,
    ) -> list[ProposalValidationViolation]:
        violations: list[ProposalValidationViolation] = []
        evidence_ids = [candidate.artifact_id for candidate in proposal.evidence_candidates]
        claim_ids = [claim.claim_id for claim in proposal.claims]
        challenger_id_values = [
            hypothesis.hypothesis_id for hypothesis in proposal.challenger_hypotheses
        ]
        challenger_ids = set(challenger_id_values)
        current_best_ids = {
            hypothesis.hypothesis_id
            for hypothesis in context.request.context_snapshot.current_best_hypotheses
        }
        known_hypothesis_ids = current_best_ids | challenger_ids

        violations.extend(
            self._duplicate_id_violations(
                label="evidence_candidates.artifact_id",
                values=evidence_ids,
            )
        )
        violations.extend(
            self._duplicate_id_violations(label="claims.claim_id", values=claim_ids)
        )
        violations.extend(
            self._duplicate_id_violations(
                label="challenger_hypotheses.hypothesis_id",
                values=challenger_id_values,
            )
        )
        evidence_id_set = set(evidence_ids)
        claim_id_set = set(claim_ids)

        for index, claim in enumerate(proposal.claims):
            missing = sorted(set(claim.artifact_ids) - evidence_id_set)
            if missing:
                violations.append(
                    ProposalValidationViolation(
                        layer=ValidationLayer.SEMANTIC,
                        location=f"claims[{index}].artifact_ids",
                        message=f"references unknown evidence artifacts: {', '.join(missing)}",
                        repairable=False,
                    )
                )

        for index, argument in enumerate(proposal.arguments):
            missing_claims = sorted(set(argument.claim_ids) - claim_id_set)
            if missing_claims:
                violations.append(
                    ProposalValidationViolation(
                        layer=ValidationLayer.SEMANTIC,
                        location=f"arguments[{index}].claim_ids",
                        message=f"references unknown claims: {', '.join(missing_claims)}",
                        repairable=False,
                    )
                )
            if argument.target_hypothesis_id not in known_hypothesis_ids:
                violations.append(
                    ProposalValidationViolation(
                        layer=ValidationLayer.SEMANTIC,
                        location=f"arguments[{index}].target_hypothesis_id",
                        message=(
                            "references hypothesis outside current snapshot or "
                            f"proposal challengers: {argument.target_hypothesis_id}"
                        ),
                        repairable=False,
                    )
                )

        for index, revision in enumerate(proposal.revision_proposals):
            if revision.hypothesis_id not in known_hypothesis_ids:
                violations.append(
                    ProposalValidationViolation(
                        layer=ValidationLayer.SEMANTIC,
                        location=f"revision_proposals[{index}].hypothesis_id",
                        message=(
                            "references hypothesis outside current snapshot or "
                            f"proposal challengers: {revision.hypothesis_id}"
                        ),
                        repairable=False,
                    )
                )
            if (
                revision.supersedes_hypothesis_id is not None
                and revision.supersedes_hypothesis_id not in known_hypothesis_ids
            ):
                violations.append(
                    ProposalValidationViolation(
                        layer=ValidationLayer.SEMANTIC,
                        location=f"revision_proposals[{index}].supersedes_hypothesis_id",
                        message=(
                            "references hypothesis outside current snapshot or "
                            f"proposal challengers: {revision.supersedes_hypothesis_id}"
                        ),
                        repairable=False,
                    )
                )

        retained_ids = context.retained_artifact_ids_after_compaction
        if retained_ids is not None:
            omitted = sorted(evidence_id_set - retained_ids)
            if omitted:
                violations.append(
                    ProposalValidationViolation(
                        layer=ValidationLayer.SEMANTIC,
                        location="evidence_candidates",
                        message=(
                            "compacted context omitted tool/artifact results used by "
                            f"proposal evidence: {', '.join(omitted)}"
                        ),
                        repairable=False,
                    )
                )
        return violations

    def _policy_violations(
        self,
        proposal: ProposalBundle,
        *,
        context: ProposalValidationContext,
    ) -> list[ProposalValidationViolation]:
        del context
        violations: list[ProposalValidationViolation] = []
        for location, value in _proposal_text_fields(proposal):
            if _CITATION_PLACEHOLDER_PATTERN.search(value):
                violations.append(
                    ProposalValidationViolation(
                        layer=ValidationLayer.POLICY,
                        location=location,
                        message="contains unresolved citation placeholder",
                        repairable=False,
                    )
                )
        return violations

    def _duplicate_id_violations(
        self,
        *,
        label: str,
        values: list[str],
    ) -> list[ProposalValidationViolation]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for value in values:
            if value in seen:
                duplicates.add(value)
            seen.add(value)
        if not duplicates:
            return []
        return [
            ProposalValidationViolation(
                layer=ValidationLayer.SEMANTIC,
                location=label,
                message=f"duplicates ids: {', '.join(sorted(duplicates))}",
                repairable=False,
            )
        ]


def build_minimal_repair_prompt(
    *,
    previous_output: str,
    violations: tuple[ProposalValidationViolation, ...],
) -> str:
    violation_lines = "\n".join(f"- {violation.format()}" for violation in violations)
    return (
        "Your previous final output failed validation.\n"
        "Return only corrected JSON. Do not add commentary.\n\n"
        "Violations:\n"
        f"{violation_lines}\n\n"
        "Previous final output:\n"
        f"{previous_output}\n"
    )


def _format_location(location: object) -> str:
    if isinstance(location, tuple):
        return ".".join(str(part) for part in location) or "<root>"
    return str(location) if location else "<root>"


def _proposal_text_fields(proposal: ProposalBundle) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = [("summary_draft", proposal.summary_draft)]
    for index, candidate in enumerate(proposal.evidence_candidates):
        fields.append((f"evidence_candidates[{index}].title", candidate.title))
        fields.append(
            (
                f"evidence_candidates[{index}].extraction_note",
                candidate.extraction_note,
            )
        )
    for index, claim in enumerate(proposal.claims):
        fields.append((f"claims[{index}].text", claim.text))
    for index, argument in enumerate(proposal.arguments):
        fields.append((f"arguments[{index}].rationale", argument.rationale))
    for index, hypothesis in enumerate(proposal.challenger_hypotheses):
        fields.append((f"challenger_hypotheses[{index}].title", hypothesis.title))
        fields.append(
            (f"challenger_hypotheses[{index}].statement", hypothesis.statement)
        )
    for index, conflict in enumerate(proposal.conflict_assessments):
        fields.append((f"conflict_assessments[{index}].summary", conflict.summary))
    for index, revision in enumerate(proposal.revision_proposals):
        fields.append((f"revision_proposals[{index}].rationale", revision.rationale))
    for index, action in enumerate(proposal.next_actions):
        fields.append((f"next_actions[{index}].description", action.description))
    return fields
