# Continual Research Bot

`codex_continual_research_bot`은 단순히 검색 결과를 모아 요약하는 wrapper가 아니라, 같은 주제를 반복 연구하면서 가설을 공격하고 갱신하는 continual research bot입니다. 사용자는 연구 주제와 실행 조건을 넣고, backend는 queue/scheduler, Codex 실행, output validation, graph/provenance, revision proposal을 묶어 evidence, hypothesis, conflict, revision pressure를 반복적으로 검증합니다.

## 현재 구현 상태

이 저장소는 아직 완성된 사용자용 CLI 제품이 아닙니다. 현재는 Python 패키지로 구현된 backend harness, strict contract, SQLite ledger, orchestrator, runtime adapter, interactive/scheduled service, validation gate, 테스트 fixture가 중심입니다.

| 구분 | 현재 경로 |
| --- | --- |
| 설치/검증 | `python -m pip install -e '.[dev]'`, `python -m pytest` |
| 상태 저장 | 호출자가 지정한 `SQLitePersistenceLedger` DB 파일 |
| 런타임 산출물 | `CodexRuntimeConfig.artifact_root/<run_id>/attempt_###/` |
| 빠른 참고 | `SPEC.md`, `ARCH.md`, `RUNTIME.md`, `AUTH_AND_EXECUTION.md`, `TASK.md` |

## 빠른 시작

### 1. 환경 준비

Python 3.11 이상이 필요합니다.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
```

Codex runtime까지 실제로 연결하려면 trusted machine에서 Codex CLI가 로그인되어 있어야 합니다. 설계상 v1 기본 실행 transport는 `codex exec --json --output-schema`이며, 사용자 소유 Codex 세션은 backend가 직접 raw token으로 다루지 않고 session ledger와 lease boundary로 감쌉니다.

### 2. 로컬 상태 위치 정하기

현재 구현은 고정된 state directory를 만들지 않습니다. 통합하는 애플리케이션이 아래 위치를 직접 정합니다.

```text
state/research.sqlite3          # topic, queue, run, session, report ledger
runtime-artifacts/              # codex raw/normalized events, final output, quarantine
```

예시는 다음처럼 시작합니다.

```python
from pathlib import Path

from codex_continual_research_bot.persistence import SQLitePersistenceLedger

ledger = SQLitePersistenceLedger(Path("state/research.sqlite3"))
ledger.initialize()
```

### 3. 연구 topic 준비

topic은 그냥 검색어가 아니라 다음 run이 공격할 수 있는 snapshot입니다. 최소한 `current_best_hypotheses`가 있어야 runtime이 시작됩니다.

```python
from codex_continual_research_bot import TopicSnapshot

ledger.create_topic(
    topic_id="topic_ai_agents",
    slug="ai-agents",
    title="AI agents for continual research",
)

ledger.store_topic_snapshot(
    TopicSnapshot.model_validate(
        {
            "topic_id": "topic_ai_agents",
            "snapshot_version": 1,
            "topic_summary": "AI agent 기반 장기 연구 자동화 가능성을 추적한다.",
            "current_best_hypotheses": [
                {
                    "hypothesis_id": "hyp_001",
                    "title": "반복 연구는 가설 경쟁을 통해 품질을 높인다.",
                    "summary": "새 evidence만 쌓는 방식보다 challenger를 생성하는 방식이 낫다.",
                }
            ],
            "challenger_targets": [
                {
                    "hypothesis_id": "hyp_001",
                    "title": "반복 연구는 가설 경쟁을 통해 품질을 높인다.",
                    "summary": "다음 run에서 우선 공격할 현재 best hypothesis다.",
                }
            ],
            "active_conflicts": [],
            "open_questions": ["support-only 반복이 정체를 만드는 조건은 무엇인가?"],
            "recent_provenance_digest": "sha256:bootstrap",
            "queued_user_inputs": [],
        }
    )
)
```

### 4. run 시작하기

현재 사용 가능한 진입점은 완성형 CLI가 아니라 Python service입니다.

| 목적 | 진입점 |
| --- | --- |
| 사용자가 즉시 실행하는 연구 | `InteractiveRunService.trigger_run(...)` |
| scheduler가 due topic을 enqueue | `ScheduledRunService.enqueue_due_runs(...)` |
| queue item 실행 | `ScheduledRunService.execute_next(...)` 또는 `QueueWorker` |
| Codex CLI 실행 adapter | `CodexRuntimeCoordinator.execute(...)` |

interactive path의 최소 동작 예시는 `tests/test_interactive.py`가 가장 안전한 참고입니다. 테스트는 fake runtime으로 검증하지만, 실제 runtime 연결 시에는 `CodexRuntimeCoordinator`가 `codex exec --json` 이벤트를 수집하고 final `ProposalBundle`을 검증합니다.

### 5. 결과 확인하기

성공한 run은 세 군데에 흔적을 남깁니다.

| 확인 위치 | 무엇을 보는가 |
| --- | --- |
| SQLite ledger | `runs`, `queue_items`, `run_events`, `interactive_run_reports`, `canonical_graph_writes` |
| runtime artifact directory | `raw_events.jsonl`, `normalized_events.jsonl`, final output, malformed/quarantine payload |
| returned report model | 사용자에게 보여줄 summary, backend state update summary, failure summary |

실패나 재시도는 queue/run/session 상태와 failure code로 확인합니다. 예를 들어 malformed proposal은 canonical graph에 바로 저장되지 않고 validation 또는 quarantine 경계에서 멈춰야 합니다.

## 전체 작동 기작

```text
User / Topic
   ↓
Queue / Scheduler
   ↓
Run Orchestrator
   ↓
Codex Runtime
   ↓
Output Validator / Repair
   ↓
Canonical Graph / Provenance
   ↓
Revision Proposal / Snapshot Input
   ↓
Next Run Selection
```

| 단계 | 책임 |
| --- | --- |
| User / Topic | 사용자가 연구 주제, 반론, 조사 방향을 넣고 현재 topic snapshot을 확인합니다. |
| Queue / Scheduler | interactive run과 scheduled run을 같은 job contract로 만들고 priority, retry, dead-letter를 관리합니다. |
| Run Orchestrator | 최신 snapshot을 로드하고 current-best attack, challenger generation, support/challenge 수집 요구사항을 runtime intent로 고정합니다. |
| Codex Runtime | `codex exec --json`을 실행 substrate로 사용해 evidence, claim, argument, proposal 초안을 생성합니다. |
| Output Validator / Repair | runtime output을 strict schema와 competition gate로 검증하고, malformed proposal은 저장 경계 전에 막습니다. |
| Canonical Graph / Provenance | evidence, claim, hypothesis, conflict, decision을 canonical graph/provenance 형태로 정규화합니다. |
| Revision Proposal / Snapshot Input | strengthen, weaken, retire, supersede 같은 revision proposal을 정리합니다. 현재 구현은 검증된 canonical graph write와 run report 저장까지 닫고, 새 snapshot materialization은 통합 계층 또는 후속 구현 경계입니다. |
| Next Run Selection | unresolved conflict, stale hypothesis, 낮은 challenger rate, user input backlog를 기준으로 다음 공격 지점을 고릅니다. |

## 왜 반복할수록 나아지는가

이 시스템은 fact를 절대값처럼 저장하지 않습니다. confidence가 다른 hypothesis를 저장하고, evidence, claim, hypothesis, provenance를 분리해 어떤 결론이 왜 나왔는지 추적합니다.

conflict는 단순 오류가 아닙니다. 서로 다른 evidence나 가설이 충돌하는 상태이며, 다음 run이 다뤄야 할 frontier입니다. 그래서 support-only 반복은 정체로 보고, current best hypothesis를 계속 공격할 challenger generation과 revision pressure를 요구합니다.

run output도 곧바로 canonical memory가 아닙니다. Codex가 만든 결과는 proposal이며, validation/canonicalization을 통과한 뒤에만 canonical graph write와 run report에 반영됩니다. 다음 snapshot이나 belief state 갱신은 이 검증된 proposal과 graph write를 입력으로 삼는 후속 경계입니다. 약한 hypothesis는 새 evidence와 경쟁 과정에서 weaken, retire, supersede될 수 있습니다.

결국 시간이 지날수록 나아지는 원리는 "많이 저장해서"가 아니라, 현재 최선의 설명을 반복해서 공격하고 challenger와 경쟁시키며 provenance가 있는 evidence로 belief state를 갱신하기 때문입니다.

## 검증

문서 변경 후 최소 검증은 아래 명령입니다.

```bash
python -m pytest
```

README 내용이 구현과 어긋나는지 확인할 때는 `SPEC.md`, `ARCH.md`, `RUNTIME.md`, `AUTH_AND_EXECUTION.md`, `TASK.md`를 함께 확인합니다.
