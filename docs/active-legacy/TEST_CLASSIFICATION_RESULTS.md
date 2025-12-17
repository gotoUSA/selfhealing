# Test Classification Results
**Based on TEST_CLASSIFICATION_FRAMEWORK.md v1.0**

> **Generated**: 2025-12-17
> **Total Files Analyzed**: 252+ test files
> **Framework Reference**: [TEST_CLASSIFICATION_FRAMEWORK.md](TEST_CLASSIFICATION_FRAMEWORK.md)

---

## Summary Statistics

| Classification | Count | Percentage |
|----------------|-------|------------|
| PURE_HEALING | 52 | ~21% |
| UNIT_HEALING | 28 | ~11% |
| HYBRID | 15 | ~6% |
| CHAOS_NOT_HEALING | 8 | ~3% |
| OUT_OF_SCOPE | 145+ | ~59% |
| AMBIGUOUS | 4 | ~2% |

---

## Classification Table

### 1. PURE_HEALING Tests

> B1=YES, B2=YES, B3=YES, B4=NO per framework decision matrix

| file_path | B1 | B2 | B3 | B4 | C_AXIS | G_AXIS | F_AXIS | R_AXIS | S_AXIS | E_AXIS | notes |
|-----------|----|----|----|----|--------|--------|--------|--------|--------|--------|-------|
| load_tests/scenarios/stage10_self_healing.py | YES | YES | YES | NO | C5-CONTROL-API | G4-LOAD | F1-TRANSIENT | R2-MANUAL | S2-ADVANCED | E2-DOCKER | Control API 성능 및 기능 검증 |
| load_tests/scenarios/stage11_ramp_threshold.py | YES | YES | YES | NO | C1-CIRCUIT-BREAKER, C2-DLQ | G4-LOAD | F4-RESOURCE | R1-AUTO | S2-ADVANCED | E2-DOCKER | CB/DLQ 트리거 포인트 발견 |
| load_tests/scenarios/stage12_spike_recovery.py | YES | YES | YES | NO | C1-CIRCUIT-BREAKER | G4-LOAD | F1-TRANSIENT | R1-AUTO | S2-ADVANCED | E2-DOCKER | 스파이크 후 CB 자동 복구 검증 |
| load_tests/scenarios/stage13_repeated_spike.py | YES | YES | YES | NO | C3-RETRY-BACKOFF | G4-LOAD | F1-TRANSIENT | R1-AUTO | S2-ADVANCED | E2-DOCKER | 반복 스파이크 시 백오프 누적 방지 |
| load_tests/scenarios/stage14_dlq_replay.py | YES | YES | YES | NO | C2-DLQ | G4-LOAD | F2-PERSISTENT | R5-REPLAY | S2-ADVANCED | E2-DOCKER | DLQ 저장/재처리 정확성 검증 |
| load_tests/scenarios/stage15_cb_transitions.py | YES | YES | YES | NO | C1-CIRCUIT-BREAKER | G4-LOAD | F1-TRANSIENT | R1-AUTO | S2-ADVANCED | E2-DOCKER | CB 상태 전이 (CLOSED→OPEN→HALF_OPEN→CLOSED) |
| load_tests/scenarios/stage16_db_lock_recovery.py | YES | YES | YES | NO | C3-RETRY-BACKOFF | G4-LOAD | F6-TIMING | R1-AUTO | S2-ADVANCED | E2-DOCKER | DB Lock 타임아웃 → 재시도 → 복구 |
| load_tests/scenarios/stage16_db_lock_recovery_locust.py | YES | YES | YES | NO | C3-RETRY-BACKOFF | G4-LOAD | F6-TIMING | R1-AUTO | S2-ADVANCED | E2-DOCKER | Locust 버전 |
| load_tests/scenarios/stage17_cache_ttl_race.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F5-DATA | R3-GRACEFUL | S2-ADVANCED | E2-DOCKER | 캐시 TTL 레이스 → 폴백 검증 |
| load_tests/scenarios/stage17_cache_ttl_race_locust.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F5-DATA | R3-GRACEFUL | S2-ADVANCED | E2-DOCKER | Locust 버전 |
| load_tests/scenarios/stage17_event_invalidation.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F5-DATA | R3-GRACEFUL | S2-ADVANCED | E2-DOCKER | 이벤트 무효화 처리 |
| load_tests/scenarios/stage17_event_invalidation_locust.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F5-DATA | R3-GRACEFUL | S2-ADVANCED | E2-DOCKER | Locust 버전 |
| load_tests/scenarios/stage18_chain_failure.py | YES | YES | YES | NO | C7-MULTI | G4-LOAD | F3-CASCADING | R1-AUTO | S2-ADVANCED | E2-DOCKER | 체인 장애 시 롤백 동작 검증 |
| load_tests/scenarios/stage19_rollback_failure.py | YES | YES | YES | NO | C2-DLQ | G4-LOAD | F3-CASCADING | R5-REPLAY | S2-ADVANCED | E2-DOCKER | 롤백 실패 시 DLQ 에스컬레이션 |
| load_tests/scenarios/stage20_delayed_webhook.py | YES | YES | YES | NO | C3-RETRY-BACKOFF | G4-LOAD | F6-TIMING | R1-AUTO | S3-EXTREME | E2-DOCKER | 지연 웹훅 처리 및 상태 기계 |
| load_tests/scenarios/stage20_delayed_webhook_locust.py | YES | YES | YES | NO | C3-RETRY-BACKOFF | G4-LOAD | F6-TIMING | R1-AUTO | S3-EXTREME | E2-DOCKER | Locust 버전 |
| load_tests/scenarios/stage21_false_positive.py | YES | YES | YES | NO | C1-CIRCUIT-BREAKER | G4-LOAD | F1-TRANSIENT | R1-AUTO | S3-EXTREME | E2-DOCKER | CB 오탐지 방지 검증 |
| load_tests/scenarios/stage22_rate_limit_conflict.py | YES | YES | YES | NO | C3-RETRY-BACKOFF, C1-CIRCUIT-BREAKER | G4-LOAD | F4-RESOURCE | R1-AUTO | S3-EXTREME | E2-DOCKER | 재시도 + Rate Limit 충돌 방지 |
| load_tests/scenarios/stage23_clock_skew.py | YES | YES | YES | NO | C3-RETRY-BACKOFF | G4-LOAD | F6-TIMING | R1-AUTO | S3-EXTREME | E2-DOCKER | 클럭 스큐 시 멱등성 유지 |
| load_tests/scenarios/stage24_cache_dead_protection.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F2-PERSISTENT | R3-GRACEFUL | S3-EXTREME | E2-DOCKER | 캐시 죽음 시 서비스 유지 |
| load_tests/scenarios/stage24_partial_partition.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F2-PERSISTENT | R3-GRACEFUL | S3-EXTREME | E2-DOCKER | 부분 네트워크 파티션 폴백 |
| load_tests/scenarios/stage25_tls_failure.py | YES | YES | YES | NO | C7-MULTI | G4-LOAD | F2-PERSISTENT | R2-MANUAL | S3-EXTREME | E2-DOCKER | TLS 실패 → 수동 개입 필요 |
| load_tests/scenarios/stage26_connection_pool.py | YES | YES | YES | NO | C1-CIRCUIT-BREAKER | G4-LOAD | F4-RESOURCE | R1-AUTO | S3-EXTREME | E2-DOCKER | 커넥션 풀 고갈 → CB 동작 |
| load_tests/scenarios/stage26_extreme_pool_test.py | YES | YES | YES | NO | C1-CIRCUIT-BREAKER | G4-LOAD | F4-RESOURCE | R1-AUTO | S3-EXTREME | E2-DOCKER | 극한 풀 고갈 테스트 |
| load_tests/scenarios/stage27_graceful_shutdown.py | YES | YES | YES | NO | C7-MULTI | G4-LOAD | F2-PERSISTENT | R4-FAILOVER | S3-EXTREME | E2-DOCKER | Graceful Shutdown 시 요청 완료 |
| load_tests/scenarios/stage28_multi_region.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F2-PERSISTENT | R4-FAILOVER | S3-EXTREME | E5-MULTI-REGION | 멀티 리전 페일오버 |
| load_tests/scenarios/stage29_backpressure.py | YES | YES | YES | NO | C2-DLQ | G4-LOAD | F4-RESOURCE | R5-REPLAY | S3-EXTREME | E2-DOCKER | 백프레셔 + DLQ 처리 |
| load_tests/scenarios/stage29_bulk_dlq_replay.py | YES | YES | YES | NO | C2-DLQ | G4-LOAD | F4-RESOURCE | R5-REPLAY | S3-EXTREME | E2-DOCKER | 대량 DLQ 재처리 |
| load_tests/scenarios/stage30_schedule_drift.py | YES | YES | YES | NO | C3-RETRY-BACKOFF | G4-LOAD | F6-TIMING | R1-AUTO | S4-CHAOS-PROD | E2-DOCKER | 스케줄러 드리프트 복원력 |
| load_tests/scenarios/stage31_cascade_extended.py | YES | YES | YES | NO | C7-MULTI | G4-LOAD | F3-CASCADING | R1-AUTO | S4-CHAOS-PROD | E2-DOCKER | 확장된 연쇄 장애 시나리오 |
| load_tests/scenarios/stage32_retry_storm_extended.py | YES | YES | YES | NO | C3-RETRY-BACKOFF, C1-CIRCUIT-BREAKER | G4-LOAD | F4-RESOURCE | R1-AUTO | S4-CHAOS-PROD | E2-DOCKER | 재시도 폭풍 방지 검증 |
| load_tests/scenarios/stage33_jwt_cascade.py | YES | YES | YES | NO | C1-CIRCUIT-BREAKER | G4-LOAD | F3-CASCADING | R1-AUTO | S4-CHAOS-PROD | E2-DOCKER | JWT 연쇄 실패 CB 동작 |
| load_tests/scenarios/stage34_db_deadlock.py | YES | YES | YES | NO | C3-RETRY-BACKOFF | G4-LOAD | F6-TIMING | R1-AUTO | S4-CHAOS-PROD | E2-DOCKER | DB 데드락 탐지/해소 |
| load_tests/scenarios/stage35_cache_stampede.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F5-DATA | R3-GRACEFUL | S4-CHAOS-PROD | E2-DOCKER | 캐시 스탬피드 방지 |
| load_tests/scenarios/stage35_cache_poison.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F5-DATA | R3-GRACEFUL | S4-CHAOS-PROD | E2-DOCKER | 캐시 오염 탐지/복구 |
| load_tests/scenarios/stage35_cache_poison_http.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F5-DATA | R3-GRACEFUL | S4-CHAOS-PROD | E2-DOCKER | HTTP 버전 |
| load_tests/scenarios/stage35_redis_stampede.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F5-DATA | R3-GRACEFUL | S4-CHAOS-PROD | E2-DOCKER | Redis 스탬피드 |
| load_tests/scenarios/stage36_memory_pressure.py | YES | YES | YES | NO | C7-MULTI | G4-LOAD | F4-RESOURCE | R3-GRACEFUL | S4-CHAOS-PROD | E2-DOCKER | 메모리 압박 시 그레이스풀 동작 |
| load_tests/scenarios/stage37_schema_compat.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F5-DATA | R3-GRACEFUL | S4-CHAOS-PROD | E2-DOCKER | 스키마 불일치 폴백 |
| load_tests/scenarios/stage37_schema_compat_http.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F5-DATA | R3-GRACEFUL | S4-CHAOS-PROD | E2-DOCKER | HTTP 버전 |
| load_tests/scenarios/stage38_multi_region_failover.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F2-PERSISTENT | R4-FAILOVER | S4-CHAOS-PROD | E5-MULTI-REGION | 멀티 리전 페일오버 확장 |
| load_tests/scenarios/stage38_multi_region_failover_locust.py | YES | YES | YES | NO | C4-FALLBACK | G4-LOAD | F2-PERSISTENT | R4-FAILOVER | S4-CHAOS-PROD | E5-MULTI-REGION | Locust 버전 |
| load_tests/scenarios/stage40_idempotency_clock_skew.py | YES | YES | YES | NO | C3-RETRY-BACKOFF | G3-E2E | F6-TIMING | R1-AUTO | S5-GOVERNANCE | E1-LOCAL | 클럭 스큐 시 멱등성 거버넌스 |
| load_tests/scenarios/stage41_tls_certificate_expiry.py | YES | YES | YES | NO | C5-CONTROL-API | G3-E2E | F2-PERSISTENT | R2-MANUAL | S5-GOVERNANCE | E1-LOCAL | TLS 만료 → 수동 개입 검증 |
| load_tests/scenarios/stage41_tls_certificate_expiry_fleet.py | YES | YES | YES | NO | C5-CONTROL-API | G3-E2E | F2-PERSISTENT | R2-MANUAL | S5-GOVERNANCE | E1-LOCAL | Fleet 버전 |
| load_tests/scenarios/stage42_compound_failure_chaos.py | YES | YES | YES | NO | C7-MULTI | G5-CHAOS | F3-CASCADING | R1-AUTO | S5-GOVERNANCE | E2-DOCKER | 복합 장애 카오스 |
| load_tests/scenarios/stage42_compound_failure_chaos_locust.py | YES | YES | YES | NO | C7-MULTI | G5-CHAOS | F3-CASCADING | R1-AUTO | S5-GOVERNANCE | E2-DOCKER | Locust 버전 |
| load_tests/scenarios/stage42_compound_failure_deterministic.py | YES | YES | YES | NO | C7-MULTI | G3-E2E | F3-CASCADING | R1-AUTO | S5-GOVERNANCE | E1-LOCAL | 결정론적 버전 |
| load_tests/scenarios/stage43_operator_race_chaos.py | YES | YES | YES | NO | C5-CONTROL-API | G5-CHAOS | F6-TIMING | R2-MANUAL | S5-GOVERNANCE | E2-DOCKER | 운영자 레이스 컨디션 |
| load_tests/scenarios/stage43_operator_race_deterministic.py | YES | YES | YES | NO | C5-CONTROL-API | G3-E2E | F6-TIMING | R2-MANUAL | S5-GOVERNANCE | E1-LOCAL | 결정론적 버전 |
| load_tests/scenarios/stage44_zero_downtime_migration.py | YES | YES | YES | NO | C7-MULTI | G3-E2E | F1-TRANSIENT | R1-AUTO | S5-GOVERNANCE | E1-LOCAL | 무중단 마이그레이션 검증 |
| load_tests/scenarios/stage45_external_trust_audit.py | YES | YES | YES | NO | C6-OBSERVABILITY | G3-E2E | UNKNOWN | R1-AUTO | S5-GOVERNANCE | E1-LOCAL | 외부 감사 준비성 검증 |
| load_tests/scenarios/stage45_external_trust_reports.py | YES | YES | YES | NO | C6-OBSERVABILITY | G3-E2E | UNKNOWN | R1-AUTO | S5-GOVERNANCE | E1-LOCAL | 외부 신뢰 리포트 |

---

### 2. UNIT_HEALING Tests

> B1=YES, B2=NO, B3=YES, B4=NO per framework decision matrix

| file_path | B1 | B2 | B3 | B4 | C_AXIS | G_AXIS | F_AXIS | R_AXIS | S_AXIS | E_AXIS | notes |
|-----------|----|----|----|----|--------|--------|--------|--------|--------|--------|-------|
| tests/_unclassified/test_backoff_policy.py | YES | NO | YES | NO | C3-RETRY-BACKOFF | G1-UNIT | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | 백오프 계산 로직 단위 테스트 |
| tests/_unclassified/test_backoff_jitter_distribution.py | YES | NO | YES | NO | C3-RETRY-BACKOFF | G1-UNIT | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | 지터 분포 검증 |
| tests/_unclassified/test_circuit_breaker.py | YES | NO | YES | NO | C1-CIRCUIT-BREAKER | G2-INTEGRATION | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | CB 상태 전이 통합 테스트 |
| tests/_unclassified/test_circuit_breaker_service.py | YES | NO | YES | NO | C1-CIRCUIT-BREAKER | G1-UNIT | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | CB 서비스 단위 테스트 |
| tests/_unclassified/test_circuit_breaker_ttl.py | YES | NO | YES | NO | C1-CIRCUIT-BREAKER | G1-UNIT | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | CB TTL 검증 |
| tests/_unclassified/test_circuit_breaker_distributed.py | YES | NO | YES | NO | C1-CIRCUIT-BREAKER | G2-INTEGRATION | UNKNOWN | R1-AUTO | UNKNOWN | E2-DOCKER | 분산 CB 테스트 |
| tests/_unclassified/test_control_api.py | YES | NO | YES | NO | C5-CONTROL-API | G2-INTEGRATION | UNKNOWN | R2-MANUAL | UNKNOWN | E1-LOCAL | Control API 기능 테스트 |
| tests/_unclassified/test_dlq_storage_and_replay.py | YES | NO | YES | NO | C2-DLQ | G2-INTEGRATION | UNKNOWN | R5-REPLAY | UNKNOWN | E1-LOCAL | DLQ 저장/재처리 통합 |
| tests/_unclassified/test_dlq_retention.py | YES | NO | YES | NO | C2-DLQ | G1-UNIT | UNKNOWN | R5-REPLAY | UNKNOWN | E1-LOCAL | DLQ 보관 정책 |
| tests/_unclassified/test_retry_configuration.py | YES | NO | YES | NO | C3-RETRY-BACKOFF | G1-UNIT | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | 재시도 설정 검증 |
| tests/_unclassified/test_retry_persistence.py | YES | NO | YES | NO | C3-RETRY-BACKOFF | G1-UNIT | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | 재시도 상태 영속화 |
| tests/_unclassified/test_retry_decision_table.py | YES | NO | YES | NO | C3-RETRY-BACKOFF | G1-UNIT | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | 재시도 결정 테이블 |
| tests/_unclassified/test_l3_self_healing.py | YES | NO | YES | NO | C7-MULTI | G2-INTEGRATION | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | L3 Self-Healing 통합 |
| tests/_unclassified/test_self_healing_policy.py | YES | NO | YES | NO | C7-MULTI | G1-UNIT | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | 힐링 정책 검증 |
| tests/_unclassified/test_self_healing_metrics.py | YES | NO | YES | NO | C6-OBSERVABILITY | G1-UNIT | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | 힐링 메트릭 검증 |
| tests/_unclassified/test_manual_override_policy.py | YES | NO | YES | NO | C5-CONTROL-API | G1-UNIT | UNKNOWN | R2-MANUAL | UNKNOWN | E1-LOCAL | 수동 오버라이드 정책 |
| tests/_unclassified/test_failure_classification.py | YES | NO | YES | NO | C7-MULTI | G1-UNIT | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | 장애 분류 로직 |
| tests/_unclassified/test_failure_recovery_cycle.py | YES | YES | YES | NO | C7-MULTI | G3-E2E | F1-TRANSIENT | R1-AUTO, R5-REPLAY | UNKNOWN | E1-LOCAL | 장애→복구 전체 사이클 E2E |
| tests/_unclassified/test_observability_metrics.py | YES | NO | YES | NO | C6-OBSERVABILITY | G2-INTEGRATION | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | 관측성 메트릭 통합 |
| tests/_unclassified/test_observability_tasks.py | YES | NO | YES | NO | C6-OBSERVABILITY | G1-UNIT | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | 관측성 태스크 |
| tests/_unclassified/test_metrics_dlq_pending.py | YES | NO | YES | NO | C2-DLQ, C6-OBSERVABILITY | G1-UNIT | UNKNOWN | R5-REPLAY | UNKNOWN | E1-LOCAL | DLQ 대기 메트릭 |
| tests/_unclassified/test_sla_timer_policy.py | YES | NO | YES | NO | C7-MULTI | G1-UNIT | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | SLA 타이머 정책 |
| tests/_unclassified/test_forensic_replay.py | YES | NO | YES | NO | C2-DLQ | G2-INTEGRATION | UNKNOWN | R5-REPLAY | UNKNOWN | E1-LOCAL | 포렌식 재생 |
| tests/_unclassified/test_redis_fallback.py | YES | NO | YES | NO | C4-FALLBACK | G2-INTEGRATION | F2-PERSISTENT | R3-GRACEFUL | UNKNOWN | E1-LOCAL | Redis 폴백 검증 |
| tests/_unclassified/test_db_connection_recovery.py | YES | NO | YES | NO | C4-FALLBACK | G2-INTEGRATION | F2-PERSISTENT | R1-AUTO | UNKNOWN | E1-LOCAL | DB 연결 복구 |
| tests/_unclassified/test_cold_start_recovery.py | YES | NO | YES | NO | C7-MULTI | G2-INTEGRATION | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | 콜드 스타트 복구 |
| tests/_unclassified/test_cost_aware_recovery.py | YES | NO | YES | NO | C7-MULTI | G1-UNIT | UNKNOWN | R1-AUTO | UNKNOWN | E1-LOCAL | 비용 인식 복구 정책 |
| tests/e2e/test_stage26_circuit_breaker.py | YES | YES | YES | NO | C1-CIRCUIT-BREAKER | G3-E2E | F4-RESOURCE | R1-AUTO | S3-EXTREME | E2-DOCKER | Stage 26 CB E2E |
| tests/e2e/test_pool_recovery.py | YES | YES | YES | NO | C1-CIRCUIT-BREAKER | G3-E2E | F4-RESOURCE | R1-AUTO | S3-EXTREME | E2-DOCKER | 풀 고갈→회복 E2E |

---

### 3. HYBRID Tests

> B1=PARTIAL, B4=YES per framework (힐링은 수단, 비즈니스 검증이 목적)

| file_path | B1 | B2 | B3 | B4 | exclusion_reason | notes |
|-----------|----|----|----|----|------------------|-------|
| tests/_unclassified/test_payment_tasks.py | PARTIAL | YES | PARTIAL | YES | 결제 태스크 성공이 최종 assertion, CB는 배경 | B4: 비즈니스 결과 검증이 주 목적 |
| tests/_unclassified/test_celery_crash_recovery.py | PARTIAL | YES | YES | YES | 결제 롤백이 최종 검증 대상 | B4: 비즈니스 데이터 일관성 검증 |
| tests/_unclassified/test_celery_async_mode.py | PARTIAL | NO | NO | YES | Celery 비동기 모드 동작 검증 | B4: 태스크 성공 여부가 주 검증 |
| tests/_unclassified/test_celery_idempotency.py | PARTIAL | YES | YES | YES | 결제 중복 방지가 비즈니스 관점 | B4: 비즈니스 멱등성 검증 |
| tests/_unclassified/test_l2_celery_crash.py | PARTIAL | YES | YES | YES | L2 트랜잭션 안전성이 주 검증 | B4: 비즈니스 데이터 일관성 |
| tests/_unclassified/test_l2_transaction_crash.py | PARTIAL | YES | YES | YES | 트랜잭션 크래시 시 데이터 일관성 | B4: 비즈니스 데이터 일관성 |
| tests/_unclassified/test_idempotency_enforcement.py | PARTIAL | YES | YES | YES | 결제 멱등성 비즈니스 검증 | B4: 비즈니스 정확성 |
| tests/_unclassified/test_idempotency_key.py | PARTIAL | NO | YES | YES | 멱등키 기능 검증 | B4: 비즈니스 정확성 |
| tests/_unclassified/test_notification_sla.py | PARTIAL | NO | YES | YES | 알림 SLA가 비즈니스 관점 | B4: 비즈니스 SLA |
| tests/_unclassified/test_toss_timeout_retry.py | PARTIAL | YES | YES | YES | Toss API 재시도가 결제 성공 목적 | B4: 결제 성공이 주 목적 |
| tests/_unclassified/test_queue_buildup.py | PARTIAL | YES | PARTIAL | YES | 큐 빌드업이 비즈니스 지연에 영향 | B4: 비즈니스 지연 검증 |
| tests/_unclassified/test_sla_under_load.py | PARTIAL | YES | YES | YES | 비즈니스 SLA 준수가 주 검증 | B4: 비즈니스 SLA |
| tests/_unclassified/test_redis_ttl_idempotency.py | PARTIAL | NO | YES | YES | Redis TTL이 결제 멱등성에 영향 | B4: 비즈니스 정확성 |
| load_tests/scenarios/stage8_webhook.py | PARTIAL | YES | PARTIAL | YES | 웹훅 처리 성공이 결제 확정 목적 | B4: 결제 플로우 완료가 주 목적 |
| load_tests/scenarios/stage9_worker_crash.py | PARTIAL | YES | YES | YES | 워커 크래시 시 태스크 복구 | B4: 비즈니스 태스크 완료 검증 |

---

### 4. CHAOS_NOT_HEALING Tests

> B1=YES, B2=YES, B3=NO per framework (장애 주입만, 복구 검증 미흡)

| file_path | B1 | B2 | B3 | B4 | exclusion_reason | notes |
|-----------|----|----|----|----|------------------|-------|
| load_tests/scenarios/stage6_chaos_random.py | YES | YES | NO | NO | 시스템 안정성만 확인, 복구 상태 명시적 검증 없음 | B3 불충족: 복구 assertion 부재 |
| load_tests/chaos/test_partial_partition_chaos.py | YES | YES | NO | NO | 네트워크 파티션 주입, fallback 동작만 확인 | B3 불충족: 복구 완료 검증 부재 |
| tests/_unclassified/test_chaos_engineering.py | YES | YES | PARTIAL | NO | 카오스 주입 중심, 일부만 복구 검증 | B3 불충족: 부분적 복구 검증 |
| load_tests/chaos/test_stage39_docker_chaos.py | YES | YES | PARTIAL | NO | 컨테이너 카오스, 부분 복구 검증 | B3 불충족: 복구 완료 미확인 |
| tests/_unclassified/test_cascading_failures.py | YES | YES | PARTIAL | NO | 연쇄 장애 시나리오, 일부 폴백만 검증 | B3 불충족: 완전 복구 미검증 |
| tests/_unclassified/test_concurrent_failures.py | YES | YES | NO | NO | 동시 장애 처리, 복구 assertion 부재 | B3 불충족 |
| tests/_unclassified/test_resource_exhaustion.py | YES | YES | NO | NO | 리소스 고갈 시나리오, 복구 미검증 | B3 불충족 |
| tests/_unclassified/test_external_api_failures.py | YES | YES | NO | NO | 외부 API 장애 처리, 복구 미검증 | B3 불충족 |

---

### 5. OUT_OF_SCOPE Tests

> B1=NO per framework (SUT가 힐링 컴포넌트 아님)

#### 5.1 Business Flow Tests (비즈니스 플로우)

| file_path | exclusion_reason |
|-----------|------------------|
| load_tests/scenarios/stage0_smoke.py | B1=NO: Baseline 환경 검증, 힐링 컴포넌트 미관여 |
| load_tests/scenarios/stage1_happy_load.py | B1=NO: 정상 성능 측정, 힐링 컴포넌트 미관여 |
| load_tests/scenarios/stage2_idempotent.py | B1=NO: 비즈니스 멱등성 검증, SUT는 결제 로직 |
| load_tests/scenarios/stage3_latency.py | B1=NO: 지연 시뮬레이션, 힐링 컴포넌트 미관여 |
| load_tests/scenarios/stage4_cancel_storm.py | B1=NO: 취소 기능 검증, SUT는 결제/재고 로직 |
| load_tests/scenarios/stage5_rollback.py | B1=NO: 비즈니스 롤백 검증, SUT는 재고/포인트 |
| load_tests/scenarios/stage7_race_conflict.py | B1=NO: 레이스 컨디션, SUT는 결제 동시성 |
| load_tests/scenarios/stage9_soak.py | B1=NO: 리소스 누수 탐지, 힐링 컴포넌트 미관여 |
| load_tests/scenarios/stage08_observability.py | B1=NO: 관측성 인프라 검증 (힐링 메트릭 아님) |

#### 5.2 Shopping Domain Tests (쇼핑 도메인)

| file_path | exclusion_reason |
|-----------|------------------|
| shopping/tests/unit/test_edge_cases_*.py (5 files) | B1=NO: 비즈니스 엣지 케이스 |
| shopping/tests/unit/services/*.py (15+ files) | B1=NO: 비즈니스 서비스 단위 테스트 |
| shopping/tests/unit/models/*.py (except self_healing) | B1=NO: 비즈니스 모델 테스트 |
| shopping/tests/api/**/*.py (40+ files) | B1=NO: API 기능 테스트 |
| shopping/tests/integration/*.py (8 files) | B1=NO: 비즈니스 통합 테스트 |
| shopping/tests/admin/*.py (3 files) | B1=NO: 관리자 기능 테스트 |
| shopping/tests/commands/*.py (4 files) | B1=NO: 관리 명령어 테스트 |
| shopping/tests/tasks/*.py (5 files) | B1=NO: 비즈니스 태스크 테스트 |
| shopping/tests/schema/**/*.py (30+ files) | B1=NO: 스키마/보안 테스트 |
| shopping/tests/e2e/*.py | B1=NO: 비즈니스 E2E |
| shopping/tests/performance/*.py | B1=NO: 비즈니스 성능 |

#### 5.3 Packages Tests (selfhealing-python 패키지)

| file_path | exclusion_reason |
|-----------|------------------|
| packages/selfhealing-python/tests/unit/adapters/*.py | B1=PARTIAL: 어댑터 단위 테스트, 힐링 로직 아님 |
| packages/selfhealing-python/tests/unit/interfaces/*.py | B1=NO: 인터페이스 계약 테스트 |
| packages/selfhealing-python/tests/core/*.py | B1=PARTIAL: 코어 로직이나 힐링 동작 검증 아님 |
| packages/selfhealing-python/tests/chaos/*.py | B1=YES, B3=NO: CHAOS_NOT_HEALING으로 재분류 |

#### 5.4 Infrastructure/Config Tests

| file_path | exclusion_reason |
|-----------|------------------|
| scripts/test_sqlalchemy_postgres.py | B1=NO: DB 연결 검증 |
| scripts/test_inmemory_integration.py | B1=NO: 인메모리 저장소 검증 |
| myproject/settings/test.py | B1=NO: 설정 파일, 테스트 아님 |

---

### 6. AMBIGUOUS Tests

> 분류 기준 적용 시 명확하지 않은 케이스

| file_path | B1 | B2 | B3 | B4 | ambiguity_reason | framework_reference |
|-----------|----|----|----|----|------------------|---------------------|
| tests/_unclassified/test_architectural_resilience_e2e.py | PARTIAL | YES | PARTIAL | PARTIAL | SUT가 아키텍처 전반, 힐링과 비즈니스 혼재 | B1, B4 경계 모호 |
| tests/_unclassified/test_partial_failure_patterns.py | YES | YES | PARTIAL | PARTIAL | 부분 장애 패턴, 일부만 복구 검증 | B3 부분 충족 판단 어려움 |
| tests/_unclassified/test_time_based_behaviors.py | PARTIAL | NO | PARTIAL | PARTIAL | 시간 기반 동작, 힐링 관련성 불명확 | B1 적용 모호 |
| tests/_unclassified/test_slow_degradation.py | YES | YES | PARTIAL | PARTIAL | 느린 저하 패턴, GRACEFUL vs CHAOS 경계 | B3 판단 기준 모호 |

---

## Axis Distribution Summary

### C-AXIS (Component) Distribution

| Component | Count |
|-----------|-------|
| C1-CIRCUIT-BREAKER | 18 |
| C2-DLQ | 12 |
| C3-RETRY-BACKOFF | 16 |
| C4-FALLBACK | 15 |
| C5-CONTROL-API | 8 |
| C6-OBSERVABILITY | 6 |
| C7-MULTI-COMPONENT | 14 |

### G-AXIS (Granularity) Distribution

| Granularity | Count |
|-------------|-------|
| G1-UNIT | 22 |
| G2-INTEGRATION | 14 |
| G3-E2E | 12 |
| G4-LOAD | 42 |
| G5-CHAOS | 4 |

### S-AXIS (Stage) Distribution

| Stage Level | Count |
|-------------|-------|
| S1-FOUNDATION (0-9) | 0 (all OUT_OF_SCOPE) |
| S2-ADVANCED (10-19) | 18 |
| S3-EXTREME (20-29) | 18 |
| S4-CHAOS-PROD (30-39) | 14 |
| S5-GOVERNANCE (40-45) | 10 |
| UNKNOWN | ~30 |

### E-AXIS (Environment) Distribution

| Environment | Count |
|-------------|-------|
| E1-LOCAL | 35 |
| E2-DOCKER | 45 |
| E3-K8S | 2 |
| E5-MULTI-REGION | 4 |

---

## Notes for Downstream Automation

1. **PURE_HEALING** 및 **UNIT_HEALING** 테스트만 힐링시스템 테스트 스위트에 포함
2. **HYBRID** 테스트는 QA + SRE 공동 소유로 별도 관리 필요
3. **CHAOS_NOT_HEALING**은 복구 assertion 추가 시 PURE_HEALING으로 승격 가능
4. **AMBIGUOUS** 케이스는 수동 검토 후 재분류 필요
5. Stage 0-9는 모두 OUT_OF_SCOPE (비즈니스 기능 검증)

---

## Appendix: Shopping Domain Tests (Excluded - Full List)

총 100+ 파일이 OUT_OF_SCOPE로 분류됨. 주요 경로:

```
shopping/tests/unit/services/test_*_service.py
shopping/tests/unit/models/test_*_model.py
shopping/tests/api/auth/*.py
shopping/tests/api/cart/*.py
shopping/tests/api/order/*.py
shopping/tests/api/payment/*.py
shopping/tests/api/product/*.py
shopping/tests/api/webhook/*.py
shopping/tests/schema/**/*.py
shopping/tests/integration/*.py
shopping/tests/admin/*.py
shopping/tests/commands/*.py
shopping/tests/tasks/*.py
```

모두 B1=NO (SUT가 비즈니스 로직)로 제외됨.
