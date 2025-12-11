# Stage 23-28 구현 계획 개요

## 📋 목차

| 문서 | 내용 | 예상 시간 |
|------|------|-----------|
| `STAGE_23_CLOCK_SKEW.md` | Clock Skew / NTP 드리프트 테스트 | 1일 |
| `STAGE_24_PARTIAL_PARTITION.md` | Partial Network Partition 테스트 | 1일 |
| `STAGE_25_TLS_FAILURE.md` | Certificate 만료 / TLS 실패 테스트 | 1일 |
| `STAGE_26_CONNECTION_POOL.md` | DB Connection Pool 고갈 테스트 | 1일 |
| `STAGE_27_GRACEFUL_SHUTDOWN.md` | Graceful Shutdown 구현 | 1일 |
| `STAGE_28_PACKAGE_CLEANUP.md` | selfhealing 패키지 정리 (4 phases) | 3-4일 |

---

## 🎯 SaaS 제품 철학

```
✅ 외부 미들웨어 의존도 0
✅ 확장성 / 이식성
✅ 안정성 / 완전성
✅ 프레임워크 독립적 (Django, FastAPI, Flask 모두 지원)
```

---

## 📊 우선순위 및 일정

### Phase 1: 핵심 안정성 (3-4일)
| Stage | 작업 | 예상 시간 |
|-------|------|-----------|
| 26 | DB Pool Watchdog | 1일 |
| 27 | Graceful Shutdown | 1일 |
| 28-1 | services/ Django fallback 정리 | 1일 |

### Phase 2: 테스트 커버리지 (3일)
| Stage | 작업 | 예상 시간 |
|-------|------|-----------|
| 23 | Clock Skew / TimeProvider | 1일 |
| 24 | Partial Network Partition | 1일 |
| 25 | TLS 실패 테스트 | 1일 |

### Phase 3: 패키지 확장 (2-3일)
| Stage | 작업 | 예상 시간 |
|-------|------|-----------|
| 28-2 | FastAPI 어댑터 | 1일 |
| 28-3 | SQLAlchemy 어댑터 | 1일 |
| 28-4 | In-Memory Repository | 0.5일 |

---

## 🎯 추천 작업 순서

```
1. Stage 26 → 2. Stage 27 → 3. Stage 28-1
   ↓
4. Stage 23 → 5. Stage 24 → 6. Stage 25
   ↓
7. Stage 28-2 → 8. Stage 28-3 → 9. Stage 28-4
```

---

## 🏗️ 현재 패키지 구조

```
packages/selfhealing-python/
├── src/selfhealing/
│   ├── core/           # ✅ 순수 Python (완료)
│   ├── interfaces/     # ✅ 추상 인터페이스 (완료)
│   ├── adapters/       # ⚠️ 확장 필요
│   │   ├── django/     # ✅ 완료
│   │   ├── celery/     # ⚠️ Django 분리 필요
│   │   ├── fastapi/    # 🔴 신규 필요
│   │   └── sqlalchemy/ # 🔴 신규 필요
│   └── services/       # ⚠️ Django fallback 정리 필요
```

---

## 📝 작업 시작 방법

새 세션에서 각 Stage 문서를 열고:

```
"STAGE_XX 문서대로 구현해줘"
```

---

## ✅ 완료 체크리스트

### 핵심 기능
- [ ] Stage 26: Connection Pool Watchdog
- [ ] Stage 27: Graceful Shutdown

### 테스트 시나리오
- [ ] Stage 23: Clock Skew 테스트
- [ ] Stage 24: Partial Partition 테스트
- [ ] Stage 25: TLS 실패 테스트

### 패키지 정리 및 확장
- [ ] Stage 28-1: services/ Django fallback 정리
- [ ] Stage 28-2: FastAPI 어댑터
- [ ] Stage 28-3: SQLAlchemy 어댑터
- [ ] Stage 28-4: In-Memory Repository

---

## 🧪 Locust 부하/카오스 테스트 계획

각 Stage별 Locust 테스트 시나리오입니다. `load_tests/scenarios/` 디렉토리에 생성됩니다.

### Stage 23: Clock Skew 테스트
**파일**: `stage23_clock_skew.py`
```
목표: 시간 동기화 문제 시 시스템 복원력 검증
시나리오:
  - 서버 간 시간 차이 발생 시 타임스탬프 검증
  - JWT 토큰 만료 시간 불일치 처리
  - 분산 락 타임아웃 정확성
부하: 100 users, 5분
검증:
  - 시간 기반 검증 에러율 < 1%
  - 타임아웃 정확성 유지
```

### Stage 24: Partial Network Partition 테스트
**파일**: `stage24_partial_partition.py`
```
목표: 부분 네트워크 단절 시 복원력 검증
시나리오:
  - DB 연결은 가능하지만 Redis 연결 불가
  - 외부 API(토스) 연결만 단절
  - 특정 서비스만 응답 지연
부하: 50 users, 3분
검증:
  - 단절된 서비스 fallback 동작
  - Circuit Breaker 정상 작동
  - 복구 후 정상 서비스 재개
```

### Stage 25: TLS/Certificate 실패 테스트
**파일**: `stage25_tls_failure.py`
```
목표: 인증서 만료/TLS 핸드셰이크 실패 시 복원력 검증
시나리오:
  - 외부 API SSL 인증서 만료 시뮬레이션
  - TLS 버전 불일치
  - 인증서 체인 검증 실패
부하: 30 users, 2분
검증:
  - TLS 에러 시 적절한 fallback
  - 에러 로깅 및 알림
  - 재시도 로직 정상 동작
```

### Stage 26: Connection Pool 고갈 테스트 ✅
**파일**: `stage26_connection_pool.py`
```
목표: DB Connection Pool 고갈 시 복원력 검증 (토스 40분 장애 재현)
시나리오:
  - 대량 동시 DB 쿼리로 Pool 고갈 유도
  - Connection Leak 시뮬레이션
  - Pool Watchdog 자동 복구 검증
부하: 200 users, 5분 (Spike 패턴)
검증:
  - Pool 고갈 감지 시간 < 10초
  - Leak 연결 자동 종료
  - Pool 확장 및 축소 정상 동작
  - 에러율 급증 후 복구 확인
```

### Stage 27: Graceful Shutdown 테스트 ✅
**파일**: `stage27_graceful_shutdown.py`
```
목표: 배포/재시작 시 요청 유실 방지 검증 (쿠팡 롤링 배포 장애 재현)
시나리오:
  - 진행 중인 요청 있을 때 SIGTERM 전송
  - Drain 기간 동안 요청 완료 확인
  - 타임아웃 시 강제 종료 동작
부하: 100 users, 3분 + Shutdown 시그널
검증:
  - 진행 중 요청 완료율 > 99%
  - 새 요청 거부 (503) 정상 동작
  - Drain 완료 후 정상 종료
```

### Stage 28: 패키지 정리 테스트
**파일**: `stage28_package_validation.py`
```
목표: 리팩토링된 패키지 정상 동작 검증
시나리오:
  - Django 어댑터 기능 검증
  - FastAPI 어댑터 기능 검증 (신규)
  - SQLAlchemy 어댑터 기능 검증 (신규)
부하: 50 users, 3분
검증:
  - 모든 어댑터 정상 동작
  - 프레임워크 간 호환성
```

---

## 📝 총 예상 소요 시간

| Phase | 작업 수 | 예상 시간 |
|-------|---------|-----------|
| Phase 1 (핵심) | 3개 | 3일 |
| Phase 2 (테스트) | 3개 | 3일 |
| Phase 3 (확장) | 3개 | 2.5일 |
| **합계** | **9개** | **약 8-9일** |
