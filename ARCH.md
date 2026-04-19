# Architecture

## 목표

`codex_continual_research_bot`은 user-owned Codex 실행 모델을 전제로, 장기적인 topic memory와 belief revision을 유지하는 Python 백엔드 시스템이다.

## 상위 구조

1. `Interface Layer`
   - chat entrypoint
   - cron entrypoint
   - 추후 MCP/API adapter
2. `Research Harness`
   - topic load
   - frontier selection
   - strategy allocation
   - evidence acquisition
   - LLM adjudication
   - belief revision
3. `Memory Layer`
   - Neo4j
   - Graphiti
   - topic snapshot
   - run artifacts
4. `Execution Layer`
   - user-owned Codex execution path
   - OpenAI Agents SDK
5. `Ops Layer`
   - Symphony workflow
   - Linear project queue
   - workspace-per-issue development flow

## Python 패키지 초안

```text
src/codex_continual_research_bot/
  app/
  interfaces/
  harness/
  planner/
  acquisition/
  canonicalization/
  adjudication/
  revision/
  graph/
  storage/
  models/
  reporting/
  scheduling/
```

## 핵심 런타임 플로우

1. `TopicManager`가 topic state를 읽는다.
2. `FrontierSelector`가 불확실성이 큰 영역을 고른다.
3. `StrategyAllocator`가 이번 run의 research strategy를 정한다.
4. `AcquisitionRunner`가 자료를 수집한다.
5. `ClaimCanonicalizer`가 raw evidence를 claim으로 정규화한다.
6. `ArgumentBuilder`가 support/challenge/supersede 관계를 만든다.
7. `LLMJudge`가 structured adjudication을 반환한다.
8. `BeliefReviser`가 hypothesis state를 갱신한다.
9. `ReportWriter`가 run summary와 next action을 남긴다.

## 저장 구조

### 그래프
- world graph
- epistemic graph
- provenance/process graph

### 파일 기반 산출물
- `artifacts/runs/<run_id>/run_summary.md`
- `artifacts/runs/<run_id>/claims.json`
- `artifacts/runs/<run_id>/judge_decisions.json`
- `artifacts/runs/<run_id>/next_actions.json`

## Symphony와의 관계

Symphony는 제품 런타임이 아니라 개발 오케스트레이터다.

- Linear 이슈를 폴링한다.
- 이슈별 워크스페이스를 만든다.
- `codex_continual_research_bot` 저장소를 clone한다.
- Codex를 실행해 개발 작업을 수행하게 한다.

즉 제품 코어는 이 저장소 안에 있고, Symphony는 개발 자동화 레이어다.
