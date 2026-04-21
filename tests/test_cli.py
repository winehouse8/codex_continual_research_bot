from __future__ import annotations

import ast
import json
import shlex
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from codex_continual_research_bot.cli import build_parser, main
from codex_continual_research_bot.cli_backend import LocalBackendGateway
from codex_continual_research_bot.cli_contracts import CliResult
from codex_continual_research_bot.contracts import (
    BackendStateUpdateSummary,
    FailureCode,
    InteractiveRunStatus,
    ProposalBundle,
    RunReportViewModel,
)
from codex_continual_research_bot.graph_canonicalization import (
    CanonicalGraphService,
    CanonicalizationContext,
    HypothesisSnapshot,
)
from codex_continual_research_bot.persistence import SQLitePersistenceLedger
from codex_continual_research_bot.ux_contracts import extract_crb_examples


ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
NOW = datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)


def run_cli(argv: list[str], backend: LocalBackendGateway) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = main(argv, backend=backend, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def parsed_json(output: str) -> CliResult:
    return CliResult.model_validate(json.loads(output))


def load_proposal() -> ProposalBundle:
    return ProposalBundle.model_validate(
        json.loads((FIXTURES_DIR / "proposal_bundle.json").read_text())
    )


def create_codex_auth_topic(backend: LocalBackendGateway) -> None:
    run_cli(["init"], backend)
    run_cli(
        [
            "topic",
            "create",
            "Codex auth boundary",
            "--objective",
            "Track session ownership risk",
        ],
        backend,
    )


def seed_graph_backed_memory(backend: LocalBackendGateway, db_path: Path) -> None:
    _, output, _ = run_cli(
        [
            "run",
            "start",
            "topic_codex_auth_boundary",
            "--input",
            "counterargument: warning-only stale sessions may be safe",
            "--json",
        ],
        backend,
    )
    started = parsed_json(output)
    run_id = str(started.data["run_id"])
    queue_item_id = str(started.data["queue_item_id"])

    ledger = SQLitePersistenceLedger(db_path)
    claimed = ledger.claim_queue_item_for_run(
        queue_item_id=queue_item_id,
        worker_id="worker-a",
        run_id=run_id,
        mode="interactive",
        now=datetime.now(timezone.utc),
    )
    assert claimed is not None
    queue_item = ledger.fetch_queue_item(queue_item_id)
    assert queue_item is not None

    canonical = CanonicalGraphService().canonicalize(
        proposal=load_proposal(),
        context=CanonicalizationContext(
            topic_id="topic_codex_auth_boundary",
            run_id=run_id,
            proposal_id="proposal_001",
            current_best_hypothesis_id="hyp_001",
            existing_hypotheses=[
                HypothesisSnapshot(
                    hypothesis_id="hyp_001",
                    title="Block scheduled run on session ambiguity",
                    statement=(
                        "A scheduled run must stop when principal or workspace "
                        "inspection is ambiguous."
                    ),
                    version=2,
                )
            ],
        ),
    )
    assert not canonical.quarantined
    report = RunReportViewModel(
        report_id="report_001",
        run_id=run_id,
        topic_id="topic_codex_auth_boundary",
        trigger_id=queue_item_id,
        idempotency_key=str(queue_item["idempotency_key"]),
        snapshot_version=1,
        status=InteractiveRunStatus.COMPLETED,
        summary="Graph-backed memory was updated.",
        proposal_digest="sha256:proposal",
        backend_state_update=BackendStateUpdateSummary(
            graph_digest=canonical.digest,
            node_count=len(canonical.graph.nodes),
            edge_count=len(canonical.graph.edges),
            review_flags=[],
        ),
        operator_failure_summary=None,
        created_at=NOW,
    )
    ledger.record_interactive_run_success(
        report=report,
        proposal_id="proposal_001",
        graph_payload=canonical.graph.model_dump(mode="json"),
        graph_digest=canonical.digest,
        node_count=len(canonical.graph.nodes),
        edge_count=len(canonical.graph.edges),
    )


def test_cli_help_lists_phase13_commands() -> None:
    help_text = build_parser().format_help()

    assert "topic" in help_text
    assert "run" in help_text
    assert "queue" in help_text
    assert "memory" in help_text
    assert "ops" in help_text


def test_readme_command_examples_parse() -> None:
    parser = build_parser()
    examples = extract_crb_examples((ROOT / "README.md").read_text())

    assert examples
    for example in examples:
        args = parser.parse_args(shlex.split(example)[1:])
        assert args.command_id


def test_readme_quickstart_uses_generated_ids_and_basic_flow_executes(
    tmp_path: Path,
) -> None:
    readme = (ROOT / "README.md").read_text()
    assert "run_2026_04_19_001" not in readme
    assert "queue_001" not in readme

    backend = LocalBackendGateway(
        db_path=tmp_path / "crb.sqlite3",
        workspace_root=tmp_path,
    )
    assert run_cli(["init", "--json"], backend)[0] == 0
    assert run_cli(["doctor", "--json"], backend)[0] == 0
    assert (
        run_cli(
            [
                "topic",
                "create",
                "Codex auth boundary",
                "--objective",
                "Track session ownership risk",
                "--json",
            ],
            backend,
        )[0]
        == 0
    )
    assert run_cli(["topic", "list", "--json"], backend)[0] == 0
    assert run_cli(["topic", "show", "topic_codex_auth_boundary"], backend)[0] == 0

    code, output, _ = run_cli(
        [
            "run",
            "start",
            "topic_codex_auth_boundary",
            "--input",
            "counterargument: warning-only stale sessions may be safe",
            "--json",
        ],
        backend,
    )
    start = parsed_json(output)
    run_id = str(start.data["run_id"])
    queue_item_id = str(start.data["queue_item_id"])

    assert code == 0
    assert run_id.startswith("run_")
    assert queue_item_id.startswith("queue_")
    assert run_cli(["run", "status", run_id, "--json"], backend)[0] == 0
    assert run_cli(["run", "resume", run_id], backend)[0] == 0
    assert (
        run_cli(
            ["queue", "list", "--topic", "topic_codex_auth_boundary", "--json"],
            backend,
        )[0]
        == 0
    )
    assert run_cli(["queue", "dead-letter", queue_item_id], backend)[0] == 0
    assert (
        run_cli(["memory", "snapshot", "topic_codex_auth_boundary", "--json"], backend)[0]
        == 0
    )
    assert (
        run_cli(["memory", "conflicts", "topic_codex_auth_boundary", "--json"], backend)[0]
        == 0
    )
    assert (
        run_cli(["memory", "hypotheses", "topic_codex_auth_boundary", "--json"], backend)[0]
        == 0
    )
    assert run_cli(["ops", "health", "--json"], backend)[0] == 0
    assert run_cli(["ops", "audit", run_id, "--json"], backend)[0] == 0


def test_topic_create_list_show_flow(tmp_path: Path) -> None:
    backend = LocalBackendGateway(db_path=tmp_path / "crb.sqlite3", workspace_root=tmp_path)

    assert run_cli(["init", "--json"], backend)[0] == 0
    code, output, _ = run_cli(
        [
            "topic",
            "create",
            "Codex auth boundary",
            "--objective",
            "Track session ownership risk",
            "--json",
        ],
        backend,
    )
    result = parsed_json(output)

    assert code == 0
    assert result.command_id == "topic.create"
    assert result.data["topic_id"] == "topic_codex_auth_boundary"

    code, output, _ = run_cli(["topic", "list", "--json"], backend)
    assert code == 0
    assert parsed_json(output).data["topics"][0]["topic_id"] == "topic_codex_auth_boundary"

    code, output, _ = run_cli(["topic", "show", "topic_codex_auth_boundary"], backend)
    assert code == 0
    assert "Current best hypotheses:" in output
    assert "Active conflicts:" in output


def test_run_start_status_resume_flow(tmp_path: Path) -> None:
    backend = LocalBackendGateway(db_path=tmp_path / "crb.sqlite3", workspace_root=tmp_path)
    run_cli(["init"], backend)
    run_cli(
        [
            "topic",
            "create",
            "Codex auth boundary",
            "--objective",
            "Track session ownership risk",
        ],
        backend,
    )

    code, output, _ = run_cli(
        [
            "run",
            "start",
            "topic_codex_auth_boundary",
            "--input",
            "counterargument: warning-only stale sessions may be safe",
            "--json",
        ],
        backend,
    )
    start = parsed_json(output)
    run_id = str(start.data["run_id"])

    assert code == 0
    assert start.data["queue"]["kind"] == "run.execute"
    assert start.data["queue"]["state"] == "queued"

    code, output, _ = run_cli(["run", "status", run_id, "--json"], backend)
    status = parsed_json(output)
    assert code == 0
    assert status.data["run_id"] == run_id
    assert status.data["queue"]["state"] == "queued"

    code, output, _ = run_cli(["run", "resume", run_id, "--json"], backend)
    resume = parsed_json(output)
    assert code == 0
    assert resume.data["run_id"] == run_id
    assert resume.data["queue"]["kind"] == "run.resume"


def test_queue_dead_letter_retry_flow_shows_failure_classification(tmp_path: Path) -> None:
    backend = LocalBackendGateway(db_path=tmp_path / "crb.sqlite3", workspace_root=tmp_path)
    run_cli(["init"], backend)
    run_cli(
        [
            "topic",
            "create",
            "Codex auth boundary",
            "--objective",
            "Track session ownership risk",
        ],
        backend,
    )
    _, output, _ = run_cli(
        [
            "run",
            "start",
            "topic_codex_auth_boundary",
            "--input",
            "question: can stale sessions be retried?",
            "--json",
        ],
        backend,
    )
    queue_item_id = str(parsed_json(output).data["queue_item_id"])
    SQLitePersistenceLedger(tmp_path / "crb.sqlite3").record_queue_dead_letter(
        queue_item_id=queue_item_id,
        failure_code=FailureCode.MALFORMED_PROPOSAL.value,
        detail="proposal failed canonicalization",
        retryable=False,
        human_review_required=True,
    )

    code, output, _ = run_cli(["queue", "dead-letter", queue_item_id, "--json"], backend)
    dead = parsed_json(output)
    assert code == 0
    assert dead.data["queue"]["failure"]["retryable"] is False
    assert dead.data["queue"]["failure"]["human_review_required"] is True

    code, output, _ = run_cli(
        ["queue", "retry", queue_item_id, "--reason", "operator approved repair", "--json"],
        backend,
    )
    retried = parsed_json(output)
    assert code == 0
    assert retried.data["queue"]["state"] == "queued"


def test_json_result_schema_for_memory_and_ops(tmp_path: Path) -> None:
    backend = LocalBackendGateway(db_path=tmp_path / "crb.sqlite3", workspace_root=tmp_path)
    run_cli(["init"], backend)
    run_cli(
        [
            "topic",
            "create",
            "Codex auth boundary",
            "--objective",
            "Track session ownership risk",
        ],
        backend,
    )

    for argv in (
        ["memory", "snapshot", "topic_codex_auth_boundary", "--json"],
        ["memory", "conflicts", "topic_codex_auth_boundary", "--json"],
        ["memory", "hypotheses", "topic_codex_auth_boundary", "--json"],
        ["ops", "health", "--json"],
    ):
        code, output, _ = run_cli(argv, backend)
        result = parsed_json(output)
        assert code == 0
        assert result.schema_id == "crb.cli.result.v1"
        assert result.ok is True


def test_human_readable_output_snapshot(tmp_path: Path) -> None:
    backend = LocalBackendGateway(db_path=tmp_path / "crb.sqlite3", workspace_root=tmp_path)
    create_codex_auth_topic(backend)

    code, output, _ = run_cli(["memory", "hypotheses", "topic_codex_auth_boundary"], backend)

    assert code == 0
    assert output == (
        "Found 1 hypothesis view(s) for topic_codex_auth_boundary.\n"
        "Memory source: topic_snapshot\n"
        "No canonical graph write found; using topic snapshot fallback.\n"
        "Graph export is not a source of truth; backend graph and provenance "
        "ledgers remain authoritative.\n"
        "- current_best: Initial objective hypothesis "
        "(hyp_codex_auth_boundary_current_best); support=0 challenge=0 conflict=0\n"
    )


def test_memory_hypotheses_uses_graph_backed_projection_when_available(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "crb.sqlite3"
    backend = LocalBackendGateway(db_path=db_path, workspace_root=tmp_path)
    create_codex_auth_topic(backend)
    seed_graph_backed_memory(backend, db_path)

    code, output, _ = run_cli(
        ["memory", "hypotheses", "topic_codex_auth_boundary", "--json"],
        backend,
    )
    result = parsed_json(output)
    hypotheses = result.data["hypotheses"]

    assert code == 0
    assert result.data["memory_source"] == "canonical_graph_write"
    assert result.data["snapshot_projection_mismatch"] is True
    assert any(
        item["role"] == "challenger"
        and item["title"] == "Allow warning-only stale session handling"
        for item in hypotheses
    )
    assert any(
        item["role"] == "current_best"
        and item["title"] == "Block scheduled run on session ambiguity"
        for item in hypotheses
    )


def test_memory_conflicts_exposes_challenge_candidates_without_active_conflicts(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "crb.sqlite3"
    backend = LocalBackendGateway(db_path=db_path, workspace_root=tmp_path)
    create_codex_auth_topic(backend)
    seed_graph_backed_memory(backend, db_path)

    code, output, _ = run_cli(
        ["memory", "conflicts", "topic_codex_auth_boundary", "--json"],
        backend,
    )
    result = parsed_json(output)

    assert code == 0
    assert result.data["conflicts"] == []
    assert result.data["challenge_candidates"]
    assert {
        candidate["status"] for candidate in result.data["challenge_candidates"]
    } == {"challenge_not_promoted_to_active_conflict"}


def test_topic_show_uses_graph_memory_summary_when_snapshot_is_stale(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "crb.sqlite3"
    backend = LocalBackendGateway(db_path=db_path, workspace_root=tmp_path)
    create_codex_auth_topic(backend)
    seed_graph_backed_memory(backend, db_path)

    code, output, _ = run_cli(["topic", "show", "topic_codex_auth_boundary"], backend)

    assert code == 0
    assert "Memory source: canonical_graph_write" in output
    assert "Latest canonical graph projection differs from the topic snapshot" in output
    assert "Block scheduled run on session ambiguity" in output
    assert "Allow warning-only stale session handling" in output


def test_graph_export_and_view_commands_write_visualization_artifacts(tmp_path: Path) -> None:
    backend = LocalBackendGateway(db_path=tmp_path / "crb.sqlite3", workspace_root=tmp_path)
    run_cli(["init"], backend)
    run_cli(
        [
            "topic",
            "create",
            "Codex auth boundary",
            "--objective",
            "Track session ownership risk",
        ],
        backend,
    )
    graph_json = tmp_path / "graph.json"
    graph_dot = tmp_path / "graph.dot"
    graph_mermaid = tmp_path / "graph.mmd"
    graph_html = tmp_path / "graph.html"

    code, output, _ = run_cli(
        [
            "graph",
            "export",
            "topic_codex_auth_boundary",
            "--format",
            "json",
            "--output",
            str(graph_json),
            "--json",
        ],
        backend,
    )
    assert code == 0
    assert parsed_json(output).data["output_path"] == str(graph_json)
    assert "not a source of truth" in graph_json.read_text()
    assert "memory_explorer" in graph_json.read_text()

    code, output, _ = run_cli(
        [
            "graph",
            "export",
            "topic_codex_auth_boundary",
            "--format",
            "dot",
            "--output",
            str(graph_dot),
            "--json",
        ],
        backend,
    )
    assert code == 0
    assert parsed_json(output).data["format"] == "dot"
    assert graph_dot.read_text().startswith("digraph crb_graph")

    code, output, _ = run_cli(
        [
            "graph",
            "export",
            "topic_codex_auth_boundary",
            "--format",
            "mermaid",
            "--output",
            str(graph_mermaid),
            "--json",
        ],
        backend,
    )
    assert code == 0
    assert parsed_json(output).data["format"] == "mermaid"
    assert graph_mermaid.read_text().startswith("graph LR")

    code, output, _ = run_cli(
        [
            "graph",
            "view",
            "topic_codex_auth_boundary",
            "--format",
            "html",
            "--output",
            str(graph_html),
            "--json",
        ],
        backend,
    )
    assert code == 0
    assert parsed_json(output).data["output_path"] == str(graph_html)
    assert "Initial objective hypothesis" in graph_html.read_text()


def test_cli_module_does_not_import_persistence_write_boundary() -> None:
    source = (ROOT / "src/codex_continual_research_bot/cli.py").read_text()
    tree = ast.parse(source)
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
    ]

    assert "SQLitePersistenceLedger" not in source
    assert all(
        not (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("codex_continual_research_bot.persistence")
        )
        for node in imports
    )
