from __future__ import annotations

import json
import re
import shlex
import threading
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from codex_continual_research_bot.cli import build_parser, main
from codex_continual_research_bot.cli_backend import LocalBackendGateway
from codex_continual_research_bot.cli_contracts import CliResult
from codex_continual_research_bot.web import create_web_server


ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"


def run_cli(argv: list[str], backend: LocalBackendGateway) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = main(argv, backend=backend, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def parsed_json(output: str) -> CliResult:
    return CliResult.model_validate(json.loads(output))


def load_sample() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "sample_topic_run.json").read_text())


def load_iso26262_sample() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "iso26262_sample_research_demo.json").read_text())


class RunningServer:
    def __init__(self, backend: LocalBackendGateway) -> None:
        self.server = create_web_server(backend=backend, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> RunningServer:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def fetch_json(base_url: str, path: str) -> dict[str, object]:
    with urlopen(f"{base_url}{path}", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_cli_help_exposes_first_run_path_and_result_locations() -> None:
    help_text = build_parser().format_help()

    assert "First run path:" in help_text
    assert "Result locations:" in help_text
    assert "crb run status <run-id> --json" in help_text
    assert "crb queue dead-letter" in help_text
    assert "crb graph export topic_codex_auth_boundary --format json" in help_text


def test_sample_topic_fixture_runs_end_to_end_and_generates_graph_walkthrough(
    tmp_path: Path,
) -> None:
    sample = load_sample()
    topic = sample["topic"]
    run = sample["run"]
    backend = LocalBackendGateway(db_path=tmp_path / "crb.sqlite3", workspace_root=tmp_path)

    assert run_cli(["init", "--json"], backend)[0] == 0
    assert run_cli(["doctor", "--json"], backend)[0] == 0
    assert (
        run_cli(
            [
                "topic",
                "create",
                topic["title"],
                "--objective",
                topic["objective"],
                "--json",
            ],
            backend,
        )[0]
        == 0
    )

    code, output, _ = run_cli(
        ["run", "start", topic["topic_id"], "--input", run["input"], "--json"],
        backend,
    )
    start = parsed_json(output)
    assert code == 0
    assert start.data["run_id"] == run["expected_run_id"]
    assert start.data["queue_item_id"] == run["expected_queue_item_id"]

    for argv in (
        ["topic", "show", topic["topic_id"], "--json"],
        ["run", "status", run["expected_run_id"], "--json"],
        ["queue", "list", "--topic", topic["topic_id"], "--json"],
        ["memory", "snapshot", topic["topic_id"], "--json"],
        ["memory", "conflicts", topic["topic_id"], "--json"],
        ["memory", "hypotheses", topic["topic_id"], "--json"],
        ["ops", "health", "--json"],
        ["ops", "audit", run["expected_run_id"], "--json"],
    ):
        code, output, _ = run_cli(argv, backend)
        result = parsed_json(output)
        assert code == 0
        assert result.ok is True

    artifact_paths = {
        "graph.json": tmp_path / "graph.json",
        "graph.dot": tmp_path / "graph.dot",
        "graph.mmd": tmp_path / "graph.mmd",
        "graph.html": tmp_path / "graph.html",
    }
    assert set(sample["expected_artifacts"]) == set(artifact_paths)

    assert (
        run_cli(
            [
                "graph",
                "export",
                topic["topic_id"],
                "--format",
                "json",
                "--output",
                str(artifact_paths["graph.json"]),
                "--json",
            ],
            backend,
        )[0]
        == 0
    )
    assert (
        run_cli(
            [
                "graph",
                "export",
                topic["topic_id"],
                "--format",
                "dot",
                "--output",
                str(artifact_paths["graph.dot"]),
                "--json",
            ],
            backend,
        )[0]
        == 0
    )
    assert (
        run_cli(
            [
                "graph",
                "export",
                topic["topic_id"],
                "--format",
                "mermaid",
                "--output",
                str(artifact_paths["graph.mmd"]),
                "--json",
            ],
            backend,
        )[0]
        == 0
    )
    assert (
        run_cli(
            [
                "graph",
                "view",
                topic["topic_id"],
                "--format",
                "html",
                "--output",
                str(artifact_paths["graph.html"]),
                "--json",
            ],
            backend,
        )[0]
        == 0
    )

    assert "not a source of truth" in artifact_paths["graph.json"].read_text()
    assert artifact_paths["graph.dot"].read_text().startswith("digraph crb_graph")
    assert artifact_paths["graph.mmd"].read_text().startswith("graph LR")
    assert "not a source of truth" in artifact_paths["graph.html"].read_text()


def test_tutorial_transcript_matches_actual_human_output(tmp_path: Path) -> None:
    sample = load_sample()
    topic = sample["topic"]
    run = sample["run"]
    backend = LocalBackendGateway(db_path=tmp_path / "crb.sqlite3", workspace_root=tmp_path)
    assert run_cli(["init"], backend)[0] == 0

    transcript_parts: list[str] = []
    for command in (
        f'crb topic create "{topic["title"]}" --objective "{topic["objective"]}"',
        f"crb topic show {topic['topic_id']}",
        f'crb run start {topic["topic_id"]} --input "{run["input"]}"',
        f"crb run status {run['expected_run_id']}",
        f"crb queue list --topic {topic['topic_id']}",
    ):
        argv = shlex.split(command)[1:]
        code, output, _ = run_cli(argv, backend)
        assert code == 0
        transcript_parts.append(f"$ {command}\n{output}")

    actual = "\n".join(transcript_parts)
    assert actual == (FIXTURES_DIR / "tutorial_transcript.txt").read_text()


def test_readme_first_user_docs_have_valid_local_links_and_required_sections() -> None:
    readme = (ROOT / "README.md").read_text()

    for heading in (
        "## Install And Start",
        "## CLI Quickstart",
        "## Graph Visualization Walkthrough",
        "## Local Web Dashboard",
        "## Web Quickstart And First Research Demo",
        "## Understanding The First Result",
        "## Troubleshooting",
        "## Terminology Glossary",
    ):
        assert heading in readme

    required_phrases = (
        "fixtures/sample_topic_run.json",
        "fixtures/tutorial_transcript.txt",
        "not a source of truth",
        "backend graph and provenance",
        "current best hypothesis",
        "challenger target",
        "active conflict",
    )
    lowered_readme = readme.lower()
    for phrase in required_phrases:
        assert phrase in lowered_readme

    local_links = [
        target
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", readme)
        if "://" not in target and not target.startswith("#")
    ]
    assert local_links
    for target in local_links:
        path = target.split("#", 1)[0]
        assert (ROOT / path).exists(), path


def test_iso26262_sample_dashboard_generation_matches_cli_state(tmp_path: Path) -> None:
    sample = load_iso26262_sample()
    topic = sample["topic"]
    run = sample["run"]
    backend = LocalBackendGateway(db_path=tmp_path / "crb.sqlite3", workspace_root=tmp_path)

    assert run_cli(["init", "--json"], backend)[0] == 0
    assert run_cli(["doctor", "--json"], backend)[0] == 0
    assert (
        run_cli(
            [
                "topic",
                "create",
                topic["title"],
                "--objective",
                topic["objective"],
                "--json",
            ],
            backend,
        )[0]
        == 0
    )
    code, output, _ = run_cli(
        ["run", "start", topic["topic_id"], "--input", run["input"], "--json"],
        backend,
    )
    start = parsed_json(output)

    assert code == 0
    assert start.data["run_id"] == run["expected_run_id"]
    assert start.data["queue_item_id"] == run["expected_queue_item_id"]

    graph_json = tmp_path / "iso26262-graph.json"
    graph_html = tmp_path / "iso26262-graph.html"
    assert (
        run_cli(
            [
                "graph",
                "export",
                topic["topic_id"],
                "--scope",
                "latest",
                "--format",
                "json",
                "--output",
                str(graph_json),
                "--json",
            ],
            backend,
        )[0]
        == 0
    )
    assert (
        run_cli(
            [
                "graph",
                "view",
                topic["topic_id"],
                "--scope",
                "latest",
                "--format",
                "html",
                "--output",
                str(graph_html),
                "--json",
            ],
            backend,
        )[0]
        == 0
    )

    with RunningServer(backend) as server:
        runs = fetch_json(server.base_url, f"/api/topics/{topic['topic_id']}/runs")
        queue = fetch_json(server.base_url, f"/api/topics/{topic['topic_id']}/queue")
        memory = fetch_json(server.base_url, f"/api/topics/{topic['topic_id']}/memory")
        graph = fetch_json(
            server.base_url,
            f"/api/topics/{topic['topic_id']}/graph/latest",
        )
        timeline = fetch_json(
            server.base_url,
            f"/api/web/runs/{run['expected_run_id']}/timeline",
        )

    timeline_items = runs["timeline_items"]
    assert any(
        item["run_id"] == run["expected_run_id"]
        and item["queue_item_id"] == run["expected_queue_item_id"]
        and item["timeline_source"] == "queue_request"
        for item in timeline_items
    )
    assert queue["queue"]["items"][0]["requested_run_id"] == run["expected_run_id"]
    assert memory["memory"]["topic_id"] == topic["topic_id"]
    assert "not a source of truth" in memory["memory"]["visualization_notice"]
    assert graph["graph"]["scope"] == "latest"
    assert timeline["schema_id"] == "crb.web.run.timeline.v1"
    assert timeline["status"]["run_id"] == run["expected_run_id"]
    assert timeline["status"]["queue"]["queue_item_id"] == run["expected_queue_item_id"]
    assert "not a source of truth" in graph_json.read_text()
    assert "not a source of truth" in graph_html.read_text()


def test_phase19_docs_and_artifacts_preserve_web_ux_gates() -> None:
    sample = load_iso26262_sample()
    dashboard_artifact = json.loads(
        (FIXTURES_DIR / "iso26262_web_dashboard_artifact.json").read_text()
    )
    readme = (ROOT / "README.md").read_text()
    walkthrough = (ROOT / "docs" / "phase-19-first-research-walkthrough.md").read_text()
    audit = (ROOT / "docs" / "phase-19-web-ux-audit.md").read_text()
    combined = "\n".join(
        [
            readme,
            walkthrough,
            audit,
            json.dumps(dashboard_artifact, sort_keys=True),
        ]
    )

    for command in sample["commands"]:
        assert command in combined

    required_phrases = (
        "not a source of truth",
        "backend state, graph, queue, and provenance ledgers remain authoritative",
        "active conflict",
        "queue_request",
        "topic_iso_26262_safety_case_drift",
        "run_5e3826f4ce35",
        "queue_5e3826f4ce35",
    )
    lowered = combined.lower()
    for phrase in required_phrases:
        assert phrase in lowered

    for target in dashboard_artifact["sanity_links"]:
        path = target.split("#", 1)[0]
        assert (ROOT / path).exists(), path
