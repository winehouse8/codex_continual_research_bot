"""Migration runner for the Phase 1 relational ledger schema."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
import sqlite3


@dataclass(frozen=True)
class Migration:
    version: str
    filename: str


MIGRATIONS = [
    Migration(version="0001_phase1_relational_ledger", filename="migrations/0001_phase1_relational_ledger.sql"),
    Migration(
        version="0002_phase3_topic_snapshot_orchestrator",
        filename="migrations/0002_phase3_topic_snapshot_orchestrator.sql",
    ),
    Migration(
        version="0003_phase8_session_auth_boundaries",
        filename="migrations/0003_phase8_session_auth_boundaries.sql",
    ),
    Migration(
        version="0004_phase9_interactive_run_path",
        filename="migrations/0004_phase9_interactive_run_path.sql",
    ),
]


def apply_migrations(connection: sqlite3.Connection) -> list[str]:
    """Apply all pending migrations and return the applied versions."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied_versions = {
        row[0]
        for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
    }
    newly_applied: list[str] = []

    for migration in MIGRATIONS:
        if migration.version in applied_versions:
            continue

        sql = resources.files("codex_continual_research_bot.persistence").joinpath(migration.filename).read_text()
        with connection:
            connection.executescript(sql)
            connection.execute(
                "INSERT INTO schema_migrations(version) VALUES (?)",
                (migration.version,),
            )
        newly_applied.append(migration.version)

    return newly_applied
