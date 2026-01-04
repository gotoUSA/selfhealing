# 10. 미들웨어 시스템 전체 조사 계획

> **문서 버전**: 1.0.0
> **생성일**: 2026-01-04
> **목적**: Self-Healing 미들웨어 시스템의 전체 흐름 파악 및 체계적 조사

---

## 📋 목차

1. [조사 배경](#1-조사-배경)
2. [조사 목표](#2-조사-목표)
3. [현재 파악된 구조](#3-현재-파악된-구조)
4. [조사 단계별 계획](#4-조사-단계별-계획)
5. [조사 체크리스트](#5-조사-체크리스트)
6. [진행 상황 추적](#6-진행-상황-추적)

---

## 1. 조사 배경

### 1.1 왜 이 조사가 필요한가?

```
┌─────────────────────────────────────────────────────────────────┐
│                      현재 상황                                   │
├─────────────────────────────────────────────────────────────────┤
│  • 시스템이 점점 커지면서 전체 흐름 파악이 어려워짐               │
│  • 미들웨어들 간의 연결 관계가 복잡함                            │
│  • 일부 기능이 미들웨어에 포함되지 않고 독립적으로 존재           │
│  • 문서와 실제 코드 간 차이 가능성                               │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 조사를 통해 해결할 문제

| 문제 | 해결 방향 |
|------|-----------|
| 전체 미들웨어 개수 불명확 | 등록/미등록 미들웨어 완전 목록화 |
| 미들웨어 간 연결 관계 불명확 | 의존성 그래프 작성 |
| 흩어진 기능들 파악 어려움 | 독립 컴포넌트 목록화 |
| 시스템 동작 흐름 이해 부족 | 요청→응답 전체 흐름도 작성 |

---

## 2. 조사 목표

### 2.1 최종 산출물

```
┌─────────────────────────────────────────────────────────────────┐
│                     예상 산출물                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  A. 미들웨어 완전 목록표                                        │
│     - 등록된 미들웨어 (settings.py)                             │
│     - 미등록 미들웨어 (선택적 사용)                             │
│     - 각 미들웨어의 기능 요약                                   │
│                                                                 │
│  B. 의존성 관계도                                               │
│     - 연결된 미들웨어 그룹                                      │
│     - 독립적 미들웨어 목록                                      │
│     - 서비스 의존성 맵                                          │
│                                                                 │
│  C. 환경변수/Feature Flag 목록                                  │
│     - 활성화/비활성화 가능한 기능                               │
│     - 각 Flag의 영향 범위                                       │
│                                                                 │
│  D. 전체 시스템 흐름도                                          │
│     - 요청 → 미들웨어 체인 → 비즈니스 로직 → 응답               │
│     - 예외 발생 시 흐름                                         │
│     - DLQ/Circuit Breaker 동작 흐름                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 현재 파악된 구조

### 3.1 등록된 미들웨어 (settings/base.py)

> **파일**: `myproject/settings/base.py` Line 78~

| 순서 | 미들웨어 | 기능 | 상태 |
|:----:|----------|------|:----:|
| 1 | `trace_id_middleware` | 분산 추적 ID 부여 | ⬜ 조사 필요 |
| 2 | `HealthBridgeMiddleware` | DB 독립 헬스체크 | ⬜ 조사 필요 |
| 3 | `TieringMiddleware` | 비상 모드 트래픽 제어 | ⬜ 조사 필요 |
| 4 | `SelfHealingMiddleware` | Circuit Breaker + DLQ | ⬜ 조사 필요 |
| 5 | `ActorContextMiddleware` | 사용자 추적 | ⬜ 조사 필요 |
| 6 | Django Core Middlewares | 세션, 인증, CSRF 등 | ⬜ 조사 필요 |
| 7 | `HybridRateLimitMiddleware` | Rate Limiting | ⬜ 조사 필요 |
| 8 | `PoolCircuitBreakerMiddleware` | DB Pool 보호 | ⬜ 조사 필요 |
| 9 | Pool Timeout Middleware | SQLAlchemy 타임아웃 | ⬜ 조사 필요 |

### 3.2 관련 문서 현황

| 문서 | 설명 | 검토 상태 |
|------|------|:--------:|
| [00_INDEX.md](00_INDEX.md) | 전체 인덱스 | ⬜ |
| [01_MIDDLEWARE_GATEWAY.md](01_MIDDLEWARE_GATEWAY.md) | 미들웨어/게이트웨이 | ⬜ |
| [02_LOGIC_ENGINE.md](02_LOGIC_ENGINE.md) | 비즈니스 로직 | ⬜ |
| [03_INFRA_ADAPTER.md](03_INFRA_ADAPTER.md) | 인프라 어댑터 | ⬜ |
| [04_AUTONOMOUS_OPS.md](04_AUTONOMOUS_OPS.md) | 자율 운영 | ⬜ |
| [53_UNCONNECTED_FEATURES_ANALYSIS.md](../53_UNCONNECTED_FEATURES_ANALYSIS.md) | 미연결 기능 분석 | ⬜ |

### 3.3 주요 코드 위치

```
packages/selfhealing-python/src/selfhealing/
├── api/
│   └── django/
│       ├── middleware.py          # SelfHealingMiddleware, HealthBridgeMiddleware
│       ├── pool_circuit_breaker.py
│       ├── rate_limit.py          # HybridRateLimitMiddleware
│       ├── tiering/               # TieringMiddleware
│       └── audit_middleware.py
├── adapters/
│   └── fastapi/
│       └── middleware.py          # FastAPI용 미들웨어
├── audit/
│   └── trace/
│       └── trace_id_middleware    # Trace ID 미들웨어
└── services/
    └── throttle/                  # Throttle 서비스

myproject/
├── middleware/
│   └── actor_middleware.py        # ActorContextMiddleware
└── settings/
    └── base.py                    # MIDDLEWARE 설정
```

---

## 4. 조사 단계별 계획

### Phase 1: 기존 문서 검토 (1~2시간)

```
┌─────────────────────────────────────────────────────────────────┐
│  Phase 1: 문서 기반 이해                                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Step 1.1: 00_INDEX.md 정독                                     │
│            → 문서 간 관계 파악                                  │
│            → 전체 구조 개요 이해                                │
│                                                                 │
│  Step 1.2: 01_MIDDLEWARE_GATEWAY.md 정독                        │
│            → 미들웨어 파이프라인 이해                           │
│            → 등록/미등록 미들웨어 구분                          │
│                                                                 │
│  Step 1.3: 53_UNCONNECTED_FEATURES_ANALYSIS.md 정독             │
│            → 독립/미연결 기능 파악                              │
│            → 통합 필요 여부 판단                                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**산출물**: 문서 검토 체크리스트 완료

---

### Phase 2: 코드 기반 검증 (2~3시간)

```
┌─────────────────────────────────────────────────────────────────┐
│  Phase 2: 코드 vs 문서 검증                                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Step 2.1: settings/base.py MIDDLEWARE 분석                     │
│            → 실제 등록된 미들웨어 목록 추출                     │
│            → 각 미들웨어 클래스 위치 확인                       │
│                                                                 │
│  Step 2.2: 각 미들웨어 코드 분석                                │
│            → __call__ 메서드 동작 확인                          │
│            → 의존하는 서비스/컴포넌트 파악                      │
│                                                                 │
│  Step 2.3: 문서와 코드 차이점 기록                              │
│            → 누락된 기능 발견                                   │
│            → 업데이트 필요한 문서 목록화                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**산출물**: 미들웨어 완전 목록표, 문서 업데이트 목록

---

### Phase 3: 의존성 분석 (1~2시간)

```
┌─────────────────────────────────────────────────────────────────┐
│  Phase 3: 연결 관계 분석                                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Step 3.1: 미들웨어 간 호출 관계                                │
│            → A 미들웨어가 B 미들웨어 결과에 의존하는가?         │
│            → 실행 순서가 중요한 미들웨어 쌍                     │
│                                                                 │
│  Step 3.2: 서비스 의존성                                        │
│            → 어떤 미들웨어가 Redis에 의존?                      │
│            → 어떤 미들웨어가 DB에 의존?                         │
│            → 어떤 미들웨어가 Celery에 의존?                     │
│                                                                 │
│  Step 3.3: 독립 컴포넌트 식별                                   │
│            → 미들웨어 없이 동작하는 기능                        │
│            → Signal 기반 기능                                   │
│            → Management Command 기능                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**산출물**: 의존성 관계도, 독립 컴포넌트 목록

---

### Phase 4: Feature Flag 정리 (1시간)

```
┌─────────────────────────────────────────────────────────────────┐
│  Phase 4: 환경변수 기반 토글                                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Step 4.1: SELFHEALING_* 환경변수 전체 추출                     │
│            → 각 변수의 기본값                                   │
│            → 영향 받는 컴포넌트                                 │
│                                                                 │
│  Step 4.2: 활성화/비활성화 테스트                               │
│            → 비활성화 시 시스템 동작 확인                       │
│            → 의존성 있는 다른 기능 영향 파악                    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**산출물**: Feature Flag 전체 목록표

---

### Phase 5: 전체 흐름도 작성 (1~2시간)

```
┌─────────────────────────────────────────────────────────────────┐
│  Phase 5: 통합 흐름도                                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Step 5.1: 정상 요청 흐름도                                     │
│            → Request → 미들웨어 체인 → View → Response          │
│                                                                 │
│  Step 5.2: 예외 발생 흐름도                                     │
│            → DB 오류 시                                         │
│            → Rate Limit 초과 시                                 │
│            → Circuit Breaker Open 시                            │
│                                                                 │
│  Step 5.3: 자동 복구 흐름도                                     │
│            → DLQ 적재 → 리플레이 → 성공/실패                    │
│            → Circuit Breaker 상태 전이                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**산출물**: 시스템 흐름도 (Mermaid/ASCII)

---

## 5. 조사 체크리스트

### 5.1 Phase 1 체크리스트

- [ ] 00_INDEX.md 정독 완료
- [ ] 01_MIDDLEWARE_GATEWAY.md 정독 완료
- [ ] 02_LOGIC_ENGINE.md 정독 완료
- [ ] 03_INFRA_ADAPTER.md 정독 완료
- [ ] 04_AUTONOMOUS_OPS.md 정독 완료
- [ ] 53_UNCONNECTED_FEATURES_ANALYSIS.md 정독 완료
- [ ] 문서 간 관계 이해 완료

### 5.2 Phase 2 체크리스트

- [ ] settings/base.py MIDDLEWARE 목록 추출
- [ ] trace_id_middleware 분석
- [ ] HealthBridgeMiddleware 분석
- [ ] TieringMiddleware 분석
- [ ] SelfHealingMiddleware 분석
- [ ] ActorContextMiddleware 분석
- [ ] HybridRateLimitMiddleware 분석
- [ ] PoolCircuitBreakerMiddleware 분석
- [ ] 문서-코드 차이점 기록

### 5.3 Phase 3 체크리스트

- [x] 미들웨어 실행 순서 의존성 분석
- [x] Redis 의존 컴포넌트 목록
- [x] DB 의존 컴포넌트 목록
- [x] Celery 의존 컴포넌트 목록
- [x] Signal 기반 기능 목록
- [x] Management Command 목록

### 5.4 Phase 4 체크리스트

- [ ] SELFHEALING_* 환경변수 전체 추출
- [ ] 각 변수 기본값 확인
- [ ] 영향 범위 문서화

### 5.5 Phase 5 체크리스트

- [x] 정상 요청 흐름도 작성
- [x] 예외 발생 흐름도 작성
- [x] 자동 복구 흐름도 작성

---

## 6. 진행 상황 추적

### 6.1 전체 진행률

```
Phase 1: ░░░░░░░░░░ 0%   (문서 검토 - 선택적 진행)
Phase 2: ██████████ 100% ✅ 완료 (2026-01-04)
Phase 3: ██████████ 100% ✅ 완료 (2026-01-04)
Phase 4: ██████████ 100% ✅ 완료 (2026-01-04)
Phase 5: ██████████ 100% ✅ 완료 (2026-01-04)
─────────────────────
Total:   ████████░░ 80%
```

### 6.2 진행 로그

| 날짜 | Phase | 작업 내용 | 결과 |
|------|:-----:|----------|------|
| 2026-01-04 | - | 조사 계획 문서 생성 | 완료 |
| 2026-01-04 | 2 | 코드 기반 미들웨어 분석 | ✅ 완료 - 19개 미들웨어 파악 |
| 2026-01-04 | 4 | Feature Flag 전체 정리 | ✅ 완료 - 35개 환경변수 정리 |
| 2026-01-04 | 3 | 의존성 분석 | ✅ 완료 - Redis/DB/Celery 의존성 매핑 |
| 2026-01-04 | 5 | 전체 흐름도 작성 | ✅ 완료 - 3개 문서 (정상/예외/복구) |

### 6.3 발견된 이슈

| ID | 발견일 | 내용 | 상태 |
|:--:|--------|------|:----:|
| | | | |

### 6.4 다음 단계

```
┌─────────────────────────────────────────────────────────────────┐
│  완료된 작업                                                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ✅ Phase 2 완료: 11_PHASE2_MIDDLEWARE_ANALYSIS_RESULT.md       │
│  ✅ Phase 4 완료: 12_PHASE4_FEATURE_FLAG_RESULT.md              │
│  ✅ Phase 3 완료: 13_PHASE3_DEPENDENCY_ANALYSIS_RESULT.md       │
│  ✅ Phase 5 완료: 14, 15, 16 흐름도 문서 3개                    │
│     - 14_PHASE5_NORMAL_REQUEST_FLOW.md (정상 요청)              │
│     - 15_PHASE5_EXCEPTION_HANDLING_FLOW.md (예외 발생)          │
│     - 16_PHASE5_AUTO_RECOVERY_FLOW.md (자동 복구)               │
│                                                                 │
│  선택적 작업:                                                   │
│  1. Phase 1: 문서 검토 (기존 문서와 결과 비교)                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📚 관련 문서

- [00_INDEX.md](00_INDEX.md) - 미들웨어 시스템 인덱스
- [01_MIDDLEWARE_GATEWAY.md](01_MIDDLEWARE_GATEWAY.md) - 미들웨어 게이트웨이
- [11_PHASE2_MIDDLEWARE_ANALYSIS_RESULT.md](11_PHASE2_MIDDLEWARE_ANALYSIS_RESULT.md) - Phase 2 결과
- [12_PHASE4_FEATURE_FLAG_RESULT.md](12_PHASE4_FEATURE_FLAG_RESULT.md) - Phase 4 결과
- [13_PHASE3_DEPENDENCY_ANALYSIS_RESULT.md](13_PHASE3_DEPENDENCY_ANALYSIS_RESULT.md) - Phase 3 결과
- [17_PHASE5_FLOW_DIAGRAMS_SUMMARY.md](17_PHASE5_FLOW_DIAGRAMS_SUMMARY.md) - **Phase 5 흐름도 요약 (통합 뷰)** ⭐
- [14_PHASE5_NORMAL_REQUEST_FLOW.md](14_PHASE5_NORMAL_REQUEST_FLOW.md) - Phase 5 정상 요청 흐름도 (상세)
- [15_PHASE5_EXCEPTION_HANDLING_FLOW.md](15_PHASE5_EXCEPTION_HANDLING_FLOW.md) - Phase 5 예외 발생 흐름도 (상세)
- [16_PHASE5_AUTO_RECOVERY_FLOW.md](16_PHASE5_AUTO_RECOVERY_FLOW.md) - Phase 5 자동 복구 흐름도 (상세)
- [53_UNCONNECTED_FEATURES_ANALYSIS.md](../53_UNCONNECTED_FEATURES_ANALYSIS.md) - 미연결 기능 분석

---

*이 문서는 조사 진행에 따라 지속적으로 업데이트됩니다.*
