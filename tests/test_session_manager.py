from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from codex_continual_research_bot.codex_app_server_inspector import (
    AuthJsonInspection,
    CodexAppServerInspector,
)
from codex_continual_research_bot.contracts import (
    FailureCode,
    SessionInspectResult,
    SessionState,
    VerificationLevel,
    derive_principal_fingerprint,
)
from codex_continual_research_bot.credential_locator import (
    CredentialLocatorError,
    credential_locator_for_principal,
)
from codex_continual_research_bot.persistence import SQLitePersistenceLedger
from codex_continual_research_bot.session_lease_store import SessionLeaseConflictError
from codex_continual_research_bot.session_manager import SessionManager, SessionPolicyError


NOW = datetime(2026, 4, 19, 11, 5, tzinfo=timezone.utc)
LAST_REFRESH = datetime(2026, 4, 19, 10, 45, tzinfo=timezone.utc)


@dataclass
class FakeAppServerClient:
    email: str = "researcher@example.com"
    account_type: str = "chatgpt"
    plan_type: str = "max"
    workspace_id: str = "ws_123456"
    trusted_project_paths: tuple[str, ...] = ()

    def account_read(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "type": self.account_type,
            "planType": self.plan_type,
            "requiresOpenaiAuth": False,
        }

    def config_read(self) -> dict[str, Any]:
        return {
            "forced_login_method": "chatgpt",
            "forced_chatgpt_workspace_id": self.workspace_id,
            "trusted_project_paths": list(self.trusted_project_paths),
        }


def fingerprint(*, email: str = "researcher@example.com", workspace_id: str = "ws_123456") -> str:
    return derive_principal_fingerprint(
        email=email,
        account_type="chatgpt",
        workspace_id=workspace_id,
    )


def make_paths(tmp_path: Path, *, principal_fingerprint: str | None = None) -> tuple[str, str]:
    fp = principal_fingerprint or fingerprint()
    base_dir = tmp_path / "runner"
    locator = credential_locator_for_principal(
        base_dir=base_dir,
        principal_fingerprint=fp,
    )
    workspace_root = base_dir / "principals" / fp / "worktrees" / "codex_continual_research_bot"
    return locator, str(workspace_root.resolve())


def make_ledger(tmp_path: Path) -> SQLitePersistenceLedger:
    ledger = SQLitePersistenceLedger(tmp_path / "sessions.sqlite3")
    ledger.initialize()
    return ledger


def make_inspection(
    tmp_path: Path,
    *,
    session_id: str = "sess_001",
    principal_id: str = "user_01",
    email: str = "researcher@example.com",
    workspace_id: str = "ws_123456",
    expected_workspace_id: str | None = "ws_123456",
    last_refresh: datetime | None = LAST_REFRESH,
    locator_override: str | None = None,
) -> SessionInspectResult:
    fp = fingerprint(email=email, workspace_id=workspace_id)
    locator, workspace_root = make_paths(tmp_path, principal_fingerprint=fp)
    client = FakeAppServerClient(
        email=email,
        workspace_id=workspace_id,
        trusted_project_paths=(workspace_root,),
    )
    inspector = CodexAppServerInspector(
        client=client,
        stale_after=timedelta(hours=24),
    )
    return inspector.inspect(
        session_id=session_id,
        principal_id=principal_id,
        purpose="scheduled_run",
        host_id="runner-seoul-01",
        credential_locator=locator_override or locator,
        workspace_root=workspace_root,
        expected_workspace_id=expected_workspace_id,
        auth_json=AuthJsonInspection(
            auth_mode="chatgpt",
            last_refresh=last_refresh,
            has_access_token=True,
            has_id_token=True,
            has_refresh_token=True,
        ),
        now=NOW,
    )


def test_happy_path_interactive_bootstrap_records_verified_session_and_lease(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    manager = SessionManager(ledger)
    inspection = make_inspection(tmp_path)

    manager.bootstrap_interactive_session(inspection)
    lease = manager.acquire_execution_lease(
        session_id="sess_001",
        purpose="scheduled_run",
        holder="worker-a",
        host_id="runner-seoul-01",
        ttl_seconds=300,
        run_id="run_001",
        now=NOW,
    )

    row = ledger.fetch_session_record("sess_001")
    assert row is not None
    assert row["state"] == SessionState.ACTIVE.value
    assert row["verification_level"] == VerificationLevel.ACCOUNT_AND_CONFIG.value
    assert row["account_fingerprint"] == inspection.principal_fingerprint
    assert row["credential_locator"] == inspection.credential_locator
    assert "auth.json" in row["credential_locator"]
    assert lease.session_id == "sess_001"
    assert lease.principal_id == "user_01"


def test_account_read_principal_fingerprint_is_verified(tmp_path: Path) -> None:
    inspection = make_inspection(tmp_path)

    assert inspection.principal_fingerprint == fingerprint()
    assert inspection.checks.principal_match is True
    assert inspection.state == SessionState.ACTIVE


def test_workspace_mismatch_rejects_bootstrap(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    manager = SessionManager(ledger)
    inspection = make_inspection(
        tmp_path,
        workspace_id="ws_other",
        expected_workspace_id="ws_123456",
    )

    with pytest.raises(SessionPolicyError) as excinfo:
        manager.bootstrap_interactive_session(inspection)

    assert excinfo.value.failure_code == FailureCode.WORKSPACE_MISMATCH
    assert ledger.fetch_session_record("sess_001") is None


def test_principal_mismatch_rejects_existing_session_update(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    manager = SessionManager(ledger)
    manager.bootstrap_interactive_session(make_inspection(tmp_path))

    changed_principal = make_inspection(
        tmp_path,
        session_id="sess_001",
        email="other@example.com",
    )

    with pytest.raises(SessionPolicyError) as excinfo:
        manager.update_from_inspection(changed_principal)

    assert excinfo.value.failure_code == FailureCode.PRINCIPAL_MISMATCH


def test_stale_session_expiry_blocks_lease_and_marks_reauth_required(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    manager = SessionManager(ledger)
    manager.bootstrap_interactive_session(make_inspection(tmp_path))
    stale = make_inspection(
        tmp_path,
        session_id="sess_001",
        last_refresh=NOW - timedelta(days=2),
    )

    with pytest.raises(SessionPolicyError) as excinfo:
        manager.update_from_inspection(stale)

    assert excinfo.value.failure_code == FailureCode.STALE_SESSION
    row = ledger.fetch_session_record("sess_001")
    assert row is not None
    assert row["state"] == SessionState.REAUTH_REQUIRED.value
    assert row["last_failure_code"] == FailureCode.STALE_SESSION.value


def test_duplicate_concurrent_lease_rejected(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    manager = SessionManager(ledger)
    manager.bootstrap_interactive_session(make_inspection(tmp_path))
    manager.acquire_execution_lease(
        session_id="sess_001",
        purpose="scheduled_run",
        holder="worker-a",
        host_id="runner-seoul-01",
        ttl_seconds=300,
        now=NOW,
    )

    with pytest.raises(SessionLeaseConflictError):
        manager.acquire_execution_lease(
            session_id="sess_001",
            purpose="interactive_run",
            holder="worker-b",
            host_id="runner-seoul-01",
            ttl_seconds=300,
            now=NOW + timedelta(seconds=1),
        )


def test_codex_home_isolation_leakage_rejected(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    manager = SessionManager(ledger)
    wrong_locator, _ = make_paths(
        tmp_path,
        principal_fingerprint=fingerprint(email="other@example.com"),
    )
    inspection = make_inspection(tmp_path, locator_override=wrong_locator)

    with pytest.raises(CredentialLocatorError):
        manager.bootstrap_interactive_session(inspection)


def test_copied_credential_continuity_fallback_cannot_bootstrap_active_session(
    tmp_path: Path,
) -> None:
    ledger = make_ledger(tmp_path)
    manager = SessionManager(ledger)
    copied = make_inspection(tmp_path).model_copy(
        update={"verification_level": VerificationLevel.AUTH_JSON_ONLY}
    )

    with pytest.raises(SessionPolicyError) as excinfo:
        manager.bootstrap_interactive_session(copied)

    assert excinfo.value.failure_code == FailureCode.EXECUTION_POLICY_REJECTED
    recorded = manager.record_continuity_fallback(copied)
    row = ledger.fetch_session_record("sess_001")
    assert row is not None
    assert recorded.state == SessionState.RENEWAL_SESSION
    assert row["state"] == SessionState.RENEWAL_SESSION.value
