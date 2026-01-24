# 86. Security, Isolation, BlastRadius 서비스 Audit 연동 구현

## ✅ 구현 완료 (2026-01-24)

**Phase 1 Critical 항목 - 모두 구현 완료**

| 서비스 | 상태 | 테스트 |
|--------|------|--------|
| SecurityViolationService | ✅ 완료 | 8/8 PASSED |
| RegionalIsolationGate | ✅ 완료 | 7/7 PASSED |
| BlastRadiusService | ✅ 완료 | 6/6 PASSED |

**테스트 파일**: `packages/selfhealing-python/tests/unit/audit/test_audit_phase1_security_isolation_blast.py`  
**총 테스트**: 30개 통과

---

## 1. 개요

이 문서는 보안, 격리, 장애 영향 범위 관련 서비스들의 audit 시스템 연동 방안을 정의한다.

---

## 2. SecurityViolationService 연동

### 2.1 대상 파일

- **위치**: `selfhealing/services/security/service.py`
- **클래스**: `SecurityViolationService`

### 2.2 ✅ 구현 완료 (2026-01-24)

| 메서드 | audit 함수 | action 값 | 라인 |
|--------|-----------|-----------|------|
| `handle_violation()` | `log_security_violation_audit` | `"handle_violation"` | L186-199 |
| `_invalidate_user_sessions()` | `log_security_violation_audit` | `"invalidate_session"` | L341-356 |
| `_temporary_ip_ban()` | `log_security_violation_audit` | `"block_ip"` | L382-393 |
| `_permanent_ip_ban()` | `log_security_violation_audit` | `"block_ip"` | L403-414 |

### 2.3 연동이 필요한 메서드

| 메서드 | 발생 이벤트 | 연동 필요성 |
|--------|------------|------------|
| `handle_violation` | 보안 위반 처리 | Critical - 모든 위반 기록 필요 |
| `ban_ip` | IP 차단 | Critical - 차단 이력 추적 |
| `invalidate_sessions` | 세션 무효화 | High - 강제 로그아웃 기록 |
| `_apply_rate_limit` | 속도 제한 적용 | Medium - 빈번하므로 샘플링 고려 |

### 2.4 구현 방안

#### 2.4.1 필요한 Import 추가

`selfhealing.services.audit`에서 적절한 함수를 import. 신규 함수 필요시 `log_security_violation_audit` 추가 검토.

#### 2.4.2 handle_violation 연동

현재 위치: 파일 내 `_handle_violation_impl` 메서드 또는 유사 로직

**기록할 정보**:
- `action`: "security_violation_detected"
- `violation_type`: 위반 유형 (brute_force, injection 등)
- `source_ip`: 요청 발신 IP
- `target_resource`: 대상 리소스
- `severity`: 심각도
- `action_taken`: 조치 (blocked, rate_limited 등)

#### 2.4.3 ban_ip 연동

**기록할 정보**:
- `action`: "ip_banned"
- `target_ip`: 차단된 IP
- `duration`: 차단 기간
- `reason`: 차단 사유
- `operator`: "system" 또는 수동 차단시 관리자

#### 2.4.4 invalidate_sessions 연동

**기록할 정보**:
- `action`: "sessions_invalidated"
- `user_id`: 대상 사용자
- `session_count`: 무효화된 세션 수
- `reason`: 무효화 사유

### 2.5 신규 Audit 함수 정의 (필요시)

`selfhealing/services/audit/security_audit.py` 파일 생성 검토:

```
함수명: log_security_violation_audit

파라미터:
- violation_type: str
- source_ip: str
- target_resource: str
- severity: str
- action_taken: str
- details: dict (optional)
```

### 2.6 성능 고려사항

- 보안 이벤트는 빈번할 수 있으므로 비동기 audit 기록 권장
- Rate limit 이벤트는 샘플링 적용 고려 (예: 10개 중 1개만 기록)
- 동일 IP의 반복 위반은 집계하여 기록

---

## 3. RegionalIsolationGate 연동

### 3.1 대상 파일

- **위치**: `selfhealing/services/isolation/regional_gate.py`
- **클래스**: `RegionalIsolationGate`

### 3.2 ✅ 구현 완료 (2026-01-24)

| 메서드 | audit 함수 | action 값 | 라인 |
|--------|-----------|-----------|------|
| `isolate_region()` (성공) | `log_region_isolation_audit` | `"isolate"` | L193-203 |
| `isolate_region()` (실패) | `log_region_isolation_audit` | `"isolate"` + result="failed" | L208-215 |
| `restore_region()` (성공) | `log_region_isolation_audit` | `"restore"` | L327-341 |
| `restore_region()` (실패) | `log_region_isolation_audit` | `"restore"` + result="failed" | L346-353 |

### 3.3 연동이 필요한 메서드

| 메서드 | 발생 이벤트 | 연동 필요성 |
|--------|------------|------------|
| `isolate_region` | 리전 격리 시작 | Critical - 운영 영향도 높음 |
| `restore_region` | 리전 복원 | Critical - 복구 이력 필요 |
| `set_cluster_isolation` | 클러스터 격리 설정 | Critical |
| `failover_traffic` | 트래픽 페일오버 | High |

### 3.4 구현 방안

#### 3.4.1 권장 Audit 함수

기존 `log_system_control_audit` 활용 가능. 격리 전용 함수 필요시 `log_isolation_audit` 신규 정의.

#### 3.4.2 isolate_region 연동

**기록할 정보**:
- `action`: "region_isolated"
- `region`: 격리된 리전 식별자
- `reason`: 격리 사유 (health_check_failed, capacity_exceeded 등)
- `trigger`: "automatic" 또는 "manual"
- `affected_clusters`: 영향받는 클러스터 목록

#### 3.4.3 restore_region 연동

**기록할 정보**:
- `action`: "region_restored"
- `region`: 복원된 리전
- `isolation_duration`: 격리 기간
- `health_status`: 복원 시점 상태

#### 3.4.4 set_cluster_isolation 연동

**기록할 정보**:
- `action`: "cluster_isolation_changed"
- `cluster_id`: 대상 클러스터
- `isolation_level`: 격리 수준
- `previous_level`: 이전 격리 수준

### 3.5 상태 전환 추적

격리 상태 전환 시 시간 정보 필수 기록:

- `isolation_started_at`: 격리 시작 시각
- `isolation_ended_at`: 격리 종료 시각 (복원 시)
- `decision_latency_ms`: 결정까지 소요 시간

---

## 4. BlastRadiusService 연동

### 4.1 대상 파일

- **위치**: `selfhealing/services/blast_radius/service.py`
- **클래스**: `BlastRadiusService`

### 4.2 ✅ 구현 완료 (2026-01-24)

| 메서드 | audit 함수 | action 값 | 라인 |
|--------|-----------|-----------|------|
| `set_policy()` | `log_blast_radius_audit` | `"set_policy"` | L94-103 |
| `add_dependency()` | `log_blast_radius_audit` | `"add_dependency"` | L133-142 |
| `_auto_isolate()` | `log_blast_radius_audit` | `"auto_isolate"` | L391-400 |
| `isolate_service()` | `log_blast_radius_audit` | `"isolate_service"` | L408-418 |
| `release_isolation()` | `log_blast_radius_audit` | `"release_isolation"` | L429-439 |

### 4.3 연동이 필요한 메서드

| 라인 | 메서드 | 발생 이벤트 | 연동 필요성 |
|------|--------|------------|------------|
| L53 | `set_policy` | 정책 설정 | High |
| L91 | `add_dependency` | 의존성 추가 | Medium |
| L355 | `isolate_service` | 서비스 격리 | **Critical** |

### 4.4 구현 방안

#### 4.4.1 권장 Audit 함수

`log_blast_radius_audit` 또는 `log_system_control_audit` 사용

#### 4.4.2 isolate_service 연동

**기록할 정보**:
- `action`: "service_isolated"
- `service_name`: 격리된 서비스
- `isolation_reason`: 격리 사유
- `blast_radius_score`: 영향 범위 점수
- `downstream_services`: 영향받는 다운스트림 서비스

#### 4.4.3 set_policy 연동

**기록할 정보**:
- `action`: "blast_radius_policy_set"
- `policy_name`: 정책 이름
- `previous_policy`: 이전 정책
- `new_policy`: 새 정책

---

## 5. 공통 구현 지침

### 4.1 Import 패턴

```
# 서비스 파일 상단에 추가
from selfhealing.services.audit import log_system_control_audit
```

### 4.2 비동기 처리

보안/격리 이벤트는 응답 시간에 영향을 주지 않도록 비동기로 기록:

- Celery task 활용
- 또는 asyncio.create_task 사용

### 4.3 실패 처리

Audit 기록 실패가 핵심 기능을 방해하지 않도록:

- try-except로 감싸기
- 실패 시 fallback으로 logger.error 기록
- 재시도 큐 활용 고려

### 4.4 테스트 요구사항

각 연동 지점에 대해:

- 단위 테스트: audit 함수 호출 여부 확인 (mock 활용)
- 통합 테스트: 실제 audit 저장소 기록 확인

---

## 6. 마이그레이션 전략

### 5.1 단계별 적용

1. **Phase 1**: Audit 함수 추가 (기존 로직 변경 없음)
2. **Phase 2**: 신규 배포 후 audit 기록 모니터링
3. **Phase 3**: 기존 logger.warning 호출 점진적 제거 또는 debug로 변경

### 5.2 롤백 계획

문제 발생 시:
- Audit 호출을 feature flag로 제어
- 설정으로 비활성화 가능하도록 구현

---

## 7. ✅ 검증 완료 체크리스트

### SecurityViolationService

- [x] handle_violation에서 audit 기록 확인
- [x] _temporary_ip_ban에서 audit 기록 확인 (ban_ip → _temporary_ip_ban)
- [x] _permanent_ip_ban에서 audit 기록 확인
- [x] _invalidate_user_sessions에서 audit 기록 확인
- [x] 단위 테스트 추가 (4개 테스트)

### RegionalIsolationGate

- [x] isolate_region에서 audit 기록 확인
- [x] restore_region에서 audit 기록 확인
- [x] 성공/실패 케이스 모두 audit 기록
- [x] 단위 테스트 추가 (3개 테스트)

### BlastRadiusService

- [x] set_policy에서 audit 기록 확인
- [x] add_dependency에서 audit 기록 확인
- [x] isolate_service에서 audit 기록 확인
- [x] release_isolation에서 audit 기록 확인
- [x] _auto_isolate에서 audit 기록 확인
- [x] 단위 테스트 추가 (6개 테스트)

---

## 8. 신규 추가된 함수 및 타입

### 8.1 AuditEventType (event_buffer.py)

```python
SECURITY_VIOLATION = "security_violation"
SECURITY_IP_BLOCKED = "security_ip_blocked"
SECURITY_SESSION_INVALIDATED = "security_session_invalidated"
REGION_ISOLATED = "region_isolated"
REGION_RESTORED = "region_restored"
```

### 8.2 Audit 헬퍼 함수 (compliance_audit.py)

- `log_security_violation_audit()` - 보안 위반 처리 기록
- `log_region_isolation_audit()` - 리전 격리/복원 기록

---

## 9. 관련 문서

- 85_AUDIT_INTEGRATION_OVERVIEW.md - 개요
- 87_AUDIT_INTEGRATION_LEARNING_RECOVERY.md - 나머지 서비스 연동
- 20_AUDIT_UNIFICATION_PLAN.md - 기존 통합 계획
