# 102. 하드코딩된 설정값 최종 감사 보고서

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: 계획
- **관련 문서**: 94-101 (Hardcoded Config 시리즈)
- **대상 패키지**: `packages/selfhealing-python/src/selfhealing`

---

## 1. 개요

### 1.1 목적
기존 문서(94-101)에서 다루지 않은 추가 하드코딩된 설정값들에 대한 최종 감사 보고서.
코드 기반 분석을 통해 발견된 모든 하드코딩된 상수들을 카테고리별로 정리.

### 1.2 분석 범위
| 디렉토리 | 발견된 하드코딩 항목 수 |
|---------|----------------------|
| `services/coordination/` | ~50건 |
| `core/` | ~40건 |
| `audit/` | ~35건 |
| `api/django/` | ~25건 |
| `services/chaos/` | ~60건 |
| `services/canary/` | ~15건 |
| `services/error_budget_gate/` | ~5건 |
| **총계** | **약 230건** |

---

## 2. 카테고리별 분석

### 2.1 Category A: 모듈 레벨 상수 (HIGH Priority)
운영 환경에서 변경이 필요한 핵심 설정값들.

#### 2.1.1 services/coordination/recovery_tasks.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `DEFAULT_TRIGGER_CHECK_INTERVAL` | 60 | L82 | 트리거 체크 주기 (초) |
| `DEFAULT_HEALTH_MONITOR_INTERVAL` | 30 | L83 | 헬스 모니터 주기 (초) |
| `DEFAULT_STALE_CHECK_INTERVAL` | 10 | L84 | 스테일 체크 주기 (초) |

#### 2.1.2 services/coordination/critical_worker.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| Worker Pool 설정 (L226-260) | 다양함 | L226-260 | worker_count, concurrency, prefetch_multiplier |

#### 2.1.3 services/coordination/regional_recovery_policy.py
| 설정 그룹 | 위치 | 설명 |
|----------|-----|------|
| CRITICAL 정책 | L195-200 | stability_check_duration_minutes=10, error_rate_threshold=0.05 |
| HIGH 정책 | L206-210 | stability_check_duration_minutes=7, error_rate_threshold=0.10 |
| MEDIUM 정책 | L216-220 | stability_check_duration_minutes=5, error_rate_threshold=0.15 |
| LOW 정책 | L226-230 | stability_check_duration_minutes=10, error_rate_threshold=0.10 |

#### 2.1.4 services/namespace_emergency/tracker.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `DEFAULT_EMERGENCY_EXPIRY_HOURS` | 8 | L68 | 긴급 상태 만료 시간 |
| `CACHE_TTL_SECONDS` | 30.0 | L71 | 캐시 TTL |

#### 2.1.5 services/namespace_emergency/cascade_detector.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `DEFAULT_ESCALATION_THRESHOLD` | 2 | L44 | 에스컬레이션 임계값 |
| `DEFAULT_CASCADE_WINDOW_MINUTES` | 30 | L47 | 캐스케이드 윈도우 |

---

### 2.2 Category B: Core 모듈 상수 (HIGH Priority)

#### 2.2.1 core/runtime_feedback.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `MAX_CONSECUTIVE_FAILURES` | 3 | L86 | 최대 연속 실패 횟수 |
| `POST_ROLLBACK_COOLDOWN` | 120 | L88 | 롤백 후 쿨다운 (초) |
| `POST_ADJUSTMENT_WAIT` | 30 | L90 | 조정 후 대기 시간 (초) |

#### 2.2.2 core/auto_rollback_guard.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `ERROR_RATE_MAJOR` | 0.1 | L135 | Major 등급 에러율 임계값 |
| `ERROR_RATE_CRITICAL` | 0.3 | L136 | Critical 등급 에러율 임계값 |
| `LATENCY_MAJOR_MS` | 5000 | L137 | Major 등급 레이턴시 임계값 |
| `LATENCY_CRITICAL_MS` | 10000 | L138 | Critical 등급 레이턴시 임계값 |
| `CONSECUTIVE_FAILURES_ALERT` | 3 | L141 | 알림 발생 연속 실패 횟수 |
| `CONSECUTIVE_FAILURES_EMERGENCY` | 5 | L142 | 긴급 상태 연속 실패 횟수 |

#### 2.2.3 core/adaptive_jitter.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `ERROR_BUDGET_DANGER_THRESHOLD` | 0.2 | L43 | 에러 버짓 위험 임계값 |
| `ERROR_BUDGET_SAFE_THRESHOLD` | 0.5 | L44 | 에러 버짓 안전 임계값 |
| `LOAD_HIGH_THRESHOLD` | 0.8 | L45 | 고부하 임계값 |
| `LOAD_LOW_THRESHOLD` | 0.3 | L46 | 저부하 임계값 |

#### 2.2.4 core/safety_bounds.py
| 설정 그룹 | 위치 | 값 범위 |
|----------|-----|--------|
| timeout_ms bounds | L53-55 | min=100, max=30000 |
| max_retries bounds | L58-60 | min=0, max=10 |
| failure_threshold bounds | L63-65 | min=0.1, max=0.9 |
| backoff_factor bounds | L68-70 | min=0.01, max=1.0 |
| batch_size bounds | L73-75 | min=10, max=10000 |
| concurrency bounds | L78-80 | min=10, max=5000 |
| half_open_timeout_ms bounds | L83-85 | min=1000, max=60000 |
| success_threshold bounds | L88-90 | min=1, max=100 |

#### 2.2.5 core/state_cache.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `BASE_TTL` | 5.0 | L38 | 기본 TTL (초) |
| `JITTER_RANGE` | 0.5 | L39 | 랜덤 지터 범위 (초) |

#### 2.2.6 core/resource_monitor.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `DEFAULT_SAFETY_MARGIN` | 0.15 | L41 | 기본 안전 마진 (15%) |

---

### 2.3 Category C: Audit 모듈 상수 (MEDIUM Priority)

#### 2.3.1 audit/integrity/sequence.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `DEFAULT_PENDING_TTL_SECONDS` | 30 | L63 | 대기 상태 TTL |
| `DEFAULT_ORPHAN_TTL_SECONDS` | 86400 | L64 | 고아 상태 TTL (24시간) |

#### 2.3.2 audit/integrity/cold_storage.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `ARCHIVE_THRESHOLD_DAYS` | 7 | L254 | 아카이브 임계 일수 |
| `DEFAULT_COLD_RETENTION_YEARS` | 7 | L255 | 콜드 스토리지 보관 기간 |

#### 2.3.3 audit/hash_chain_safety.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `DEFAULT_TIMEOUT_SECONDS` | 300 | L485 | 기본 타임아웃 (5분) |
| `DEFAULT_TIMEOUT_SECONDS` | 120 | L605 | 날짜별 타임아웃 (2분) |
| `MAX_REDIS_ENTRIES` | 1000 | L725 | Redis 최대 엔트리 수 |

#### 2.3.4 audit/cascade_auditor.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `MAX_INDEX_SIZE` | 10000 | L98 | 최대 인덱스 크기 |

#### 2.3.5 audit/integrity/anchor.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `DEFAULT_RETENTION_DAYS` | 90 | L47 | 기본 보관 일수 |

#### 2.3.6 audit/resilience/buffer.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `MAX_ENTRIES` | 10000 | L38 | 최대 엔트리 수 |
| `FLUSH_INTERVAL_SECONDS` | 30.0 | L39 | 플러시 간격 |

#### 2.3.7 audit/integrity/cross_cluster_linker.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `LOCAL_ANCHOR_TTL_DAYS` | 90 | L129 | 로컬 앵커 TTL |
| `GLOBAL_ANCHOR_TTL_DAYS` | 365 | L130 | 글로벌 앵커 TTL |

#### 2.3.8 audit/integrity/health_score.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `HEALTHY_THRESHOLD` | 95.0 | L110 | 건강 상태 임계값 |
| `WARNING_THRESHOLD` | 80.0 | L111 | 경고 상태 임계값 |
| `CRITICAL_THRESHOLD` | 50.0 | L112 | 위험 상태 임계값 |

---

### 2.4 Category D: API 모듈 상수 (MEDIUM Priority)

#### 2.4.1 api/django/rate_limit.py
| 상수명 | 값 | 위치 | 설명 |
|-------|---|-----|------|
| `DEFAULT_RATE_LIMIT` | 100 | L42 | 기본 요청 제한 (분당) |
| `DEFAULT_WINDOW_SECONDS` | 60 | L43 | 기본 윈도우 |
| `EMERGENCY_RATE_LIMIT` | 10 | L47 | 긴급 요청 제한 |
| `EMERGENCY_WINDOW_SECONDS` | 60 | L48 | 긴급 윈도우 |
| `PING_INTERVAL` | 5 | L247 | 핑 간격 |
| `FAILURE_THRESHOLD` | 3 | L248 | 실패 임계값 |
| `RECOVERY_JITTER_MAX` | 10 | L249 | 복구 지터 최대값 |

---

### 2.5 Category E: Chaos 모듈 상수 (LOW Priority)
Chaos Engineering 관련 상수들은 테스트/실험용이므로 외부화 우선순위가 낮음.

#### 2.5.1 services/chaos/experiments/hypothesis.py
| 설정 | 위치 | 설명 |
|-----|-----|------|
| CIRCUIT_BREAKER_HYPOTHESIS | L156-160 | 회로 차단기 가설 기본값 |
| DLQ_HYPOTHESIS | L168-170 | DLQ 가설 기본값 |
| GRACEFUL_DEGRADATION_HYPOTHESIS | L177-179 | Graceful Degradation 가설 |
| 기타 9개 가설 템플릿 | L186-258 | 각종 Chaos 실험 가설 템플릿 |

#### 2.5.2 services/chaos/synthetic_load.py
| 상수 | 값 | 위치 | 설명 |
|-----|---|-----|------|
| target_rps 기본값 | 100 | L305 | 기본 RPS |
| duration_seconds 기본값 | 60 | L306 | 기본 지속시간 |
| period | 30 | L534 | 기본 주기 |

---

## 3. 구현 우선순위

### Phase 1: 핵심 운영 설정 (Week 1-2)
1. `core/runtime_feedback.py` 상수 외부화
2. `core/auto_rollback_guard.py` 상수 외부화
3. `core/adaptive_jitter.py` 임계값 외부화
4. `api/django/rate_limit.py` 상수 외부화

### Phase 2: Coordination 서비스 (Week 3-4)
5. `services/coordination/recovery_tasks.py` 간격 설정 외부화
6. `services/coordination/critical_worker.py` Worker Pool 설정 외부화
7. `services/coordination/regional_recovery_policy.py` 정책 설정 외부화
8. `services/namespace_emergency/*.py` 상수 외부화

### Phase 3: Audit 모듈 (Week 5-6)
9. `audit/integrity/*.py` TTL 및 임계값 외부화
10. `audit/resilience/buffer.py` 버퍼 설정 외부화
11. `audit/cascade_auditor.py` 크기 제한 외부화

### Phase 4: Safety Bounds 및 기타 (Week 7-8)
12. `core/safety_bounds.py` 바운드 설정 외부화
13. `core/state_cache.py` TTL 설정 외부화
14. `core/resource_monitor.py` 안전 마진 외부화

### Phase 5: Chaos 모듈 (Optional)
15. Chaos 관련 기본값들은 현재 상태 유지 (테스트/실험용)

---

## 4. 다음 문서

- **103_HARDCODED_CONFIG_CORE_REFACTORING.md**: Core 모듈 리팩토링 상세 계획
- **104_HARDCODED_CONFIG_COORDINATION_REFACTORING.md**: Coordination 서비스 리팩토링 계획
- **105_HARDCODED_CONFIG_AUDIT_REFACTORING.md**: Audit 모듈 리팩토링 계획
