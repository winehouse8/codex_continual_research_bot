CREATE TABLE operation_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_operation_audit_subject
ON operation_audit_events(scope, subject_id, created_at ASC);

CREATE TRIGGER operation_audit_events_no_update
BEFORE UPDATE ON operation_audit_events
BEGIN
    SELECT RAISE(ABORT, 'operation_audit_events are append-only');
END;

CREATE TRIGGER operation_audit_events_no_delete
BEFORE DELETE ON operation_audit_events
BEGIN
    SELECT RAISE(ABORT, 'operation_audit_events are append-only');
END;

CREATE TABLE operator_alerts (
    id TEXT PRIMARY KEY,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    topic_id TEXT,
    queue_item_id TEXT,
    run_id TEXT,
    session_id TEXT,
    detail TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_operator_alerts_type_created
ON operator_alerts(alert_type, created_at DESC);
