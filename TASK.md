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
