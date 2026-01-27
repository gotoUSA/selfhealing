# X-Test Artifact Cleaner (Auto-Cleanup)

**문서 번호:** 139  
**작성일:** 2026-01-27  
**상태:** 설계 완료  
**선행 문서:** 138_XTEST_PERMISSION_DUAL_LOCK.md

---

## 1. 목적

X-Test 세션 종료 후 남겨진 테스트 아티팩트(CB 상태, DLQ 항목, Idempotency 키 등)를 자동으로 정리하여 시스템 오염을 방지한다.

### 1.1 현재 문제

| 문제 | 현재 상태 |
|------|----------|
| 세션 TTL | 없음 (영구 지속) |
| 자동 정리 | 없음 (수동 Reset만) |
| CB 상태 원복 | 수동 API 호출 필요 |
| DLQ 테스트 항목 | 명시적 삭제 필요 |

### 1.2 목표

| 항목 | 목표 |
|------|------|
| 세션 TTL | 4시간 (긴 시나리오 테스트 고려) |
| 자동 정리 | Celery Beat 30분 주기 |
| 정리 대상 | CB 상태, DLQ 항목, Idempotency 키, Rate Limit 카운터 |

---

## 2. 현재 상태 분석

### 2.1 기존 Cleanup 태스크 패턴

| 파일 | 태스크 | 스케줄 |
|------|--------|--------|
| `tasks/cleanup_tasks.py` | `archive_old_dlq_entries_task` | 매일 03:00 |
| `tasks/cleanup_tasks.py` | `cleanup_old_recovery_sessions_task` | 매일 04:00 |
| `celery_tasks/circuit_breaker_tasks.py` | `check_manual_override_expiry_task` | 5분마다 |

### 2.2 기존 정리 메서드

| 파일 | 메서드 | 용도 |
|------|--------|------|
| `views/xtest/integration.py` | `ResetView.post()` | 수동 컴포넌트 리셋 |
| `views/xtest/dlq.py` | `DLQResetView.post()` | DLQ 테스트 항목 삭제 |
| `views/xtest/idempotency.py` | `ClearKeysView.post()` | Idempotency 키 삭제 |

### 2.3 Beat Schedule 통합 구조

| 파일 | 함수 | 역할 |
|------|------|------|
| `adapters/celery/beat_schedule.py` | `get_selfhealing_beat_schedule()` | 전체 스케줄 통합 |

---

## 3. 설계

### 3.1 세션 메타데이터 저장

**Redis 키 구조:**

| 키 | 타입 | 내용 |
|----|------|------|
| `xtest:session:{session_id}` | Hash | 세션 메타데이터 |
| `xtest:session:active` | Set | 활성 세션 ID 목록 |

**세션 메타데이터 필드:**

| 필드 | 설명 |
|------|------|
| `created_at` | 생성 시간 (ISO 8601) |
| `ttl_hours` | TTL (기본 4시간) |
| `user` | 생성 사용자 |
| `components` | 영향받은 컴포넌트 목록 |
| `artifacts` | 생성된 아티팩트 ID 목록 |

### 3.2 XTestCleanupService

**위치:** `services/xtest_cleanup_service.py` (신규)

| 메서드 | 역할 |
|--------|------|
| `cleanup_expired_sessions()` | 만료 세션 정리 |
| `restore_cb_states()` | X-Test CB 상태 원복 |
| `purge_dlq_entries()` | X-Test DLQ 항목 삭제 |
| `clear_idempotency_keys()` | X-Test Idempotency 키 삭제 |
| `reset_rate_limit_counters()` | X-Test Rate Limit 초기화 |

### 3.3 Cleanup 범위

| 컴포넌트 | 정리 동작 |
|---------|----------|
| Circuit Breaker | `xtest_mode=True` 상태를 CLOSED로 원복 |
| DLQ | `source="x-test-mode"` 항목 삭제 |
| Idempotency | `xtest:idempotency:*` 키 삭제 |
| Rate Limiter | `xtest:rate_limit:*` 카운터 삭제 |
| 시나리오 결과 | 인메모리 `_scenario_results` 정리 |

### 3.4 Settings

**위치:** `settings/xtest_cleanup.py` (신규)

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `session_ttl_hours` | 4 | 세션 만료 시간 |
| `cleanup_interval_minutes` | 30 | 정리 주기 |
| `cb_auto_restore` | True | CB 자동 원복 |
| `dlq_auto_purge` | True | DLQ 자동 삭제 |
| `idempotency_auto_clear` | True | Idempotency 자동 삭제 |

---

## 4. 구현 순서

### Step 1: Settings 정의

**파일:** `settings/xtest_cleanup.py`

| 순서 | 항목 |
|------|------|
| 1-1 | `XTestCleanupSettings` 클래스 정의 |
| 1-2 | 환경변수 매핑 |
| 1-3 | `get_xtest_cleanup_settings()` 함수 |

### Step 2: 세션 메타데이터 관리

**파일:** `services/xtest_session_manager.py` (신규)

| 순서 | 항목 |
|------|------|
| 2-1 | `XTestSessionManager` 클래스 |
| 2-2 | `create_session()` - 세션 생성 + Redis 저장 |
| 2-3 | `get_session()` - 세션 조회 |
| 2-4 | `get_expired_sessions()` - 만료 세션 목록 |
| 2-5 | `register_artifact()` - 아티팩트 등록 |

### Step 3: XTestCleanupService 구현

**파일:** `services/xtest_cleanup_service.py`

| 순서 | 항목 |
|------|------|
| 3-1 | 정리 메서드 구현 |
| 3-2 | 기존 Repository 연동 |
| 3-3 | Audit 로깅 연동 |

### Step 4: Celery 태스크 생성

**파일:** `tasks/xtest_cleanup_tasks.py` (신규)

| 순서 | 항목 |
|------|------|
| 4-1 | `cleanup_xtest_artifacts()` thin wrapper |
| 4-2 | `cleanup_xtest_artifacts_task` Celery 태스크 |
| 4-3 | `get_xtest_cleanup_beat_schedule()` 스케줄 정의 |

### Step 5: Beat Schedule 통합

**파일:** `adapters/celery/beat_schedule.py`

| 순서 | 항목 |
|------|------|
| 5-1 | `include_xtest_cleanup` 플래그 추가 |
| 5-2 | `get_xtest_cleanup_beat_schedule()` import 및 통합 |

### Step 6: XTestModeMixin 연동

**파일:** `api/django/views/xtest/base.py`

| 순서 | 항목 |
|------|------|
| 6-1 | 요청 시작 시 세션 생성/갱신 |
| 6-2 | 아티팩트 생성 시 세션에 등록 |

---

## 5. 테스트 계획

### 5.1 단위 테스트

| 테스트 케이스 | 검증 항목 |
|--------------|----------|
| `test_session_creation` | 세션 메타데이터 저장 |
| `test_session_expiry_detection` | 만료 세션 감지 |
| `test_artifact_registration` | 아티팩트 등록 |
| `test_cleanup_cb_states` | CB 상태 원복 |
| `test_cleanup_dlq_entries` | DLQ 항목 삭제 |
| `test_cleanup_idempotency_keys` | Idempotency 키 삭제 |

### 5.2 통합 테스트

| 테스트 시나리오 |
|----------------|
| 세션 생성 → 4시간 경과 → 자동 정리 확인 |
| DLQ 주입 → 정리 태스크 실행 → 항목 삭제 확인 |
| CB 조작 → 정리 태스크 실행 → CLOSED 상태 확인 |

---

## 6. 모니터링

### 6.1 메트릭

| 메트릭 | 설명 |
|--------|------|
| `xtest_sessions_active` | 현재 활성 세션 수 |
| `xtest_cleanup_runs_total` | 정리 태스크 실행 횟수 |
| `xtest_artifacts_cleaned_total` | 정리된 아티팩트 수 |

### 6.2 알림

| 조건 | 알림 |
|------|------|
| 활성 세션 > 100 | WARNING |
| 정리 태스크 실패 | ERROR |

---

## 7. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `tasks/cleanup_tasks.py` | Celery 태스크 패턴 |
| `adapters/celery/beat_schedule.py` | Beat 스케줄 통합 |
| `views/xtest/integration.py` | `ResetView` 정리 로직 |
| `services/audit/xtest_audit.py` | Audit 로깅 |

---

**다음 문서:** 140_XTEST_REGIONAL_BOUNDARY.md
