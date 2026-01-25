# 94. 하드코딩된 설정값 리팩토링 개요

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: 계획
- **관련 문서**: 90-93 (Config Migration 시리즈)
- **대상 패키지**: `packages/selfhealing-python/src/selfhealing`

---

## 1. 개요

### 1.1 문제 정의
`packages/selfhealing-python` 내 일부 파일에서 설정값이 코드에 직접 하드코딩되어 있음.  
이미 `settings/` 폴더에 Pydantic Settings 기반 설정 시스템이 구축되어 있으나, 일부 모듈에서 이를 활용하지 않고 상수나 기본값을 직접 정의하고 있음.

### 1.2 목표
- 하드코딩된 설정값을 Pydantic Settings 체계로 통합
- 환경 변수 기반 설정 오버라이드 지원
- 설정값의 단일 진실 공급원(Single Source of Truth) 확립

### 1.3 범위
| 영역 | 파일 수 | 발견 항목 |
|-----|--------|----------|
| API 뷰 | 6개 파일 | ~15건 |
| 서비스/모델 | 12개 파일 | ~25건 |
| 태스크 | 8개 파일 | ~12건 |
| Audit 모듈 | 7개 파일 | ~10건 |
| 함수 파라미터 기본값 | 10개 파일 | ~15건 |
| **소계 (문서 95-99)** | **43개 파일** | **약 77건** |
| Core 모듈 | 5개 파일 | ~19건 |
| Error Budget 추가 | 2개 파일 | ~10건 |
| Namespace Emergency | 3개 파일 | ~6건 |
| Canary 추가 | 2개 파일 | ~4건 |
| SLO | 1개 파일 | ~4건 |
| Canary Watchdog Task | 1개 파일 | ~3건 |
| Throttle Adaptive | 1개 파일 | ~3건 |
| Chaos 추가 | 2개 파일 | ~14건 |
| Audit 추가 | 4개 파일 | ~21건 |
| 기타 (Permissions, Control API) | 2개 파일 | ~3건 |
| **소계 (문서 100 - 1차)** | **23개 파일** | **약 87건** |
| Utils (jitter) | 1개 파일 | ~8건 |
| Error Budget Gate | 1개 파일 | ~2건 |
| Critical Worker 추가 | 1개 파일 | ~8건 |
| Regional Gate | 1개 파일 | ~1건 |
| Idempotency Service | 1개 파일 | ~4건 |
| Forensic Audit Bridge | 1개 파일 | ~4건 |
| FinOps | 1개 파일 | ~1건 |
| **소계 (문서 100 - 2차)** | **7개 파일** | **약 28건** |
| Ring Buffer | 1개 파일 | ~2건 |
| Sampling | 1개 파일 | ~4건 |
| Retry Handler | 1개 파일 | ~4건 |
| Chaos Blast Radius 추가 | 1개 파일 | ~8건 |
| Chaos Base Models | 1개 파일 | ~5건 |
| Governance Checks | 1개 파일 | ~4건 |
| Load Shedding | 1개 파일 | ~1건 |
| Self Audit | 1개 파일 | ~1건 |
| Traffic Aware Replay | 1개 파일 | ~1건 |
| **소계 (문서 100 - 3차)** | **9개 파일** | **약 30건** |
| Graceful Degradation | 1개 파일 | ~9건 |
| Cleanup Tasks | 1개 파일 | ~4건 |
| Recovery Tasks 추가 | 1개 파일 | ~2건 |
| **소계 (문서 100 - 4차)** | **3개 파일** | **약 15건** |
| Canary Locking | 1개 파일 | ~1건 |
| Drift Detection | 1개 파일 | ~1건 |
| Cache Adapters | 7개 파일 | ~7건 |
| **소계 (문서 100 - 5차)** | **9개 파일** | **약 9건** |
| **총계** | **94개 파일** | **약 246건** |

---

## 2. 문서 구성

| 문서 번호 | 제목 | 내용 |
|----------|------|------|
| 94 | 개요 (본 문서) | 문제 정의, 목표, 전체 구현 순서 |
| 95 | API 뷰 설정 외부화 | stress_views, canary, auto_tuning 등 |
| 96 | 서비스 계층 설정 외부화 | dlq_models, corruption_shield, recovery_circuit_breaker 등 |
| 97 | 태스크 설정 외부화 | cleanup_tasks, intelligence_tasks, notification_policy 등 |
| 98 | Audit 모듈 설정 외부화 | resilient_recorder, cascade_config, wal 등 |
| 99 | 함수 파라미터 기본값 통합 | cleanup_service, pending_config, dashboard_service 등 |
| **100** | **추가 발견 설정 외부화** | core/, error_budget/, namespace_emergency/, chaos/, slo.py 등 |

---

## 3. 전체 구현 순서

### Phase 1: 신규 Settings 모듈 생성 (우선순위: 높음)
1. `settings/stress_test.py` 생성 - API 스트레스 테스트 설정
2. `settings/cleanup.py` 생성 - 클린업 작업 설정
3. `settings/precomputed_cache.py` 생성 - 캐시 TTL 설정

### Phase 2: 기존 Settings 확장 (우선순위: 높음)
4. `settings/dlq.py` 확장 - dlq_models.py 기본값 통합
5. `settings/corruption_shield.py` 확장 - config.py 기본값 통합
6. `settings/recovery_circuit_breaker.py` 확장 - 누락된 설정 추가

### Phase 3: 서비스 계층 리팩토링 (우선순위: 중간)
7. `services/dlq_models.py` - Settings 연동
8. `services/corruption_shield/config.py` - Settings 연동
9. `services/coordination/recovery_circuit_breaker.py` - Settings 연동
10. `services/coordination/redis_key_guard.py` - Settings 연동
11. `services/security/models.py` - Settings 연동

### Phase 4: API 뷰 리팩토링 (우선순위: 중간)
12. `api/django/stress_views.py` - Settings 연동
13. `api/django/views/canary.py` - Settings 연동
14. `api/django/views/auto_tuning.py` - Settings 연동
15. `api/django/views/cascade.py` - Settings 연동
16. `api/django/views/xtest/observability.py` - Settings 연동

### Phase 5: 태스크 리팩토링 (우선순위: 중간)
17. `tasks/cleanup_tasks.py` - Settings 연동
18. `tasks/intelligence_tasks.py` - Settings 연동
19. `tasks/notification_policy.py` - Settings 연동
20. `services/coordination/recovery_tasks.py` - Settings 연동

### Phase 6: Audit 모듈 리팩토링 (우선순위: 낮음)
21. `audit/resilient_recorder.py` - Settings 연동
22. `audit/cascade_config.py` - Settings 연동
23. `audit/wal.py` - Settings 연동
24. `audit/integrity/sequence.py` - Settings 연동

### Phase 7: 기타 서비스 리팩토링 (우선순위: 낮음)
25. `services/cleanup_service.py` - Settings 연동
26. `services/pending_config.py` - Settings 연동
27. `services/dashboard_service.py` - Settings 연동
28. `services/circuit_breaker/manual_control.py` - Settings 연동
29. `adapters/memory/layered_repository.py` - Settings 연동

### Phase 8: 검증 및 테스트 - 1차 (문서 95-99)
30. 기존 테스트 통과 확인
31. 환경 변수 오버라이드 테스트 추가
32. 문서 업데이트

---

## 추가 발견 항목 (문서 100 기반)

### Phase 9: Core 모듈 리팩토링 (우선순위: 높음)
33. `settings/backoff.py` 생성 - Backoff 전략 설정
34. `core/backoff.py` - Settings 연동
35. `settings/pool_monitor.py` 생성 - Pool 모니터링 설정
36. `core/pool_monitor.py` - Settings 연동
37. `core/connection_health.py` - Settings 연동
38. `core/decision_engine.py` - Settings 연동
39. `core/apply_strategy.py` - Settings 연동

### Phase 10: Error Budget 확장 리팩토링 (우선순위: 중간)
40. `settings/error_budget_propagation.py` 확장 - 전파/위기 설정 추가
41. `services/error_budget/constants.py` - Settings 연동
42. `services/error_budget/backfill.py` - Settings 연동

### Phase 11: Namespace Emergency 리팩토링 (우선순위: 중간)
43. `settings/namespace_emergency.py` 생성 - 비상 관리 설정
44. `services/namespace_emergency/cascade_detector.py` - Settings 연동
45. `services/namespace_emergency/tracker.py` - Settings 연동
46. `services/namespace_emergency/escalation_audit.py` - Settings 연동

### Phase 12: Canary/SLO/Watchdog 리팩토링 (우선순위: 중간)
47. `settings/canary.py` 생성 - Canary 배포 설정
48. `services/canary/service.py`, `cross_cluster.py` - Settings 연동
49. `settings/slo.py` 확장 - Burn Rate 설정 추가
50. `slo.py` - Settings 연동
51. `settings/canary_watchdog.py` 생성 - Watchdog 설정
52. `tasks/canary_watchdog.py` - Settings 연동

### Phase 13: Chaos/Throttle 리팩토링 (우선순위: 낮음)
53. `settings/throttle.py` 확장 - Adaptive Throttle 설정 추가
54. `services/throttle/adaptive.py` - Settings 연동
55. `settings/chaos.py` 확장 - Traffic Shaper 설정 추가
56. `services/chaos/traffic_shaper.py` - Settings 연동
57. `settings/chaos_safety_caps.py` 생성 - Chaos 안전 캡 설정
58. `services/chaos/constants.py` - Settings 연동

### Phase 14: Audit 모듈 추가 리팩토링 (우선순위: 낮음)
59. `settings/audit_reconciler.py` 생성 - Reconciler 설정
60. `audit/reconciler.py` - Settings 연동 (from_env → from_settings 전환)
61. `audit/sync_worker.py` - Settings 연동 (from_env → from_settings 전환)
62. `audit/audit_integration.py` - Settings 연동
63. `audit/audit_watchdog.py` - Settings 연동 (from_env → from_settings 전환)

### Phase 15: 기타 파일 리팩토링 (우선순위: 낮음)
64. `api/django/permissions.py` - Settings 연동 (EMERGENCY_EXPIRY_HOURS)
65. `services/control_api_service.py` - Settings 연동 (timedelta 하드코딩)

### Phase 16: 최종 검증 및 테스트 (우선순위: 높음)
66. 전체 테스트 통과 확인
67. 환경 변수 오버라이드 통합 테스트
68. 전체 문서 최종 업데이트

---

## 추가 발견 항목 - 2차 (문서 100 확장)

### Phase 17: Utils 모듈 리팩토링 (우선순위: 중간)
69. `settings/jitter.py` 생성 - Jitter 설정
70. `utils/jitter.py` - Settings 연동

### Phase 18: 추가 서비스 리팩토링 (우선순위: 낮음)
71. `settings/gate_fault.py` 생성 - Gate Fault Detector 설정
72. `services/error_budget_gate/fault_detector.py` - Settings 연동
73. `settings/critical_worker.py` 확장 - Worker Queue 설정 추가
74. `services/coordination/critical_worker.py` - Settings 연동
75. `settings/regional_gate.py` 생성 - Regional Gate 설정
76. `services/isolation/regional_gate.py` - Settings 연동

### Phase 19: Anti-Flapping/Forensic 리팩토링 (우선순위: 낮음)
77. `settings/anti_flapping.py` 확장 - AntiFlappingWindow 설정 추가
78. `services/idempotency_service.py` - Settings 연동
79. `settings/forensic.py` 생성 - Forensic Rate Limiter 설정
80. `services/forensic_audit_bridge.py` - Settings 연동

### Phase 20: FinOps 리팩토링 (우선순위: 낮음)
81. `settings/finops.py` 생성 - FinOps 설정
82. `services/finops/service.py` - Settings 연동

### Phase 21: 최종 검증 (우선순위: 높음)
83. 전체 테스트 재실행
84. 환경 변수 문서 최종 업데이트

---

## 추가 발견 항목 - 3차 (문서 100 확장)

### Phase 22: Audit 추가 모듈 리팩토링 (우선순위: 낮음)
85. `settings/ring_buffer.py` 생성 - Ring Buffer 설정
86. `audit/ring_buffer.py` - Settings 연동
87. `settings/sampling.py` 생성 - Sampling 설정
88. `audit/performance/sampling.py` - Settings 연동

### Phase 23: Chaos 추가 모듈 리팩토링 (우선순위: 낮음)
89. `settings/chaos_blast_radius.py` 확장 - 시간/트래픽 제한 추가
90. `services/chaos/blast_radius.py` - Settings 연동
91. `settings/steady_state.py` 생성 - Steady State 설정
92. `services/chaos/base/models.py` - Settings 연동

### Phase 24: 핵심 서비스 리팩토링 (우선순위: 중간)
93. `settings/retry.py` 확장 - RetryConfig 설정 추가
94. `services/retry_handler.py` - Settings 연동
95. `settings/governance.py` 생성 - Governance 설정
96. `services/governance_checks.py` - Settings 연동

### Phase 25: 기타 모듈 리팩토링 (우선순위: 낮음)
97. `settings/self_audit.py` 생성 - Self Audit 설정
98. `audit/self_audit.py` - Settings 연동
99. `audit/cascade_load_shedding.py` - Settings 연동
100. `tasks/traffic_aware_replay.py` - Settings 연동

### Phase 26: 최종 종합 검증 (우선순위: 높음)
101. 전체 테스트 실행
102. 환경 변수 통합 문서 작성
103. 설정 마이그레이션 가이드 작성

### Phase 27: 4차 발견 모듈 리팩토링 (우선순위: 낮음)
104. `settings/graceful_degradation.py` 생성 - Fallback/CircuitBreaker 설정
105. `audit/graceful_degradation/enums.py` - Settings 연동
106. `settings/cleanup.py` 확장 - Cleanup Task 설정 추가
107. `tasks/cleanup_tasks.py` - Settings 연동
108. `settings/recovery_tasks.py` 확장 - Recovery Task 설정 추가
109. `services/coordination/recovery_tasks.py` - Settings 연동

### Phase 28: 최종 검증 (우선순위: 높음)
110. 전체 테스트 재실행
111. 환경 변수 문서 최종 업데이트

### Phase 29: 5차 발견 모듈 리팩토링 (우선순위: 낮음)
112. `settings/canary_locking.py` 생성 - Canary Lock 설정
113. `services/canary/locking.py` - Settings 연동
114. `settings/drift_detection.py` 생성 - Drift Detection 설정
115. `tasks/drift_detection.py` - Settings 연동
116. `settings/cache_adapter.py` 생성 - Cache Adapter 공통 설정
117. `interfaces/cache_provider.py` - Settings 연동
118. `adapters/cache/*.py` - Settings 연동

### Phase 30: 최종 종합 검증 (우선순위: 높음)
119. 전체 테스트 최종 실행
120. 설정 문서 완성

---

## 4. 리팩토링 패턴

### 4.1 Before (하드코딩)
```
# 패턴 1: 함수 파라미터 기본값
def cleanup_expired(self, max_age_hours: int = 24) -> int:

# 패턴 2: dict.get() 기본값
lock_timeout_ms = int(body.get("lock_timeout_ms", 1))

# 패턴 3: 모듈 레벨 상수
DEFAULT_TRIGGER_CHECK_INTERVAL = 60

# 패턴 4: dataclass 필드 기본값
retention_days: int = 30
```

### 4.2 After (Settings 사용)
```
# 패턴 1: Settings에서 기본값 가져오기
settings = get_cleanup_settings()
def cleanup_expired(self, max_age_hours: int = None) -> int:
    max_age_hours = max_age_hours or settings.default_max_age_hours

# 패턴 2: Settings 기본값 활용
settings = get_stress_test_settings()
lock_timeout_ms = int(body.get("lock_timeout_ms", settings.default_lock_timeout_ms))

# 패턴 3: Settings 참조로 대체
from selfhealing.settings import get_recovery_task_settings
DEFAULT_TRIGGER_CHECK_INTERVAL = get_recovery_task_settings().trigger_check_interval

# 패턴 4: Settings 연동 factory 사용
@classmethod
def from_settings(cls, settings=None, **overrides):
    settings = settings or get_dlq_settings()
    return cls(retention_days=settings.retention_days, ...)
```

---

## 5. 주의사항

### 5.1 하위 호환성
- 기존 함수 시그니처는 유지 (선택적 파라미터로 전환)
- 기존 기본값과 동일한 값으로 Settings 기본값 설정
- 기존 테스트가 모두 통과해야 함

### 5.2 순환 참조 방지
- Settings 모듈은 다른 모듈에 의존하지 않아야 함
- 지연 임포트 패턴 사용 권장

### 5.3 환경 변수 네이밍 규칙
- 접두사: `SELFHEALING_`
- 모듈명: `STRESS_TEST_`, `CLEANUP_`, `CACHE_` 등
- 설정명: `DEFAULT_LOCK_TIMEOUT_MS`, `MAX_AGE_HOURS` 등
- 예: `SELFHEALING_STRESS_TEST_DEFAULT_LOCK_TIMEOUT_MS`

---

## 6. 성공 기준

1. **하드코딩 제거**: 식별된 **246건**의 하드코딩이 Settings로 이전
   - 1차 (문서 95-99): 약 77건
   - 2차 (문서 100 - 1차): 약 87건
   - 3차 (문서 100 - 2차): 약 28건
   - 4차 (문서 100 - 3차): 약 30건
   - 5차 (문서 100 - 4차): 약 15건
   - 6차 (문서 100 - 5차): 약 9건
2. **테스트 통과**: 기존 테스트 100% 통과
3. **환경 변수 지원**: 모든 설정이 환경 변수로 오버라이드 가능
4. **문서화**: 새로운 환경 변수에 대한 문서 업데이트

---

## 7. 예상 총 소요 시간

| 문서 | 범위 | 예상 소요 |
|------|------|----------|
| 95 | API 뷰 | ~4시간 |
| 96 | 서비스 계층 | ~6시간 |
| 97 | 태스크 | ~6.5시간 |
| 98 | Audit 모듈 | ~7시간 |
| 99 | 함수 파라미터 | ~5.5시간 |
| 100 | 추가 발견 항목 (1차+2차+3차+4차+5차) | ~17시간 |
| **총계** | | **약 46시간** |
