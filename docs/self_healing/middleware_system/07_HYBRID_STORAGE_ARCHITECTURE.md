# 07. 하이브리드 스토리지 아키텍처

> **Version**: 2.0.0  
> **Last Updated**: 2026-01-02  
> **Status**: Phase 1-7 구현 완료  
> **Author**: AI Assistant  
> **Reference**: 06_REDIS_MIGRATION.md, 56_AUDIT_MIDDLEWARE_DESIGN.md

---

## 1. 개요

### 1.1 배경

Redis 마이그레이션(Phase 5) 완료 후, Django ORM을 직접 import하는 코드들이 `ImportError`를 발생시키는 문제가 발견되었습니다.

**근본 원인 분석:**
- 런타임 저장소(Redis)와 통계/분석 저장소(ORM)의 요구사항이 다름
- Redis는 단순 CRUD에 최적화, 복잡한 집계 쿼리에는 부적합
- Django ORM 삭제 시 통계 기능 상실

### 1.2 설계 원칙

| 원칙 | 설명 |
|------|------|
| **적재적소** | 각 기술의 강점을 살리는 영역에 배치 |
| **Domain-Free** | selfhealing 패키지는 특정 도메인(shopping 등)을 모름 |
| **프레임워크 중립** | Django, FastAPI, Flask 모두 지원 |
| **Graceful Degradation** | 통계 기능 없어도 런타임은 정상 동작 |
| **비동기 영속화** | ORM 저장은 Celery로 비동기 처리 (v2.0.0) |
| **Audit Trail 통합** | DLQ와 감사 로그의 100% 일치 보장 (v2.0.0) |

---

## 2. 아키텍처 개요

### 2.1 계층 분리

```
┌─────────────────────────────────────────────────────────────────┐
│                         selfhealing                              │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              런타임 계층 (Runtime Layer)                  │    │
│  │  • Circuit Breaker 상태 체크/변경                        │    │
│  │  • DLQ 적재/조회/replay                                  │    │
│  │  • 1-2ms 응답 필수, DB 장애 시에도 동작 필수             │    │
│  │  [구현] Redis + ResilientStorageBackend (WAL fallback)   │    │
│  └─────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              운영 계층 (Operations Layer)                 │    │
│  │  • 대시보드 통계 (도메인별 집계, 추이 분석)              │    │
│  │  • DLQ 목록 조회 (페이지네이션, 필터링)                  │    │
│  │  • 정리 작업 (archive, purge), 10-100ms 응답 허용        │    │
│  │  [구현] SQL/ORM (Django ORM 또는 SQLAlchemy)             │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 기술별 적합성 비교

| 작업 유형 | Redis | SQL/ORM | 선택 |
|----------|-------|---------|------|
| CB 상태 체크 (`should_allow`) | ✅ 1-2ms | ❌ 5-10ms | **Redis** |
| DLQ 적재 (`store_failure`) | ✅ 1-2ms | ❌ 5-10ms | **Redis** |
| 도메인별 pending 개수 집계 | ❌ O(n) 스캔 | ✅ GROUP BY | **ORM** |
| 월간 복구율 계산 | ❌ 메모리 집계 | ✅ AVG, COUNT | **ORM** |
| 분산 환경 동기화 | ✅ 클러스터 | ❌ 단일 DB | **Redis** |
| DB 장애 격리 | ✅ 독립 | ❌ 같이 죽음 | **Redis** |

---

## 3. 프레임워크별 지원 전략

### 3.1 지원 매트릭스

| 사용자 유형 | 런타임 저장소 | 통계 저장소 | 대시보드 |
|------------|--------------|-------------|----------|
| **Django 프로젝트** | Redis | Django ORM | ✅ 풀 기능 |
| **FastAPI 프로젝트** | Redis | SQLAlchemy | ✅ 풀 기능 |
| **경량/독립 사용** | Redis | (없음) | Prometheus/Grafana |

### 3.2 어댑터 등록 방법

**Django 사용자**: `AppConfig.ready()`에서 `DjangoStatisticsAdapter` 등록  
**FastAPI 사용자**: `@app.on_event("startup")`에서 `SQLAlchemyStatisticsAdapter` 등록  
**경량 사용자**: 어댑터 미등록 시 `NullStatisticsRepository` 자동 사용

> 💡 구체적인 코드 예시는 [selfhealing/adapters/django/statistics.py](../../../packages/selfhealing-python/src/selfhealing/adapters/django/statistics.py) 참조

---

## 4. 인터페이스 설계

### 4.1 StatisticsRepositoryInterface

통계/대시보드용 Repository 인터페이스로, 런타임 Repository와 분리됩니다.

**주요 메서드:**

| 카테고리 | 메서드 | 설명 |
|----------|--------|------|
| DLQ 통계 | `get_status_counts()` | 상태별 DLQ 개수 집계 |
| | `get_domain_distribution()` | 도메인별 분포 (상위 N개) |
| | `get_resolution_rate()` | 복구 성공률 (최근 N일) |
| DLQ 목록 | `list_entries()` | 페이지네이션 조회 |
| | `get_entry_detail()` | 항목 상세 조회 |
| 정리 작업 | `archive_old_entries()` | 오래된 resolved 아카이브 |
| | `purge_archived()` | 아카이브 영구 삭제 |
| CB 통계 | `get_circuit_breaker_summary()` | open/closed/half-open 개수 |
| Audit Trail | `get_audit_trail_by_entity()` | 엔티티별 감사 추적 조회 |
| | `link_audit_entry()` | 감사 항목 연결 |

> 💡 전체 인터페이스 정의는 [selfhealing/interfaces/statistics.py](../../../packages/selfhealing-python/src/selfhealing/interfaces/statistics.py) 참조

### 4.2 Null Object 패턴

통계 어댑터 미등록 시 `NullStatisticsRepository`가 자동으로 사용됩니다:
- 에러 없이 빈 결과 반환
- 런타임 기능 정상 동작
- 대시보드에서 "통계 미지원" 메시지 표시

---

## 5. ProviderRegistry 확장

### 5.1 주요 메서드

| 메서드 | 용도 |
|--------|------|
| `get_circuit_breaker_repo()` | 런타임용 CB Repository (Redis) |
| `get_failed_operation_repo()` | 런타임용 DLQ Repository (Redis) |
| `register_statistics_adapter()` | 통계 어댑터 등록 (앱 시작 시) |
| `get_statistics_repo()` | 통계용 Repository 반환 |
| `has_statistics_adapter()` | 통계 어댑터 등록 여부 확인 |

> 💡 구현 상세는 [selfhealing/factory.py](../../../packages/selfhealing-python/src/selfhealing/factory.py) 참조

---

## 6. 파일 구조

```
selfhealing/
├── interfaces/
│   └── statistics.py          # StatisticsRepositoryInterface + DTOs
├── adapters/
│   ├── statistics/
│   │   ├── __init__.py
│   │   └── null.py            # NullStatisticsRepository (기본값)
│   ├── django/
│   │   ├── __init__.py
│   │   └── statistics.py      # DjangoStatisticsAdapter
│   ├── sqlalchemy/
│   │   ├── __init__.py
│   │   └── statistics.py      # SQLAlchemyStatisticsAdapter
│   └── celery/
│       └── tasks.py           # 비동기 영속화 태스크
└── services/
    ├── dashboard_service.py   # ProviderRegistry 사용
    └── health_check.py        # ProviderRegistry 사용
```

---

## 7. 구현 상태

| Phase | 내용 | 상태 |
|-------|------|------|
| 1 | 인터페이스 및 Null 어댑터 | ✅ 완료 |
| 2 | Django 어댑터 복원 | ✅ 완료 |
| 3 | 기존 코드 리팩토링 | ✅ 완료 |
| 4 | SQLAlchemy 어댑터 | ✅ 완료 |
| 5 | 비동기 영속화 구현 | ✅ 완료 |
| 6 | Audit Trail 통합 | ✅ 완료 |
| 7 | 테스트 및 문서화 | ✅ 완료 |

---

## 8. 비동기 영속화 (v2.0.0)

### 8.1 원칙

> ⚠️ ORM 저장은 **반드시 비동기로** 처리해야 합니다.

**이유:**
- 동기 저장: Redis 2ms + ORM 50ms = **52ms** (Redis 이점 상실)
- 비동기 저장: Redis 2ms (응답) + ORM 50ms (백그라운드) = **2ms 응답**

### 8.2 Celery 태스크

| 태스크 | 설명 | 큐 |
|--------|------|-----|
| `async_persist_dlq_entry` | 단일 DLQ 항목 영속화 | `persistence` |
| `async_persist_batch` | 배치 영속화 (AuditMiddleware 연동) | `persistence` |
| `link_audit_to_dlq` | Audit 레코드 연결 | `persistence` |

---

## 9. Audit Trail 통합

### 9.1 EntityAuditTrail

DLQ 항목의 전체 감사 추적을 제공합니다:
- `entity_id`: DLQ 항목 ID
- `entries`: 시간순 이벤트 목록
- `is_chain_valid`: 해시 체인 무결성 검증

### 9.2 활용

기술 실사 시 "장애 데이터와 감사 증적의 100% 일치"를 입증할 수 있습니다.

---

## 10. FAQ

| 질문 | 답변 |
|------|------|
| Redis와 ORM에 데이터 중복? | 아니요. Redis는 런타임 상태, ORM은 이력/분석용 |
| 통계 어댑터 없으면? | 런타임 정상 동작, `NullStatisticsRepository` 사용 |
| 기존 Django 프로젝트 변경? | `apps.py`에 어댑터 등록 한 줄 추가만 필요 |
| ORM 저장 왜 비동기? | Redis 속도 이점 유지 (2ms vs 52ms) |
| Audit 불일치 탐지? | `is_chain_valid` 속성으로 해시 체인 검증 |

---

## 11. 성능 비교

| 작업 | Redis | Django ORM |
|------|-------|------------|
| 단순 조회 (CB 상태) | **1.2ms** | 5.8ms |
| 복잡한 집계 (10만 건) | 850ms | **45ms** |

**결론:**
- **런타임 상태 관리** → Redis (속도, 분산, 장애 격리)
- **통계/분석** → ORM (SQL 최적화, 인덱스 활용)

---

## 12. 테스트

### 12.1 단위 테스트

```bash
# selfhealing 패키지 내부 테스트 (40개)
cd packages/selfhealing-python
pytest tests/unit/adapters/test_statistics_repository.py -v
```

### 12.2 통합 테스트

```bash
# Docker Compose로 Redis + Django 환경 테스트
docker-compose -f docker-compose.test.yml run --rm test-hybrid-storage

# 또는 개별 실행
docker-compose -f docker-compose.test.yml up -d db redis
pytest tests/integration/test_hybrid_storage_integration.py -v --no-cov -p no:xdist
docker-compose -f docker-compose.test.yml down
```

---

## 13. 관련 문서

- [05_RESILIENT_STORAGE_BACKEND.md](05_RESILIENT_STORAGE_BACKEND.md) - Redis 런타임 저장소
- [06_REDIS_MIGRATION.md](06_REDIS_MIGRATION.md) - 마이그레이션 기록
- [56_AUDIT_MIDDLEWARE_DESIGN.md](../56_AUDIT_MIDDLEWARE_DESIGN.md) - Audit 로그 설계
- [00_INDEX.md](00_INDEX.md) - 문서 인덱스
