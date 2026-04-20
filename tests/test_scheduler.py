from __future__ import annotations

from datetime import datetime, timedelta, timezone

from codex_continual_research_bot.scheduler import (
    SchedulerPolicyEvaluator,
    TopicScheduleCandidate,
    competition_pressure_score,
)


def make_candidate(
    *,
    topic_id: str = "topic_001",
    next_run_after: datetime | None = None,
    current_best_hypothesis_count: int = 1,
    challenger_target_count: int = 1,
    recent_challenger_count: int = 1,
    recent_revision_count: int = 1,
    unresolved_conflict_count: int = 0,
    open_question_count: int = 0,
    queued_user_input_count: int = 0,
    support_challenge_imbalance: float = 0.0,
    consecutive_stagnant_runs: int = 0,
) -> TopicScheduleCandidate:
    return TopicScheduleCandidate(
        topic_id=topic_id,
        next_run_after=next_run_after
        or datetime(2026, 4, 19, tzinfo=timezone.utc),
        current_best_hypothesis_count=current_best_hypothesis_count,
        challenger_target_count=challenger_target_count,
        recent_challenger_count=recent_challenger_count,
        recent_revision_count=recent_revision_count,
        unresolved_conflict_count=unresolved_conflict_count,
        open_question_count=open_question_count,
        queued_user_input_count=queued_user_input_count,
        support_challenge_imbalance=support_challenge_imbalance,
        consecutive_stagnant_runs=consecutive_stagnant_runs,
    )


def test_repeated_run_stagnation_schedules_competition_refresh() -> None:
    now = datetime(2026, 4, 19, tzinfo=timezone.utc)
    candidate = make_candidate(
        next_run_after=now,
        recent_challenger_count=0,
        recent_revision_count=0,
        consecutive_stagnant_runs=2,
    )

    selected = SchedulerPolicyEvaluator().select_refresh_topics(
        [candidate],
        now=now,
    )

    assert [selection.topic_id for selection in selected] == ["topic_001"]
    assert selected[0].score >= 50
    assert "repeated run stagnation" in selected[0].reasons
    assert "no recent challenger generation" in selected[0].reasons


def test_scheduler_noops_when_competition_pressure_is_healthy() -> None:
    now = datetime(2026, 4, 19, tzinfo=timezone.utc)
    candidate = make_candidate(next_run_after=now)

    selected = SchedulerPolicyEvaluator().select_refresh_topics(
        [candidate],
        now=now,
    )

    assert selected == []
    assert competition_pressure_score(candidate).score == 0


def test_scheduler_ignores_freshness_until_topic_is_due() -> None:
    now = datetime(2026, 4, 19, tzinfo=timezone.utc)
    candidate = make_candidate(
        next_run_after=now + timedelta(minutes=10),
        recent_challenger_count=0,
        recent_revision_count=0,
        unresolved_conflict_count=3,
        consecutive_stagnant_runs=2,
    )

    selected = SchedulerPolicyEvaluator().select_refresh_topics(
        [candidate],
        now=now,
    )

    assert selected == []


def test_scheduler_prioritizes_user_backlog_and_conflict_pressure() -> None:
    now = datetime(2026, 4, 19, tzinfo=timezone.utc)
    low_pressure = make_candidate(
        topic_id="topic_low",
        next_run_after=now,
        recent_challenger_count=0,
        recent_revision_count=0,
    )
    high_pressure = make_candidate(
        topic_id="topic_high",
        next_run_after=now,
        recent_challenger_count=0,
        recent_revision_count=0,
        unresolved_conflict_count=2,
        queued_user_input_count=3,
        support_challenge_imbalance=0.75,
    )

    selected = SchedulerPolicyEvaluator().select_refresh_topics(
        [low_pressure, high_pressure],
        now=now,
    )

    assert [selection.topic_id for selection in selected] == [
        "topic_high",
        "topic_low",
    ]
    assert "queued user input backlog" in selected[0].reasons
    assert "unresolved conflicts" in selected[0].reasons
