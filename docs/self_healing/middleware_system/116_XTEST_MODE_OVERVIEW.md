# X-Test-Mode 기능 확장 개요

**문서 번호:** 116
**작성일:** 2026-01-26
**상태:** 계획 수립
**관련 Stage:** Stage 48+ (Chaos Proof)

---

## 1. 배경

### 1.1 현재 X-Test-Mode 지원 범위

X-Test-Mode는 Rate Limiter(L1)를 우회하여 L2/L3 Self-Healing 동작을 테스트 환경에서 직접 관찰하기 위한 API입니다.

**현재 지원 컴포넌트:**

| 컴포넌트 | 파일 위치 | 엔드포인트 |
|----------|----------|-----------|
| Circuit Breaker | `api/django/views/xtest/circuit_breaker.py` | 7개 API |
| Error Budget | `api/django/views/xtest/error_budget.py` | 1개 API |
| System Snapshot | `api/django/views/xtest/snapshot.py` | 1개 API |
| Observability | `api/django/views/xtest/observability.py` | 6개 API |

### 1.2 누락된 핵심 컴포넌트

Self-Healing 아키텍처 기준 (`docs/self_healing/02_ARCHITECTURE.md`) 누락된 컴포넌트:

| 컴포넌트 | 서비스 파일 | 테스트 필요성 |
|----------|------------|--------------|
| **DLQ** | `services/dlq/` | 장애 저장 → 복구 사이클 관찰 |
| **Replay** | `services/replay_service.py` | CB 복구 시 자동 재생 검증 |
| **Retry Handler** | `services/retry_handler.py` | Exponential Backoff 동작 확인 |
| **Rate Limiter** | `api/django/rate_limit.py` | Rate Limit 동작 관찰 (bypass 아닌) |
| **Idempotency** | `services/idempotency_service.py` | 멱등성 충돌 시나리오 |

---

## 2. 확장 목표

### 2.1 Self-Healing 전체 사이클 테스트

```
[장애 발생]
     ↓
[Retry 시도] ──(실패)──→ [DLQ 저장]
     ↓                        ↓
[CB OPEN]               [Pending 상태]
     ↓                        ↓
[Fast Fail]             [CB CLOSED 감지]
     ↓                        ↓
[복구 감지]             [자동 Replay]
     ↓                        ↓
[CB CLOSED]             [Resolved]
```

### 2.2 개별 컴포넌트 동작 관찰

- **DLQ**: 저장 → 조회 → 상태 변경
- **Replay**: 수동 재생 → 배치 재생 → 조건부 재생
- **Retry**: Backoff 간격 → 최대 재시도 → DLQ 라우팅
- **Rate Limiter**: 임계값 → 차단 → 복구
- **Idempotency**: 키 생성 → 중복 감지 → 충돌 해결

---

## 3. 문서 구조

| 문서 번호 | 제목 | 컴포넌트 |
|----------|------|---------|
| 116 | X-Test-Mode 기능 확장 개요 (본 문서) | 전체 |
| 117 | DLQ 테스트 API 설계 | DLQ |
| 118 | Replay 테스트 API 설계 | Replay |
| 119 | Retry Handler 테스트 API 설계 | Retry |
| 120 | Rate Limiter 테스트 API 설계 | Rate Limiter |
| 121 | Idempotency 테스트 API 설계 | Idempotency |
| 122 | 통합 테스트 시나리오 | 전체 사이클 |

---

## 4. 공통 설계 원칙

### 4.1 보안 요구사항

기존 X-Test-Mode 보안 패턴 준수 (`api/django/views/xtest/base.py`):

1. **헤더 검증**: `X-Test-Mode: chaos-monkey` 필수
2. **환경 검증**: `DEBUG=True` 또는 `CHAOS_ENABLED=true`
3. **프로덕션 차단**: `ENVIRONMENT=production` 시 완전 차단

### 4.2 구현 패턴

- `XTestModeMixin` 상속
- `check_chaos_permission()` 호출
- `collect_system_snapshot()` 스냅샷 기록
- 감사 로깅 포함

### 4.3 응답 형식

```json
{
    "status": "success|error|warning",
    "component": "dlq|replay|retry|rate_limit|idempotency",
    "action": "inject|observe|reset|trigger",
    "result": { ... },
    "timestamp": "ISO8601",
    "snapshot": { ... }
}
```

---

## 5. 구현 우선순위

### Phase 1: 핵심 (HIGH)

| 순위 | 컴포넌트 | 문서 | 이유 |
|------|---------|------|------|
| 1 | DLQ | 117 | CB와 직접 연동, Self-Healing 핵심 |
| 2 | Replay | 118 | DLQ와 쌍으로 동작 |

### Phase 2: 중요 (MEDIUM)

| 순위 | 컴포넌트 | 문서 | 이유 |
|------|---------|------|------|
| 3 | Retry | 119 | DLQ 라우팅 전 동작 |
| 4 | Rate Limiter | 120 | L1 동작 관찰 (bypass 아닌) |

### Phase 3: 보완 (LOW)

| 순위 | 컴포넌트 | 문서 | 이유 |
|------|---------|------|------|
| 5 | Idempotency | 121 | 엣지 케이스 테스트용 |
| 6 | 통합 | 122 | 전체 사이클 검증 |

---

## 6. 관련 코드 참조

### 6.1 기존 X-Test-Mode 구조

```
packages/selfhealing-python/src/selfhealing/api/django/views/xtest/
├── __init__.py          # Export 및 엔드포인트 문서화
├── base.py              # XTestModeMixin, 스냅샷, 힐링 이벤트
├── circuit_breaker.py   # CB 관련 7개 View
├── error_budget.py      # EB 주입 1개 View
├── snapshot.py          # 시스템 스냅샷 1개 View
└── observability.py     # 타임라인, Blast Radius 등 6개 View
```

### 6.2 타겟 서비스 구조

```
packages/selfhealing-python/src/selfhealing/services/
├── dlq/                 # DLQ 서비스 (Mixin 패턴)
├── replay_service.py    # Replay 서비스
├── retry_handler.py     # Retry Handler
├── idempotency_service.py # 멱등성 서비스
└── ...

packages/selfhealing-python/src/selfhealing/api/django/
└── rate_limit.py        # Rate Limiter (Middleware + 유틸)
```

---

## 7. 일정 (예상)

| Phase | 문서 | 예상 소요 |
|-------|------|----------|
| Phase 1 | 117, 118 | 2-3일 |
| Phase 2 | 119, 120 | 2일 |
| Phase 3 | 121, 122 | 1-2일 |
| **총합** | | **5-7일** |

---

## 8. 다음 단계

1. **문서 117**: DLQ 테스트 API 상세 설계
2. **문서 118**: Replay 테스트 API 상세 설계
3. **문서 119-121**: 개별 컴포넌트 설계
4. **문서 122**: 통합 테스트 시나리오

---

**관련 문서:**
- `docs/self_healing/19_CHAOS_PROOF_ROADMAP.md` - Stage 48-50 로드맵
- `docs/self_healing/02_ARCHITECTURE.md` - Self-Healing 아키텍처
- `docs/self_healing/04_DEAD_LETTER_QUEUE.md` - DLQ 상세
- `docs/self_healing/05_RETRY_BACKOFF.md` - Retry 상세
- `docs/self_healing/06_REPLAY_SYSTEM.md` - Replay 상세
