# 112. 하드코딩 설정값 리팩토링 - 종합 로드맵 (정리됨)

## 문서 정보

| 항목 | 내용 |
|------|------|
| 문서 번호 | 112 |
| 작성일 | 2026-01-26 |
| 수정일 | 2026-01-26 |
| 대상 | packages/selfhealing-python 전체 |
| 총 예상 작업량 | **24개 파일, 약 35개 설정값** (정리 후) |

---

## 1. 개요

기존 119개 항목에서 **저가치 항목 84개를 제외**하고 실제 마이그레이션 가치가 있는 35개 항목만 남김.

### 1.1 정리 기준

| 분류 | 조치 | 예시 |
|------|------|------|
| **고가치** | Settings 마이그레이션 | Celery retry, HTTP timeout, Cache TTL |
| **저가치** | 하드코딩 유지 | `page=1`, `timedelta(hours=1)`, 알고리즘 상수 |

---

## 2. 문서 구성 (정리 후)

| 문서 | 대상 | 파일 수 | 설정값 수 |
|------|------|---------|----------|
| [108번](108_HARDCODED_CONFIG_REFACTORING_PART1_CELERY_TASKS.md) | Celery Task 데코레이터 | 6개 | 16개 |
| [109번](109_HARDCODED_CONFIG_REFACTORING_PART2_CLASS_CONSTANTS.md) | 클래스/모듈 상수 | 8개 | 11개 |
| [111번](111_HARDCODED_CONFIG_REFACTORING_PART4_TIMEOUT_TTL.md) | Timeout/TTL | 10개 | 10개 |
| ~~110번~~ | ~~Dataclass 기본값~~ | 삭제됨 | 이미 `from_settings()` 존재 |

**총계: 24개 파일, 37개 설정값**

---

## 3. 삭제된 항목

### 3.1 110번 문서 (전체 삭제)

**삭제 이유**: 대부분 이미 `from_settings()` 팩토리 메서드 존재

| 파일 | 상태 |
|------|------|
| `core/backoff.py` | ✅ `from_settings()` 존재 |
| `audit/sync_worker.py` | ✅ `from_settings()` 존재 |
| `audit/reconciler.py` | ✅ `from_settings()` 존재 |
| `services/coordination/recovery_circuit_breaker.py` | ✅ `from_settings()` 존재 |

### 3.2 111번에서 제외된 항목

| 항목 | 제외 이유 |
|------|----------|
| `page=1` | 페이지네이션 표준값 |
| `page_size=20` | UI 표준값 |
| `limit=100` | API 기본값 |
| `timedelta(hours=1)` | 알고리즘 로직 상수 |
| `timedelta(minutes=5)` | 안전 기간 로직 |

---

## 4. 전체 구현 순서

### Phase 1: Celery Tasks (Week 1) - 108번

**우선순위: 높음** (운영 중 재시도 조정 필요)

| 작업 | 파일 수 | 설정값 수 |
|------|---------|----------|
| recovery_tasks.py | 1 | 4 |
| cleanup_tasks.py | 1 | 4 |
| daily_report.py | 1 | 1 |
| governance.py | 1 | 1 |
| config_apply.py | 1 | 2 |
| chaos_scheduler.py | 1 | 4 |

---

### Phase 2: Class Constants (Week 2) - 109번

**우선순위: 중간** (TTL 조정 필요)

| 작업 | 파일 수 | 설정값 수 |
|------|---------|----------|
| rate_limit adapters | 2 | 2 |
| airgap adapter | 1 | 1 |
| audit buffer | 1 | 1 |
| error_budget multiplier | 1 | 2 |
| recovery_dashboard | 1 | 2 |
| metrics snapshot | 1 | 1 |
| safe_gauge | 1 | 1 |

---

### Phase 3: Timeout/TTL (Week 3) - 111번

**우선순위: 중간** (외부 연동 timeout)

| 작업 | 파일 수 | 설정값 수 |
|------|---------|----------|
| HTTP timeout | 2 | 2 |
| Cache TTL | 4 | 4 |
| Cleanup max_age | 4 | 4 |

---

## 5. 하지 않을 작업 (명시적 스킵)

| 카테고리 | 항목 수 | 이유 |
|----------|---------|------|
| API 페이지네이션 | ~15개 | 표준 UI 값, 변경 필요 없음 |
| timedelta 알고리즘 | ~20개 | 로직에 밀접, 바꾸면 동작 변경 |
| Dataclass 기본값 | ~25개 | `from_settings()` 이미 존재 |
| 내부 버퍼 크기 | ~10개 | 성능 튜닝용, 거의 안 바꿈 |

---

## 6. 검증 체크리스트

| 단계 | 항목 | 완료 |
|------|------|------|
| Phase 1 | Celery Tasks Settings 연동 | ☐ |
| Phase 2 | Class Constants Settings 연동 | ☐ |
| Phase 3 | Timeout/TTL Settings 연동 | ☐ |
| 공통 | 하위 호환성 확인 | ☐ |
| 공통 | 단위 테스트 통과 | ☐ |
| 공통 | 환경변수 문서 업데이트 | ☐ |

---

## 7. 요약

| 항목 | Before | After | 절감 |
|------|--------|-------|------|
| 파일 수 | 60개 | **24개** | 60% 감소 |
| 설정값 수 | 119개 | **37개** | 69% 감소 |
| 예상 작업 시간 | 5주 | **3주** | 40% 감소 |

**결론**: 고가치 항목에 집중하여 ROI 극대화
