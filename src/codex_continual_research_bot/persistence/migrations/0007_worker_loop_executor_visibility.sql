ALTER TABLE worker_loops ADD COLUMN executor_kind TEXT NOT NULL DEFAULT 'fixture';
ALTER TABLE worker_loops ADD COLUMN last_error TEXT;
ALTER TABLE worker_loop_iterations ADD COLUMN failure_detail TEXT;
