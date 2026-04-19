# codex_continual_research_bot

Codex 기반 user-owned backend 모델을 전제로 하는 Continual Research Bot 프로젝트.

## 목표

- 동일 토픽에 대한 반복 연구
- claim / hypothesis / conflict / decision 중심의 belief revision
- chat + cron 공용 research harness
- Neo4j + Graphiti 기반 장기 메모리
- Symphony를 통한 개발 작업 오케스트레이션

## 문서

- [SPEC.md](./SPEC.md)
- [ARCH.md](./ARCH.md)
- [TASKS.md](./TASKS.md)

## 개발 방식

- 실제 제품 개발은 이 저장소에서 진행한다.
- 개발 오케스트레이션은 Symphony + Linear를 사용한다.
- Symphony는 이 저장소를 이슈별 워크스페이스로 clone해서 Codex 작업을 실행한다.

## 현재 상태

- 초기 저장소 생성 완료
- 핵심 제품 문서 추가
- Symphony 연동 설정 진행 중
