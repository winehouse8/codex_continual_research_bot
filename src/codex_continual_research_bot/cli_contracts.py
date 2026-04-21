"""Stable CLI result envelope for Phase 13 command handlers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import Field, StrictBool, StrictStr

from codex_continual_research_bot.contracts import StrictModel


CLI_RESULT_SCHEMA_ID = "crb.cli.result.v1"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CliFailure(StrictModel):
    failure_code: StrictStr = Field(min_length=1)
    retryable: StrictBool
    human_review_required: StrictBool
    detail: StrictStr = Field(min_length=1)


class CliBackendError(RuntimeError):
    def __init__(
        self,
        *,
        failure_code: str,
        detail: str,
        retryable: bool = False,
        human_review_required: bool = True,
    ) -> None:
        super().__init__(detail)
        self.failure_code = failure_code
        self.detail = detail
        self.retryable = retryable
        self.human_review_required = human_review_required


class CliResult(StrictModel):
    schema_id: StrictStr = Field(default=CLI_RESULT_SCHEMA_ID)
    command_id: StrictStr = Field(min_length=1)
    ok: StrictBool
    summary: StrictStr = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
    failure: CliFailure | None = None
    generated_at: datetime = Field(default_factory=utcnow)


def cli_result_json(result: CliResult) -> str:
    return json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=False) + "\n"
