from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from codex_continual_research_bot.cli_backend import LocalBackendGateway
from codex_continual_research_bot.persistence import SQLitePersistenceLedger
from test_web import RunningServer, seed_run_state_backend


playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)


def artifact_dir() -> Path:
    path = Path(os.environ.get("CRB_PLAYWRIGHT_ARTIFACT_DIR", ROOT / "artifacts" / "playwright"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def screenshot(page: object, path: Path) -> str:
    page.screenshot(path=str(path), full_page=True)
    return str(path.relative_to(ROOT))


def seed_running_worker_loop(backend: LocalBackendGateway) -> None:
    ledger = SQLitePersistenceLedger(backend.db_path)
    acquired = ledger.acquire_worker_loop(
        loop_id="loop_playwright_running",
        topic_id="topic_codex_auth_boundary",
        worker_id="playwright-worker",
        lease_expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )
    assert acquired is not None


def seed_converged_worker_loop(backend: LocalBackendGateway) -> None:
    ledger = SQLitePersistenceLedger(backend.db_path)
    stopped = ledger.stop_worker_loop(
        loop_id="loop_playwright_running",
        state="converged",
        stop_reason="max_consecutive_no_yield",
        now=NOW + timedelta(minutes=1),
    )
    assert stopped is not None


def test_dashboard_playwright_screenshots_cover_run_state_tabs(tmp_path: Path) -> None:
    backend = seed_run_state_backend(tmp_path)
    seed_running_worker_loop(backend)
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
                expect(page.get_by_text("지금 실행 중")).to_be_visible()
                expect(page.locator("#runningNowObjective")).to_contain_text("running challenger")
                expect(page.locator("#workerLoopState")).to_contain_text("running")
                expect(page.locator("#overviewHelp")).to_contain_text("개요를 읽는 법")
                paths["overview"] = screenshot(page, artifacts / "overview.png")
                paths["worker_loop_running"] = screenshot(
                    page,
                    artifacts / "worker-loop-running.png",
                )

                seed_converged_worker_loop(backend)
                page.reload(wait_until="networkidle")
                page.get_by_role("heading", name="topic_codex_auth_boundary").wait_for()
                expect(page.locator("#workerLoopState")).to_contain_text("converged")
                expect(page.locator("#workerLoopState")).to_contain_text(
                    "max_consecutive_no_yield"
                )
                paths["worker_loop_converged"] = screenshot(
                    page,
                    artifacts / "worker-loop-converged.png",
                )

                page.get_by_role("button", name="대시보드 읽는 법").click()
                expect(page.get_by_role("heading", name="대시보드 읽는 법")).to_be_visible()
                expect(page.locator("#helpPanelContent")).to_contain_text("HYP · 가설")
                expect(page.locator("#helpPanelContent")).to_contain_text("Dead-letter")
                paths["help"] = screenshot(page, artifacts / "help.png")
                page.get_by_role("button", name="닫기").click()

                page.get_by_role("button", name="그래프").click()
                expect(page.locator("#graphCanvas svg")).to_be_visible()
                expect(page.get_by_text("선택한 node")).to_be_visible()
                expect(page.locator("#graphFilters")).to_contain_text("최선 가설")
                expect(page.locator("#graphFilters")).to_contain_text("도전자")
                expect(page.locator("#graphFilters")).to_contain_text("근거")
                expect(page.locator("#graphFilters")).to_contain_text("출처 기록")
                expect(page.locator("#graphLegend")).to_contain_text("CLA")
                expect(page.locator("#graphLegend")).to_contain_text("HYP")
                expect(page.locator("#graphLegend")).to_contain_text("EVI")
                expect(page.locator("#graphLegend")).to_contain_text("PRO")
                expect(page.locator("#graphLegend")).to_contain_text("가설")
                expect(page.locator("#graphLegend")).to_contain_text("도전")
                expect(page.locator("#detailRelations")).not_to_be_empty()
                paths["graph"] = screenshot(page, artifacts / "graph.png")

                page.get_by_role("button", name="실행").click()
                expect(page.get_by_text("Run Timeline")).to_be_visible()
                expect(page.locator("#runsList")).to_contain_text("running challenger")
                expect(page.locator("#runsList")).to_contain_text("요청")
                expect(page.locator("#runsList")).to_contain_text("시작")
                expect(page.locator("#runsList")).to_contain_text("Duration")
                expect(page.locator("#runsList")).to_contain_text("아직 시작 전")
                expect(page.locator("#runsList")).to_contain_text("Graph 관계")
                paths["runs"] = screenshot(page, artifacts / "runs.png")

                page.get_by_role("button", name="큐").click()
                expect(page.get_by_text("실행 중 · 1")).to_be_visible()
                expect(page.get_by_text("대기 · 1")).to_be_visible()
                expect(page.get_by_text("완료 · 1")).to_be_visible()
                expect(page.get_by_text("Dead-letter · 1")).to_be_visible()
                expect(page.get_by_text("Stale claim · 1")).to_be_visible()
                expect(page.locator("#queueHelpList")).to_contain_text("격리된 실패")
                expect(page.locator("#queueList")).to_contain_text("Failure code")
                expect(page.locator("#queueList")).to_contain_text("Human review")
                expect(page.locator("#queueList")).to_contain_text("다음 행동")
                paths["queue"] = screenshot(page, artifacts / "queue.png")

                page.get_by_role("button", name="메모리").click()
                expect(page.get_by_role("heading", name="Memory Projection", exact=True)).to_be_visible()
                expect(page.locator("#memoryList")).to_contain_text("현재 최선 가설")
                expect(page.locator("#memoryList")).to_contain_text("도전자 가설")
                expect(page.locator("#memoryList")).to_contain_text("충돌")
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
    assert set(paths) == {
        "overview",
        "worker_loop_running",
        "worker_loop_converged",
        "help",
        "graph",
        "runs",
        "queue",
        "memory",
    }
