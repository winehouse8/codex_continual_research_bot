from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from test_web import RunningServer, seed_run_state_backend


playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def artifact_dir() -> Path:
    path = Path(os.environ.get("CRB_PLAYWRIGHT_ARTIFACT_DIR", ROOT / "artifacts" / "playwright"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def screenshot(page: object, path: Path) -> str:
    page.screenshot(path=str(path), full_page=True)
    return str(path.relative_to(ROOT))


def test_dashboard_playwright_screenshots_cover_run_state_tabs(tmp_path: Path) -> None:
    backend = seed_run_state_backend(tmp_path)
    artifacts = artifact_dir()
    paths: dict[str, str] = {}

    with RunningServer(backend) as server:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                page.goto(f"{server.base_url}/dashboard", wait_until="networkidle")
                page.get_by_role("heading", name="topic_codex_auth_boundary").wait_for()

                expect = playwright_api.expect
                expect(page.locator("#runningCount")).to_have_text("1")
                expect(page.locator("#queuedCount")).to_have_text("1")
                expect(page.locator("#completedCount")).to_have_text("1")
                expect(page.locator("#deadLetterCount")).to_have_text("1")
                expect(page.locator("#staleCount")).to_have_text("1")
                expect(page.get_by_text("Running now")).to_be_visible()
                expect(page.locator("#runningNowObjective")).to_contain_text("running challenger")
                paths["overview"] = screenshot(page, artifacts / "overview.png")

                page.get_by_role("button", name="Graph").click()
                expect(page.locator("#graphCanvas svg")).to_be_visible()
                expect(page.get_by_text("Selected node")).to_be_visible()
                expect(page.locator("#graphFilters")).to_contain_text("Current best")
                expect(page.locator("#graphFilters")).to_contain_text("Challengers")
                expect(page.locator("#graphFilters")).to_contain_text("Evidence")
                expect(page.locator("#graphFilters")).to_contain_text("Provenance")
                expect(page.locator("#detailRelations")).not_to_be_empty()
                paths["graph"] = screenshot(page, artifacts / "graph.png")

                page.get_by_role("button", name="Runs").click()
                expect(page.get_by_text("Run Timeline")).to_be_visible()
                expect(page.locator("#runsList")).to_contain_text("running challenger")
                expect(page.locator("#runsList")).to_contain_text("Graph relation")
                paths["runs"] = screenshot(page, artifacts / "runs.png")

                page.get_by_role("button", name="Queue").click()
                expect(page.get_by_text("Running · 1")).to_be_visible()
                expect(page.get_by_text("Queued · 1")).to_be_visible()
                expect(page.get_by_text("Completed · 1")).to_be_visible()
                expect(page.get_by_text("Dead-letter · 1")).to_be_visible()
                expect(page.get_by_text("Stale claim · 1")).to_be_visible()
                paths["queue"] = screenshot(page, artifacts / "queue.png")

                page.get_by_role("button", name="Memory").click()
                expect(page.get_by_text("Memory Projection")).to_be_visible()
                expect(page.locator("#memoryList")).to_contain_text("Current best")
                expect(page.locator("#memoryList")).to_contain_text("Challengers")
                expect(page.locator("#memoryList")).to_contain_text("Conflicts")
                paths["memory"] = screenshot(page, artifacts / "memory.png")
            finally:
                browser.close()

    manifest = {
        "schema_id": "crb.playwright.screenshot_manifest.v1",
        "topic_id": "topic_codex_auth_boundary",
        "screenshots": paths,
    }
    (artifacts / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    assert set(paths) == {"overview", "graph", "runs", "queue", "memory"}
