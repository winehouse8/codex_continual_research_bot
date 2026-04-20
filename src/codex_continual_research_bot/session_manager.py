"""Session manager for user-owned Codex auth and execution leases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from codex_continual_research_bot.contracts import (
    FailureCode,
    SessionInspectResult,
    SessionState,
    VerificationLevel,
)
from codex_continual_research_bot.credential_locator import (
    validate_credential_binding,
)
from codex_continual_research_bot.persistence import SQLitePersistenceLedger, SessionLeaseRecord
from codex_continual_research_bot.session_lease_store import SessionLeaseStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionPolicyError(RuntimeError):
    """Raised when auth/session policy would otherwise fail open."""

    def __init__(self, *, failure_code: FailureCode, detail: str) -> None:
        super().__init__(detail)
        self.failure_code = failure_code
        self.detail = detail


@dataclass(frozen=True)
class SessionManagerConfig:
    max_validation_age: timedelta = timedelta(hours=24)


class SessionManager:
    """Owns bootstrap, inspect persistence, and pre-execution lease gating."""

    def __init__(
        self,
        ledger: SQLitePersistenceLedger,
        *,
        lease_store: SessionLeaseStore | None = None,
        config: SessionManagerConfig | None = None,
    ) -> None:
        self._ledger = ledger
        self._lease_store = lease_store or SessionLeaseStore(ledger)
        self._config = config or SessionManagerConfig()

    def bootstrap_interactive_session(
        self,
        inspection: SessionInspectResult,
    ) -> SessionInspectResult:
        self._validate_bootstrap_inspection(inspection)
        resolved = validate_credential_binding(
            locator=inspection.credential_locator,
            principal_fingerprint=inspection.principal_fingerprint,
            workspace_root=inspection.workspace_root,
        )
        self._ledger.record_session_inspection(
            inspection,
            codex_home=str(resolved.codex_home),
        )
        self._ledger.append_session_event(
            session_id=inspection.session_id,
            event_type="session.bootstrap_verified",
            payload={
                "principal_id": inspection.principal_id,
                "host_id": inspection.host_id,
                "workspace_root": inspection.workspace_root,
                "verification_level": inspection.verification_level.value,
            },
            created_at=inspection.inspected_at,
        )
        return inspection

    def record_continuity_fallback(
        self,
        inspection: SessionInspectResult,
    ) -> SessionInspectResult:
        if inspection.verification_level != VerificationLevel.AUTH_JSON_ONLY:
            raise SessionPolicyError(
                failure_code=FailureCode.EXECUTION_POLICY_REJECTED,
                detail="continuity fallback is only for auth-json-continuity-only inspection",
            )
        downgraded = inspection.model_copy(update={"state": SessionState.RENEWAL_SESSION})
        resolved = validate_credential_binding(
            locator=downgraded.credential_locator,
            principal_fingerprint=downgraded.principal_fingerprint,
            workspace_root=downgraded.workspace_root,
        )
        self._ledger.record_session_inspection(
            downgraded,
            codex_home=str(resolved.codex_home),
        )
        self._ledger.append_session_event(
            session_id=downgraded.session_id,
            event_type="session.continuity_fallback_recorded",
            payload={"verification_level": downgraded.verification_level.value},
            created_at=downgraded.inspected_at,
        )
        return downgraded

    def update_from_inspection(self, inspection: SessionInspectResult) -> None:
        existing = self._ledger.fetch_session_record(inspection.session_id)
        if existing is not None:
            self._validate_existing_binding(existing=existing, inspection=inspection)
        if inspection.state != SessionState.ACTIVE:
            if existing is not None:
                self._ledger.transition_session_state(
                    session_id=inspection.session_id,
                    state=SessionState.REAUTH_REQUIRED,
                    failure_code=self._failure_for_checks(inspection).value,
                    now=inspection.inspected_at,
                )
            raise SessionPolicyError(
                failure_code=self._failure_for_checks(inspection),
                detail="session inspection did not satisfy all active checks",
            )
        self._validate_bootstrap_inspection(inspection)
        resolved = validate_credential_binding(
            locator=inspection.credential_locator,
            principal_fingerprint=inspection.principal_fingerprint,
            workspace_root=inspection.workspace_root,
        )
        self._ledger.record_session_inspection(
            inspection,
            codex_home=str(resolved.codex_home),
        )
        self._ledger.append_session_event(
            session_id=inspection.session_id,
            event_type="session.inspect_verified",
            payload={
                "principal_id": inspection.principal_id,
                "host_id": inspection.host_id,
                "workspace_root": inspection.workspace_root,
                "verification_level": inspection.verification_level.value,
            },
            created_at=inspection.inspected_at,
        )

    def acquire_execution_lease(
        self,
        *,
        session_id: str,
        purpose: str,
        holder: str,
        host_id: str,
        ttl_seconds: int,
        run_id: str | None = None,
        lease_id: str | None = None,
        now: datetime | None = None,
    ) -> SessionLeaseRecord:
        record = self._ledger.fetch_session_record(session_id)
        if record is None:
            raise SessionPolicyError(
                failure_code=FailureCode.AUTH_MATERIAL_MISSING,
                detail=f"session {session_id} does not exist",
            )
        self._validate_leaseable_record(record=record, host_id=host_id, now=now)
        return self._lease_store.acquire(
            session_id=session_id,
            principal_id=record["principal_id"],
            purpose=purpose,
            holder=holder,
            host_id=host_id,
            ttl_seconds=ttl_seconds,
            lease_id=lease_id,
            run_id=run_id,
            now=now,
        )

    def release_execution_lease(self, *, session_id: str, lease_id: str | None = None) -> bool:
        return self._lease_store.release(session_id=session_id, lease_id=lease_id)

    def _validate_bootstrap_inspection(self, inspection: SessionInspectResult) -> None:
        if inspection.verification_level != VerificationLevel.ACCOUNT_AND_CONFIG:
            raise SessionPolicyError(
                failure_code=FailureCode.EXECUTION_POLICY_REJECTED,
                detail="new bootstrap requires account/read and config/read verification",
            )
        if inspection.state != SessionState.ACTIVE:
            raise SessionPolicyError(
                failure_code=self._failure_for_checks(inspection),
                detail="session is not active after inspection",
            )
        if not all(
            (
                inspection.checks.principal_match,
                inspection.checks.workspace_match,
                inspection.checks.trust_configured,
                inspection.checks.session_fresh,
            )
        ):
            raise SessionPolicyError(
                failure_code=self._failure_for_checks(inspection),
                detail="session inspection failed one or more mandatory checks",
            )
        if inspection.workspace_root not in inspection.config.trusted_project_paths:
            raise SessionPolicyError(
                failure_code=FailureCode.WORKSPACE_MISMATCH,
                detail="workspace_root is not trusted in the session config",
            )

    def _validate_existing_binding(
        self,
        *,
        existing: dict[str, object],
        inspection: SessionInspectResult,
    ) -> None:
        expected = {
            "principal_id": inspection.principal_id,
            "account_fingerprint": inspection.principal_fingerprint,
            "workspace_id": inspection.workspace_id,
            "workspace_root": inspection.workspace_root,
        }
        for key, value in expected.items():
            persisted = existing.get(key)
            if persisted and persisted != value:
                failure = (
                    FailureCode.PRINCIPAL_MISMATCH
                    if key in {"principal_id", "account_fingerprint"}
                    else FailureCode.WORKSPACE_MISMATCH
                )
                raise SessionPolicyError(
                    failure_code=failure,
                    detail=f"persisted {key} does not match inspection result",
                )

    def _validate_leaseable_record(
        self,
        *,
        record: dict[str, object],
        host_id: str,
        now: datetime | None,
    ) -> None:
        if record["state"] != SessionState.ACTIVE.value:
            raise SessionPolicyError(
                failure_code=FailureCode.AUTH_MATERIAL_MISSING,
                detail="session is not active",
            )
        if record["verification_level"] != VerificationLevel.ACCOUNT_AND_CONFIG.value:
            raise SessionPolicyError(
                failure_code=FailureCode.EXECUTION_POLICY_REJECTED,
                detail="lease requires account/read and config/read verification",
            )
        if record["host_id"] != host_id:
            raise SessionPolicyError(
                failure_code=FailureCode.RUNNER_HOST_UNAVAILABLE,
                detail="lease host does not match session host binding",
            )
        last_validated = _parse_timestamp(record.get("last_validated_at"))
        current = now or _utcnow()
        if last_validated is None or current - last_validated > self._config.max_validation_age:
            self._ledger.transition_session_state(
                session_id=str(record["session_id"]),
                state=SessionState.REAUTH_REQUIRED,
                failure_code=FailureCode.STALE_SESSION.value,
                now=current,
            )
            raise SessionPolicyError(
                failure_code=FailureCode.STALE_SESSION,
                detail="session validation is stale; healthcheck or renewal is required",
            )
        validate_credential_binding(
            locator=str(record["credential_locator"]),
            principal_fingerprint=str(record["account_fingerprint"]),
            workspace_root=str(record["workspace_root"]),
        )

    def _failure_for_checks(self, inspection: SessionInspectResult) -> FailureCode:
        if not inspection.checks.principal_match:
            return FailureCode.PRINCIPAL_MISMATCH
        if not inspection.checks.workspace_match or not inspection.checks.trust_configured:
            return FailureCode.WORKSPACE_MISMATCH
        if not inspection.checks.session_fresh:
            return FailureCode.STALE_SESSION
        return FailureCode.EXECUTION_POLICY_REJECTED


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
