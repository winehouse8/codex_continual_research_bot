ALTER TABLE runs ADD COLUMN snapshot_version INTEGER;

CREATE TABLE topic_snapshots (
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    snapshot_version INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(topic_id, snapshot_version)
);

CREATE INDEX idx_topic_snapshots_latest
ON topic_snapshots(topic_id, snapshot_version DESC);
