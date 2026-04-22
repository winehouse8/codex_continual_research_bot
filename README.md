# Continual Research Bot

`codex_continual_research_bot`은 `crb` CLI를 기본 사용자 인터페이스로 쓰는 continual research system입니다. 사용자는 CLI에서 topic을 만들고 run, queue, memory, graph를 조작하며, localhost Web UI는 같은 backend state를 한눈에 보는 read-only dashboard입니다. Codex는 사용자가 직접 쓰는 UI가 아니라 서비스 내부 runtime에서 검색, 추론, proposal 생성을 수행하는 엔진이고, backend는 그 proposal을 검증한 뒤 canonical graph와 ledger에 저장하는 state authority입니다.

## Install And Start

개발 환경에서는 저장소 루트에서 패키지를 editable install 하거나 `PYTHONPATH=src`를 사용합니다. 기본 로컬 DB는 `.crb/crb.sqlite3`이며, 실험용 DB를 분리하려면 `CRB_DB_PATH`를 지정합니다.

```bash
export CRB_DB_PATH=.crb/tutorial.sqlite3
```

`crb doctor`는 DB 생성 전에도 현재 설정과 workspace를 점검합니다. `crb init`은 로컬 backend storage와 migration을 초기화합니다.

## CLI Quickstart

가장 짧은 실행 흐름은 아래 순서입니다.

```bash
crb init --json
crb doctor --json
crb topic create "Codex auth boundary" --objective "Track session ownership risk" --json
crb run start topic_codex_auth_boundary --input "counterargument: warning-only stale sessions may be safe" --json
crb worker run --topic topic_codex_auth_boundary --loop --max-iterations 3 --json
crb worker status --topic topic_codex_auth_boundary --json
crb topic show topic_codex_auth_boundary
crb memory hypotheses topic_codex_auth_boundary --json
crb memory conflicts topic_codex_auth_boundary --json
crb graph export topic_codex_auth_boundary --scope history --format json --output graph-history.json
crb web serve
```

`run start`의 JSON 응답에는 `run_id`와 `queue_item_id`가 들어 있습니다. 이후 `run status`, `queue dead-letter`, `ops audit`에는 그 값을 사용합니다. 현재 `crb web serve`는 `--topic` 옵션을 받지 않습니다. dashboard를 띄운 뒤 브라우저의 topic selector에서 topic을 고릅니다. 포트만 바꾸고 싶으면 `crb web serve --port 8787`처럼 실행할 수 있습니다.

`crb worker run`의 기본 executor는 `codex`이며 `codex exec --json` invocation artifact를 `.crb/worker-artifacts` 아래에 남깁니다. deterministic local proposal 경로는 테스트와 fixture 검증 전용이며, 필요할 때만 `crb worker run --executor fixture ...`로 명시합니다.

튜토리얼 fixture는 `fixtures/sample_topic_run.json`이고, golden transcript는 `fixtures/tutorial_transcript.txt`입니다.

## 사용자가 보는 흐름

```text
사용자
  ↓
crb CLI / localhost Web UI
  ↓
Backend Orchestrator
  ↓
Queue / Scheduler
  ↓
Codex Runtime
  ↓
Output Validator / Repair
  ↓
Canonical Graph / Ledger
  ↓
Web UI Projection / CLI Summary
```

| 단계 | 역할 |
| --- | --- |
| 사용자 | 연구 주제, 반론, 질문, 운영 판단을 입력합니다. |
| `crb` CLI | topic/run/queue/memory/graph/ops를 다루는 안정적인 entrypoint입니다. |
| Backend Orchestrator | topic snapshot, queue, run lifecycle, idempotency, frontier를 관리합니다. |
| Codex Runtime | backend가 만든 run request를 바탕으로 검색, 추론, 후보 결론 생성을 수행합니다. |
| Output Validator / Repair | Codex output을 truth가 아니라 proposal로 보고 schema, provenance, graph write 조건을 검증합니다. |
| Canonical Graph / Ledger | 검증된 evidence, claim, hypothesis, conflict, provenance, run event를 authoritative하게 저장합니다. |
| CLI Summary / Graph Visualization | backend state를 읽은 view를 보여줍니다. source of truth는 아닙니다. |

## 주요 CLI 명령

| 목적 | 명령 |
| --- | --- |
| 환경 확인 | `crb doctor --json` |
| 로컬 저장소 초기화 | `crb init --json` |
| topic 생성 | `crb topic create "Codex auth boundary" --objective "Track session ownership risk" --json` |
| topic 목록 | `crb topic list --json` |
| topic 상태 보기 | `crb topic show topic_codex_auth_boundary` |
| run 시작 | `crb run start topic_codex_auth_boundary --input "counterargument: warning-only stale sessions may be safe" --json` |
| run 상태 보기 | `crb run status "$run_id" --json` |
| run 재개 요청 | `crb run resume "$run_id"` |
| worker loop 실행 | `crb worker run --topic topic_codex_auth_boundary --loop --max-iterations 3 --json` |
| worker loop fixture 실행 | `crb worker run --topic topic_codex_auth_boundary --loop --executor fixture --max-iterations 3 --json` |
| worker loop 상태 | `crb worker status --topic topic_codex_auth_boundary --json` |
| worker loop 중지 | `crb worker stop --topic topic_codex_auth_boundary --json` |
| queue 보기 | `crb queue list --topic topic_codex_auth_boundary --json` |
| dead-letter 확인 | `crb queue dead-letter "$queue_item_id"` |
| queue retry 요청 | `crb queue retry "<dead-letter-queue-item-id>" --reason "operator confirmed transient transport failure" --json` |
| stale claim 복구 | `crb queue recover-stale "$queue_item_id" --reason "operator confirmed stale worker lease" --action retry --json` |
| memory 요약 | `crb memory snapshot topic_codex_auth_boundary --json` |
| conflict 보기 | `crb memory conflicts topic_codex_auth_boundary --json` |
| hypothesis 보기 | `crb memory hypotheses topic_codex_auth_boundary --json` |
| graph JSON export | `crb graph export topic_codex_auth_boundary --scope latest --format json --output graph.json` |
| graph history export | `crb graph export topic_codex_auth_boundary --scope history --format json --output graph-history.json` |
| graph DOT export | `crb graph export topic_codex_auth_boundary --format dot --output graph.dot` |
| graph Mermaid export | `crb graph export topic_codex_auth_boundary --format mermaid --output graph.mmd` |
| graph HTML 보기 | `crb graph view topic_codex_auth_boundary --scope latest --format html --output graph.html` |
| 운영 상태 확인 | `crb ops health --json` |
| run audit | `crb ops audit "$run_id" --json` |
| 완료 run replay 요청 | `crb ops replay "<completed-run-id>" --reason "operator replay audit" --json` |
| 로컬 dashboard 실행 | `crb web serve` |

아래 예시는 CLI 계약 테스트가 검증하는 실제 명령 모음입니다.

```bash
crb init --json
crb doctor --json
crb topic create "Codex auth boundary" --objective "Track session ownership risk" --json
crb topic list --json
crb topic show topic_codex_auth_boundary
crb topic show topic_codex_auth_boundary --json
crb run start topic_codex_auth_boundary --input "counterargument: warning-only stale sessions may be safe" --json
crb run status "$run_id" --json
crb run resume "$run_id"
crb worker run --topic topic_codex_auth_boundary --loop --max-iterations 3 --json
crb worker status --topic topic_codex_auth_boundary --json
crb worker stop --topic topic_codex_auth_boundary --json
crb queue list --topic topic_codex_auth_boundary --json
crb queue retry "<dead-letter-queue-item-id>" --reason "operator confirmed transient transport failure" --json
crb queue dead-letter "$queue_item_id"
crb memory snapshot topic_codex_auth_boundary --json
crb memory conflicts topic_codex_auth_boundary --json
crb memory hypotheses topic_codex_auth_boundary --json
crb graph export topic_codex_auth_boundary --scope latest --format json --output graph.json
crb graph export topic_codex_auth_boundary --scope history --format json --output graph-history.json
crb graph export topic_codex_auth_boundary --format dot --output graph.dot
crb graph export topic_codex_auth_boundary --format mermaid --output graph.mmd
crb graph view topic_codex_auth_boundary --scope latest --format html --output graph.html
crb ops health --json
crb ops audit "$run_id" --json
crb ops replay "<completed-run-id>" --reason "operator replay audit" --json
crb web serve
```

## Codex의 역할

- 사용자는 연구를 시작하기 위해 Codex를 직접 열 필요가 없습니다.
- Codex는 backend 내부 runtime에서 research run을 수행합니다.
- Codex output은 truth가 아니라 `ProposalBundle` 후보입니다.
- proposal은 output validator, repair, canonicalizer, persistence boundary를 통과해야 graph/ledger에 저장됩니다.
- backend는 topic state, queue, provenance, session/lease, run lifecycle, canonical graph write를 소유합니다.

## Terminology Glossary

아래 용어는 CLI summary와 memory/graph view에서 반복해서 등장합니다.

| 용어 | 의미 |
| --- | --- |
| `current best hypothesis` | backend의 현재 최선 설명입니다. 사실이나 절대 진실이 아니라 새 evidence와 challenger에 의해 바뀔 수 있습니다. |
| `challenger target` | 공격, 대안 생성, adversarial verification 대상으로 선택된 hypothesis 또는 conflict입니다. |
| `active conflict` | 아직 해결되지 않았고 숨기면 안 되는 tension입니다. 다음 run의 연구 압력이 됩니다. |
| `memory` | Codex session context가 아니라 backend-owned hypothesis, evidence, conflict, provenance state입니다. |
| `provenance` | claim, hypothesis, conflict, graph write가 어디서 왔는지 설명하는 source/process trace입니다. |

## Agentic Memory와 Belief Revision

이 프로젝트는 fact database가 아니라 competing hypothesis system입니다.

| 객체 | 의미 |
| --- | --- |
| Evidence | claim이나 hypothesis를 지지하거나 흔드는 원천 자료입니다. |
| Claim | evidence에서 추출된 검증 가능한 주장입니다. |
| Hypothesis | 현재 best 또는 challenger로 경쟁하는 설명 단위입니다. |
| Conflict | 오류가 아니라 다음 research pressure를 만드는 unresolved tension입니다. |
| Provenance | 어떤 run, source, process에서 판단이 나왔는지 추적하는 기록입니다. |

반복 run이 좋아지는 이유는 많이 저장해서가 아닙니다. current-best hypothesis를 계속 공격하고 challenger를 생성하며, support evidence와 challenge evidence를 함께 비교하기 때문입니다. support-only 반복은 stagnation으로 보고, 약한 hypothesis는 weaken, retire, supersede될 수 있습니다. 시간이 지나며 좋아지는 것은 provenance가 연결된 evidence로 경쟁 가설을 계속 갱신하기 때문입니다.

## 연구 진행 판단법

연구가 잘 진행되는지는 "결론이 많아졌는가"보다 "가설 경쟁과 검증 압력이 유지되는가"로 봅니다. CLI에서는 `topic show`, `run status`, `queue list`, `memory hypotheses`, `memory conflicts`, `graph export`를 함께 확인하고, Web UI에서는 Overview, Graph, Runs, Queue, Memory tab을 같은 기준으로 봅니다.

| 상태 | 확인 신호 |
| --- | --- |
| 잘 진행 중 | 새로운 evidence/claim이 추가되고 graph digest나 node count가 바뀝니다. |
| 잘 진행 중 | current best hypothesis에 support와 challenge가 모두 붙습니다. |
| 잘 진행 중 | challenger hypothesis나 challenger target이 생깁니다. |
| 잘 진행 중 | active conflict, dead-letter, stale queue가 숨겨지지 않고 화면이나 CLI summary에 표시됩니다. |
| 잘 진행 중 | next action queue가 의미 있는 후속 검증 작업을 만듭니다. |
| 주의 필요 | dead-letter가 반복되거나 retry 후에도 같은 failure code가 남습니다. |
| 주의 필요 | claimed queue item이 오래 유지되어 stale worker lease처럼 보입니다. |
| 주의 필요 | support-only run이 반복되고 challenge evidence가 거의 없습니다. |
| 주의 필요 | current best가 오래 바뀌지 않고 challenger도 늘지 않습니다. |
| 주의 필요 | graph projection과 `topic show` / `memory snapshot` summary가 크게 다릅니다. 이 경우 canonical graph/ledger를 기준으로 확인합니다. |

## Graph Visualization Walkthrough

Graph export와 HTML view는 backend-owned canonical graph 또는 topic snapshot에서 만든 projection입니다. 검토, 공유, diff에는 유용하지만 not a source of truth입니다. Graph artifact의 authority notice는 backend graph and provenance ledgers가 authoritative하다는 점을 명시합니다. `--scope latest`는 최신 canonical graph write 하나를 보여주고, `--scope history`는 topic의 전체 canonical graph write history를 누적 projection해 run별 provenance와 revision relation을 함께 보여줍니다.

| 파일 | 용도 |
| --- | --- |
| `graph.json` | `memory_explorer`를 포함한 machine-readable visualization artifact |
| `graph.dot` | Graphviz 기반 리뷰 diff |
| `graph.mmd` | Mermaid 기반 문서/이슈 공유 |
| `graph.html` | 의존성 없는 로컬 inspection page |
| `docs/graph-explorer-sample.html` | web graph explorer의 sample HTML snapshot artifact |

web graph explorer는 packaged local SVG renderer를 사용합니다. 별도 CDN asset을 불러오지 않으며, current best, challenger, evidence, conflict, provenance node group과 challenge/conflict/provenance relation을 서로 다른 style class로 렌더링합니다. `Latest` / `History` toggle은 CLI의 `--scope latest` / `--scope history`와 같은 projection boundary를 사용합니다.

## Local Web Dashboard

`crb web serve`는 기본적으로 `127.0.0.1:8765`에 read-only dashboard를 실행합니다. 아래 명령을 실행하면 브라우저에서 `http://127.0.0.1:8765/dashboard`를 열고 topic selector에서 topic을 선택합니다. 포트를 바꾸려면 `--port`를 사용합니다.

```bash
crb web serve
```

Web UI는 topic 생성, run 시작, queue retry 같은 write 작업을 제공하지 않습니다. 그런 작업은 CLI로 실행합니다. Dashboard와 API는 backend read model을 보여주는 projection이며, source of truth는 backend state, canonical graph, queue, provenance ledger입니다.

| 화면 | 사용자가 보는 것 | 판단 기준 |
| --- | --- | --- |
| Overview | topic 상태, snapshot version, running/queued/completed/dead-letter/stale count, `Running now` 실행 카드, current best, active conflict | 총 queue 수와 현재 실행 중 worker 수를 혼동하지 않는지, stale/dead-letter가 숨겨지지 않는지 봅니다. |
| Hypothesis Board | 현재 구현에서는 Overview의 `Current Best`와 Memory projection으로 확인합니다. current best, challenger, support/challenge count를 같이 봅니다. | challenger가 없거나 support-only 반복이면 stagnation 가능성이 있습니다. |
| Graph Explorer | evidence, claim, hypothesis, conflict, provenance node와 edge projection | `Latest`는 최신 graph write, `History`는 누적 projection입니다. graph는 inspection surface이지 authority가 아닙니다. |
| Run Timeline | run ledger, queue request, latest event, graph relation summary, queued next action | completed run이어도 graph write가 없거나 failure code가 남으면 audit이 필요합니다. |
| Queue | queued, running/claimed, completed, stale, retryable, dead-letter work를 별도 그룹으로 표시 | dead-letter 반복, stale claim 장기 유지, retry 불가 failure는 blocker 후보입니다. |
| Memory | current best, challenger, conflict 상태와 hypothesis/evidence/conflict/challenge candidate count, graph digest | count가 변하지 않거나 active conflict가 계속 누적되면 후속 run input을 조정합니다. |

주요 read-only API:

| 목적 | 경로 |
| --- | --- |
| topic 목록 | `GET /api/topics` |
| topic overview | `GET /api/topics/{topic_id}` |
| topic runs | `GET /api/topics/{topic_id}/runs` |
| topic queue | `GET /api/topics/{topic_id}/queue` |
| topic worker loop | `GET /api/topics/{topic_id}/worker-loop` |
| topic memory | `GET /api/topics/{topic_id}/memory` |
| topic graph latest | `GET /api/topics/{topic_id}/graph/latest` |
| topic graph history | `GET /api/topics/{topic_id}/graph/history` |
| topic dashboard bundle | `GET /api/web/topics/{topic_id}/dashboard` |

`dashboard` bundle은 `worker_loop`, `run_state.status_counts`, `run_state.running_now`, `run_state.queue_groups`, `run_state.run_timeline_items`를 포함합니다. `worker_loop`는 active state, iteration count, consecutive no-yield streak, stop reason, last meaningful graph change를 보여줍니다. `running_now`는 `objective`, `run_id`, `queue_item_id`, `latest_event`, 관련 graph node 요약을 함께 보여주므로 현재 작업이 실제 실행 중인지, 대기 중인지, stale claim인지, dead-letter인지 바로 판독할 수 있습니다.

모든 non-GET 요청은 `read_only_web_surface`로 거부됩니다. Graph tab은 current best / challenger / evidence / conflict / provenance filter, selected node detail panel, provenance selector, visual empty/error state를 제공합니다.

Playwright 기반 UX 검증은 Chromium browser가 설치된 환경에서 아래처럼 실행합니다. 테스트는 `artifacts/playwright/overview.png`, `graph.png`, `runs.png`, `queue.png`, `memory.png`, `manifest.json`을 생성합니다.

```bash
python -m playwright install chromium
pytest tests/test_web_playwright.py
```

## Web Quickstart And First Research Demo

아래 ISO26262 sample walkthrough는 clean checkout에서 CLI와 localhost web UI가 같은 backend state를 보여주는지 확인하는 Phase 19 smoke path입니다. 스크린샷 대신 명령, fixture, dashboard tab별 확인 지점을 고정합니다.

```bash
export CRB_DB_PATH=.crb/iso26262-demo.sqlite3
crb init --json
crb doctor --json
crb topic create "ISO 26262 safety case drift" --objective "Track whether ASIL decomposition evidence still supports the current safety case after a supplier tool qualification change." --json
crb run start topic_iso_26262_safety_case_drift --input "counterargument: supplier tool confidence may be stale after the latest qualification delta" --json
crb run status run_5e3826f4ce35 --json
crb queue list --topic topic_iso_26262_safety_case_drift --json
crb memory snapshot topic_iso_26262_safety_case_drift --json
crb memory hypotheses topic_iso_26262_safety_case_drift --json
crb graph export topic_iso_26262_safety_case_drift --scope latest --format json --output iso26262-graph.json --json
crb graph view topic_iso_26262_safety_case_drift --scope latest --format html --output iso26262-graph.html --json
crb web serve
```

브라우저에서 `http://127.0.0.1:8765/dashboard`를 열고 `topic_iso_26262_safety_case_drift`를 선택합니다.

| Dashboard tab | 확인할 상태 |
| --- | --- |
| Overview | ISO26262 topic과 objective에서 초기화된 current best hypothesis |
| Runs | `run_5e3826f4ce35`가 worker claim 전에도 `queue_request` timeline item으로 표시됨 |
| Queue | `queue_5e3826f4ce35`가 같은 requested run id를 가리킴 |
| Memory | graph digest, hypothesis count, conflict count가 projection으로 표시됨 |
| Graph | latest/history toggle과 current best / challenger / evidence / conflict / provenance filters |

이 web dashboard와 graph artifact는 모두 projection입니다. `not a source of truth` authority notice가 보이지 않거나 active conflict 상태가 숨겨지면 UX regression으로 취급합니다.

Phase 19 산출물:

| 파일 | 용도 |
| --- | --- |
| `fixtures/iso26262_sample_research_demo.json` | ISO26262 first research demo command and expected id fixture |
| `fixtures/iso26262_web_dashboard_artifact.json` | sample graph dashboard artifact and visual QA checklist |
| `docs/phase-19-first-research-walkthrough.md` | first-user walkthrough transcript |
| `docs/phase-19-web-ux-audit.md` | final UX audit notes and visual QA checklist |

## Understanding The First Result

첫 run은 곧바로 최종 결론을 남기기보다 queued/resumable backend job을 만드는 경우가 많습니다. `run_id`와 `queue_item_id`를 기준으로 run lifecycle, queue 상태, memory projection, graph artifact를 차례로 확인합니다.

- `crb run status "$run_id" --json`은 run lifecycle과 실패 분류를 보여줍니다.
- `crb queue list --topic topic_codex_auth_boundary --json`은 queued, retryable, human-review-required work를 보여줍니다.
- `crb queue dead-letter "$queue_item_id"`는 failure code와 retry 가능 여부를 보여줍니다.
- `crb memory hypotheses topic_codex_auth_boundary --json`은 current best hypothesis와 challenger를 보여줍니다.
- `crb memory conflicts topic_codex_auth_boundary --json`은 active conflict를 숨기지 않고 보여줍니다.

## Troubleshooting

- `backend_not_initialized`: 같은 `CRB_DB_PATH`로 `crb init --json`을 실행합니다.
- `topic_not_found`: `crb topic list --json`으로 실제 `topic_id`를 확인합니다.
- `run_not_found`: `run start` JSON 응답에서 받은 `run_id`를 사용했는지 확인합니다.
- `queue_item_not_found`: `run start` 응답 또는 `queue list`에서 받은 `queue_item_id`를 사용합니다.
- `queue_retry_rejected`: 먼저 dead-letter 상태와 retryability를 확인합니다.
- `replay_rejected`: replay는 completed run artifact가 있을 때만 요청할 수 있습니다.
- graph 파일이 보이지 않음: `--output`에 지정한 정확한 경로와 parent directory 권한을 확인합니다.

## 현재 한계와 운영상 주의

- 실제 외부 검색이나 scheduled execution 경로에서는 Codex 인증과 trusted runtime 환경이 필요할 수 있습니다.
- scheduled run은 backend policy, session health, lease 상태에 의존합니다.
- CLI 출력은 backend state를 읽은 view 또는 enqueue receipt입니다. CLI가 persistence authority가 아닙니다.
- graph visualization은 projection이며 canonical graph/ledger를 대체하지 않습니다.
- replay는 completed run artifact가 있을 때만 backend policy를 통해 요청할 수 있습니다.

## 더 읽을 문서

- [SPEC.md](SPEC.md): 제품 목표와 belief revision 원리
- [ARCH.md](ARCH.md): backend authority, orchestrator, graph, scheduler 경계
- [RUNTIME.md](RUNTIME.md): Codex runtime과 proposal validation 경계
- [AUTH_AND_EXECUTION.md](AUTH_AND_EXECUTION.md): Codex auth/session/lease 운영 모델
- [docs/cli-ux-contract.md](docs/cli-ux-contract.md): CLI command contract와 UX read model
- [docs/phase-19-first-research-walkthrough.md](docs/phase-19-first-research-walkthrough.md): ISO26262 first research demo
- [docs/phase-19-web-ux-audit.md](docs/phase-19-web-ux-audit.md): final web UX audit and visual QA checklist
