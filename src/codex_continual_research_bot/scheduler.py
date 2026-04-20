"""Phase 4 scheduler selection driven by competition pressure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TopicScheduleCandidate:
    topic_id: str
    next_run_after: datetime
    current_best_hypothesis_count: int
    challenger_target_count: int
    recent_challenger_count: int
    recent_revision_count: int
    unresolved_conflict_count: int
    open_question_count: int
    queued_user_input_count: int
    support_challenge_imbalance: float
    consecutive_stagnant_runs: int


@dataclass(frozen=True)
class SchedulerSelection:
    topic_id: str
    score: float
    reasons: tuple[str, ...]


def competition_pressure_score(
    candidate: TopicScheduleCandidate,
) -> SchedulerSelection:
    score = 0.0
    reasons: list[str] = []

    if candidate.current_best_hypothesis_count <= 0:
        return SchedulerSelection(
            topic_id=candidate.topic_id,
            score=0.0,
            reasons=("no current-best hypothesis to attack",),
        )
    if candidate.challenger_target_count <= 0:
        score += 40
        reasons.append("no challenger target")
    if candidate.recent_challenger_count <= 0:
        score += 35
        reasons.append("no recent challenger generation")
    if candidate.recent_revision_count <= 0:
        score += 25
        reasons.append("no recent revision pressure")
    if candidate.unresolved_conflict_count > 0:
        score += min(30, candidate.unresolved_conflict_count * 10)
        reasons.append("unresolved conflicts")
    if candidate.open_question_count > 0:
        score += min(20, candidate.open_question_count * 5)
        reasons.append("open questions")
    if candidate.queued_user_input_count > 0:
        score += min(24, candidate.queued_user_input_count * 8)
        reasons.append("queued user input backlog")
    if candidate.support_challenge_imbalance > 0:
        score += min(20, candidate.support_challenge_imbalance * 20)
        reasons.append("support/challenge imbalance")
    if candidate.consecutive_stagnant_runs > 0:
        score += min(60, candidate.consecutive_stagnant_runs * 20)
        reasons.append("repeated run stagnation")

    return SchedulerSelection(
        topic_id=candidate.topic_id,
        score=score,
        reasons=tuple(reasons),
    )


class SchedulerPolicyEvaluator:
    """Selects refresh work only when due topics lack competition pressure."""

    def __init__(self, *, minimum_score: float = 50.0) -> None:
        self._minimum_score = minimum_score

    def select_refresh_topics(
        self,
        candidates: list[TopicScheduleCandidate],
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[SchedulerSelection]:
        current_time = now or _utcnow()
        selections = [
            competition_pressure_score(candidate)
            for candidate in candidates
            if candidate.next_run_after <= current_time
        ]
        selected = [
            selection
            for selection in selections
            if selection.score >= self._minimum_score
        ]
        selected.sort(key=lambda selection: (-selection.score, selection.topic_id))
        if limit is None:
            return selected
        return selected[:limit]
