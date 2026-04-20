"""Credential locator policy for per-principal Codex auth isolation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse


class CredentialLocatorError(RuntimeError):
    """Raised when a credential locator would break principal isolation."""


@dataclass(frozen=True)
class ResolvedCredentialLocator:
    uri: str
    auth_json_path: Path
    codex_home: Path
    principal_root: Path


def credential_locator_for_principal(*, base_dir: Path, principal_fingerprint: str) -> str:
    """Build the v1 file locator under a principal-specific CODEX_HOME."""

    if not principal_fingerprint.strip():
        raise CredentialLocatorError("principal fingerprint is required")
    auth_path = (
        base_dir
        / "principals"
        / principal_fingerprint
        / "codex-home"
        / "auth.json"
    )
    return auth_path.resolve().as_uri()


def resolve_credential_locator(locator: str) -> ResolvedCredentialLocator:
    """Resolve a supported credential locator without reading secret material."""

    parsed = urlparse(locator)
    if parsed.scheme not in {"", "file"}:
        raise CredentialLocatorError(
            "only file credential locators are supported for shared runner v1"
        )
    if parsed.scheme == "file":
        raw_path = unquote(parsed.path)
    else:
        raw_path = locator
    auth_path = Path(raw_path).expanduser().resolve()
    if auth_path.name != "auth.json":
        raise CredentialLocatorError("file credential locator must point to auth.json")
    codex_home = auth_path.parent
    if codex_home.name != "codex-home":
        raise CredentialLocatorError("auth.json must live directly under codex-home")
    return ResolvedCredentialLocator(
        uri=locator,
        auth_json_path=auth_path,
        codex_home=codex_home,
        principal_root=codex_home.parent,
    )


def validate_credential_binding(
    *,
    locator: str,
    principal_fingerprint: str,
    workspace_root: str | Path,
) -> ResolvedCredentialLocator:
    """Ensure auth, config, and workspace paths bind to one principal root."""

    resolved = resolve_credential_locator(locator)
    workspace_path = Path(workspace_root).expanduser().resolve()
    if resolved.principal_root.name != principal_fingerprint:
        raise CredentialLocatorError(
            "credential locator principal root does not match principal fingerprint"
        )
    expected_worktrees = resolved.principal_root / "worktrees"
    if not _is_relative_to(workspace_path, expected_worktrees):
        raise CredentialLocatorError(
            "workspace_root must live under the same principal worktrees directory"
        )
    if _is_relative_to(workspace_path, resolved.codex_home):
        raise CredentialLocatorError("workspace_root must not live inside CODEX_HOME")
    return resolved


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
