# Continual Research Bot SPEC

## 1. 목적

이 문서는 특정 주제에 대해 여러 번, 장기간 연구를 수행하면서 이전 연구 결과를 누적 활용하고, 새 증거에 따라 기존 가설을 수정하는 `Continual Research Bot`의 제품 및 시스템 스펙을 정의한다.

이 시스템의 본질은 단순한 검색 봇이나 요약 봇이 아니다. 핵심은 다음이다.

- 이전 연구를 기억한다.
- 새 정보를 기존 믿음 체계와 비교한다.
- 충돌과 반박을 해석한다.
- 가설을 강화, 약화, 분기, 폐기할 수 있다.
- 반복될수록 같은 행동을 반복하는 것이 아니라 더 정보가치가 큰 방향으로 연구를 진화시킨다.

즉, 이 시스템은 `search engine wrapper`가 아니라 `belief revision engine for research`여야 한다.

본 문서의 현재 전제는 다음과 같다.

- 실행 모델은 `user-owned backend`를 따른다.
- 메인 LLM power는 사용자가 이미 보유한 `OpenAI Codex Max plan` 실행 경로를 활용하는 방향으로 설계한다.
- 백엔드 하네스는 Python 기반으로 구현한다.
- 에이전트 루프는 OpenAI Agents SDK를 사용하되, belief revision 상태기계는 애플리케이션이 직접 소유한다.

## 2. 핵심 문제 정의

### 2.1 우리가 만들고 싶은 것
하나의 주제에 대해 여러 번 연구를 수행할수록, 시스템이 더 좋은 가설 상태에 도달하도록 만드는 지속형 연구 시스템.

### 2.2 일반적인 접근의 한계
아래 구조만으로는 부족하다.

- 대화 로그 누적
- 문서 chunk + vector DB
- 단순 knowledge graph
- conflict yes/no 분류기

이유는 연구 시스템의 핵심 문제가 `정보 저장`이 아니라 `믿음 수정`이기 때문이다.

### 2.3 설계의 핵심 전환
이 시스템은 `fact storage`보다 `reason maintenance`를 중심에 둬야 한다.

즉, 시스템이 관리해야 하는 것은:

- 무엇을 알고 있는가
- 왜 그렇게 믿는가
- 무엇이 그것을 반박하는가
- 어떤 변화 때문에 믿음이 수정되었는가
- 다음엔 무엇을 검증해야 하는가

## 3. 제품 정의

### 3.1 한 줄 정의
`Continual Research Bot`은 토픽별 장기 메모리, 가설 그래프, provenance, belief revision 기록을 유지하면서, 대화형 실행과 스케줄 실행을 통해 지속적으로 연구 상태를 개선하는 시스템이다.

### 3.2 시스템의 본질
이 시스템은 다음 세 가지를 동시에 수행해야 한다.

- `Knowledge Accumulation`
- `Hypothesis Management`
- `Belief Revision`

이 셋 중 하나라도 빠지면 원하는 효능이 나오지 않는다.

## 4. 제품 목표

### 4.1 목표
- 같은 주제에 대한 반복 연구를 가능하게 한다.
- 이전 연구 결과를 이후 실행의 실질적인 입력으로 사용한다.
- 새 증거가 기존 가설을 강화하는지, 약화하는지, 뒤집는지 판단한다.
- 반복 실행될수록 접근 전략이 다양화되도록 한다.
- 모든 핵심 결론이 근거와 출처를 갖도록 한다.
- unresolved tension을 보존하고, 후속 실행이 이를 재검토하게 만든다.

### 4.2 비목표
- 절대적 진실 판정
- 정적 규칙만으로 contradiction 처리
- 첫 버전부터 복잡한 자유형 멀티에이전트 군집 운영
- 단순 문서 검색을 knowledge graph라고 부르는 구조

## 5. 핵심 설계 원칙

### 5.1 Research Is Non-Monotonic
연구는 새 증거가 들어와도 기존 결론이 그대로 누적되는 과정이 아니다. 새 정보는 기존 믿음을 바꿀 수 있다.

### 5.2 Conflict Is Not Enough
`Conflict Detection`만으로는 부족하다. 중요한 것은 `왜 conflict가 생겼고 어떤 belief revision이 필요한가`이다.

### 5.3 Provenance Is Mandatory
출처와 생성 과정을 모르는 claim은 장기 연구에서 신뢰할 수 없다.

### 5.4 Time Matters
모순처럼 보이는 정보 중 상당수는 시간 차이에서 발생한다. 모든 핵심 claim은 시간 정보를 포함해야 한다.

### 5.5 Repetition Must Increase Information Gain
반복 실행은 같은 검색을 되풀이하는 것이 아니라, uncertainty를 줄이고 hypothesis space를 더 잘 탐색해야 한다.

## 6. 주요 사용자 시나리오

### 6.1 대화형 연구
사용자가 채팅으로 토픽을 지정하면, 시스템은 해당 토픽의 현재 belief state를 불러오고, 필요한 추가 연구를 수행한 뒤 최신 가설 상태와 근거를 반환한다.

### 6.2 주기적 연구
cron job이 특정 토픽을 주기적으로 다시 조사한다. 이때 시스템은 새 정보 수집뿐 아니라 기존 belief state의 재검토를 수행한다.

### 6.3 장기 추적
하나의 토픽을 수 주 또는 수 개월 동안 추적하며 아래 상태를 유지한다.

- 강화되는 hypothesis
- 약화되는 hypothesis
- 경쟁하는 대안 hypothesis
- unresolved conflict
- stale belief
- 후속 검증 질문

### 6.4 User-Owned Execution
사용자는 자신의 Codex 환경에 연결된 상태에서 토픽을 생성하고 연구를 실행한다. 시스템은 사용자의 Codex 기반 실행 능력을 활용해:

- interactive research run
- 장기 topic state 갱신
- scheduled rerun
- hypothesis revision

을 수행하되, belief state와 graph state는 사용자 소유 백엔드가 유지한다.

## 7. 시스템 관점의 핵심 개념

### 7.1 Topic
장기적으로 연구를 누적하는 단위.

### 7.2 Run
하나의 연구 실행 단위. interactive 또는 scheduled일 수 있다.

### 7.3 Evidence
새로 수집되었거나 입력된 관측 자료. 문서, 링크, 수치, 메모, transcript, API 응답 등이 될 수 있다.

### 7.4 Claim
Evidence로부터 정규화된 원자적 주장.

### 7.5 Hypothesis
여러 claim을 바탕으로 형성된 상위 수준 설명 또는 믿음.

### 7.6 ConflictCase
새로운 정보가 기존 claim/hypothesis와 tension을 일으킨 사건.

### 7.7 Decision
ConflictCase나 synthesis 결과에 따라 수행된 belief revision 결정.

### 7.8 Question
아직 검증되지 않았거나 분해가 필요한 연구 질문.

### 7.9 Strategy
한 run이 채택한 연구 접근 방식.

## 8. 이 시스템에서 Continual Research의 정의

이 시스템에서 continual research란 아래 네 가지를 모두 만족하는 상태를 뜻한다.

### 8.1 Memory Reuse
이전 run의 산출물이 다음 run의 입력에 실제로 사용된다.

### 8.2 Belief Revision
기존 가설이 새 근거에 따라 수정될 수 있다.

### 8.3 Strategy Diversification
반복 실행될수록 접근이 다양해진다.

### 8.4 Frontier Tracking
시스템은 현재 어디가 가장 불확실하고 어디를 파야 정보가치가 큰지 추적한다.

## 9. 설계 결론: 3층 논리지 그래프

이 시스템의 핵심 저장 구조는 단일 fact graph가 아니라 아래의 `3층 논리지 그래프`다.

### 9.1 Layer 1: World Graph
세상에 대해 말해진 내용의 정규화 표현.

포함 요소:

- Entity
- Event
- Relation
- Time
- Attribute

역할:

- 실제 세계에 대한 구조화 표현
- graph traversal과 retrieval의 기반
- temporal fact 관리

### 9.2 Layer 2: Epistemic Graph
시스템이 무엇을 왜 믿고 있는지를 표현하는 층.

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

### 9.3 Layer 3: Provenance / Process Graph
이 시스템이 왜 이런 판단을 하게 되었는지의 생성 과정을 표현하는 층.

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

## 10. Conflict 분류 체계

LLM Judge는 새 claim과 기존 belief state를 비교할 때 최소 아래 분류를 지원해야 한다.

- `no_conflict`
- `reinforcement`
- `contradiction`
- `temporal_mismatch`
- `granularity_mismatch`
- `source_disagreement`
- `supersession`
- `insufficient_grounding`

## 11. Belief Revision 엔진

### 11.1 허용되는 상태 전이
- `reinforce`
- `weaken`
- `split`
- `supersede`
- `retire`
- `defer`
- `escalate_to_human`

## 12. Research Harness 설계

### 12.1 한 run의 표준 흐름
1. `Load Topic State`
2. `Frontier Selection`
3. `Strategy Allocation`
4. `Evidence Acquisition`
5. `Claim Canonicalization`
6. `Argument Build`
7. `LLM Adjudication`
8. `Belief Revision`
9. `State Compression`
10. `Next Action Planning`

## 13. 실행 모드

### 13.1 Interactive Mode
- 사용자의 채팅 요청으로 즉시 run 실행

### 13.2 Scheduled Mode
- cron 또는 잡 워커 기반 실행

### 13.3 Execution Ownership
본 시스템은 `user-owned backend`를 기본 실행 모델로 채택한다.

## 14. LLM Execution Strategy

### 14.1 기본 원칙
본 시스템은 백엔드 하네스의 주된 LLM power를 `OpenAI Codex Max plan 연계 경로`에서 가져오는 것을 목표로 한다.

### 14.2 시스템의 역할 분담
- `Codex`
  추론, 도구 호출, 검색, 단계별 agentic 실행
- `Our Backend`
  topic graph, provenance, conflict modeling, belief revision, frontier selection, scheduling

## 15. 저장소 및 기술 방향

### 15.1 권장 방향
- graph DB: `Neo4j`
- temporal/incremental memory layer: `Graphiti`
- backend runtime: `Python`
- agent framework: `OpenAI Agents SDK`
- primary LLM execution path: `user-owned OpenAI Codex Max plan`

### 15.2 Agents SDK 사용 원칙
SDK가 담당하는 것:

- tool calling loop
- tracing
- handoff
- session handling
- context compaction 보조

애플리케이션이 직접 담당하는 것:

- research state machine
- hypothesis lifecycle
- conflict taxonomy
- belief revision authority
- graph update policy

## 16. v1 범위

### 16.1 Must Have
- topic 생성 및 관리
- interactive run
- scheduled run
- 3층 그래프 기반 상태 모델
- evidence to claim canonicalization
- support/challenge 관계 생성
- structured LLM adjudication
- belief revision 기록
- run report 및 next action 생성
- user-owned Codex execution 연동
- Python backend harness
- OpenAI Agents SDK 기반 agent loop

## 17. 최종 권장안

이 시스템은 `knowledge graph bot`이 아니라 `research truth-maintenance system`으로 설계해야 한다.

핵심 성공 조건은 단 하나다.

`같은 토픽을 n번 연구했을 때, 시스템이 더 많은 정보를 저장했느냐`가 아니라 `더 좋은 belief state에 도달했느냐`이다.
