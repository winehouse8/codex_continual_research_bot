CREATE TABLE topics (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE queue_items (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    requested_run_id TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    priority INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    available_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    claimed_by TEXT,
    claimed_at TEXT,
    last_failure_code TEXT,
    last_failure_detail TEXT,
    last_failure_retryable INTEGER,
    last_failure_human_review INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_queue_claimable
ON queue_items(state, available_at, priority DESC, created_at ASC);

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    queue_item_id TEXT NOT NULL UNIQUE REFERENCES queue_items(id) ON DELETE RESTRICT,
    idempotency_key TEXT NOT NULL UNIQUE REFERENCES idempotency_keys(idempotency_key),
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    run_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE run_events (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id, seq)
);

CREATE TRIGGER run_events_no_update
BEFORE UPDATE ON run_events
BEGIN
    SELECT RAISE(ABORT, 'run_events are append-only');
END;

CREATE TRIGGER run_events_no_delete
BEFORE DELETE ON run_events
BEGIN
    SELECT RAISE(ABORT, 'run_events are append-only');
END;

CREATE TABLE session_ledger (
    session_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    state TEXT NOT NULL,
    credential_locator TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE session_leases (
    session_id TEXT PRIMARY KEY REFERENCES session_ledger(session_id) ON DELETE CASCADE,
    holder TEXT NOT NULL,
    leased_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES session_ledger(session_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER session_events_no_update
BEFORE UPDATE ON session_events
BEGIN
    SELECT RAISE(ABORT, 'session_events are append-only');
END;

CREATE TRIGGER session_events_no_delete
BEFORE DELETE ON session_events
BEGIN
    SELECT RAISE(ABORT, 'session_events are append-only');
END;

CREATE TABLE scheduler_policies (
    topic_id TEXT PRIMARY KEY REFERENCES topics(id) ON DELETE CASCADE,
    policy_kind TEXT NOT NULL,
    cadence_minutes INTEGER NOT NULL,
    jitter_minutes INTEGER NOT NULL DEFAULT 0,
    next_run_after TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
