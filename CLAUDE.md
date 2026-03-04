# CLAUDE.md — SelfHealing 프로젝트 지시사항

## 프로젝트 개요

Django 5.2 + DRF + PostgreSQL 15 + Redis 7 + Celery + Kafka + Prometheus + OTEL + K8s 기반의
엔터프라이즈급 Self-Healing 라이브러리 프로젝트.

- **핵심 패키지**: `packages/selfhealing-python/src/selfhealing/`
- **테스트베드**: `shopping/` (주문/결제/포인트) — 아키텍처 판단 근거로 사용 금지

## 프로젝트 모듈 구조

| 모듈 | 핵심 책임 |
|------|-----------|
| `core/` | 백오프, Circuit Breaker 상태, 실행 엔진, TLS, 풀 모니터, Graceful Shutdown |
| `services/` | 60+ 서비스 — CB, DLQ, Replay, Retry, Saga, Chaos, Governance, Canary, 에러예산, 포렌식 |
| `adapters/` | 23개 어댑터 — Django/Redis/Celery/Kafka/Postgres/Memory/Cache 등 |
| `interfaces/` | Repository/Cache/Queue/Framework 추상 인터페이스 |
| `audit/` | GDPR/CCPA 준수 감사 로깅, Hash Chain, WAL, 다중 백엔드 |
| `coordination/` | Redis/etcd 리더 선출, DLQ Consumer 조율 |
| `multiregion/` | Active-Active, CRDT, 페일오버, Quorum Witness |
| `metrics/` | Prometheus 메트릭, Drift 감지, Reliability Manager |
| `scaling/` | Token Bucket, Load Shedding, HPA 메트릭, Graceful Degradation |
| `meta/` | Meta-Watchdog — 자기 자신을 모니터링 |
| `resilience/` | Bulkhead, Hedging, Policy Composer |
| `settings/` | 50+ Pydantic 설정 (SELFHEALING_ 환경변수) |
| `factory.py` | ProviderRegistry — 어댑터 중앙 등록소 |
| `observability/` | OpenTelemetry 초기화 |

## 코드 작성 규칙

- **추측 금지**: 확실하지 않으면 기존 코드를 먼저 읽고 진행한다
- **일관성 유지**: 기존 네이밍 컨벤션, import 스타일, 에러 처리 패턴을 따른다
- **코드 우선**: docs/ 문서와 실제 코드가 불일치하면 코드를 기준으로 한다
- **중복 확인**: 새 기능 구현 전 `services/`, `core/`, `adapters/`에 유사 구현이 있는지 먼저 확인한다
- **코드 근거**: 답변 시 관련 파일 경로와 핵심 코드 스니펫을 `파일경로:라인번호` 형태로 인용한다

## 금지사항

- 코드를 읽지 않고 일반론만으로 답변하지 말 것
- `shopping/` 앱을 아키텍처 판단 근거로 사용하지 말 것
- 사용자가 명시적으로 요청하지 않은 코드 리팩토링 제안을 하지 말 것
- 주석에 코드 라인 번호를 작성하지 말 것
- 클래스명/함수명/파일명에 `phase`, `reference` 등 문서 참조 용어 사용 금지

## 테스트 위치 원칙

- `packages/selfhealing-python/` 코드의 순수 단위 테스트 → `packages/selfhealing-python/tests/unit/`
- 전역 `tests/` 폴더는 통합/인프라 테스트 전용
- `packages/selfhealing-python/tests/` 내 `services/`, `core/`, `audit/` 폴더는 레거시 — 신규 테스트는 `unit/` 하위에 배치

## 사용 가능한 커스텀 명령어

| 명령어 | 설명 |
|--------|------|
| `/execute` | 구현계획 문서를 읽고 코드·단위테스트·통합테스트까지 5단계로 구현 |
| `/verify` | 구현문서·코드·테스트의 정합성을 6단계로 검증하고 불일치 해소 |
| `/review` | 코드 품질·설계·가독성·보안 등 체크리스트 기반 코드 리뷰 |
| `/advisor` | 프로젝트 Q&A, 아이디어 평가, 역제안을 수행하는 어드바이저 |
