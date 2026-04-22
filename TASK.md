# Continual Research Bot TASK

## 1. Goal

`TASK.md`의 목적은 현재 설계 문서들을 실제 구현 이슈로 분해 가능한 수준의
`execution roadmap + validation gate`로 닫는 것이다.

이 문서는 아래를 강제한다.

- 구현 순서는 `dependency order`를 따라야 한다.
- 각 단계는 `산출물`, `완료 정의`, `필수 테스트`, `실패 게이트`가 있어야 한다.
- 테스트가 없는 task는 완료로 간주하지 않는다.
- 검증되지 않은 단계의 결과를 다음 단계 전제로 사용하면 안 된다.

Source of truth:

- `SPEC.md`
- `ARCH.md`
- `RUNTIME.md`
- `AUTH_AND_EXECUTION.md`

## 2. Planning Principles

### 2.1 Core Principle

이 시스템은 `search wrapper`가 아니라 `belief revision system`이다.
따라서 구현 순서는 UI 편의보다 아래 불변조건을 먼저 닫아야 한다.

- backend가 state authority를 가진다.
- graph write는 canonicalization과 validation 이후에만 허용된다.
- runtime output은 `proposal`일 뿐이며 바로 persistence하면 안 된다.
- auth/session/principal/workspace 검증은 fail-open이 아니라 fail-closed여야 한다.
- repeated run은 누적 저장이 아니라 challenger generation과 revision pressure를 유지해야 한다.

### 2.2 Non-Negotiable Gates

아래 조건 중 하나라도 미충족이면 다음 단계로 넘어가면 안 된다.

- schema contract가 migration, serializer, validator와 일치하지 않음
- idempotency key가 write boundary 전체를 덮지 못함
- principal / workspace / CODEX_HOME isolation이 증명되지 않음
- malformed proposal이 repair 또는 quarantine 없이 persistence 경계에 도달함
- replay / resume 결과가 기존 run ledger와 불일치함
- queue retry가 duplicate write 또는 lost update를 유발함
- stagnation detection 없이 scheduled rerun만 추가됨

### 2.3 Required Validation Evidence

각 단계 완료 시 아래 중 적어도 하나를 남겨야 한다.

- automated test result
- reproducible integration command
- failure injection 기록
- replay artifact 또는 audit log 샘플

검증 evidence 없이 상태만 바뀐 구현은 완료로 인정하지 않는다.

## 3. Recommended Delivery Structure

각 단계는 가능한 한 별도 Linear 하위 이슈로 쪼갠다.

권장 템플릿:

```text
Task:
Owner boundary:
Depends on:
Artifacts:
Required tests:
Failure modes:
Exit gate:
```

## 4. Global Test Philosophy

모든 단계에서 아래 관점을 기본으로 사용한다.

- `happy path`만 확인하지 않는다.
- malformed input, stale state, duplicate execution, mismatch identity를 우선 공격한다.
- `되면 통과`가 아니라 `잘못된 상태가 persistence까지 못 가는지`를 본다.
- silent fallback, silent repair, silent truncation을 금지한다.
- fail 시 `retryable`, `terminal`, `human-review-required`를 구분한다.

특히 아래는 반복적으로 공격해야 하는 공통 실패군이다.

- ownership ambiguity
- auth ambiguity
- invalid proposal acceptance
- duplicate write
- queue mutation mismatch
- stale session reuse
- workspace mismatch
- principal mismatch
- unresolved citation placeholder persistence
- repeated run stagnation

## 5. Phase Plan

## Phase 0. Delivery Baseline And Contracts

목적:
구현 전 단계에서 참조할 canonical contract와 validation fixture를 먼저 고정한다.

Includes:

- repository-level module skeleton 확정
- canonical payload fixture 정의
- test taxonomy와 failure code taxonomy 정의
- validation evidence 기록 방식 정의

Artifacts:

- `RunExecutionRequest`, `ProposalBundle`, `RuntimeEvent` fixture
- session inspect fixture
- queue/job fixture
- failure code enum 초안
- test matrix 문서 초안

Depends on:

- `ARCH.md`
- `RUNTIME.md`
- `AUTH_AND_EXECUTION.md`

Required tests:

- fixture schema parse test
- fixture backward-compatibility snapshot test
- malformed fixture rejection test
- unknown enum / additionalProperties rejection test

Failure modes:

- 서로 다른 문서가 서로 다른 field 이름을 사용함
- runtime fixture와 persistence fixture가 shape mismatch를 가짐
- failure reason taxonomy가 없어 retry policy를 일관되게 적용할 수 없음

Exit gate:

- canonical fixture가 테스트에서 사용 가능해야 한다.
- 이후 단계는 ad-hoc JSON shape를 새로 만들면 안 된다.

## Phase 1. Backend Relational Schema And Persistence Ledger

목적:
run/state/queue/session/idempotency authority를 관계형 저장소에 먼저 고정한다.

Includes:

- `topics`
- `runs`
- `run_events`
- `queue_items`
- `idempotency_keys`
- `session_ledger`
- `session_leases`
- `session_events`
- `scheduler_policies`

Artifacts:

- migration
- ORM or query layer
- append-only event write path
- idempotency ledger write/read API

Depends on:

- Phase 0

Required tests:

- happy path migration test
- rollback / re-run migration idempotency test
- duplicate idempotency key rejection test
- append-only run event immutability test
- concurrent dequeue claim race test
- queue retry counter update test
- stale lease release test
- persistence transaction rollback test

Failure modes:

- duplicate write가 같은 run_id 또는 idempotency key로 허용됨
- queue claim과 run row 생성이 atomic하지 않아 orphan state가 생김
- retry bookkeeping이 last-write-wins로 덮여 audit chain이 깨짐

Exit gate:

- run, queue, idempotency, session 관련 최소 테이블과 transaction boundary가 닫혀야 한다.
- duplicate execution을 DB 레벨에서 막지 못하면 다음 단계 금지.

## Phase 2. Graph Schema, Provenance Graph, And Canonicalization

목적:
Graphiti 또는 Codex output이 곧바로 canonical graph가 되지 않도록 중간 정규화 계층을 먼저 구현한다.

Includes:

- world representation graph schema
- epistemic graph schema
- provenance / process graph schema
- canonicalizer
- dedupe policy
- temporal scope normalization
- hypothesis versioning / supersession relation

Artifacts:

- Neo4j schema constraints
- canonical node/edge mapping spec
- Graphiti-to-Neo4j normalization path
- invalid graph proposal quarantine path

Depends on:

- Phase 1

Required tests:

- happy path canonicalization test
- malformed node/edge payload rejection test
- duplicate evidence / claim dedupe test
- temporal scope missing rejection test
- stale hypothesis version supersession test
- provenance reference missing rejection test
- repeated canonicalization idempotency test
- same proposal replay consistency test

Failure modes:

- 동일 evidence가 중복 node로 축적됨
- challenger hypothesis가 existing best hypothesis와 연결되지 않음
- provenance 없는 claim이 canonical graph에 저장됨
- temporal conflict가 단순 contradiction으로 오분류됨

Exit gate:

- canonicalizer 없이 direct graph write가 가능한 경로가 남아 있으면 안 된다.
- provenance와 temporal scope를 강제하지 못하면 orchestrator 구현 금지.

## Phase 3. Topic Snapshot Read Model And Orchestrator State Machine

목적:
runtime 호출 이전에 backend가 무엇을 읽고 어떤 상태 전이로 run을 관리하는지 고정한다.

Includes:

- topic snapshot loader
- run lifecycle state machine
- frontier selection input shape
- current-best attack requirement
- challenger generation requirement
- reconciliation / retirement stage definition

Artifacts:

- orchestrator service
- state transition rules
- topic snapshot query API
- run intent builder

Depends on:

- Phase 1
- Phase 2

Required tests:

- happy path state transition test
- invalid transition rejection test
- missing topic snapshot fail-closed test
- duplicate run start idempotency test
- run resume from persisted state test
- queue item to run intent mapping test
- stale snapshot version mismatch rejection test
- current-best attack omitted proposal rejection test

Failure modes:

- orchestrator가 run state를 메모리에서만 관리함
- current best hypothesis 공격 없이 단순 검색 run이 통과함
- snapshot version drift로 stale context를 기준으로 persistence함

Exit gate:

- state machine diagram과 실제 코드 경로가 일치해야 한다.
- run이 최소 competition loop 요구조건을 검증하지 못하면 runtime 구현 금지.

## Phase 4. Queue, Worker, Retry, Dead-Letter, Scheduler Foundation

목적:
interactive / scheduled 실행을 동일 job contract로 처리하고, 실패를 명시적으로 분류한다.

Includes:

- `run.execute`
- `run.resume`
- `user_input.process`
- `topic.refresh_schedule`
- `graph.repair`
- retry / backoff policy
- dead-letter queue
- scheduler policy evaluator

Artifacts:

- worker entrypoints
- dequeue/claim/ack/nack path
- retry matrix
- dead-letter inspection path
- scheduler selection function

Depends on:

- Phase 1
- Phase 3

Required tests:

- happy path worker execution test
- duplicate queue delivery idempotency test
- retryable failure requeue test
- terminal failure dead-letter routing test
- queue mutation mismatch rejection test
- backlog prioritization test
- repeated run stagnation scheduling test
- scheduler no-op when competition pressure is already healthy test

Failure modes:

- same queue item이 두 worker에서 동시에 실행됨
- retry 후 duplicate graph write가 발생함
- dead-letter 없이 무한 재시도함
- scheduler가 freshness만 보고 challenger pressure를 무시함

Exit gate:

- duplicate delivery와 terminal failure handling이 닫히기 전에는 live runtime 연결 금지.
- scheduler는 `refresh timer`가 아니라 `competition pressure` 기반 selection을 증명해야 한다.

## Phase 5. Codex Exec Runtime Client And Event Ingestion

목적:
`codex exec --json`을 backend control 아래에서 실행하고 event ledger를 authority로 기록한다.

Includes:

- runtime coordinator
- prompt assembly
- exec launcher
- JSONL event ingestion
- artifact collection
- deadline / budget enforcement

Artifacts:

- `codex exec --json` wrapper
- event normalization pipeline
- artifact store layout
- execution metrics collector

Depends on:

- Phase 0
- Phase 1
- Phase 3
- Phase 4

Required tests:

- happy path exec ingestion test
- malformed JSONL event rejection test
- partial event stream recovery test
- timeout handling test
- process crash retry classification test
- budget exceeded fail-closed test
- wrong workspace root invocation rejection test
- replay from stored artifact consistency test

Failure modes:

- final output만 저장하고 intermediate events를 잃어버림
- transport failure와 model failure를 구분하지 못함
- workspace root / sandbox policy가 invocation마다 흔들림

Exit gate:

- event ledger 없이 output만 믿는 구현은 금지.
- runtime은 backend-owned budgets와 execution policy를 강제해야 한다.

## Phase 6. Tool Registry, Validation, Execution Policy

목적:
tool 사용을 backend registry와 policy 아래로 고정해 drift와 privilege creep를 막는다.

Includes:

- tool manifest registry
- schema validation
- policy class (`read_only`, `deterministic_write`, forbidden non-idempotent write)
- sandbox / network / writable root policy
- tool result normalization

Artifacts:

- tool manifest schema
- policy validator
- executor wrapper
- denied-call audit log

Depends on:

- Phase 0
- Phase 5

Required tests:

- happy path tool dispatch test
- malformed tool args rejection test
- unknown tool rejection test
- forbidden tool class rejection test
- non-idempotent write block test
- permission boundary test for writable roots
- tool output drift normalization test
- tool timeout / retryability classification test

Failure modes:

- 모델이 manifest에 없는 tool을 호출해도 통과함
- writable root가 workspace 바깥까지 열림
- tool output shape drift가 validator 없이 downstream으로 전파됨

Exit gate:

- registry 없는 직접 tool 실행 경로가 있으면 안 된다.
- permission boundary와 fail-closed policy를 테스트로 증명해야 한다.

## Phase 7. Output Validator, Repair Loop, And Context Compaction

목적:
invalid proposal, citation hole, schema drift, context overflow가 persistence 경계에 도달하기 전에 차단한다.

Includes:

- syntax validator
- schema validator
- semantic validator
- policy validator
- minimal repair prompt path
- repair budget handling
- context compaction artifact

Artifacts:

- validator stack
- repair attempt orchestrator
- quarantine store
- compaction summary format

Depends on:

- Phase 5
- Phase 6

Required tests:

- happy path validation test
- malformed JSON proposal repair test
- invalid enum / missing required field repair test
- unresolved citation placeholder rejection test
- hypothesis id inconsistency rejection test
- repair budget exhausted quarantine test
- compaction after long context referential integrity test
- tool result omitted after compaction rejection test

Failure modes:

- repair loop가 semantic invalid proposal을 억지로 통과시킴
- compaction 후 evidence reference가 끊김
- invalid proposal이 quarantine 없이 graph write로 넘어감

Exit gate:

- validator와 repair loop 없이 persistence 연결 금지.
- compaction이 referential integrity를 깨뜨리면 resume/replay 구현 금지.

## Phase 8. Auth, Session Manager, Lease Model, And Per-Principal `CODEX_HOME` Isolation

목적:
user-owned Codex auth를 backend가 좁은 lease contract로만 사용하게 하고 principal/workspace 혼선을 차단한다.

Includes:

- session ledger implementation
- credential locator
- app-server inspector
- bootstrap / inspect / refresh path
- lease issuance and release
- per-principal `CODEX_HOME` isolation
- workspace trust binding

Artifacts:

- `session_manager.py`
- `credential_locator.py`
- `codex_app_server_inspector.py`
- `session_lease_store.py`
- session healthcheck job

Depends on:

- Phase 1
- Phase 5

Required tests:

- happy path interactive bootstrap test
- `account/read` principal fingerprint verification test
- workspace mismatch rejection test
- principal mismatch rejection test
- stale session expiry handling test
- duplicate concurrent lease rejection test
- `CODEX_HOME` isolation leakage test
- copied credential continuity-only fallback restriction test

Failure modes:

- user A session으로 user B workspace가 실행됨
- workspace root가 맞아도 principal fingerprint가 틀린데 통과함
- shared `CODEX_HOME` 때문에 config/auth cache가 섞임
- expired session을 refresh 실패 후에도 계속 재사용함

Exit gate:

- principal, workspace, host binding, trust level을 모두 검증하기 전 scheduled execution 금지.
- fail-open auth path가 하나라도 남아 있으면 interactive live execution도 금지.

## Phase 9. Interactive Run Path

목적:
사용자가 Codex에서 즉시 run을 시작하고, backend가 validated proposal만 반영하는 end-to-end 경로를 닫는다.

Includes:

- topic read
- run trigger API
- topic snapshot load
- runtime execution
- validation / canonicalization / persistence
- user-visible run report

Artifacts:

- interactive run endpoint
- run report view model
- operator-visible failure summary

Depends on:

- Phase 2
- Phase 3
- Phase 4
- Phase 5
- Phase 6
- Phase 7
- Phase 8

Required tests:

- happy path interactive e2e test
- invalid user input classification test
- workspace mismatch fail-closed test
- duplicate trigger idempotency test
- malformed proposal quarantine e2e test
- stale snapshot during interactive run rejection test
- user-visible summary and backend state update consistency test
- interrupted run resume consistency test

Failure modes:

- UI summary는 성공처럼 보이지만 backend state update는 실패함
- user input이 구조화되지 않은 freeform blob로만 남아 frontier에 반영되지 않음
- interrupted run이 resume 후 다른 conclusion을 비정상적으로 중복 기록함

Exit gate:

- interactive run은 summary와 canonical state update를 함께 남겨야 한다.
- duplicate trigger, stale snapshot, invalid proposal에 대한 fail-closed 경로가 없으면 scheduled path 구현 금지.

## Phase 10. Scheduled Run Path

목적:
trusted runner에서만 headless scheduled execution을 허용하고, interactive와 동일한 validation/persistence contract를 재사용한다.

Includes:

- schedule evaluator
- session health preflight
- deferred / retryable / terminal classification
- scheduled run execution policy
- run report and operator notification

Artifacts:

- scheduler worker
- preflight auth check
- deferred run reason model
- reauth-required notification path

Depends on:

- Phase 4
- Phase 8
- Phase 9

Required tests:

- happy path scheduled e2e test
- session expired preflight rejection test
- no active lease available defer test
- principal mismatch scheduled block test
- workspace mismatch scheduled block test
- repeated run stagnation detection before enqueue test
- retry after transient transport failure test
- terminal auth failure no-retry test

Failure modes:

- scheduled worker가 interactive보다 약한 validation으로 실행됨
- reauth required 상태인데 계속 queue retry만 반복함
- stale topic을 이유로 너무 자주 rerun하여 stagnation만 누적함

Exit gate:

- scheduled path는 interactive path보다 약한 auth/validation policy를 가지면 안 된다.
- stagnation detection과 auth preflight가 없으면 release 금지.

## Phase 11. Observability, Repair, Replay, And Operational Controls

목적:
운영자가 실패 원인, duplicate write, stagnation, repair efficacy를 확인하고 안전하게 복구할 수 있게 한다.

Includes:

- run/event/queue/session dashboards
- repair job
- replay job
- stagnation metrics
- alerting
- audit query surfaces

Artifacts:

- operational dashboards
- replay CLI or admin path
- repair workflow
- alert definitions

Depends on:

- Phase 1 through Phase 10

Required tests:

- happy path replay consistency test
- same artifact repeated replay determinism test
- repair job after canonicalization failure test
- dead-letter recovery test
- missing artifact replay rejection test
- alert emission on repeated auth failure test
- alert emission on stagnation threshold breach test
- audit trail completeness test

Failure modes:

- replay가 original run ledger와 다른 결과를 남김
- repair job이 원인 분리 없이 원본 evidence를 손상시킴
- stagnation이 발생해도 운영자가 알 수 없음

Exit gate:

- human review 없이도 자동 복구가 아니라 `safe repair + explicit audit`가 되어야 한다.
- replay determinism을 증명하지 못하면 운영 handoff 불가.


## Phase 12. UX Contract, CLI Information Architecture, And Read Models

목적:
처음 보는 사용자가 연구를 시작하고 상태를 이해할 수 있도록 CLI/UX contract를 먼저 고정한다.

Includes:

- CLI command taxonomy
- user-facing terminology
- JSON output schema
- human-readable summary shape
- topic/run/queue/memory read models
- graph visualization artifact contract

Artifacts:

- CLI command spec
- UX view model types or docs
- graph export schema fixture
- README update with command examples

Depends on:

- Phase 11

Required tests:

- CLI command spec snapshot test
- JSON output fixture parse test
- human summary golden snapshot test
- graph export fixture parse test
- README command examples sanity check

Failure modes:

- CLI가 backend state authority를 우회하는 command를 약속함
- user-facing summary가 uncertainty/conflict를 숨김
- graph visualization이 source of truth처럼 오해됨

Exit gate:

- 구현 phase가 시작되기 전에 CLI/UX contract가 테스트 가능한 fixture로 고정되어야 한다.

## Phase 13. CLI Implementation And Operator Workflow

목적:
사용자가 실제로 topic/run/queue/memory/ops 상태를 CLI로 다룰 수 있게 한다.

Includes:

- `crb` console entrypoint
- `topic create/list/show`
- `run start/status/resume`
- `queue list/retry/dead-letter`
- `memory snapshot/conflicts/hypotheses`
- `ops health/audit/replay`
- `--json` and human-readable output modes

Artifacts:

- CLI module
- command handlers
- output formatter
- command help text
- README quickstart update

Depends on:

- Phase 12
- Phase 9
- Phase 10
- Phase 11

Required tests:

- CLI help test
- command parser test
- topic create/list/show test
- run start/status/resume test
- queue/dead-letter command test
- `--json` schema test
- human-readable output snapshot test
- no direct persistence write bypass test

Failure modes:

- CLI command가 service/orchestrator boundary를 우회함
- JSON output이 automation에 불안정함
- user-facing status가 retryable/terminal/human-review failure를 구분하지 못함

Exit gate:

- README만 보고 기본 연구 흐름을 CLI로 실행/확인할 수 있어야 한다.

## Phase 14. Graph Visualization And Memory Explorer

목적:
사용자가 current best, challenger, evidence, conflict, provenance를 graph artifact로 이해할 수 있게 한다.

Includes:

- topic subgraph export
- hypothesis/evidence/conflict/provenance projection
- DOT / Mermaid / JSON export
- lightweight HTML artifact generation when feasible without heavy dependency
- conflict-focused memory explorer summary

Artifacts:

- graph view module
- export schema
- sample visualization artifacts
- README visualization section

Depends on:

- Phase 12
- Phase 13
- Phase 2
- Phase 11

Required tests:

- graph export determinism test
- conflict subgraph export test
- provenance edge inclusion test
- missing node/reference rejection test
- DOT/Mermaid/JSON snapshot tests
- HTML artifact smoke test when implemented

Failure modes:

- visualization이 canonical graph와 다른 relation을 보여줌
- conflict/provenance를 누락해 사용자가 belief revision 이유를 이해하지 못함
- export가 non-deterministic해 diff/replay가 불가능함

Exit gate:

- 최소 하나의 topic snapshot에서 graph artifact를 생성하고, current best / challenger / conflict / evidence provenance를 확인할 수 있어야 한다.

## Phase 15. End-To-End First-User Experience And Documentation

목적:
처음 보는 사용자가 README와 CLI help만 보고 연구를 시작하고 결과를 이해하는 end-to-end path를 닫는다.

Includes:

- first-run tutorial flow
- sample topic fixture
- CLI quickstart validation
- graph visualization walkthrough
- troubleshooting guide
- terminology glossary

Artifacts:

- README final update
- sample topic/run fixture
- tutorial transcript or golden output
- troubleshooting section

Depends on:

- Phase 13
- Phase 14

Required tests:

- README quickstart command smoke test
- sample topic end-to-end test
- tutorial output snapshot test
- graph walkthrough artifact generation test
- docs link check or grep-based sanity check

Failure modes:

- README가 실제 CLI와 불일치함
- 처음 보는 사용자가 결과 위치나 failure 상태를 찾지 못함
- agentic memory/conflict/belief revision 설명이 실제 구현과 다름

Exit gate:

- clean checkout에서 README 안내를 따라 최소 sample research flow를 실행하고 결과/graph/failure 상태를 확인할 수 있어야 한다.


## Phase 16. Local Web UI UX Contract And View Models

목적:
CLI만으로는 파악하기 어려운 연구 진행 상태를 localhost web dashboard에서 한눈에 볼 수 있도록 UX contract와 view model을 먼저 고정한다.

Includes:

- dashboard information architecture
- overview / hypothesis board / graph explorer / run timeline view model
- web API JSON schemas
- sample topic fixture for visual UI
- empty/error/loading state contract

Artifacts:

- web UI UX spec doc or fixture
- web view model types
- API response fixtures
- sample graph UI fixture

Depends on:

- Phase 12
- Phase 13
- Phase 14
- Phase 15

Required tests:

- overview view model fixture parse test
- graph explorer JSON schema test
- run timeline fixture test
- empty/dead-letter/stale-claim state snapshot test
- authority notice presence test

Failure modes:

- UI가 graph projection을 source of truth처럼 보이게 함
- conflict/dead-letter/stale claim 상태가 정상처럼 보임
- 사용자가 현재 우세 가설과 다음 연구 행동을 한눈에 알 수 없음

Exit gate:

- web implementation 전에 화면별 데이터 contract가 고정되어야 한다.

## Phase 17. Localhost Web Server And Dashboard Implementation

목적:
`crb web serve` 또는 동등한 명령으로 localhost dashboard를 실행하고, topic overview와 run/queue/memory 상태를 브라우저에서 확인할 수 있게 한다.

Includes:

- local HTTP server
- dashboard HTML shell
- `/api/topics`, `/api/topics/{id}`, `/api/topics/{id}/runs`, `/api/topics/{id}/queue`
- read-only default policy
- CLI command integration

Artifacts:

- web server module
- web API handlers
- static HTML/CSS/JS shell
- `crb web serve` command
- README local web section

Depends on:

- Phase 16

Required tests:

- web server route smoke test
- API JSON response schema test
- localhost bind default test
- `crb web serve --help` test
- read-only no direct write test
- HTML shell smoke test

Failure modes:

- web server가 remote interface에 무심코 bind됨
- UI route가 DB write boundary를 우회함
- API가 CLI/backend read model과 다른 상태를 보여줌

Exit gate:

- sample DB로 localhost dashboard를 열고 overview/run/queue 상태를 확인할 수 있어야 한다.

## Phase 18. Interactive Graph Explorer And Visual Research UX

목적:
사용자가 연구가 잘 진행 중인지 graph와 timeline을 한눈에 이해할 수 있도록 interactive graph explorer를 구현한다.

Includes:

- Cytoscape.js 또는 동등한 graph-specific renderer integration
- node/edge type styling
- current best / challenger / evidence / conflict / provenance filters
- selected node detail panel
- run/provenance filtering
- graph history/latest toggle
- visual empty/error states

Artifacts:

- graph explorer frontend
- embedded/vendored asset policy
- sample HTML snapshot or screenshot artifact
- README visual walkthrough

Depends on:

- Phase 16
- Phase 17
- Phase 14

Required tests:

- graph explorer data adapter test
- node/edge style class assignment test
- filter state unit test or DOM snapshot test
- selected node detail rendering test
- history/latest toggle test
- sample topic visual smoke test

Failure modes:

- 큰 graph에서 UI가 사용 불가능하게 느려짐
- node/edge 색상이나 필터가 belief relation을 오해하게 만듦
- conflict와 uncertainty가 시각적으로 묻힘
- CDN asset 의존 때문에 offline/local 환경에서 UI가 깨짐

Exit gate:

- sample topic에서 current best, challenger, evidence, provenance, conflict/challenge relation을 브라우저에서 탐색할 수 있어야 한다.

## Phase 19. Web UX Final Audit And First Research Demo

목적:
처음 보는 사용자가 CLI와 localhost web UI를 함께 사용해 실제 연구 상태를 이해할 수 있는지 end-to-end로 검증한다.

Includes:

- first-user web walkthrough
- ISO26262 sample research demo artifact
- README update with web dashboard screenshots or textual walkthrough
- visual QA checklist
- UX terminology cleanup

Artifacts:

- README web UI section
- sample graph dashboard artifact
- walkthrough transcript
- final UX audit notes

Depends on:

- Phase 17
- Phase 18

Required tests:

- README web quickstart smoke test
- sample topic dashboard generation test
- graph artifact/link sanity test
- UX copy grep test for authority notice and conflict visibility
- final `pytest` run

Failure modes:

- web UI는 있지만 사용자가 무엇을 봐야 하는지 모름
- CLI와 web UI가 서로 다른 상태를 보여줌
- README가 실제 web command와 불일치함

Exit gate:

- clean checkout에서 README를 따라 CLI로 sample topic을 만들고 localhost web UI에서 graph/timeline/memory 상태를 확인할 수 있어야 한다.


## Phase 20. Visual Run-State Dashboard And Playwright UX Verification

목적:
사용자가 localhost Web UI에서 현재 연구가 실제로 돌고 있는지, 멈췄는지, 어떤 작업이 어떤 논리 그래프와 연결되는지 한눈에 파악할 수 있게 한다.

Includes:

- Overview의 running/queued/completed/dead-letter/stale count 분리
- `Running now` 또는 동등한 현재 실행 상태 카드
- 현재 실행 중 queue/run의 objective, run id, queue id, latest event 표시
- 실행 중 작업과 hypothesis/evidence/conflict/next action graph relation 연결 표시
- Graph tab의 current best / challenger / evidence / provenance / conflict 필터 가독성 개선
- Runs/Queue/Memory tab에서 멈춤/대기/실패 상태가 명확히 드러나는 empty/error state
- Playwright E2E test와 주요 탭 screenshot artifact

Artifacts:

- Web UI view model / API 확장
- dashboard UI 개선
- Playwright E2E test suite
- screenshot artifacts for Overview / Graph / Runs / Queue / Memory
- README 또는 docs의 Web UI 사용/판독 가이드 업데이트

Depends on:

- Phase 16
- Phase 17
- Phase 18
- Phase 19

Required tests:

- Playwright E2E: localhost dashboard loads sample topic
- Playwright E2E: Overview screenshot includes running/queued/completed/dead-letter/stale counts
- Playwright E2E: Graph tab screenshot includes logical graph and selected node/detail panel
- Playwright E2E: Runs tab screenshot shows current/empty execution state clearly
- Playwright E2E: Queue tab screenshot separates queued, claimed/running, completed, dead-letter
- Playwright E2E: Memory tab screenshot shows current best/challenger/conflict state
- API/view model unit tests for running-now relation to queue/run/graph nodes
- screenshot artifact paths recorded in Linear comment / PR handoff

Failure modes:

- 사용자가 `Queue 57`을 보고 57개 Codex session이 병렬 실행 중이라고 오해함
- 실행 중 작업이 0개인지, queue 대기인지, dead-letter인지 구분하지 못함
- 논리 구성도 graph와 현재 run/queue item의 연결이 UI에서 보이지 않음
- graph는 예쁘지만 research status를 판단할 수 없음
- screenshot 없이 DOM/unit test만 통과해 실제 UX가 검증되지 않음

Exit gate:

- 실제 sample DB 또는 fixture DB로 localhost Web UI를 띄우고, 주요 탭 screenshot을 남겨야 한다.
- 사용자 관점에서 현재 실행 중인 작업 수와 작업 내용, 또는 멈춤 상태를 10초 안에 파악할 수 있어야 한다.
- graph tab에서 현재 우세 가설, challenger, evidence/provenance, 관련 run/queue context를 확인할 수 있어야 한다.
- Playwright E2E와 screenshot 검증이 통과해야 한다.


## Phase 21. Autonomous Research Worker Loop And Convergence Stop

목적:
사용자가 한 번 명령을 내리면 특정 topic의 queued research tasks를 자동으로 하나씩 실행하고, 유의미한 수확이 없거나 충분히 수렴하면 안전하게 멈추는 research worker loop를 구현한다.

Includes:

- `crb worker run --topic <topic_id> --loop` 또는 동등한 CLI
- `crb worker status --topic <topic_id>` 상태 조회
- topic당 단일 active worker loop lease / heartbeat
- queue item claim -> Codex runtime execution -> ProposalBundle validation -> canonical graph/memory update -> next queue 반복
- iteration별 yield 판정
- consecutive no-yield stop
- max iterations / budget / empty queue / repeated malformed proposal stop
- stop reason과 yield history persistence
- Web UI에서 active loop, iteration count, no-yield streak, stop reason 표시

Artifacts:

- worker loop service
- convergence / yield analysis module
- CLI commands and JSON contracts
- loop state persistence migration
- Web API/view model update
- README usage section
- tests and screenshot artifacts

Depends on:

- Phase 4
- Phase 5
- Phase 7
- Phase 9
- Phase 11
- Phase 13
- Phase 20

Required tests:

- unit: yielded iteration when graph digest or meaningful graph counts change
- unit: no-yield when run quarantines or graph is unchanged
- unit: convergence policy stops at `max_consecutive_no_yield`
- unit: convergence policy stops at `max_iterations` and budget thresholds
- integration: fake runtime yields once then no-yields until loop stops
- integration: repeated malformed proposal stops without infinite retry
- integration: empty queue with no active conflicts stops as converged/paused
- CLI JSON tests for worker run/status/stop
- Web API/view-model tests for loop state
- Playwright screenshot for running loop and stopped/converged loop states

Failure modes:

- worker consumes Codex tokens indefinitely without meaningful graph updates
- queued count is mistaken for active worker count
- malformed proposals are retried forever
- multiple workers run the same topic concurrently and corrupt queue/run state
- loop stops silently without operator-visible reason
- convergence is claimed merely because queue is empty while unresolved conflicts remain
- Git/Linear implementation orchestrator is confused with CRB research worker runtime

Exit gate:

- 실제 sample topic 또는 fixture topic에서 worker loop를 실행해 at least one successful yielded iteration과 one no-yield/convergence stop path를 검증해야 한다.
- CLI와 Web UI에서 running / queued / stopped / converged / blocked를 구분할 수 있어야 한다.
- stop reason, iteration count, consecutive no-yield, last meaningful graph change가 backend ledger와 UI에 남아야 한다.
- loop가 멈춘 뒤 다시 시작해도 idempotency와 queue claim safety가 유지되어야 한다.


## Phase 22. Korean First-User Dashboard UX Overhaul And Run Timing

목적:
처음 보는 사용자가 dashboard만 보고 연구 상태, graph 의미, run 시간, queue/dead-letter 의미, 다음 행동을 이해할 수 있도록 Web UI를 한국어-first UX로 전면 개선한다.

Includes:

- Dashboard 기본 사용자-facing copy 한국어화
- Overview / Graph / Runs / Queue / Memory 각 탭 상단 도움말
- “대시보드 읽는 법” help panel 또는 overlay
- Graph legend / glossary: `TOP`, `HYP`, `CLA`, `EVI`, `PRO`, `CON`, `supports`, `challenges`, `visualizes`
- Runs 탭 run별 요청/시작/완료/실패/중단 시각과 duration 표시
- Queue 탭 `Queued`, `Running/Claimed`, `Completed`, `Dead-letter`, `Stale` 설명
- Dead-letter의 failure code, retry 가능 여부, human-review-required, 다음 행동 설명
- source-of-truth / projection 차이를 쉬운 한국어로 설명
- Web UI screenshot artifact 갱신

Artifacts:

- Web view model / API 확장
- Korean dashboard copy / glossary data
- CSS/layout redesign for first-user readability
- README 또는 docs의 dashboard 사용 설명 업데이트
- Playwright E2E screenshots: Overview / Graph / Runs / Queue / Memory / Help

Depends on:

- Phase 17
- Phase 18
- Phase 20
- Phase 21

Required tests:

- unit: run timing view model derives requested/claimed/completed/duration labels
- unit: missing timestamps are shown as unknown/not-yet-completed without crashing
- unit: glossary includes TOP/HYP/CLA/EVI/PRO/CON/supports/challenges/visualizes/dead-letter
- unit: queue state help includes retryable and human-review-required guidance
- copy regression: dashboard primary user-facing labels are Korean by default
- Playwright E2E: Graph screenshot shows Korean legend and acronym explanations
- Playwright E2E: Runs screenshot shows start/end/duration or unknown/not-yet-completed states
- Playwright E2E: Queue screenshot explains Dead-letter and next action
- Playwright E2E: Help panel/overlay is reachable and screenshot artifact is recorded

Failure modes:

- 처음 보는 사용자가 `CLA`, `HYP`, `EVI`, `PRO` 의미를 몰라 graph를 해석하지 못함
- Runs 탭에서 언제 실행됐고 언제 끝났는지 몰라 연구 진행 시간을 판단하지 못함
- Dead-letter가 무엇인지 몰라 실패/보류/재시도 가능성을 구분하지 못함
- dashboard가 영어/내부 용어 중심이라 한국어 사용자에게 불친절함
- 도움말이 README에만 있고 dashboard 안에서는 찾을 수 없음
- screenshot 없이 DOM/unit test만 통과해 실제 first-user UX가 검증되지 않음

Exit gate:

- 처음 보는 사용자가 dashboard 안의 도움말만으로 각 탭의 의미와 다음 행동을 이해할 수 있어야 한다.
- Graph 탭에서 약어 badge와 edge 의미가 한국어로 설명되어야 한다.
- Runs 탭에서 run timing과 duration이 보여야 한다.
- Queue 탭에서 Dead-letter의 의미와 조치 방향이 보여야 한다.
- Playwright screenshot artifact가 Linear/PR handoff에 남아야 한다.

## 6. Cross-Phase Dependency Summary

권장 구현 순서:

1. Phase 0: contracts and fixtures
2. Phase 1: relational authority
3. Phase 2: graph schema and canonicalization
4. Phase 3: orchestrator state machine
5. Phase 4: queue / worker / scheduler foundation
6. Phase 5: Codex exec runtime client
7. Phase 6: tool registry / policy
8. Phase 7: output validator / repair / compaction
9. Phase 8: auth / session / lease / CODEX_HOME isolation
10. Phase 9: interactive run path
11. Phase 10: scheduled run path
12. Phase 11: observability / repair / replay
13. Phase 12: UX contract / CLI information architecture / read models
14. Phase 13: CLI implementation / operator workflow
15. Phase 14: graph visualization / memory explorer
16. Phase 15: first-user experience / documentation
17. Phase 16: local web UI UX contract / view models
18. Phase 17: localhost web server / dashboard
19. Phase 18: interactive graph explorer / visual research UX
20. Phase 19: web UX final audit / first research demo
21. Phase 20: visual run-state dashboard / Playwright UX verification
22. Phase 21: autonomous research worker loop / convergence stop
23. Phase 22: Korean first-user dashboard UX / run timing / glossary

이 순서를 바꾸면 생기는 대표 리스크:

- schema authority 전에 runtime을 만들면 invalid output이 저장 경계까지 올라간다.
- canonicalization 전에 graph write를 열면 hypothesis lifecycle이 깨진다.
- validator 전에 interactive path를 붙이면 malformed proposal이 운영 상태를 오염시킨다.
- auth isolation 전에 scheduled path를 열면 principal/workspace 혼선이 발생한다.

## 7. Definition Of Done For Any Future Task

각 구현 이슈는 아래를 만족해야 완료다.

- scope가 위 phase 중 하나에 명확히 매핑된다.
- 산출물이 코드, schema, 문서, 운영 경계 중 무엇인지 명확하다.
- required tests가 실제로 추가되거나 실행된다.
- failure mode 중 최소 하나 이상이 재현 가능하게 공격되었다.
- validation evidence가 남는다.
- 다음 phase의 전제가 되는 contract를 문서 또는 테스트로 고정했다.

## 8. Explicit Stop Rules

아래 상황이면 구현을 멈추고 새 이슈 또는 설계 업데이트가 필요하다.

- Graphiti output과 canonical schema 사이 충돌이 반복되는데 normalization rule이 정의되지 않음
- Codex auth 정책 변화로 ChatGPT-managed scheduled path가 더 이상 안전하게 유지되지 않음
- replay determinism이 transport 제약 때문에 구조적으로 보장되지 않음
- scheduler가 stagnation 감소보다 비용 증폭만 만들고 selection policy가 방어되지 않음
- user-visible summary와 backend state update를 원자적으로 묶을 수 없는 구조적 결함이 발견됨
- CLI/visualization이 backend authority를 우회하거나 source-of-truth처럼 오해될 수 있음
- localhost web UI가 conflict/dead-letter/stale-claim 상태를 숨겨 운영자가 연구가 잘 되고 있다고 오해함
- Web UI가 총 queue 수와 현재 실행 중 작업 수를 구분하지 못해 병렬 실행 상태를 오해하게 만듦
- Research worker loop가 no-yield / convergence / budget / human-review-required stop 조건 없이 Codex runtime을 계속 소비함
- Dashboard가 영어/약어/내부 용어 중심이라 처음 보는 사용자가 graph, run 시간, dead-letter 의미를 이해하지 못함
