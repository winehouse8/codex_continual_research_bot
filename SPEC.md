# Continual Research Bot SPEC

## 1. Executive Summary

`Continual Research Bot`은 특정 주제에 대해 장기간 반복 연구를 수행하면서,
이전 실행 결과를 누적 저장하는 것을 넘어서 기존 믿음 체계를 계속 수정해 나가는
`belief revision system for research`다.

### 1.1 Bottom Line

이 문서의 핵심 결론은 아래 다섯 줄로 요약된다.

- 이 시스템은 `search wrapper`나 `summary bot`으로 설계하면 안 된다.
- 이 시스템은 `fact database`가 아니라 `hypothesis competition and revision system`이어야 한다.
- 사용자와 메인 에이전트의 접점은 `Codex`여야 한다.
- 장기 상태, provenance, conflict 해소, 스케줄 실행은 사용자 소유 백엔드가 맡아야 한다.
- v1은 `추가 전용 외부 API 없이`, 사용자가 이미 보유한 `OpenAI Codex Max plan` 실행 경로를 주된 LLM 동력으로 삼는 방향이 타당하다.

즉, 우리가 만드는 것은 "더 많이 저장하는 시스템"이 아니라
"반복될수록 더 많은 challenger hypothesis를 만들고 경쟁시켜 더 좋은 가설 상태에 접근하는 시스템"이다.

### 1.2 Go / No-Go Verdict

- `Go`: Codex Max 중심 경로만으로도 v1은 설계 가능하다.
- `No-Go`: 다만 Codex 자체를 장기 메모리, 스케줄러, belief authority로 간주하면 실패한다.
- `Required`: backend가 state, queue, provenance, revision policy를 직접 소유해야 한다.
- `Required`: 시스템의 기본 객체는 fact가 아니라 hypothesis여야 한다.

### 1.3 Reading Guide

이 문서는 아래 질문에 순서대로 답하도록 구성한다.

1. 무엇을 만드는가
2. 왜 단순 search/summarization으로는 부족한가
3. Codex와 backend는 무엇을 각각 맡는가
4. v1에서 반드시 필요한 것과 미뤄도 되는 것은 무엇인가

### 1.4 Buildability Check

이 스펙만으로도 우리가 원하는 방향의 v1을 설계할 수 있는지에 대한 판단은 아래와 같다.

- `Yes`: `Codex-centered UX`, `hypothesis-first state model`, `user-owned backend authority`라는 세 축은 충분히 정의되어 있다.
- `Yes`: 추가 전용 API 없이도 `user-owned OpenAI Codex Max plan` 경로를 주 실행 엔진으로 쓰는 설계는 성립한다.
- `Only If`: 다만 이 문서를 `Codex가 장기 메모리와 스케줄러까지 맡는 구조`로 해석하면 구현은 실패한다.
- `Only If`: 또 `fact accumulation system`으로 해석하면 제품 목표와 어긋난다.

즉, 이 스펙의 핵심 구현 조건은 아래 네 가지다.

- backend가 topic state, queue, provenance, revision history를 직접 소유한다.
- 모든 핵심 결론은 immutable fact가 아니라 revisable hypothesis로 관리한다.
- Codex는 사용자 접점이자 research execution engine으로 사용한다.
- 각 run이 끝날 때 user-visible summary와 backend-owned state update가 동시에 남는다.

## 2. Product Thesis

### 2.1 One-Line Definition

`Continual Research Bot`은 토픽별 장기 메모리, 가설 그래프, provenance,
belief revision 기록을 유지하면서 interactive run과 scheduled run을 통해
지속적으로 연구 상태를 개선하는 시스템이다.

### 2.2 What Success Means

성공 기준은 아래 한 문장으로 정의한다.

`같은 topic을 n번 연구했을 때, 시스템이 더 많은 문장을 쌓았느냐가 아니라 더 많은 challenger를 만들고 더 강한 비판과 경쟁을 거쳐 더 좋은 hypothesis state에 도달했느냐`

### 2.3 What This Is Not

아래와 같은 시스템은 본 스펙의 목표를 충족하지 못한다.

- 대화 로그를 길게 누적하는 시스템
- 문서 chunk와 vector DB만 쌓는 시스템
- 정적 fact를 저장했다고 가정하는 knowledge graph
- conflict를 yes/no로만 판정하는 파이프라인
- 검색 결과를 요약해 반환하고 상태 수정은 하지 않는 에이전트

## 3. Core Product Decisions

### 3.1 No Absolute Facts, Only Competing Hypotheses

이 시스템은 "세상에 확정된 fact가 저장된다"는 철학을 기본 전제로 삼지 않는다.
대신 다음 전제를 채택한다.

- 시스템이 갖는 것은 `truth`가 아니라 `current best hypothesis state`다.
- 모든 핵심 결론은 언제든 새 evidence에 의해 강화, 약화, 분기, 폐기될 수 있다.
- claim은 독립적으로 존재할 수 있지만, 제품의 중심 객체는 claim 자체보다 `hypothesis`다.
- contradiction은 오류 신호라기보다 belief revision을 유도하는 입력이다.
- 더 합리적이고 진실에 가까운 가설은 `explicit hypothesis competition`과 `selection pressure`를 통해서만 살아남는다.

즉, 이 시스템은 facts를 축적하는 구조가 아니라
`high-confidence hypotheses under continual competition`을 관리하는 구조여야 한다.

이 문장에서 말하는 competition은 비유가 아니라 설계 원리다.

- 모든 run은 현재 best hypothesis를 방어만 하는 것이 아니라 공격 가능한 대상으로 다뤄야 한다.
- 시스템은 새 evidence를 찾는 것만으로 충분하지 않고, `challenger hypothesis generation`을 계속 수행해야 한다.
- 발전은 memory accumulation만으로 생기지 않고 `critical challenge`, `adversarial verification`, `selection pressure`에서 생긴다.

### 3.2 Why Repetition Can Produce Better Beliefs

반복 연구가 발전을 낳는 이유는 단순 누적이 아니라 경쟁 구조를 반복하기 때문이다.

핵심 메커니즘은 아래와 같다.

1. 현재 best hypothesis를 기준 상태로 불러온다.
2. 그 가설을 공격하거나 대체할 수 있는 challenger hypothesis를 생성한다.
3. support evidence와 challenge evidence를 모두 탐색한다.
4. adversarial verification과 reconciliation을 거쳐 더 강한 설명을 남기고 약한 설명을 약화 또는 폐기한다.
5. 남은 unresolved conflict를 다음 run의 frontier로 되돌린다.

즉 반복 실행의 가치는 "더 많이 본다"가 아니라
"더 많이 경쟁시키고, 더 많이 비판하고, 더 자주 잘못된 hypothesis를 퇴출시킨다"에 있다.

### 3.3 Codex-Centered User Experience

사용자와 메인 에이전트가 만나는 주 인터페이스는 `Codex`다.

사용자는 Codex에서 다음 행동을 할 수 있어야 한다.

- 새로운 research topic 생성
- 특정 topic의 현재 연구 경과 확인
- 현재 시점의 최선 가설과 결론 확인
- topic에 대한 아이디어, 의견, 반론, 조사 방향 제안
- 제안된 아이디어를 research queue에 적재

사용자가 추가한 의견은 system of record가 아니며 자동 진실로 채택되지 않는다.
하지만 다음을 만족하는 `research candidate input`으로 취급해야 한다.

- 후속 조사 가치가 있다.
- 기존 hypothesis와 tension을 일으킬 수 있다.
- 새로운 search / validation task를 생성할 수 있다.

### 3.4 User-Owned Backend

백엔드는 사용자가 소유하고 제어한다. 백엔드의 역할은 다음과 같다.

- topic state 저장
- hypothesis graph 저장
- provenance 저장
- conflict modeling
- belief revision 기록
- frontier selection
- competition pressure 유지
- scheduled rerun
- user suggestion queue 관리

Codex는 주된 reasoning and action engine이고, 백엔드는 장기 상태와 연구 운영 계층이다.

### 3.5 Codex Max Sufficiency

v1의 중요한 설계 질문은 "추가 전용 API 없이 Codex Max plan 중심으로 작동 가능한가"다.
현재 판단은 `가능하지만 경계가 분명한 조건부 가능`이다.

판단을 한 줄로 쓰면 다음과 같다.

`Codex는 충분한 reasoning/acting engine이지만, 시스템 authority는 아니다.`

가능한 이유는 다음과 같다.

- Codex는 interactive research, tool use, browsing, synthesis에 충분한 실행력을 제공한다.
- 사용자의 기존 Codex 사용 환경을 그대로 활용하면 추가 LLM 조달 계층을 최소화할 수 있다.
- 백엔드가 belief revision state machine과 graph authority를 직접 소유하면,
  LLM은 판단과 실행을 담당하고 시스템 핵심 제어는 애플리케이션에 남길 수 있다.

반드시 분리해야 하는 책임은 다음과 같다.

- `Codex`: research execution, search, synthesis, argument construction, adjudication assistance
- `Backend`: topic state, provenance, scheduling, queue, revision policy, graph writes

주의할 점은 다음과 같다.

- Codex 자체가 장기 상태 저장소 역할을 대신할 수는 없다.
- 반복 연구의 일관성과 재현성을 위해 backend-owned provenance가 필수다.
- scheduled run과 queue-driven execution은 Codex 단독이 아니라 backend harness가 orchestration해야 한다.
- 사용자가 보유한 Codex 경로를 활용하더라도, 실패 복구와 재실행 경계는 애플리케이션이 정의해야 한다.

정리하면, v1은 `additional proprietary research API` 없이도 가능하지만,
그 전제는 `Codex + user-owned backend + explicit state machine` 조합이다.

## 4. Problem Statement

### 4.1 What We Want to Build

하나의 주제에 대해 여러 번 연구를 수행할수록 시스템이 더 좋은 가설 상태에 도달하도록 만드는 지속형 연구 시스템.

### 4.2 Why Naive Approaches Fail

일반적인 연구 자동화 구조는 보통 아래 수준에서 멈춘다.

- 검색
- 요약
- 저장
- 재호출

하지만 장기 연구의 핵심 문제는 `information retrieval`이 아니라 `belief maintenance`다.
중요한 질문은 다음이다.

- 지금 시스템은 무엇을 가장 그럴듯하다고 보는가
- 왜 그렇게 보는가
- 무엇이 그 가설을 지지하는가
- 무엇이 그 가설을 반박하는가
- 새 evidence가 들어오면 어떤 결정을 내려야 하는가
- 다음 run은 무엇을 검증해야 정보가치가 큰가

## 5. Product Goals

### 5.1 Goals

- 같은 topic에 대한 반복 연구를 가능하게 한다.
- 이전 run의 결과가 다음 run의 실질적 입력으로 재사용된다.
- 새 evidence가 기존 hypothesis를 강화, 약화, 대체, 분기시킬 수 있다.
- 반복될수록 새로운 challenger hypothesis가 계속 생성된다.
- unresolved tension을 보존하고 후속 run이 이를 다시 다룬다.
- 반복 실행될수록 접근 전략이 다양해지고 competition pressure가 유지되며 정보가치가 증가한다.
- 모든 핵심 결론에 provenance와 근거가 연결된다.
- 사용자가 Codex에서 현재 시점의 best hypothesis와 reasoning trace를 이해할 수 있다.
- 사용자의 의견과 아이디어를 backlog가 아니라 structured research input으로 흡수한다.

### 5.2 Non-Goals

- 절대 진실 판정
- 정적 규칙만으로 contradiction 처리
- 첫 버전부터 자율 멀티에이전트 군집 최적화
- 단순 문서 검색 시스템을 graph research system으로 포장하기
- backend 없이 Codex 세션 로그만으로 장기 상태를 대체하기

## 6. Design Principles

### 6.1 Research Is Non-Monotonic

연구는 새 정보가 들어와도 기존 결론이 그대로 누적되는 과정이 아니다.
새 정보는 기존 믿음을 수정할 수 있다.

### 6.2 Hypothesis Is The Primary Unit

원자적 claim은 필요하지만, 시스템이 관리해야 할 핵심 단위는 `hypothesis`다.
claim 관리만으로는 장기 연구의 경쟁 구조를 표현할 수 없다.

### 6.3 Provenance Is Mandatory

출처, 생성 시점, 추출 과정, adjudication 결과를 알 수 없는 결론은
장기 연구에서 신뢰할 수 없다.

### 6.4 Time Matters

겉보기 contradiction 중 상당수는 시간차에서 발생한다.
핵심 claim과 hypothesis는 가능한 한 temporal scope를 가져야 한다.

### 6.5 Repetition Must Increase Information Gain

반복 실행은 같은 검색을 되풀이하는 것이 아니라 uncertainty를 줄이고,
hypothesis space를 더 효율적으로 탐색해야 한다.

반복이 발전이 되려면 최소 아래 중 일부가 매 run에서 일어나야 한다.

- current best hypothesis attack
- challenger hypothesis generation
- adversarial verification
- reconciliation
- weak hypothesis retirement

### 6.6 Backend Owns State, LLM Does Not

LLM은 reasoning engine이지만 state authority는 아니다.
장기 topic state와 revision history는 반드시 backend가 소유해야 한다.

### 6.7 Stagnation Is A First-Class Failure Mode

반복 실행이 있다고 해서 자동으로 발전이 생기지는 않는다.
아래 상태는 모두 `stagnation failure mode`로 취급해야 한다.

- 새 evidence가 거의 추가되지 않는다.
- 기존 hypothesis가 비판 없이 계속 accept된다.
- challenger generation이 멈춘다.
- support만 쌓이고 challenge가 거의 발생하지 않는다.
- revision이나 retirement 없이 같은 belief state만 반복된다.

즉 시스템은 `no new competition, no progress`라는 원칙을 가져야 한다.

## 7. User Experience

### 7.1 Primary Entry Point

주 사용자 인터페이스는 `Codex`다.

### 7.2 User Actions

사용자는 Codex에서 아래 액션을 수행한다.

1. 새 research topic 생성
2. 특정 topic의 현재 상태 조회
3. 현재 시점의 best hypothesis와 supporting / challenging evidence 확인
4. 자신의 의견, 반론, 단서, 검색 아이디어 제안
5. 해당 입력을 후속 연구 큐에 적재

### 7.3 Expected System Responses

시스템은 사용자에게 최소 아래 내용을 제공해야 한다.

- 현재 시점의 best hypotheses
- 각 hypothesis의 confidence / status
- 왜 그렇게 판단했는지에 대한 핵심 근거
- unresolved conflict
- stale belief 여부
- next questions / next actions
- 사용자가 남긴 suggestion이 queue에 어떻게 반영되었는지에 대한 상태

### 7.4 User Suggestions

사용자 입력은 다음 세 종류 중 하나로 흡수될 수 있어야 한다.

- `candidate hypothesis`
- `counterargument`
- `research lead`

이 입력은 바로 채택되지 않고 queue를 통해 검토되며,
이후 run에서 search, validation, adjudication 대상으로 사용된다.

### 7.5 Canonical UX Contract

사용자가 Codex에서 보게 되는 기본 상호작용은 아래 세 흐름으로 정리할 수 있다.

1. `Topic Creation`
   사용자가 새 topic을 만들면 backend는 초기 question set, 초기 hypothesis candidates, 첫 research queue를 생성한다.
2. `Topic Review`
   사용자가 기존 topic을 열면 시스템은 현재 best hypotheses, 핵심 supporting/challenging evidence, unresolved conflicts, next actions를 보여준다.
3. `User Input -> Queue`
   사용자가 의견이나 반론을 남기면 시스템은 이를 truth로 채택하지 않고 `candidate input`으로 분류해 queue에 적재하고, 어떤 후속 조사로 연결됐는지 다시 보여준다.

이 계약이 중요한 이유는 다음과 같다.

- 사용자는 항상 `지금 무엇을 믿는지`와 `왜 그렇게 믿는지`를 Codex에서 바로 이해할 수 있어야 한다.
- backend는 사용자의 입력을 누락 없이 연구 파이프라인으로 연결해야 한다.
- Codex 응답은 단순 요약이 아니라 `current belief state + next research actions`를 함께 제공해야 한다.

## 8. Operating Model

### 8.1 Interactive Research

사용자가 Codex에서 topic을 지정해 즉시 연구를 실행한다.
시스템은 현재 topic state를 불러오고, 필요한 추가 조사 후 상태를 갱신한다.

### 8.2 Scheduled Research

cron 또는 job worker가 topic을 주기적으로 재실행한다.
핵심은 새 정보 수집뿐 아니라 기존 belief state에 selection pressure를 다시 가하는 것이다.

### 8.3 Long-Term Tracking

하나의 topic을 수 주 또는 수 개월 동안 추적하며 다음 상태를 유지한다.

- 강화되는 hypothesis
- 약화되는 hypothesis
- 경쟁하는 대안 hypothesis
- unresolved conflict
- stale belief
- follow-up questions

## 9. Core Domain Model

### 9.1 Topic

장기적으로 연구를 누적하는 단위.

### 9.2 Run

하나의 연구 실행 단위. interactive 또는 scheduled일 수 있다.

### 9.3 Evidence

문서, 링크, 수치, 메모, transcript, API 응답 등 관측 가능한 입력.

### 9.4 Claim

evidence에서 추출한 정규화된 원자적 주장.

### 9.5 Hypothesis

여러 claim과 argument를 바탕으로 형성된 상위 수준 설명.
시스템의 핵심 판단 단위다.

### 9.6 ConflictCase

새로운 정보가 기존 claim 또는 hypothesis와 tension을 일으킨 사건.

### 9.7 Decision

adjudication 이후 수행된 belief revision 결정.

### 9.8 Question

아직 검증되지 않았거나 더 분해가 필요한 연구 질문.

### 9.9 Strategy

각 run이 채택한 연구 접근 방식.

### 9.10 UserInput

사용자가 Codex를 통해 제공한 아이디어, 반론, 단서, 조사 요청.

## 10. Continual Research Criteria

이 시스템에서 continual research라고 부르기 위한 최소 조건은 아래 네 가지다.

### 10.1 Memory Reuse

이전 run의 산출물이 다음 run의 실제 입력으로 사용된다.

### 10.2 Belief Revision

기존 hypothesis가 새 evidence에 따라 수정될 수 있다.

### 10.3 Strategy Diversification

반복 실행될수록 접근 전략이 다양화된다.

### 10.4 Frontier Tracking

지금 어디가 가장 불확실하고, 어디를 파야 정보가치가 큰지 추적한다.

### 10.5 Competition Maintenance

반복 실행이 단순 refresh로 끝나지 않도록 challenger generation과 critical challenge를 유지한다.

## 11. Logical Graph Architecture

본 시스템의 핵심 저장 구조는 단일 fact graph가 아니라 `3-layer research graph`다.

### 11.1 Layer 1: World Representation Graph

세상에 대해 말해진 내용을 구조화해 표현하는 층.

포함 요소:

- Entity
- Event
- Relation
- Time
- Attribute

역할:

- retrieval과 graph traversal 기반 제공
- temporal context 표현
- hypothesis가 참조할 수 있는 world-level structure 제공

주의:

- 이 층은 "절대 fact 저장소"가 아니라 관측 가능한 세계 표현의 정규화 층이다.

### 11.2 Layer 2: Epistemic Graph

시스템이 무엇을 왜 믿는지를 표현하는 핵심 층.

포함 요소:

- Claim
- Hypothesis
- Question
- Argument
- ConflictCase
- Confidence
- Status

핵심 관계:

- `Evidence SUPPORTS Claim`
- `Evidence CHALLENGES Claim`
- `Claim SUPPORTS Hypothesis`
- `Claim WEAKENS Hypothesis`
- `Hypothesis ALTERNATIVE_TO Hypothesis`
- `Hypothesis SUPERSEDES Hypothesis`
- `ConflictCase INVOLVES Claim`
- `Question TARGETS Hypothesis`
- `UserInput SUGGESTS Hypothesis`
- `UserInput TRIGGERS Question`

### 11.3 Layer 3: Provenance / Process Graph

시스템이 왜 그런 판단을 하게 되었는지의 생성 과정을 표현하는 층.

포함 요소:

- Source
- Document
- Run
- Extraction
- ToolCall
- Prompt
- JudgeDecision
- Reviewer
- Decision
- QueueItem

## 12. Conflict Taxonomy

LLM judge 또는 adjudication step은 최소 아래 분류를 지원해야 한다.

- `no_conflict`
- `reinforcement`
- `contradiction`
- `temporal_mismatch`
- `granularity_mismatch`
- `source_disagreement`
- `supersession`
- `insufficient_grounding`

핵심은 conflict 존재 여부보다
`왜 tension이 발생했는지`와 `revision action이 무엇이어야 하는지`를 식별하는 것이다.

## 13. Belief Revision Engine

### 13.1 Allowed Revision Actions

- `reinforce`
- `weaken`
- `split`
- `supersede`
- `retire`
- `defer`
- `escalate_to_human`

### 13.2 Revision Requirements

각 revision decision은 최소 아래를 남겨야 한다.

- 대상 hypothesis 또는 claim
- 사용된 evidence
- conflict classification
- decision rationale
- actor
- timestamp
- resulting state

## 14. Research Harness

### 14.1 Standard Run Flow

1. `Load Topic State`
2. `Select Frontier`
3. `Allocate Strategy`
4. `Attack Current Best Hypothesis`
5. `Generate Challenger Hypotheses`
6. `Acquire Evidence`
7. `Canonicalize Claims`
8. `Build Arguments`
9. `Adjudicate`
10. `Revise Beliefs`
11. `Retire Weak Hypotheses`
12. `Compress State`
13. `Plan Next Actions`

### 14.2 What The Harness Must Own

- scheduled orchestration
- queue consumption
- graph read/write
- provenance persistence
- retry / idempotency boundaries
- run report generation

### 14.3 Minimum Outputs Per Run

각 run이 끝나면 최소 아래 산출물이 남아야 한다.

- `updated hypothesis state`
- `revision decisions`
- `new challenger hypotheses or explicit reason why none were produced`
- `new or updated provenance records`
- `user-visible run summary`
- `next questions / next actions`
- `queue mutations`

즉, run의 성공 조건은 "검색을 한 번 수행했다"가 아니라
"belief state를 평가 가능하게 갱신했고, 그 결과를 사용자와 backend가 모두 재사용 가능하게 남겼다"여야 한다.

## 15. Execution Strategy

### 15.1 Primary Execution Model

본 시스템은 `user-owned backend`를 기본 실행 모델로 채택한다.
핵심은 `Codex가 중심 UX`이되 `backend가 시스템 authority`라는 점이다.

실행 모드는 두 가지다.

- `Interactive`: 사용자가 Codex에서 topic을 열고 현재 상태 확인, 추가 조사, 의견 제안을 수행한다.
- `Scheduled`: backend가 topic frontier와 queue를 기준으로 run을 생성하고 Codex execution path를 호출한다.

즉, Codex는 사용자의 주 인터페이스이자 연구 실행 엔진이지만,
스스로 장기 데몬처럼 상태를 소유하는 주체는 아니다.

### 15.2 Role Split

`Codex`의 책임:

- 검색
- 도구 호출
- synthesis
- argument construction
- adjudication assistance

`Our Backend`의 책임:

- topic graph
- queue
- scheduling
- provenance
- revision policy
- state transitions

### 15.3 Agents SDK Usage

`OpenAI Agents SDK`는 v1의 기본 경로가 아니다.
필요하다면 future fallback 또는 대안 orchestration layer로 검토할 수 있다.

검토 시 기대할 수 있는 보조 기능:

- tool calling loop
- tracing
- handoff
- session handling
- context compaction 보조

하지만 아래 책임은 SDK로 넘길 수 없고 애플리케이션이 직접 담당해야 한다.

- research state machine
- hypothesis lifecycle
- conflict taxonomy
- belief revision authority
- graph update policy

### 15.4 Codex Max Path Constraints

`user-owned OpenAI Codex Max plan` 경로만으로 v1을 구성할 수 있다는 판단은
아래 제약을 시스템 설계에 반영할 때만 유효하다.

- 이 경로는 공식 API 기반 경로보다 덜 안정적일 수 있다.
- Codex session은 장기 상태 저장소가 아니므로 topic state는 항상 backend에서 복원해야 한다.
- scheduled run은 backend가 직접 큐를 소비하고 Codex execution path를 호출하는 구조여야 한다.
- headless scheduled execution과 세션 유지, 인증 수명 관리는 backend가 직접 책임져야 한다.
- provenance, revision log, retry state는 Codex 대화 로그가 아니라 backend storage에 기록해야 한다.
- run 실패 시 어느 단계부터 재실행 가능한지 애플리케이션이 idempotency boundary를 정의해야 한다.
- Codex는 reasoning/acting engine으로 사용하되, 최종 graph write authority는 backend가 유지해야 한다.
- 즉, 쉬운 경로라서가 아니라 제품 제약에 맞춘 전략적 선택으로 이해해야 한다.

## 16. Recommended Technical Direction

### 16.1 v1 Stack

- backend runtime: `Python`
- graph DB: `Neo4j`
- temporal / incremental memory layer: `Graphiti`
- primary execution path: `Only Codex`
- no extra API token required: `true`
- optional future orchestration alternative: `OpenAI Agents SDK`

### 16.2 Why This Stack

- Python은 orchestration과 backend iteration 속도가 빠르다.
- Neo4j는 relationship-heavy epistemic structure 표현에 적합하다.
- Graphiti는 incremental memory / temporal memory 계층 보완에 적합하다.
- Codex 경로를 기본 execution engine으로 두면 사용자 UX와 backend LLM power를 하나의 경로로 정렬할 수 있다.
- Agents SDK는 필요할 때만 검토 가능한 대안이지 v1 필수 전제는 아니다.

## 17. v1 Scope

### 17.1 Must Have

- topic 생성 및 관리
- interactive run
- scheduled run
- user input queue
- 3-layer graph 기반 상태 모델
- evidence to claim canonicalization
- support / challenge 관계 생성
- structured adjudication
- belief revision 기록
- run report 및 next actions 생성
- user-owned Codex execution 연동
- Python backend harness
- backend-owned state / provenance / queue / scheduler
- `Only Codex`, `no extra API token required` 실행 경로

### 17.2 Explicitly Deferred

- fully autonomous multi-agent swarm
- complete truth resolution without human review
- domain-specific specialized adapters for every data source
- high-cost custom model routing layer

### 17.3 v1 Exit Criteria

v1이 성립했다고 보려면 최소 아래 질문에 `yes`라고 답할 수 있어야 한다.

- 같은 topic을 다시 실행했을 때 이전 hypothesis state를 실제로 재사용하는가
- 새 evidence가 들어왔을 때 기존 hypothesis를 강화, 약화, 분기, 폐기 중 하나로 수정할 수 있는가
- 각 핵심 결론에 provenance와 revision history를 연결할 수 있는가
- 사용자가 Codex에서 현재 best hypothesis, 반론, next action을 바로 이해할 수 있는가
- 사용자의 의견이 queue를 통해 후속 연구 입력으로 흡수되는가

### 17.4 Implementation Readiness Checklist

구현 착수 전에 아래 항목이 명확하면 이 스펙은 실제 빌드 가능한 수준에 가깝다.

- topic, hypothesis, evidence, user input의 저장 스키마가 정의되어 있는가
- run report의 사용자 표시 포맷이 정의되어 있는가
- revision decision의 필수 필드와 기록 위치가 정의되어 있는가
- queue item이 어떤 타입을 가지는지와 소비 규칙이 정의되어 있는가
- retry / idempotency boundary가 어느 단계에서 끊기는지 정의되어 있는가

반대로 위 항목이 비어 있으면 방향성은 맞더라도 구현 중 해석 차이로 쉽게 흔들릴 수 있다.


## 18. User-Facing Interface Expansion

현재 v1은 backend authority와 연구 loop를 닫는 데 집중했다.
다음 product slice는 사용자가 이 시스템을 실제 연구 도구로 쓸 수 있게 하는
`CLI + readable UX + graph visualization`이다.

### 18.1 UX Goal

처음 보는 사용자는 아래 세 가지를 명령 몇 개로 할 수 있어야 한다.

1. 연구 topic을 만들고 첫 run을 시작한다.
2. 현재 best hypotheses, conflicts, evidence, next actions를 확인한다.
3. graph / memory 상태를 시각적으로 훑어보고 어떤 믿음이 왜 바뀌었는지 이해한다.

CLI는 단순 debug shell이 아니라 제품의 primary operator surface다.
좋은 UX의 기준은 "모든 내부 기능 노출"이 아니라 "다음 연구 판단에 필요한 상태를 빠르게 이해"하는 것이다.

### 18.2 Required CLI Surface

v1 CLI는 최소 아래 명령군을 제공해야 한다.

```text
crb init / doctor
crb topic create / list / show
crb run start / status / resume
crb queue list / retry / dead-letter
crb memory snapshot / conflicts / hypotheses
crb graph export / view
crb ops health / audit / replay
```

명령은 JSON 출력과 human-readable 출력 둘 다 지원해야 한다.
자동화와 테스트는 JSON을 사용하고, 사용자는 기본 human-readable summary를 본다.

### 18.3 Graph Visualization Requirement

사용자는 graph DB를 직접 열지 않고도 현재 연구 상태를 볼 수 있어야 한다.
최소 지원 surface:

- topic snapshot의 hypothesis / evidence / conflict subgraph export
- CLI에서 읽을 수 있는 text summary
- 파일로 저장 가능한 graph JSON
- lightweight HTML 또는 Mermaid/DOT 기반 visualization artifact

시각화는 source of truth가 아니다. canonical graph와 ledger가 authority이며,
visualization은 그 상태를 읽기 쉽게 보여주는 projection이다.

### 18.4 UX Safety Rules

- CLI는 backend write boundary를 우회하면 안 된다.
- visualization은 canonical graph를 수정하지 않는다.
- user-facing summary는 confidence와 uncertainty를 숨기지 않는다.
- conflict는 error처럼 숨기지 말고, research pressure로 보여준다.
- "run succeeded"와 "belief improved"를 구분한다.

### 18.5 UX Exit Criteria

이 확장이 완료되려면 처음 보는 사용자가 README와 CLI help만 보고 아래를 수행할 수 있어야 한다.

- 새 topic 생성
- interactive run 시작
- run status와 audit trail 확인
- current best / challenger / conflict 확인
- graph artifact export 및 열람
- failed/retry/dead-letter 상태 이해


## 19. Local Web Research UI Expansion

CLI는 안정적인 operator surface지만, 긴 연구가 실제로 잘 진행되는지 파악하려면 한눈에 들어오는 visual dashboard가 필요하다.
다음 product slice는 `localhost`에서 실행되는 read-only-first web UI다.

### 19.1 Web UI Goal

사용자는 브라우저에서 아래 질문에 30초 안에 답할 수 있어야 한다.

1. 지금 연구 topic이 어디까지 진행됐는가?
2. 현재 우세 가설과 challenger는 무엇인가?
3. 어떤 evidence가 support / challenge 관계를 만들었는가?
4. unresolved conflict, stale queue, dead-letter, stagnation risk가 있는가?
5. 다음에 시스템이 무엇을 연구하려고 하는가?

### 19.2 Primary UX Views

최소 화면은 아래 네 가지다.

- `Overview`: topic status, latest runs, queue health, dead-letter/stale claim warnings
- `Hypothesis Board`: current best, challengers, confidence/status, support/challenge counts
- `Graph Explorer`: evidence / claim / hypothesis / conflict / provenance network
- `Run Timeline`: run events, validation/repair, graph writes, queued next actions

### 19.3 Visualization Requirements

Web UI는 graph export 파일을 단순히 열람하는 수준을 넘어 interactive exploration을 제공해야 한다.
권장 방향은 graph-specific browser library를 사용하는 것이다.
현재 후보는 `Cytoscape.js`다. 이유는 graph visualization/interaction에 특화되어 있고,
node/edge style, filtering, layout, event handling을 직접 다루기 쉽기 때문이다.
D3는 범용 visualization에는 강하지만 graph interaction을 제품 UX로 만들려면 더 많은 custom code가 필요하다.

단, dependency가 UX를 지배하면 안 된다. backend authority와 export schema가 먼저이고,
web library는 projection renderer다.

### 19.4 Web UI Safety Rules

- Web UI는 기본적으로 read-only다.
- write action은 CLI/service boundary와 동일한 backend command를 호출해야 한다.
- graph node를 드래그하거나 숨기는 행위는 backend state를 바꾸지 않는다.
- visualization은 source of truth가 아니라 projection임을 화면에 명시한다.
- conflict와 uncertainty는 숨기지 않는다.
- dead-letter와 stale claimed queue는 정상 실행처럼 보이면 안 된다.

### 19.5 Web UI Exit Criteria

완료 기준:

- `crb web serve` 또는 동등한 명령으로 localhost UI를 띄울 수 있다.
- sample topic에서 Overview, Graph Explorer, Run Timeline을 볼 수 있다.
- graph node click 시 evidence/provenance/detail panel이 열린다.
- filter로 node type, edge type, run id, unresolved conflict를 좁힐 수 있다.
- screenshot/golden HTML 또는 browser smoke test로 핵심 UX가 검증된다.

### 19.6 Visual Run-State Comprehension Requirement

Web UI는 사용자가 현재 연구가 실제로 실행 중인지, 대기 중인지, 멈췄는지 한눈에 알 수 있어야 한다. 단순히 총 queue 개수만 보여주면 안 된다.

필수 표현:

- 지금 실행 중인 run / claimed queue item 수
- 대기 중인 queued item 수
- completed / dead-letter / stale claimed 분리 표시
- 현재 실행 중인 작업이 있으면 해당 queue item, run id, objective, latest event 표시
- 실행 중인 작업이 graph의 어떤 hypothesis / evidence / conflict / next action과 연결되는지 표시
- 아무 것도 실행 중이 아니면 `현재 실행 중인 연구 없음`을 명확하게 표시

이 요구는 Playwright 기반 E2E와 주요 탭 screenshot으로 검증해야 한다.

## 20. Autonomous Research Worker Loop And Convergence Stop

현재 CLI는 run을 시작하거나 queue를 관찰할 수 있지만, 사용자가 매번 수동으로 다음 queue item을 실행해야 하면 continual research system이 아니다. 다음 product slice는 topic별 research worker loop다.

### 20.1 Goal

사용자는 한 번의 명령으로 특정 topic에 대해 연구를 계속 진행시킬 수 있어야 한다.

예상 UX:

```bash
crb worker run --topic topic_iso26262_agentic_workflow_automation --loop \
  --max-iterations 20 \
  --max-consecutive-no-yield 4
```

이 worker는 queued next action을 하나씩 claim하고, Codex runtime을 실행하고, ProposalBundle을 검증하고, canonical graph / memory / queue를 업데이트한다. 단, 무한 토큰 소비를 막기 위해 명시적인 convergence / no-yield stop gate가 있어야 한다.

### 20.2 Research Yield Definition

한 iteration은 아래 중 하나 이상을 만족해야 `yielded`로 본다.

- canonical graph digest가 변경됨
- 새로운 evidence / claim / hypothesis / provenance node가 저장됨
- current best에 대한 support 또는 challenge relation이 새로 생김
- active conflict가 생성, 조정, escalate, reconcile 됨
- revision proposal이 current best를 retain / weaken / retire / supersede하는 의미 있는 압력을 만듦
- next action queue가 중복이 아닌 새 검증 작업을 생성함

반대로 아래는 no-yield로 본다.

- malformed proposal 또는 validation quarantine으로 backend state update가 없음
- 기존 graph와 의미상 같은 graph를 다시 저장함
- queue item만 소비하고 새 evidence / argument / revision pressure가 없음
- 같은 failure class가 반복됨
- next action이 dedupe되어 새 작업이 늘지 않음

### 20.3 Stop Conditions

worker loop는 아래 조건 중 하나를 만족하면 자동 중단해야 한다.

- `max_iterations` 도달
- `max_consecutive_no_yield` 도달
- topic이 convergence state로 전환됨
- queue가 비었고 active conflict / required next action이 없음
- dead-letter 또는 malformed proposal rate가 threshold를 초과함
- Codex/runtime budget, wall-clock budget, or operator-defined cost budget 도달
- auth/session/workspace/credential policy가 fail-closed됨
- human-review-required failure가 발생했고 policy가 auto-skip을 허용하지 않음

중단은 실패가 아니라 operator-visible decision이어야 한다. CLI와 Web UI는 `paused`, `converged`, `blocked`, `budget_exhausted`, `no_yield_stop`을 구분해서 보여줘야 한다.

### 20.4 Convergence Heuristic

충분히 연구가 된 상태는 단순히 queue가 비었다는 뜻이 아니다. 아래 신호를 함께 봐야 한다.

- current best가 여러 iteration 동안 바뀌지 않음
- challenger가 반복적으로 current best를 이기지 못함
- active conflict가 없거나 모두 reconciled/escalated 상태임
- high-priority next action이 남아 있지 않음
- 최근 `N`회 iteration의 graph yield가 threshold 이하임
- 남은 질문이 implementation detail 또는 low-priority validation으로 수렴함

v1은 완벽한 epistemic convergence를 주장하지 않는다. 대신 audit 가능한 heuristic으로 `stop_reason`, `yield_history`, `last_meaningful_change`, `remaining_questions`를 기록한다.

### 20.5 Safety Rules

- worker는 기본적으로 topic당 한 개만 실행된다.
- queue claim lease와 worker heartbeat가 있어야 한다.
- UI는 running worker count와 queued count를 분리해서 보여준다.
- worker는 failed queue를 무한 retry하면 안 된다.
- malformed proposal 반복은 prompt를 계속 밀어붙이는 대신 validator/contract 개선 후보로 surfacing한다.
- stop decision은 provenance event로 남긴다.
- 자동 research loop는 Git/Linear implementation orchestrator와 분리된 runtime이다.

### 20.6 Exit Criteria

- `crb worker run --topic ... --loop` 또는 동등한 명령이 topic queue를 자동으로 소비한다.
- worker loop는 no-yield / convergence / budget / human-review-required 조건에서 중단된다.
- CLI에서 현재 loop 상태, iteration count, yield history, stop reason을 볼 수 있다.
- Web UI에서 running worker, queued next, no-yield streak, convergence/blocked state를 볼 수 있다.
- tests가 forced no-yield, repeated malformed proposal, empty queue, successful yield, budget stop을 검증한다.

## 21. Korean First-User Dashboard UX Overhaul

현재 dashboard는 backend state를 많이 보여주지만, 처음 보는 사용자는 `CLA`, `HYP`, `EVI`, `PRO`, `Dead-letter`, `Queue`, `Running`, `worker loop`, `yield` 같은 용어를 바로 이해하기 어렵다. 다음 product slice는 기능 추가가 아니라 first-user comprehension을 제품 요구사항으로 승격하는 UX 전면 개선이다.

### 21.1 Goal

처음 보는 사용자는 dashboard만 보고 아래 질문에 30초 안에 답할 수 있어야 한다.

1. 지금 연구가 실제로 돌고 있는가, 멈췄는가, 대기 중인가?
2. 각 run은 언제 시작했고, 언제 끝났고, 얼마나 걸렸는가?
3. `CLA`, `HYP`, `EVI`, `PRO`가 무엇을 의미하는가?
4. `Dead-letter`는 실패인가, 보류인가, 사람이 봐야 하는 상태인가?
5. Graph에서 어떤 노드와 edge가 현재 가설 신뢰도를 높이거나 낮추는가?
6. 다음에 무엇을 하면 되는가?

### 21.2 Korean-First Copy Requirement

Dashboard의 기본 사용자-facing copy는 한국어여야 한다.

- 탭 이름, 카드 제목, empty state, error state, tooltip, glossary, onboarding 문구는 기본 한국어다.
- machine-readable 값, command, file path, URL, run id, queue id, commit SHA는 원문을 유지한다.
- 영어 약어를 써야 하면 바로 옆에 한국어 의미를 함께 보여준다. 예: `HYP · 가설`.
- README와 screenshot artifact도 한국어 UX 기준으로 설명한다.

### 21.3 Run Timing Requirement

Runs 탭과 Overview의 실행 상태 카드는 run/queue lifecycle의 시간 정보를 명확히 보여줘야 한다.

필수 표현:

- 요청 시각
- 시작/claim 시각
- 완료/실패/중단 시각
- 소요 시간
- latest event 시각
- 상태별 badge: 실행 중 / 대기 중 / 완료 / 실패 보관 / 중단 / 수렴

시각은 local timezone과 ISO timestamp 중 하나 이상을 제공해야 하며, tooltip이나 detail panel에서 raw timestamp를 확인할 수 있어야 한다.

### 21.4 Graph Glossary Requirement

Graph 탭에는 항상 보이는 범례와 용어 도움말이 있어야 한다.

최소 용어:

- `TOP`: 연구 주제(topic)
- `HYP`: 가설(hypothesis)
- `CLA`: 주장/클레임(claim)
- `EVI`: 근거(evidence)
- `PRO`: 출처/실행 이력(provenance)
- `CON`: 충돌/갈등(conflict)
- `supports`: 지지한다
- `challenges`: 반박/약화한다
- `visualizes`: 시각화 projection 관계

각 용어는 “왜 중요한지”를 한 문장으로 설명해야 한다. 예: `EVI`는 가설을 직접 믿게 만드는 값이 아니라 claim을 뒷받침하는 근거 후보라는 점을 설명한다.

### 21.5 Dead-Letter And Queue Help Requirement

Queue 탭은 상태명을 내부 용어 그대로 보여주는 수준을 넘어 사용자 행동까지 알려줘야 한다.

- `Queued`: 아직 실행되지 않은 대기 작업
- `Running/Claimed`: worker가 잡아서 처리 중인 작업
- `Completed`: 검증과 저장까지 끝난 작업
- `Dead-letter`: 자동 처리에 실패해 더 이상 자동 retry하지 않는 보관함 상태
- `Stale`: worker가 잡았지만 heartbeat/lease가 오래되어 복구가 필요한 상태

Dead-letter에는 `왜 실패했는지`, `retry 가능한지`, `사람 검토가 필요한지`, `다음 행동이 무엇인지`를 보여줘야 한다.

### 21.6 First-User Help System

Dashboard는 README를 읽지 않아도 기본 사용법을 알 수 있게 해야 한다.

필수 UX:

- 첫 방문 또는 도움말 버튼에서 열리는 “대시보드 읽는 법” 패널
- 각 탭 상단의 짧은 설명
- 주요 카드의 tooltip 또는 inline help
- `이 숫자는 실제 실행 중인 Codex 세션 수가 아닙니다` 같은 오해 방지 문구
- source-of-truth / projection 차이를 쉬운 말로 설명
- “멈췄다면 무엇을 볼 것인가” 안내

### 21.7 Exit Criteria

- 한국어 first-user copy가 dashboard 전반에 적용된다.
- Runs 탭에서 run별 요청/시작/완료/소요 시간을 볼 수 있다.
- Graph 탭에서 노드 약어와 edge 의미를 dashboard 내부에서 이해할 수 있다.
- Queue 탭에서 Dead-letter 의미와 다음 행동을 이해할 수 있다.
- Playwright E2E와 screenshot artifact가 Overview / Graph / Runs / Queue / Memory / Help 상태를 검증한다.

## 22. Final Recommendation

이 시스템은 `knowledge graph bot`이 아니라
`research truth-maintenance system`으로 설계해야 한다.

정확히는 더 엄밀하게 다음처럼 정의하는 편이 맞다.

`A Codex-centered continual research system that maintains competing hypotheses, revises beliefs over time, and uses a user-owned backend as the authority for state, provenance, and scheduling.`

이 정의가 중요한 이유는 세 가지다.

- 제품의 중심이 검색이 아니라 hypothesis revision임을 고정한다.
- 사용자 UX의 중심이 Codex임을 고정한다.
- 추가 API 의존 없이도 v1이 가능한 구조적 전제를 분명히 한다.
