# Self-Healing 런타임 설정 API 구현 계획

> **목적**: 모든 수치 설정을 API로 런타임 변경 가능하도록 구현
> **작성일**: 2025-12-19
> **상태**: 🚧 진행 중

---

## 📋 구현할 API 엔드포인트

### 1. 설정 조회/수정 통합 API

| 엔드포인트 | 메서드 | 기능 |
|-----------|--------|------|
| `/api/self-healing/config/` | GET | 전체 설정 조회 |
| `/api/self-healing/config/circuit-breaker/` | GET/PUT | Circuit Breaker 설정 |
| `/api/self-healing/config/dlq/` | GET/PUT | DLQ 설정 |
| `/api/self-healing/config/retry/` | GET/PUT | Retry 설정 |
| `/api/self-healing/config/sla/` | GET/PUT | SLA 설정 |
| `/api/self-healing/config/rate-limit/` | GET/PUT | Rate Limit 설정 |
| `/api/self-healing/config/security/` | GET/PUT | Security 설정 |
| `/api/self-healing/config/idempotency/` | GET/PUT | Idempotency 설정 |
| `/api/self-healing/config/notification/` | GET/PUT | Notification 설정 |
| `/api/self-healing/config/forensic/` | GET/PUT | Forensic 설정 |
| `/api/self-healing/config/metrics/` | GET/PUT | Metrics 설정 |
| `/api/self-healing/config/slo/` | GET/PUT | SLO 설정 |

---

## 📊 등록할 수치 설정 전체 목록

### 1. CircuitBreakerConfig
| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `enabled` | bool | True | 활성화 여부 |
| `failure_threshold` | int | 5 | 실패 임계값 (CB 열림) |
| `recovery_timeout` | int | 60 | 복구 타임아웃 (초) |
| `success_threshold` | int | 2 | 성공 임계값 (CB 닫힘) |
| `half_open_max_calls` | int | 3 | Half-open 최대 호출 수 |
| `half_open_request_limit` | int | 10 | Half-open 요청 제한 |
| `rate_limit_cascade_threshold` | int | 10 | 429 연쇄 감지 임계값 |
| `rate_limit_cascade_window_seconds` | int | 60 | 연쇄 감지 윈도우 |
| `self_ddos_protection_enabled` | bool | True | Self-DDoS 보호 활성화 |
| `self_ddos_request_threshold` | int | 100 | Self-DDoS 요청 임계값 |
| `self_ddos_window_seconds` | int | 10 | Self-DDoS 윈도우 |
| `self_ddos_backoff_multiplier` | float | 2.0 | 백오프 배수 |

### 2. DLQConfig
| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `enabled` | bool | True | 활성화 여부 |
| `max_retries` | int | 3 | 최대 재시도 횟수 |
| `retry_delay` | int | 60 | 재시도 지연 (초) |
| `expiry_hours` | int | 72 | 만료 시간 |
| `retention_days` | int | 30 | 보관 기간 (일) |
| `batch_size` | int | 10 | 배치 크기 |
| `max_replay_attempts` | int | 2 | 최대 재생 시도 |

### 3. RetryConfig
| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `max_attempts` | int | 3 | 최대 시도 횟수 |
| `backoff_strategy` | str | "exponential" | 백오프 전략 |
| `backoff_base` | int | 4 | 지수 백오프 기본값 |
| `base_delay` | float | 1.0 | 기본 지연 (초) |
| `max_delay` | float | 300.0 | 최대 지연 (초) |
| `min_delay` | int | 1 | 최소 지연 (초) |
| `jitter` | bool | True | 지터 활성화 |
| `jitter_percent` | int | 25 | 지터 퍼센트 |

### 4. SLAConfig
| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `default_hours` | int | 24 | 기본 SLA 시간 |
| `thresholds_by_domain` | dict | {} | 도메인별 SLA 임계값 |

### 5. RateLimitConfig
| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `base_delay` | float | 1.0 | 기본 지연 (초) |
| `max_delay` | float | 60.0 | 최대 지연 (초) |
| `jitter_percent` | float | 30.0 | 지터 퍼센트 |
| `default_retry_after` | float | 5.0 | 기본 Retry-After (초) |
| `backoff_multiplier` | float | 2.0 | 백오프 배수 |

### 6. SecurityConfig
| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `rate_limit_window_seconds` | int | 60 | Rate limit 윈도우 |
| `rate_limit_max_requests` | int | 100 | 최대 요청 수 |
| `temporary_ban_hours` | int | 1 | 임시 차단 시간 |
| `permanent_ban_threshold` | int | 5 | 영구 차단 임계값 |
| `suspicious_ip_cache_timeout` | int | 86400 | 의심 IP 캐시 타임아웃 |
| `injection_ban_hours` | int | 24 | 인젝션 차단 시간 |
| `failed_login_threshold` | int | 5 | 로그인 실패 임계값 |

### 7. IdempotencyConfig
| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `default_cache_ttl` | int | 60 | 기본 캐시 TTL (초) |
| `extended_cache_ttl` | int | 300 | 확장 캐시 TTL (초) |
| `short_cache_ttl` | int | 60 | 짧은 캐시 TTL (초) |
| `clock_skew_tolerance_seconds` | float | 5.0 | 클럭 스큐 허용 |

### 8. NotificationConfig
| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `enabled` | bool | True | 활성화 여부 |
| `critical_threshold` | int | 10 | 크리티컬 알림 임계값 |
| `warning_threshold` | int | 5 | 경고 임계값 |
| `slack_block_text_limit` | int | 3000 | Slack 블록 텍스트 제한 |
| `description_max_length` | int | 500 | 설명 최대 길이 |
| `action_taken_max_length` | int | 200 | 액션 최대 길이 |
| `title_max_length` | int | 150 | 제목 최대 길이 |
| `notification_timeout_seconds` | int | 10 | 알림 타임아웃 |

### 9. ForensicConfig
| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `error_message_max_length` | int | 500 | 에러 메시지 최대 길이 |
| `response_body_max_length` | int | 5000 | 응답 본문 최대 길이 |
| `user_agent_max_length` | int | 500 | User-Agent 최대 길이 |
| `max_stack_frames` | int | 50 | 최대 스택 프레임 |
| `max_stacktrace_length` | int | 10000 | 최대 스택트레이스 길이 |
| `max_context_size_bytes` | int | 65536 | 최대 컨텍스트 크기 |

### 10. MetricsConfig
| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `enabled` | bool | True | 활성화 여부 |
| `prefix` | str | "selfhealing" | 메트릭 프리픽스 |
| `collection_interval` | int | 60 | 수집 간격 (초) |
| `export_prometheus` | bool | True | Prometheus 내보내기 |

### 11. SLOConfig (slo.py)
| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `target` | float | 0.999 | SLO 목표 (예: 99.9%) |
| `window_days` | int | 30 | 롤링 윈도우 기간 |
| `warning_threshold` | float | auto | 경고 임계값 |
| `critical_threshold` | float | auto | 크리티컬 임계값 |

---

## 🏗️ 구현 작업 목록

### Phase 1: 기본 인프라 (현재 진행)
- [x] 문서 작성
- [ ] RuntimeConfigManager 클래스 구현
- [ ] 설정 저장/로드를 위한 StateBackend 활용
- [ ] 설정 변경 시 감사 로그 기록

### Phase 2: API Views 구현
- [ ] ConfigViewBase 추상 클래스 생성
- [ ] CircuitBreakerConfigView
- [ ] DLQConfigView
- [ ] RetryConfigView
- [ ] SLAConfigView
- [ ] RateLimitConfigView
- [ ] SecurityConfigView
- [ ] IdempotencyConfigView
- [ ] NotificationConfigView
- [ ] ForensicConfigView
- [ ] MetricsConfigView
- [ ] SLOConfigView
- [ ] AllConfigView (전체 조회)

### Phase 3: Serializers 구현
- [ ] CircuitBreakerConfigSerializer
- [ ] DLQConfigSerializer
- [ ] RetryConfigSerializer
- [ ] SLAConfigSerializer
- [ ] RateLimitConfigSerializer
- [ ] SecurityConfigSerializer
- [ ] IdempotencyConfigSerializer
- [ ] NotificationConfigSerializer
- [ ] ForensicConfigSerializer
- [ ] MetricsConfigSerializer
- [ ] SLOConfigSerializer

### Phase 4: URL 등록 및 테스트
- [ ] urls.py에 모든 엔드포인트 등록
- [ ] 단위 테스트 작성
- [ ] 통합 테스트 작성

---

## 📁 파일 구조

```
packages/selfhealing-python/src/selfhealing/
├── api/
│   └── django/
│       ├── views/
│       │   ├── __init__.py      # 수정: 새 뷰 export
│       │   └── config.py        # 새로 생성
│       ├── serializers/
│       │   ├── __init__.py      # 수정: 새 시리얼라이저 export
│       │   └── config.py        # 새로 생성
│       └── urls.py              # 수정: 새 URL 패턴 추가
├── services/
│   └── runtime_config.py        # 새로 생성: 런타임 설정 관리자
└── core/
    └── config.py                # 수정: set_* 메서드 추가
```

---

## 🔐 보안 고려사항

1. **권한**: 모든 설정 변경 API는 `IsAdminUser` 권한 필요
2. **감사 로그**: ConfigChangeTracker를 통한 모든 변경 기록
3. **유효성 검증**: 각 설정값의 범위/타입 검증
4. **캐시 무효화**: 설정 변경 시 관련 캐시 자동 무효화

---

## ⚠️ 주의사항

1. **서비스별 오버라이드**: 글로벌 설정 외에 서비스별 오버라이드 지원 필요
2. **실시간 반영**: 설정 변경 즉시 반영 (재시작 불필요)
3. **롤백 기능**: 이전 설정으로 롤백 가능해야 함
4. **기본값 복원**: 기본값으로 리셋하는 API 제공

---

## 📝 작업 시작 체크리스트

1. [x] 모든 수치 설정 파악 완료
2. [x] 현재 등록된 API 확인 완료
3. [x] 구현 계획 문서화 완료
4. [ ] RuntimeConfigManager 구현 시작
5. [ ] 설정 Serializers 구현
6. [ ] 설정 Views 구현
7. [ ] URL 등록
8. [ ] 테스트 작성
