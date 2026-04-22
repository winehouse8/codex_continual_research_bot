CREATE TABLE worker_loops (
    loop_id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    worker_id TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    stopped_at TEXT,
    stop_reason TEXT,
    iteration_count INTEGER NOT NULL DEFAULT 0,
    consecutive_no_yield INTEGER NOT NULL DEFAULT 0,
    malformed_proposal_streak INTEGER NOT NULL DEFAULT 0,
    last_queue_item_id TEXT,
    last_run_id TEXT,
    last_graph_digest TEXT,
    last_meaningful_change TEXT,
    yield_history_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_worker_loops_state_topic
ON worker_loops(state, topic_id);

CREATE UNIQUE INDEX idx_worker_loops_one_running_topic
ON worker_loops(topic_id)
WHERE state = 'running';

CREATE TABLE worker_loop_iterations (
    loop_id TEXT NOT NULL REFERENCES worker_loops(loop_id) ON DELETE CASCADE,
    iteration INTEGER NOT NULL,
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    queue_item_id TEXT,
    run_id TEXT,
    yielded INTEGER NOT NULL,
    yield_reason TEXT NOT NULL,
    graph_digest_before TEXT,
    graph_digest_after TEXT,
    node_count_before INTEGER,
    node_count_after INTEGER,
    edge_count_before INTEGER,
    edge_count_after INTEGER,
    queue_state TEXT,
    failure_code TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(loop_id, iteration)
);

CREATE TRIGGER worker_loop_iterations_no_update
BEFORE UPDATE ON worker_loop_iterations
BEGIN
    SELECT RAISE(ABORT, 'worker_loop_iterations are append-only');
END;

CREATE TRIGGER worker_loop_iterations_no_delete
BEFORE DELETE ON worker_loop_iterations
BEGIN
    SELECT RAISE(ABORT, 'worker_loop_iterations are append-only');
END;
