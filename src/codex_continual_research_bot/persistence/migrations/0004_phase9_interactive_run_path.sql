CREATE TABLE interactive_run_reports (
    report_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    trigger_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE REFERENCES idempotency_keys(idempotency_key),
    snapshot_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(topic_id, trigger_id)
);

CREATE INDEX idx_interactive_run_reports_topic_created
ON interactive_run_reports(topic_id, created_at DESC);

CREATE TABLE canonical_graph_writes (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    proposal_id TEXT NOT NULL,
    graph_digest TEXT NOT NULL,
    node_count INTEGER NOT NULL,
    edge_count INTEGER NOT NULL,
    graph_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
