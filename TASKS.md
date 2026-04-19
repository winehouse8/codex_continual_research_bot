# Initial Tasks

## 목표

Symphony와 Linear를 사용해 병렬로 맡길 수 있는 초기 개발 작업을 정의한다.

## Milestone 1: Repository Bootstrap

- `CB-1` Python 프로젝트 초기화
  - `pyproject.toml`
  - 기본 패키지 구조
  - lint/test 명령 정의
- `CB-2` 환경설정 계층 추가
  - `.env.example`
  - settings loader
  - runtime config schema
- `CB-3` 개발 문서 정리
  - README 개선
  - SPEC/ARCH 링크 정리
  - 로컬 실행 가이드 작성

## Milestone 2: Core Models

- `CB-4` 도메인 모델 정의
  - Topic
  - Run
  - Evidence
  - Claim
  - Hypothesis
  - ConflictCase
  - Decision
- `CB-5` graph schema 초안
  - world graph
  - epistemic graph
  - provenance graph
- `CB-6` artifact schema 정의
  - claims.json
  - judge_decisions.json
  - run_summary.md

## Milestone 3: Harness Skeleton

- `CB-7` research harness 골격 구현
  - run lifecycle
  - stage interface
  - state object
- `CB-8` planner/frontier selector 초안
  - frontier scoring
  - strategy allocation
- `CB-9` reporting skeleton 구현
  - run summary
  - next actions

## Milestone 4: Integrations

- `CB-10` Neo4j adapter 추가
- `CB-11` Graphiti integration spike
- `CB-12` Codex execution adapter 초안
- `CB-13` Agents SDK experiment

## Milestone 5: First End-to-End

- `CB-14` dummy topic 기준 local e2e
- `CB-15` claim canonicalization mock path
- `CB-16` structured adjudication mock path
- `CB-17` belief revision mock path

## 운영 원칙

- 이슈는 작고 검토 가능하게 유지한다.
- 한 이슈는 하나의 명확한 결과물을 가진다.
- 초기에는 scaffold와 state machine 골격에 집중한다.
