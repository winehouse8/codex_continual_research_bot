from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from codex_continual_research_bot.ux_contracts import (
    CliCommandSpec,
    GraphExportArtifact,
    UXReadModelBundle,
    canonical_json,
    command_prefix,
    extract_crb_examples,
    render_human_topic_summary,
)


ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
REQUIRED_COMMAND_IDS = {
    "init",
    "doctor",
    "topic.create",
    "topic.list",
    "topic.show",
    "run.start",
    "run.status",
    "run.resume",
    "queue.list",
    "queue.retry",
    "queue.dead-letter",
    "memory.snapshot",
    "memory.conflicts",
    "memory.hypotheses",
    "graph.export",
    "graph.view",
    "ops.health",
    "ops.audit",
    "ops.replay",
}


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


def test_cli_command_spec_matches_snapshot() -> None:
    payload = load_fixture("cli_command_spec.json")
    parsed = CliCommandSpec.model_validate(payload)

    assert canonical_json(parsed.model_dump(mode="json")) == (
        FIXTURES_DIR / "cli_command_spec.json"
    ).read_text()


def test_cli_command_spec_preserves_backend_authority_boundary() -> None:
    spec = CliCommandSpec.model_validate(load_fixture("cli_command_spec.json"))

    assert {command.command_id for command in spec.commands} == REQUIRED_COMMAND_IDS
    mutating_commands = [
        command for command in spec.commands if command.state_mutation.value != "none"
    ]
    assert mutating_commands
    assert all("backend" in command.authority_boundary.lower() for command in mutating_commands)
    assert all(
        "direct graph write" not in command.authority_boundary.lower()
        for command in spec.commands
    )


def test_cli_command_spec_rejects_mutating_direct_graph_write() -> None:
    payload = load_fixture("cli_command_spec.json")
    mutating_command = next(
        command for command in payload["commands"] if command["state_mutation"] != "none"
    )
    mutating_command["authority_boundary"] = "Performs a direct graph write."

    with pytest.raises(ValidationError):
        CliCommandSpec.model_validate(payload)


def test_json_output_fixture_parses_as_ux_read_model_bundle() -> None:
    payload = load_fixture("ux_read_models.json")
    parsed = UXReadModelBundle.model_validate(payload)

    assert parsed.topic.current_best_hypotheses
    assert parsed.topic.active_conflicts
    assert parsed.run.backend_state_update_applied is True
    assert "not a source of truth" in parsed.memory.visualization_notice.lower()
    assert canonical_json(parsed.model_dump(mode="json")) == (
        FIXTURES_DIR / "ux_read_models.json"
    ).read_text()


def test_completed_run_output_rejects_hidden_backend_state_update() -> None:
    payload = load_fixture("ux_read_models.json")
    payload["run"]["backend_state_update"] = None

    with pytest.raises(ValidationError):
        UXReadModelBundle.model_validate(payload)


def test_human_summary_matches_golden_snapshot() -> None:
    bundle = UXReadModelBundle.model_validate(load_fixture("ux_read_models.json"))
    rendered = render_human_topic_summary(bundle)

    assert rendered == (FIXTURES_DIR / "human_summary_topic_show.txt").read_text()
    assert "Active conflicts:" in rendered
    assert "Uncertainty:" in rendered
    assert "Backend state update:" in rendered
    assert "- Applied: yes" in rendered
    assert "not a source of truth" in rendered


def test_human_summary_distinguishes_unapplied_backend_update() -> None:
    payload = load_fixture("ux_read_models.json")
    payload["run"]["backend_state_update_applied"] = False
    bundle = UXReadModelBundle.model_validate(payload)

    rendered = render_human_topic_summary(bundle)

    assert "- Required: yes" in rendered
    assert "- Applied: no" in rendered


def test_graph_export_fixture_parses_and_round_trips() -> None:
    payload = load_fixture("graph_export.json")
    parsed = GraphExportArtifact.model_validate(payload)
    node_types = {node.node_type.value for node in parsed.nodes}

    assert parsed.authority_notice
    assert {"hypothesis", "evidence", "conflict"} <= node_types
    assert "not a source of truth" in parsed.authority_notice.lower()
    assert canonical_json(parsed.model_dump(mode="json")) == (
        FIXTURES_DIR / "graph_export.json"
    ).read_text()


def test_graph_export_rejects_missing_authority_notice() -> None:
    payload = load_fixture("graph_export.json")
    payload["authority_notice"] = "Visualization export."

    with pytest.raises(ValidationError):
        GraphExportArtifact.model_validate(payload)


def test_graph_export_rejects_edges_to_missing_nodes() -> None:
    payload = load_fixture("graph_export.json")
    payload["edges"][0]["target_node_id"] = "missing_node"

    with pytest.raises(ValidationError):
        GraphExportArtifact.model_validate(payload)


def test_readme_command_examples_are_declared_by_command_spec() -> None:
    readme = (ROOT / "README.md").read_text()
    examples = extract_crb_examples(readme)
    spec = CliCommandSpec.model_validate(load_fixture("cli_command_spec.json"))
    declared_examples = {
        example for command in spec.commands for example in command.examples
    }
    declared_prefixes = {
        command_prefix(example) for command in spec.commands for example in command.examples
    }

    assert examples
    assert set(examples) <= declared_examples
    assert {command_prefix(example) for example in examples} == declared_prefixes
