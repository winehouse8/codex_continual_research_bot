"""Scheduled preflight job for Codex session health."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from codex_continual_research_bot.contracts import (
    FailureCode,
    SessionInspectResult,
    SessionState,
)
from codex_continual_research_bot.session_manager import SessionManager, SessionPolicyError


SessionInspectionLoader = Callable[[], SessionInspectResult]


@dataclass(frozen=True)
class SessionHealthcheckResult:
    session_id: str
    state: SessionState
    leaseable: bool
    failure_code: FailureCode | None = None
    failure_detail: str | None = None


class SessionHealthcheckJob:
    """Runs inspect before scheduled execution and records a fail-closed state."""

    def __init__(self, session_manager: SessionManager) -> None:
        self._session_manager = session_manager

    def run(self, load_inspection: SessionInspectionLoader) -> SessionHealthcheckResult:
        inspection = load_inspection()
        try:
            self._session_manager.update_from_inspection(inspection)
        except SessionPolicyError as exc:
            return SessionHealthcheckResult(
                session_id=inspection.session_id,
                state=SessionState.REAUTH_REQUIRED,
                leaseable=False,
                failure_code=exc.failure_code,
                failure_detail=exc.detail,
            )
        return SessionHealthcheckResult(
            session_id=inspection.session_id,
            state=SessionState.ACTIVE,
            leaseable=True,
        )
