# Continual Research Bot

`codex_continual_research_bot`은 `crb` CLI로 조작하는 continual research system입니다. 사용자는 CLI에서 topic을 만들고, run을 실행하고, queue/memory/graph 상태를 확인합니다. Codex는 사용자가 직접 조작해야 하는 화면이 아니라 서비스 내부 runtime에서 검색, 추론, proposal 생성을 수행하는 엔진입니다. backend는 그 proposal을 검증한 뒤 canonical graph와 ledger에 저장하는 state authority입니다.

## Install And Start

개발 환경에서는 저장소 루트에서 패키지를 editable install 하거나 `PYTHONPATH=src`를 사용합니다. 기본 로컬 DB는 `.crb/crb.sqlite3`이며, 실험용 DB를 분리하려면 `CRB_DB_PATH`를 지정합니다.

```bash
export CRB_DB_PATH=.crb/tutorial.sqlite3
```

`crb doctor`는 DB 생성 전에도 현재 설정과 workspace를 점검합니다. `crb init`은 로컬 backend storage와 migration을 초기화합니다.

## CLI Quickstart

가장 짧은 실행 흐름은 아래 순서입니다.

```bash
crb doctor --json
crb init --json
crb topic create "Codex auth boundary" --objective "Track session ownership risk" --json
crb run start topic_codex_auth_boundary --input "counterargument: warning-only stale sessions may be safe" --json
crb topic show topic_codex_auth_boundary
crb memory conflicts topic_codex_auth_boundary --json
crb graph view topic_codex_auth_boundary --scope latest --format html --output graph.html
```

`run start`의 JSON 응답에는 `run_id`와 `queue_item_id`가 들어 있습니다. 이후 상태 확인에는 그 값을 사용합니다.

튜토리얼 fixture는 `fixtures/sample_topic_run.json`이고, golden transcript는 `fixtures/tutorial_transcript.txt`입니다.

## 사용자가 보는 흐름

```text
사용자
  ↓
crb CLI
  ↓
Backend Orchestrator
  ↓
Codex Runtime
  ↓
Output Validator / Repair
  ↓
Canonical Graph / Ledger
  ↓
CLI Summary / Graph Visualization
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
| queue 보기 | `crb queue list --topic topic_codex_auth_boundary --json` |
| dead-letter 확인 | `crb queue dead-letter "$queue_item_id"` |
| queue retry 요청 | `crb queue retry "<dead-letter-queue-item-id>" --reason "operator confirmed transient transport failure" --json` |
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

## Graph Visualization Walkthrough

Graph export와 HTML view는 backend-owned canonical graph 또는 topic snapshot에서 만든 projection입니다. 검토, 공유, diff에는 유용하지만 not a source of truth입니다. Graph artifact의 authority notice는 backend graph and provenance ledgers가 authoritative하다는 점을 명시합니다. `--scope latest`는 최신 canonical graph write 하나를 보여주고, `--scope history`는 topic의 전체 canonical graph write history를 누적 projection해 run별 provenance와 revision relation을 함께 보여줍니다.

| 파일 | 용도 |
| --- | --- |
| `graph.json` | `memory_explorer`를 포함한 machine-readable visualization artifact |
| `graph.dot` | Graphviz 기반 리뷰 diff |
| `graph.mmd` | Mermaid 기반 문서/이슈 공유 |
| `graph.html` | 의존성 없는 로컬 inspection page |

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
