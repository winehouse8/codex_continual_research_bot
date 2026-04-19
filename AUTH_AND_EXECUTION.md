# Continual Research Bot Auth And Execution Design

## 1. Goal

`AUTH_AND_EXECUTION.md`는 `DEE-12`의 두 번째 축인 `user-owned Codex Max plan OAuth execution architecture`를 구현 가능한 수준으로 닫는 문서다.

핵심 질문은 하나다.

`사용자 소유 ChatGPT/Codex access를 backend가 어떻게 안전하게 사용하면서 interactive run과 scheduled run을 모두 지원할 것인가`

본 문서는 다음을 정의한다.

- auth/session bootstrap flow
- refresh / renewal lifecycle
- headless scheduled execution 경로
- session expiry / re-auth recovery
- user credential isolation
- local vs hosted runtime boundary
- Codex adapter contract와 provider/session abstraction
- session ledger schema

## 2. External Constraints

OpenAI 공식 문서에서 확인되는 제약:

- Codex는 ChatGPT sign-in 또는 API key sign-in을 지원한다.
- Codex cloud는 ChatGPT sign-in이 필요하다.
- CLI/IDE는 ChatGPT 또는 API key 모두 가능하다.
- active ChatGPT sign-in session은 local cache를 재사용하며, active use 중에는 토큰 refresh가 자동으로 수행된다.
- headless 환경에서는 `codex login --device-auth` 또는 trusted-machine에서 생성한 `~/.codex/auth.json` 복사가 fallback으로 제시된다.
- automation에서는 API key가 권장 기본값이지만, trusted runner에서는 ChatGPT-managed auth도 가능하다.

이 문서의 설계 판단:

- v1 제품 thesis가 `user-owned Codex Max plan path`를 전제로 하므로, `trusted runner + ChatGPT-managed auth` 경로를 설계의 중심으로 둔다.
- 다만 공식 문서가 automation의 기본 경로로는 API key를 권장하므로, scheduled execution은 `trusted/private runtime only`로 제한해야 한다.

## 3. Operating Modes

### 3.1 Supported Modes

| Mode | Primary auth | Runtime location | Purpose |
| --- | --- | --- | --- |
| interactive-local | ChatGPT OAuth | user-owned machine | Codex UI initiated research |
| scheduled-trusted-runner | ChatGPT OAuth | private trusted host | periodic reruns using user-owned session |
| emergency-api-fallback | API key | private trusted host | break-glass operator path, not default product path |

### 3.2 Unsupported Modes In v1

- public CI runners using user ChatGPT auth
- shared multi-tenant hosted execution with cross-user session reuse
- backend-owned master OAuth account serving multiple users
- any design that copies raw user credentials into application database

## 4. Design Thesis

한 줄 요약:

`The backend never becomes the identity authority; it only leases a user-approved Codex execution session through a narrow adapter contract and records enough metadata to recover safely.`

핵심 원칙:

- user principal과 backend execution principal은 항상 1:1로 연결된다.
- raw OAuth material은 가능하면 OS credential store 또는 isolated secret volume에만 저장한다.
- relational DB에는 raw token 대신 `session ledger metadata`만 저장한다.
- scheduled execution은 `consent + trusted host + renewable session`이 모두 만족할 때만 허용한다.
- auth refresh는 runtime concern이 아니라 별도 `session manager` concern이다.

## 5. High-Level Architecture

```mermaid
flowchart LR
    U[User] --> UI[Codex / Backend UI]
    UI --> SM[Session Manager]
    SM --> CS[Credential Sink]
    SM --> SL[(Session Ledger)]
    ORCH[Research Orchestrator] --> EA[Execution Adapter]
    EA --> SM
    EA --> CLI[Codex CLI Runtime]
    CLI --> CS
    CLI --> OA[OpenAI Auth Service]
    CLI --> EA
    EA --> ORCH
```

구성 요소:

- `Session Manager`: bootstrap, refresh, health check, renewal orchestration
- `Credential Sink`: `~/.codex/auth.json` 또는 keyring-backed isolated storage
- `Session Ledger`: DB에 저장되는 metadata와 lease 상태
- `Execution Adapter`: run 시점에 특정 principal session을 attach
- `Codex CLI Runtime`: 실제 Codex execution surface

## 6. Principal And Session Model

### 6.1 Principal Types

- `user_principal`: 실제 ChatGPT/Codex plan 소유자
- `runtime_principal`: 특정 trusted host에서 해당 user principal을 대신해 Codex를 실행하는 identity binding
- `operator_principal`: 시스템 운영자. user session을 임의 대체할 권한이 없다.

### 6.2 Session Types

- `interactive_session`: 사용자가 직접 login을 시작한 session
- `leased_execution_session`: interactive bootstrap 이후 scheduler가 재사용하는 session
- `renewal_session`: re-auth 또는 device-auth 복구 중인 임시 상태
- `revoked_session`: 더 이상 실행에 사용 불가한 상태

## 7. Adapter Contracts

### 7.1 Auth Provider Contract

```text
AuthProvider.beginBootstrap(principal_id, mode) -> BootstrapChallenge
AuthProvider.completeBootstrap(challenge_id, proof) -> SessionMaterialHandle
AuthProvider.refresh(session_handle) -> RefreshResult
AuthProvider.inspect(session_handle) -> SessionHealth
AuthProvider.revoke(session_handle) -> void
```

### 7.2 Execution Provider Contract

```text
ExecutionProvider.acquireLease(principal_id, purpose, ttl) -> ExecutionLease
ExecutionProvider.run(request, lease) -> RuntimeResult
ExecutionProvider.releaseLease(lease_id, outcome) -> void
ExecutionProvider.healthcheck(lease_id) -> LeaseHealth
```

### 7.3 Why Two Contracts

- auth bootstrap/refresh와 run execution은 lifecycle이 다르다.
- future provider abstraction에서 `Codex CLI`, `Codex app server`, `OpenClaw-like bridge`를 같은 execution interface로 묶기 쉽다.
- session refresh failure가 runtime implementation과 강하게 결합되는 것을 막는다.

## 8. Bootstrap Flow

### 8.1 Interactive Bootstrap

정상 경로:

1. 사용자가 backend에서 `Enable scheduled research with my Codex account`를 승인한다.
2. backend는 `Session Manager.beginBootstrap` 호출 후 local trusted host에 bootstrap intent를 생성한다.
3. trusted host에서 `codex login` 또는 headless면 `codex login --device-auth`를 수행한다.
4. Codex CLI가 OpenAI login flow를 완료하고 local credential sink를 갱신한다.
5. `Session Manager.inspect`가 credential presence, workspace restriction, principal fingerprint를 기록한다.
6. session ledger에 `active` 상태와 host binding이 생성된다.

### 8.2 Headless Bootstrap Fallback

공식 문서가 허용하는 fallback을 제품 정책으로 좁게 해석하면 아래와 같다.

1. browser 가능한 trusted machine에서 `codex login` 수행
2. file-based credential storage가 설정된 경우 `~/.codex/auth.json` 확보
3. secure copy로 trusted runner의 isolated secret volume에 전달
4. first healthcheck run을 통해 refresh 가능 여부 확인
5. session ledger를 `active`로 승격

주의:

- 이 경로는 private trusted host에서만 허용한다.
- auth.json은 password 수준의 secret으로 취급한다.
- application DB나 general blob store에 저장하지 않는다.

## 9. Session Ledger Schema

DB에는 raw token이 아니라 아래 metadata만 저장한다.

| Field | Type | Notes |
| --- | --- | --- |
| `session_id` | uuid | internal stable id |
| `principal_id` | uuid | user principal reference |
| `provider` | text | `openai-codex-chatgpt` |
| `host_id` | text | trusted runtime host |
| `credential_locator` | text | secret path or keyring locator |
| `state` | text | `bootstrapping`, `active`, `refreshing`, `expired`, `reauth_required`, `revoked` |
| `workspace_id` | text nullable | if workspace restriction applies |
| `account_fingerprint` | text | non-secret identifier for mismatch detection |
| `last_validated_at` | timestamptz | last successful inspect |
| `last_refreshed_at` | timestamptz nullable | best effort metadata |
| `lease_count` | int | concurrent lease guard |
| `last_failure_code` | text nullable | auth / transport / policy |
| `last_failure_at` | timestamptz nullable | recovery orchestration |
| `reauth_url` | text nullable | bootstrap challenge pointer if pending |
| `created_at` | timestamptz | audit |
| `updated_at` | timestamptz | audit |

보조 테이블:

- `session_leases`
- `session_events`
- `session_host_bindings`

## 10. Session State Machine

```mermaid
stateDiagram-v2
    [*] --> Bootstrapping
    Bootstrapping --> Active: bootstrap verified
    Bootstrapping --> ReauthRequired: bootstrap abandoned or invalid
    Active --> Refreshing: proactive refresh or run-triggered refresh
    Refreshing --> Active: refresh success
    Refreshing --> Expired: refresh token invalid
    Active --> Expired: execution reports auth failure
    Expired --> ReauthRequired: healthcheck confirms unusable
    ReauthRequired --> Bootstrapping: user restarts login
    Active --> Revoked: user disconnects
    ReauthRequired --> Revoked: issue closed or access withdrawn
```

## 11. Lease Model

scheduled execution과 interactive execution이 같은 credential sink를 동시에 만질 수 있으므로,
direct session access 대신 `lease`를 도입한다.

lease 규칙:

- 하나의 session은 기본적으로 단일 active lease만 허용
- interactive run이 시작되면 scheduled lease는 대기 또는 skip
- lease에는 `purpose`, `run_id`, `expires_at`, `host_id`가 포함
- stale lease는 heartbeat timeout으로 회수

`ExecutionLease` 예시:

```json
{
  "lease_id": "lease_01",
  "session_id": "sess_01",
  "principal_id": "user_01",
  "purpose": "scheduled_run",
  "host_id": "runner-seoul-01",
  "expires_at": "2026-04-19T12:30:00Z"
}
```

## 12. Interactive Vs Scheduled Execution

### 12.1 Shared Execution Contract

interactive와 scheduled 모두 같은 `RunExecutionRequest`를 사용한다.
차이는 context source와 auth readiness check뿐이다.

| Dimension | Interactive | Scheduled |
| --- | --- | --- |
| initiator | user action | scheduler |
| auth prerequisite | current session usable | active leaseable session required |
| freshness check | optional | mandatory |
| recovery on auth failure | immediate user prompt | mark `reauth_required` and skip future runs |
| summary delivery | synchronous to user | persisted report + user notification |

### 12.2 Scheduled Execution Sequence

```mermaid
sequenceDiagram
    participant SCH as Scheduler
    participant ORCH as Orchestrator
    participant SM as Session Manager
    participant EA as Execution Adapter
    participant CLI as Codex CLI
    participant DB as Ledger / Stores

    SCH->>ORCH: enqueue run.execute
    ORCH->>SM: acquireLease(principal, scheduled_run)
    SM->>DB: mark lease active
    ORCH->>EA: run(request, lease)
    EA->>SM: inspect(session)
    SM-->>EA: session healthy
    EA->>CLI: codex exec ...
    CLI-->>EA: runtime events + final output
    EA-->>ORCH: validated proposal bundle
    ORCH->>DB: persist run + release lease
    SM->>DB: lease closed
```

## 13. Refresh And Renewal Policy

### 13.1 Proactive Refresh

OpenAI 문서상 active use 중 CLI가 refresh를 자동 수행하므로,
backend는 토큰 expiry 자체보다 `session usability`를 모니터링한다.

권장 정책:

- every scheduled run 전에 lightweight `inspect` 또는 no-op healthcheck 실행
- last validation이 오래된 session은 scheduler가 먼저 healthcheck job 발행
- refresh는 run 도중 무조건 시도하지 말고, session manager를 통해 한 번만 시도

### 13.2 Refresh Outcomes

- `success`: `last_refreshed_at` 업데이트, run 계속
- `retryable_failure`: 짧은 backoff 후 healthcheck queue로 이동
- `hard_failure`: `reauth_required` 전이, future scheduled runs 중단
- `principal_mismatch`: 즉시 revoke, security incident로 기록

### 13.3 Renewal

renewal은 refresh보다 상위 개념이다.

- refresh: existing session continuation
- renewal: user 재인증을 통한 새로운 bootstrap

renewal이 필요한 경우:

- auth.json missing
- refresh token invalid
- workspace restriction mismatch
- MFA or security challenge 재통과 필요
- host migration으로 credential sink 재배치 필요

## 14. Local Vs Hosted Runtime Boundary

### 14.1 Local-First v1

v1 권장 경계:

- credential sink는 사용자 소유 또는 사용자가 신뢰하는 private host에 둔다.
- backend control plane은 hosted 가능하지만, execution plane은 private runtime을 기본으로 둔다.
- public SaaS multi-tenant worker에서 user ChatGPT OAuth를 직접 보관/실행하지 않는다.

### 14.2 Why This Boundary Is Required

- 공식 문서는 ChatGPT-managed auth automation을 trusted runner 같은 좁은 환경에서만 다룬다.
- ChatGPT OAuth material은 API key보다 회전/복구 경로가 덜 단순하다.
- 제품 thesis도 `user-owned backend authority`를 전제로 하므로, credential path 역시 사용자 통제 하에 있어야 한다.

## 15. Credential Isolation

필수 정책:

- user별 credential locator 분리
- host filesystem path도 principal별 isolated directory 사용
- one host one session file 공유 금지
- application logs에서 token, auth file path, raw callback URL redaction
- session ledger는 raw secret을 저장하지 않음
- session export/import는 operator action이 아니라 explicit user bootstrap action으로만 허용

권장 저장 전략:

- 1순위: OS keyring
- 2순위: encrypted secret volume + file-based `auth.json`
- 금지: plain relational DB column, S3-like generic bucket, issue comment, run artifact bundle

## 16. Failure Handling

### 16.1 Failure Classes

| Failure | Detection point | Handling |
| --- | --- | --- |
| auth material missing | inspect before run | `reauth_required` |
| refresh failed | healthcheck or run start | single refresh attempt, then fail closed |
| principal mismatch | inspect | revoke immediately |
| runner host unavailable | lease acquire | reschedule on same trusted pool only |
| concurrent session mutation | lease conflict | serialize and retry later |
| CLI login path blocked | bootstrap | device-auth or trusted-machine copy fallback |

### 16.2 Fail Closed Policy

auth ambiguity가 발생하면 run을 계속하지 않는다.

예시:

- session이 누구 것인지 fingerprint가 확실하지 않음
- expected workspace와 실제 workspace가 다름
- copied auth cache가 refresh는 되지만 principal metadata 확인이 안 됨

이 경우 결과 품질 문제가 아니라 보안/ownership 문제이므로 즉시 중단한다.

## 17. OpenClaw-Inspired But Narrower Abstraction

OpenClaw에서 참고할 점:

- auth profile을 token sink로 두고 runtime이 한 곳에서만 읽도록 한 점
- provider hook에 `run`, `runNonInteractive`, `prepareRuntimeAuth` 같은 경계를 둔 점
- session bookkeeping을 provider/runtime 사이 hook으로 분리한 점

우리 구조에서 다르게 가져갈 점:

- provider ecosystem 확장보다 `OpenAI Codex path`를 우선한다.
- multi-provider genericity보다 `single reliable auth/session ledger`를 먼저 닫는다.
- auth profile 전체를 product storage로 가져오지 않고, credential locator + session metadata만 DB에 남긴다.

## 18. Minimum Module Breakdown

- `session_manager.py`
- `credential_locator.py`
- `execution_adapter.py`
- `codex_cli_provider.py`
- `session_healthcheck_job.py`
- `session_lease_store.py`
- `session_event_store.py`

각 모듈의 1차 책임:

- `session_manager.py`: bootstrap, inspect, refresh, revoke
- `credential_locator.py`: keyring/file locator resolution
- `execution_adapter.py`: lease 획득 후 runtime request 실행
- `codex_cli_provider.py`: `codex login`, `codex exec`, healthcheck wrapper
- `session_healthcheck_job.py`: scheduled preflight
- `session_lease_store.py`: concurrency guard
- `session_event_store.py`: audit trail

## 19. Implementation Order

1. session ledger + lease tables 정의
2. credential locator abstraction 구현
3. bootstrap + inspect path 구현
4. execution adapter가 특정 session lease를 붙여 `codex exec` 실행하도록 구현
5. healthcheck / refresh orchestration 추가
6. scheduled run gating 추가
7. reauth notification / UX 연결

## 20. Residual Risks

- ChatGPT-managed auth는 API key보다 automation 적합성이 낮다.
- official automation guidance가 API key를 기본 권장하므로, 장기적으로 provider support 정책 변화 리스크가 있다.
- file-based auth cache를 쓰는 경우 host compromise 영향이 크다.
- user-owned private runner가 항상 online이 아닐 수 있다.

v1 대응:

- private trusted host requirement를 제품 제약으로 명시
- auth healthcheck를 run 시작 전에 강제
- break-glass API key mode를 내부 운영 fallback으로만 유지

## 21. References

- OpenAI Codex authentication docs: `https://developers.openai.com/codex/auth`
- OpenAI Codex non-interactive docs: `https://developers.openai.com/codex/noninteractive`
- OpenAI Help: Using Codex with your ChatGPT plan: `https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan`
- OpenClaw OAuth concepts: `https://github.com/openclaw/openclaw/blob/main/docs/concepts/oauth.md`
- OpenClaw provider hooks: `https://github.com/openclaw/openclaw/blob/main/docs/concepts/model-providers.md`
