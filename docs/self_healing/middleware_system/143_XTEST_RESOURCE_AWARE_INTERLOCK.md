# X-Test Resource-Aware Chaos Interlock

**문서 번호:** 143  
**작성일:** 2026-01-27  
**상태:** 설계 완료  
**선행 문서:** 142_XTEST_CAUSATION_ID_PREFIX.md

---

## 1. 목적

시스템 CPU/메모리가 과부하 상태일 때 X-Test 요청을 자동 차단하여, 테스트가 운영 시스템에 추가 부담을 주는 것을 방지한다.

### 1.1 현재 문제

| 문제 | 현재 상태 |
|------|----------|
| CPU/메모리 체크 없음 | 시스템 과부하에서도 테스트 가능 |
| 횟수 제한만 존재 | 최대 20회 주입 제한 |
| 리소스 기반 차단 없음 | `SafetyGuard`에 리소스 체크 미포함 |

### 1.2 목표

| 항목 | 목표 |
|------|------|
| CPU 임계값 | 80% 초과 시 차단 |
| 메모리 임계값 | 85% 초과 시 차단 |
| HTTP 응답 | 429 Too Many Requests + Retry-After |

---

## 2. 현재 상태 분석

### 2.1 기존 리소스 모니터링

| 파일 | 클래스/함수 | 역할 |
|------|------------|------|
| `core/resource_monitor.py` | `CgroupResourceMonitor` | 컨테이너 메모리 모니터링 |
| `core/resource_monitor.py` | `get_memory_usage_percent()` | 메모리 사용률 |
| `core/resource_monitor.py` | `check_safe_for_exhaustion()` | Chaos 안전성 체크 |
| `views/xtest/base.py` | `collect_system_snapshot()` | CPU/메모리 수집 (psutil) |

### 2.2 기존 SafetyGuard 체크

| 파일 | 체크 항목 |
|------|----------|
| `services/governance/safety_guard.py` | Error Budget |
| `services/governance/safety_guard.py` | Kill Switch |
| `services/governance/safety_guard.py` | Emergency Mode |
| `services/governance/safety_guard.py` | Panic Threshold |
| `services/governance/safety_guard.py` | CB Freeze Mode |
| `services/governance/safety_guard.py` | Active Incidents |

### 2.3 RecoveryGate 임계값

| 파일 | 임계값 | 용도 |
|------|--------|------|
| `services/recovery_gate.py` | `cpu_threshold_percent: 80` | 복구 허용 여부 |
| `services/recovery_gate.py` | `error_rate_threshold: 0.05` | 에러율 체크 |

---

## 3. 설계

### 3.1 ResourceGuard 클래스

**위치:** `services/governance/resource_guard.py` (신규)

| 메서드 | 역할 |
|--------|------|
| `is_safe_for_chaos()` | CPU/메모리 체크 후 안전 여부 반환 |
| `get_resource_status()` | 현재 리소스 상태 조회 |
| `get_recommended_wait()` | 권장 대기 시간 반환 |

### 3.2 임계값 설정

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `cpu_threshold_percent` | 80 | CPU 임계값 (RecoveryGate와 일관성) |
| `memory_threshold_percent` | 85 | 메모리 임계값 |
| `check_interval_seconds` | 1 | 체크 간격 |

### 3.3 Settings

**위치:** `settings/resource_guard.py` (신규)

| 설정 | 환경변수 | 기본값 |
|------|---------|--------|
| `cpu_threshold` | `XTEST_CPU_THRESHOLD` | 80 |
| `memory_threshold` | `XTEST_MEMORY_THRESHOLD` | 85 |
| `enabled` | `XTEST_RESOURCE_CHECK_ENABLED` | true |

### 3.4 429 응답 형식

| 필드 | 값 |
|------|-----|
| `error` | `resource_overloaded` |
| `message` | 상세 메시지 |
| `cpu_percent` | 현재 CPU 사용률 |
| `memory_percent` | 현재 메모리 사용률 |
| `retry_after` | 권장 대기 시간 (초) |

**HTTP 헤더:**

| 헤더 | 값 |
|------|-----|
| `Retry-After` | 30 (초) |

---

## 4. 구현 순서

### Step 1: Settings 정의

**파일:** `settings/resource_guard.py`

| 순서 | 항목 |
|------|------|
| 1-1 | `ResourceGuardSettings` 클래스 |
| 1-2 | 환경변수 매핑 |
| 1-3 | `get_resource_guard_settings()` 함수 |

### Step 2: ResourceGuard 클래스 생성

**파일:** `services/governance/resource_guard.py`

| 순서 | 항목 |
|------|------|
| 2-1 | `ResourceGuard` 클래스 |
| 2-2 | `is_safe_for_chaos()` 구현 |
| 2-3 | `CgroupResourceMonitor` 연동 (컨테이너 우선) |
| 2-4 | `psutil` 폴백 (일반 환경) |

### Step 3: XTestModeMixin 연동

**파일:** `api/django/views/xtest/base.py`

| 순서 | 항목 |
|------|------|
| 3-1 | `check_resource_constraints()` 메서드 추가 |
| 3-2 | `ResourceGuard.is_safe_for_chaos()` 호출 |
| 3-3 | 429 Response 생성 |
| 3-4 | `Retry-After` 헤더 추가 |

### Step 4: 체크 순서 통합

**파일:** `api/django/views/xtest/base.py`

| 순서 | 체크 |
|------|------|
| 1차 | `HasChaosTestPermission` (RBAC) |
| 2차 | `check_resource_constraints()` (리소스) |
| 3차 | `check_chaos_permission()` (헤더/환경) |

### Step 5: __init__.py 업데이트

**파일:** `services/governance/__init__.py`

| 순서 | 항목 |
|------|------|
| 5-1 | `ResourceGuard` export 추가 |

---

## 5. 리소스 측정 우선순위

### 5.1 컨테이너 환경

| 순서 | 소스 | 조건 |
|------|------|------|
| 1 | `CgroupResourceMonitor` | cgroup v2 사용 가능 |
| 2 | `CgroupResourceMonitor` | cgroup v1 폴백 |
| 3 | `psutil` | cgroup 미지원 시 |

### 5.2 메모리 측정

| 환경 | 측정 방법 |
|------|----------|
| Kubernetes | cgroup memory.current / memory.max |
| Docker | cgroup memory.usage_in_bytes |
| 일반 | psutil.virtual_memory() |

---

## 6. 테스트 계획

### 6.1 단위 테스트

| 테스트 케이스 | 검증 항목 |
|--------------|----------|
| `test_cpu_threshold_blocks` | CPU 80% 초과 시 차단 |
| `test_memory_threshold_blocks` | 메모리 85% 초과 시 차단 |
| `test_below_threshold_allows` | 임계값 미만 시 허용 |
| `test_retry_after_header` | 429 응답에 Retry-After 포함 |
| `test_cgroup_priority` | 컨테이너 환경에서 cgroup 우선 |

### 6.2 통합 테스트

| 테스트 시나리오 |
|----------------|
| 고부하 상황 시뮬레이션 → X-Test 차단 확인 |
| 부하 해소 후 → X-Test 허용 확인 |

---

## 7. 모니터링

### 7.1 메트릭

| 메트릭 | 설명 |
|--------|------|
| `xtest_resource_blocked_total` | 리소스 부족으로 차단된 횟수 |
| `xtest_resource_check_cpu` | 체크 시점 CPU 사용률 |
| `xtest_resource_check_memory` | 체크 시점 메모리 사용률 |

### 7.2 알림

| 조건 | 알림 |
|------|------|
| resource_blocked > 5/min | WARNING |
| 연속 10회 차단 | ERROR |

---

## 8. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `core/resource_monitor.py` | `CgroupResourceMonitor` |
| `services/governance/safety_guard.py` | `SafetyGuard` 체크 패턴 |
| `services/recovery_gate.py` | CPU 임계값 참조 (80%) |
| `api/django/views/xtest/base.py` | `collect_system_snapshot()` psutil 패턴 |

---

**다음 문서:** 144_XTEST_CROSS_REGION_SCENARIO.md
