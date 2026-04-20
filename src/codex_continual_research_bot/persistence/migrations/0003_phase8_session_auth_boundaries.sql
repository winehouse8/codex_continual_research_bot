ALTER TABLE session_ledger ADD COLUMN provider TEXT NOT NULL DEFAULT 'openai-codex-chatgpt';
ALTER TABLE session_ledger ADD COLUMN host_id TEXT NOT NULL DEFAULT '';
ALTER TABLE session_ledger ADD COLUMN workspace_root TEXT NOT NULL DEFAULT '';
ALTER TABLE session_ledger ADD COLUMN account_fingerprint TEXT NOT NULL DEFAULT '';
ALTER TABLE session_ledger ADD COLUMN plan_type TEXT;
ALTER TABLE session_ledger ADD COLUMN verification_level TEXT NOT NULL DEFAULT 'auth-json-continuity-only';
ALTER TABLE session_ledger ADD COLUMN last_validated_at TEXT;
ALTER TABLE session_ledger ADD COLUMN last_refreshed_at TEXT;
ALTER TABLE session_ledger ADD COLUMN last_failure_code TEXT;
ALTER TABLE session_ledger ADD COLUMN last_failure_at TEXT;
ALTER TABLE session_ledger ADD COLUMN codex_home TEXT NOT NULL DEFAULT '';
ALTER TABLE session_ledger ADD COLUMN lease_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE session_leases ADD COLUMN lease_id TEXT;
ALTER TABLE session_leases ADD COLUMN principal_id TEXT;
ALTER TABLE session_leases ADD COLUMN purpose TEXT;
ALTER TABLE session_leases ADD COLUMN run_id TEXT;
ALTER TABLE session_leases ADD COLUMN host_id TEXT;
ALTER TABLE session_leases ADD COLUMN heartbeat_at TEXT;
ALTER TABLE session_leases ADD COLUMN released_at TEXT;

CREATE UNIQUE INDEX idx_session_leases_lease_id
ON session_leases(lease_id)
WHERE lease_id IS NOT NULL;

CREATE TABLE session_host_bindings (
    session_id TEXT NOT NULL REFERENCES session_ledger(session_id) ON DELETE CASCADE,
    host_id TEXT NOT NULL,
    workspace_root TEXT NOT NULL,
    codex_home TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(session_id, host_id)
);
