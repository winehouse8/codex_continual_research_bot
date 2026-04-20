"""Narrow app-server inspection surface for Codex session identity checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from codex_continual_research_bot.contracts import (
    AccountSnapshot,
    AuthJsonSnapshot,
    ConfigSnapshot,
    LoginMethod,
    SessionChecks,
    SessionInspectResult,
    SessionState,
    VerificationLevel,
    derive_principal_fingerprint,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CodexAppServerClient(Protocol):
    """Minimal protocol used by the backend; transport lifecycle stays hidden."""

    def account_read(self) -> dict[str, Any]:
        """Return the loopback app-server `account/read` result."""

    def config_read(self) -> dict[str, Any]:
        """Return the loopback app-server `config/read` result."""


@dataclass(frozen=True)
class AuthJsonInspection:
    auth_mode: str
    last_refresh: datetime | None
    has_access_token: bool
    has_id_token: bool
    has_refresh_token: bool


@dataclass(frozen=True)
class CodexAppServerInspector:
    client: CodexAppServerClient
    stale_after: timedelta = timedelta(hours=24)

    def inspect(
        self,
        *,
        session_id: str,
        principal_id: str,
        purpose: str,
        host_id: str,
        credential_locator: str,
        workspace_root: str,
        auth_json: AuthJsonInspection,
        expected_workspace_id: str | None = None,
        now: datetime | None = None,
    ) -> SessionInspectResult:
        inspected_at = now or _utcnow()
        account_payload = self.client.account_read()
        config_payload = self.client.config_read()
        account = AccountSnapshot(
            email=account_payload["email"],
            type=account_payload["type"],
            plan_type=account_payload["planType"],
            requires_openai_auth=account_payload["requiresOpenaiAuth"],
        )
        forced_workspace_id = config_payload["forced_chatgpt_workspace_id"]
        config = ConfigSnapshot(
            forced_login_method=config_payload["forced_login_method"],
            forced_chatgpt_workspace_id=forced_workspace_id,
            trusted_project_paths=list(config_payload["trusted_project_paths"]),
        )
        if auth_json.last_refresh is None:
            last_refresh = datetime.fromtimestamp(0, timezone.utc)
        else:
            last_refresh = auth_json.last_refresh
        auth_snapshot = AuthJsonSnapshot(
            auth_mode=auth_json.auth_mode,
            last_refresh=last_refresh,
            has_access_token=auth_json.has_access_token,
            has_id_token=auth_json.has_id_token,
            has_refresh_token=auth_json.has_refresh_token,
        )
        fingerprint = derive_principal_fingerprint(
            email=account.email,
            account_type=account.type,
            workspace_id=config.forced_chatgpt_workspace_id,
        )
        workspace_match = (
            expected_workspace_id is None
            or forced_workspace_id == expected_workspace_id
        )
        trust_configured = workspace_root in config.trusted_project_paths
        has_all_tokens = (
            auth_snapshot.has_access_token
            and auth_snapshot.has_id_token
            and auth_snapshot.has_refresh_token
        )
        session_fresh = (
            has_all_tokens
            and auth_snapshot.auth_mode == LoginMethod.CHATGPT
            and inspected_at - auth_snapshot.last_refresh <= self.stale_after
        )
        checks = SessionChecks(
            principal_match=True,
            workspace_match=workspace_match,
            trust_configured=trust_configured,
            session_fresh=session_fresh,
        )
        state = (
            SessionState.ACTIVE
            if all(
                (
                    checks.principal_match,
                    checks.workspace_match,
                    checks.trust_configured,
                    checks.session_fresh,
                )
            )
            else SessionState.REAUTH_REQUIRED
        )
        return SessionInspectResult(
            session_id=session_id,
            principal_id=principal_id,
            purpose=purpose,
            host_id=host_id,
            credential_locator=credential_locator,
            state=state,
            workspace_id=forced_workspace_id,
            workspace_root=workspace_root,
            verification_level=VerificationLevel.ACCOUNT_AND_CONFIG,
            login_method=LoginMethod.CHATGPT,
            principal_fingerprint=fingerprint,
            account=account,
            config=config,
            auth_json=auth_snapshot,
            checks=checks,
            inspected_at=inspected_at,
            last_validated_at=inspected_at,
            last_refreshed_at=auth_snapshot.last_refresh,
        )
