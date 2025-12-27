# Load Test Stage 검증 계획서

> **작성일**: 2025-12-28  
> **목적**: 쇼핑 API + Self-Healing 시스템 분리 후 전체 스테이지 검증 계획

---

## 📊 현황 요약

| 항목 | 수치 |
|------|------|
| 전체 스테이지 파일 | 87개 |
| 고유 기능 스테이지 | **60개** |
| 중복 파일 (스킵) | 27개 |
| 완료된 스테이지 | 18개 |
| 남은 작업 | **42개** |
| 예상 소요 시간 | 약 7-8일 |

---

## ✅ 완료된 스테이지 (18개)

| Stage | 폴더 | 파일명 | 결과 | 완료일 |
|-------|------|--------|------|--------|
| 1 | load | stage1_happy_load.py | ✅ PASS | 2025-12-25 |
| 2 | integration | stage2_idempotent.py | ✅ PASS | 2025-12-26 |
| 3 | load | stage3_latency.py | ✅ PASS | 2025-12-26 |
| 4 | hybrid | stage4_cancel_storm.py | ✅ PASS | 2025-12-26 |
| 5 | integration | stage5_rollback_healing.py | ✅ PASS | 2025-12-26 |
| 6 | chaos | stage6_extreme_chaos.py | ✅ PASS | 2025-12-27 |
| 7 | integration | stage7_race_extreme.py | ✅ PASS | 2025-12-27 |
| 8 | integration | stage8_webhook_selfhealing_v2.py | ✅ PASS | 2025-12-27 |
| 9 | chaos | stage9_worker_crash_selfhealing.py | ✅ PASS | 2025-12-27 |
| 10 | integration | stage10_self_healing.py | ✅ PASS | 2025-12-27 |
| 11 | load | stage11_ramp_threshold.py | ✅ PASS | 2025-12-27 |
| 12 | hybrid | stage12_spike_recovery.py | 🏆 PLATINUM | 2025-12-27 |
| 46 | integration | stage46_audit_observability.py | ✅ PASS | 2025-12-24 |
| 47 | chaos | stage47_destruction_test.py | ⚠️ PARTIAL | 2025-12-26 |
| 48 | chaos | stage48_xtest_mode.py | ✅ PASS | 2025-12-26 |
| 49 | chaos | stage49_docker_chaos.py | ⚠️ PARTIAL | 2025-12-26 |
| 50 | chaos | stage50_health_bridge_test.py | ✅ PASS | 2025-12-26 |
| 51 | chaos | stage51_observability.py | ✅ PASS | 2025-12-26 |

---

## 🔵 해야 할 스테이지 (42개)

### 1차 우선순위: 핵심 기능 (5개) - 예상 4시간

| # | Stage | 폴더 | 파일명 | 목적 |
|---|-------|------|--------|------|
| 1 | 0 | load | stage0_selfhealing_smoke.py | Self-Healing API 전체 동작 확인 |
| 2 | 13 | hybrid | stage13_repeated_spike.py | 반복 스파이크 백오프 축적 검증 |
| 3 | 14 | integration | stage14_dlq_api_test.py | DLQ API 전체 사이클 |
| 4 | 14 | integration | stage14_dlq_replay.py | DLQ 재처리 정확성 |
| 5 | 15 | integration | stage15_cb_transitions.py | CB 상태 전환 자동화 |

### 2차 우선순위: 캐시/DB 정합성 (5개) - 예상 5.5시간

| # | Stage | 폴더 | 파일명 | 목적 |
|---|-------|------|--------|------|
| 6 | 17 | integration | stage17_cache_ttl_race_locust.py | 캐시 TTL Race Condition |
| 7 | 17 | integration | stage17_event_invalidation_locust.py | 이벤트 기반 캐시 무효화 |
| 8 | 24 | integration | stage24_cache_dead_protection.py | Redis 죽어도 DB 과부하 방지 |
| 9 | 35 | integration | stage35_cache_poison_http.py | 캐시 오염 감지/복구 |
| 10 | 35 | integration | stage35_redis_stampede.py | Stampede 분산 락 |

### 3차 우선순위: 네트워크/인프라 Chaos (5개) - 예상 6.5시간

| # | Stage | 폴더 | 파일명 | 목적 |
|---|-------|------|--------|------|
| 11 | 18 | chaos | stage18_chain_failure.py | 서비스 체인 장애 롤백 |
| 12 | 19 | chaos | stage19_rollback_failure.py | 롤백 실패 이중장애 |
| 13 | 24 | chaos | stage24_partial_partition.py | 부분 네트워크 단절 복원력 |
| 14 | 25 | chaos | stage25_tls_failure.py | TLS/인증서 복원력 |
| 15 | 27 | chaos | stage27_graceful_shutdown.py | 배포 시 요청 유실 방지 |

### 4차 우선순위: 멀티리전/K8s (4개) - 예상 7.5시간 ⭐ 필수

| # | Stage | 폴더 | 파일명 | 목적 |
|---|-------|------|--------|------|
| 16 | 28 | chaos | stage28_multi_region.py | 멀티리전 네트워크 장애 |
| 17 | 34 | chaos | stage34_db_deadlock.py | 대규모 DB Deadlock |
| 18 | 38 | chaos | stage38_multi_region_failover_locust.py | 멀티리전 Failover |
| 19 | 39 | chaos | stage39_k8s_runtime_chaos.py | 컨테이너 OOM/재시작 |

### 5차 우선순위: 고급 시나리오 (5개) - 예상 7시간

| # | Stage | 폴더 | 파일명 | 목적 |
|---|-------|------|--------|------|
| 20 | 21 | hybrid | stage21_false_positive.py | False Positive 방지 |
| 21 | 22 | hybrid | stage22_rate_limit_conflict.py | Self-DDoS 방지 |
| 22 | 31 | hybrid | stage31_cascade_extended.py | 복잡 Cascade 장애 |
| 23 | 32 | hybrid | stage32_retry_storm_extended.py | Retry Storm 메모리 관리 |
| 24 | 33 | hybrid | stage33_jwt_cascade.py | JWT 만료 Stampede |

### 6차 우선순위: 거버넌스/Audit (5개) - 예상 6.5시간

| # | Stage | 폴더 | 파일명 | 목적 |
|---|-------|------|--------|------|
| 25 | 23 | integration | stage23_clock_skew.py | Clock Skew/NTP 드리프트 |
| 26 | 29 | integration | stage29_backpressure.py | 백프레셔 정책 (503) |
| 27 | 29 | integration | stage29_bulk_dlq_replay.py | 대량 DLQ 10만건 |
| 28 | 30 | integration | stage30_schedule_drift.py | 스케줄러 드리프트 |
| 29 | 37 | integration | stage37_schema_compat_http.py | 스키마 호환성 Rolling Update |

### 7차 우선순위: pytest 기반 통합 (9개) - 예상 12.5시간

| # | Stage | 폴더 | 파일명 | 목적 |
|---|-------|------|--------|------|
| 30 | 39 | integration | stage39_k8s_governance_boundaries.py | K8s 거버넌스 경계 |
| 31 | 40 | integration | stage40_idempotency_clock_skew.py | 멱등성 + 시간왜곡 |
| 32 | 41 | integration | stage41_tls_certificate_expiry.py | TLS 만료 Part 1 |
| 33 | 41 | integration | stage41_tls_certificate_expiry_fleet.py | TLS Fleet 관리 |
| 34 | 42 | integration | stage42_compound_failure_deterministic.py | 복합 장애 결정론적 |
| 35 | 43 | integration | stage43_operator_race_deterministic.py | 운영자 경쟁 결정론적 |
| 36 | 44 | integration | stage44_zero_downtime_migration.py | 무중단 마이그레이션 |
| 37 | 45 | integration | stage45_external_trust_audit.py | 외부 신뢰 감사 Part 1 |
| 38 | 45 | integration | stage45_external_trust_reports.py | 외부 신뢰 리포트 |

### 8차 우선순위: 기타 고유 (4개) - 예상 5시간

| # | Stage | 폴더 | 파일명 | 목적 |
|---|-------|------|--------|------|
| 39 | 9 | load | stage9_soak.py | 장시간 안정성 (30분+) |
| 40 | 14 | integration | stage14_outbox_http.py | Outbox 패턴 |
| 41 | 20 | integration | stage20_delayed_webhook_locust.py | 지연 Webhook |
| 42 | 35 | integration | stage35_distributed_test_v2.py | Multi-Worker 분산 |

---

## ⛔ 스킵할 스테이지 (27개)

### 이유: 동일 기능의 다른 버전 (대표 버전만 유지)

| 스킵 파일 | 대신 유지할 파일 | 스킵 이유 |
|-----------|-----------------|----------|
| load/stage0_smoke.py | stage0_selfhealing_smoke.py | SH 버전이 더 포괄적 |
| chaos/stage6_chaos_random.py | stage6_extreme_chaos.py | 극한 버전이 상위 호환 |
| chaos/stage9_worker_crash.py | stage9_worker_crash_selfhealing.py | SH 버전에 통합됨 |
| chaos/stage16_db_lock_recovery.py | stage16_db_lock_recovery_locust.py | Locust 버전이 부하 테스트 가능 |
| chaos/stage26_connection_pool.py | stage26_extreme_pool_test.py | 극한 버전이 상위 호환 |
| chaos/stage38_multi_region_failover.py | stage38_multi_region_failover_locust.py | Locust 버전이 부하 테스트 가능 |
| chaos/stage42_compound_failure_chaos.py | stage42_compound_failure_chaos_locust.py | Locust 버전이 부하 테스트 가능 |
| hybrid/stage36_memory_pressure.py | stage36_real_http.py | 실제 HTTP 버전이 현실적 |
| integration/stage5_rollback.py | stage5_rollback_healing.py | SH 버전에 통합됨 |
| integration/stage7_race_conflict.py | stage7_race_extreme.py | 극한 버전에 통합됨 |
| integration/stage8_webhook.py | stage8_webhook_selfhealing_v2.py | v2에 통합됨 |
| integration/stage8_webhook_selfhealing.py | stage8_webhook_selfhealing_v2.py | v2에 통합됨 |
| integration/stage14_outbox.py | stage14_outbox_http.py | HTTP 버전이 실제적 |
| integration/stage17_cache_ttl_race.py | stage17_cache_ttl_race_locust.py | Locust 버전이 부하 테스트 가능 |
| integration/stage17_event_invalidation.py | stage17_event_invalidation_locust.py | Locust 버전이 부하 테스트 가능 |
| integration/stage20_delayed_webhook.py | stage20_delayed_webhook_locust.py | Locust 버전이 부하 테스트 가능 |
| integration/stage35_cache_poison.py | stage35_cache_poison_http.py | HTTP 버전이 실제적 |
| integration/stage35_cache_stampede.py | stage35_redis_stampede.py | Redis 분산 락 버전이 실제적 |
| integration/stage35_distributed_test.py | stage35_distributed_test_v2.py | v2가 개선됨 |
| integration/stage37_schema_compat.py | stage37_schema_compat_http.py | HTTP 버전이 실제적 |

### 이유: scenarios 루트의 중복 파일

| 스킵 파일 | 스킵 이유 |
|-----------|----------|
| scenarios/stage42_compound_failure_chaos_locust.py | chaos/ 폴더에 동일 파일 존재 |

---

## 📈 진행 순서 권장

```
Week 1 (Day 1-2)
├── 1차: 핵심 기능 (5개)
└── 2차: 캐시/DB 정합성 (5개)

Week 1 (Day 3-4)
├── 3차: 네트워크/인프라 (5개)
└── 4차: 멀티리전/K8s (4개) ⭐

Week 2 (Day 5-6)
├── 5차: 고급 시나리오 (5개)
└── 6차: 거버넌스/Audit (5개)

Week 2 (Day 7-8)
├── 7차: pytest 통합 (9개)
└── 8차: 기타 고유 (4개)
```

---

## 🎯 검증 완료 기준

### 필수 통과 조건
- [ ] 모든 CRITICAL 스테이지 PASS
- [ ] 멀티리전 Failover 정상 동작 (Stage 28, 38)
- [ ] K8s 런타임 Chaos 복원력 확인 (Stage 39)
- [ ] Zero-Downtime Migration 성공 (Stage 44)
- [ ] 중복 결제 0건 유지
- [ ] 데이터 정합성 100%

### 품질 지표
- P95 Latency < 200ms (정상 부하)
- Error Rate < 1% (Chaos 상황)
- CB Recovery Time < 30초
- DLQ Replay 성공률 > 99%

---

## 📝 변경 이력

| 날짜 | 변경 내용 |
|------|----------|
| 2025-12-28 | 최초 작성: 42개 진행, 27개 스킵 계획 수립 |
