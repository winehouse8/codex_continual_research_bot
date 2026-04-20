"""Lease issuance guard for Codex execution sessions."""

from __future__ import annotations

from datetime import datetime

from codex_continual_research_bot.persistence import (
    DuplicateSessionLeaseError,
    SQLitePersistenceLedger,
    SessionLeaseRecord,
)


class SessionLeaseConflictError(RuntimeError):
    """Raised when another non-expired lease already owns the session."""


class SessionLeaseStore:
    """Small facade that keeps single-active-lease semantics explicit."""

    def __init__(self, ledger: SQLitePersistenceLedger) -> None:
        self._ledger = ledger

    def acquire(
        self,
        *,
        session_id: str,
        principal_id: str,
        purpose: str,
        holder: str,
        host_id: str,
        ttl_seconds: int,
        lease_id: str | None = None,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> SessionLeaseRecord:
        try:
            return self._ledger.acquire_session_lease(
                session_id=session_id,
                principal_id=principal_id,
                purpose=purpose,
                holder=holder,
                host_id=host_id,
                ttl_seconds=ttl_seconds,
                lease_id=lease_id,
                run_id=run_id,
                now=now,
            )
        except DuplicateSessionLeaseError as exc:
            raise SessionLeaseConflictError(
                f"session {session_id} already has an active lease"
            ) from exc

    def release(self, *, session_id: str, lease_id: str | None = None) -> bool:
        return self._ledger.release_session_lease(
            session_id=session_id,
            lease_id=lease_id,
        )
