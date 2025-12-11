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

## 📝 총 예상 소요 시간

| Phase | 작업 수 | 예상 시간 |
|-------|---------|-----------|
| Phase 1 (핵심) | 3개 | 3일 |
| Phase 2 (테스트) | 3개 | 3일 |
| Phase 3 (확장) | 3개 | 2.5일 |
| **합계** | **9개** | **약 8-9일** |
