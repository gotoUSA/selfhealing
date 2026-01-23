# Deprecated/Re-export 정리 마스터 플랜

> **문서 버전**: 1.0  
> **작성일**: 2026-01-23  
> **목적**: 시스템 내 모든 Deprecated 및 불필요한 Re-export 패턴 정리

---

## 1. 개요

### 1.1 문서 구성

| 문서 | 내용 |
|------|------|
| **80_DEPRECATION_CLEANUP_MASTER_PLAN.md** (현재) | 마스터 플랜, 전체 목록, 실행 순서 |
| [81_PHASE1_DEPRECATED_FUNCTIONS.md](81_PHASE1_DEPRECATED_FUNCTIONS.md) | Phase 1: Deprecated 함수/메서드 정리 |
| [82_PHASE2_DEPRECATED_MODULES.md](82_PHASE2_DEPRECATED_MODULES.md) | Phase 2: Deprecated 모듈 제거 |
| [83_PHASE3_REEXPORT_CLEANUP.md](83_PHASE3_REEXPORT_CLEANUP.md) | Phase 3: 불필요 Re-export 정리 |
| [84_PHASE4_API_DEPRECATION.md](84_PHASE4_API_DEPRECATION.md) | Phase 4: Deprecated API 제거 |

### 1.2 작업 원칙

1. **테스트 우선**: 각 변경 전 관련 테스트 확인 및 업데이트
2. **점진적 적용**: Phase별로 순차 진행, 각 Phase 완료 후 통합 테스트
3. **롤백 가능**: 각 변경은 독립적 커밋, 문제 시 롤백 가능
4. **문서화**: 변경 내용 CHANGELOG 기록

---

## 2. 전체 현황 요약

### 2.1 Deprecated 항목 (제거 대상)

| 카테고리 | 개수 | 우선순위 |
|----------|------|----------|
| Model 메서드 | 7개 | P1 |
| Service 함수 | 5개 | P1 |
| 모듈 전체 | 3개 | P2 |
| API Endpoints | 2개 | P3 |
| 클래스 별칭 | 4개 | P2 |
| 상수 | 4개 | P2 |

### 2.2 Re-export 항목 (정리 대상)

| 카테고리 | 유지 | 정리 | 설명 |
|----------|------|------|------|
| Public API (`__init__.py`) | ✅ | - | 패키지 진입점 |
| Wrapper 모듈 | - | ⚠️ | 검토 후 결정 |
| 편의용 Re-export | - | ✅ | 직접 import로 전환 |

---

## 3. 실행 순서 (Execution Order)

```
┌─────────────────────────────────────────────────────────────────────┐
│                         실행 순서 다이어그램                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  [준비 단계]                                                         │
│  └─ Step 0: 현재 테스트 상태 확인 (pytest 실행)                       │
│                                                                     │
│  [Phase 1] Deprecated 함수/메서드 정리 (1-2일)                        │
│  ├─ Step 1.1: DeprecationWarning 표준화 추가                         │
│  ├─ Step 1.2: 사용처 검색 및 마이그레이션                              │
│  ├─ Step 1.3: 테스트 업데이트                                        │
│  └─ Step 1.4: 통합 테스트 실행                                       │
│                                                                     │
│  [Phase 2] Deprecated 모듈/클래스 정리 (1일)                          │
│  ├─ Step 2.1: 모듈 사용처 검색                                       │
│  ├─ Step 2.2: 직접 import로 변경                                     │
│  ├─ Step 2.3: Deprecated 모듈에 강화 경고 추가                        │
│  └─ Step 2.4: 통합 테스트 실행                                       │
│                                                                     │
│  [Phase 3] 불필요 Re-export 정리 (2-3일)                              │
│  ├─ Step 3.1: Re-export 사용처 분석                                  │
│  ├─ Step 3.2: shopping 앱 Re-export 제거                             │
│  ├─ Step 3.3: selfhealing wrapper 모듈 정리                          │
│  └─ Step 3.4: 통합 테스트 실행                                       │
│                                                                     │
│  [Phase 4] Deprecated API 제거 (1일)                                 │
│  ├─ Step 4.1: API 사용 현황 로그 확인                                 │
│  ├─ Step 4.2: Deprecated API View 제거                               │
│  ├─ Step 4.3: URL 패턴 정리                                          │
│  └─ Step 4.4: API 테스트 업데이트                                    │
│                                                                     │
│  [완료 단계]                                                         │
│  ├─ Step 5: 전체 테스트 실행                                         │
│  ├─ Step 6: CHANGELOG 업데이트                                       │
│  └─ Step 7: 코드 리뷰 및 머지                                         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Phase별 상세 체크리스트

### Phase 1: Deprecated 함수/메서드 (81_PHASE1_DEPRECATED_FUNCTIONS.md) ✅ 완료

- [x] Return 모델 메서드 7개 → ReturnService 호출로 전환 + DeprecationWarning
- [x] `track_replay()` 데코레이터 → 이미 DeprecationWarning 있음
- [x] `store_with_forensic_context()` → 이미 NotImplementedError 발생
- [x] `get_circuit_breaker_status()` → `get_fault_detector_status()` 전환 + DeprecationWarning
- [x] `reset_circuit_breaker()` → `reset_fault_detector()` 전환 + DeprecationWarning
- [x] `_should_bypass_for_xtest()` → 이미 DeprecationWarning 있음

### Phase 2: Deprecated 모듈/클래스 (82_PHASE2_DEPRECATED_MODULES.md) ✅ 완료

- [x] `selfhealing.audit.hash_chain_performance` → 이미 DeprecationWarning 있음 (유지)
- [x] `selfhealing.services.factory` (단일 모듈) → DeprecationWarning 추가
- [x] `selfhealing.metrics.jitter` → 이미 DeprecationWarning 있음 (유지)
- [x] `CircuitState` → `GateFaultState` + `__getattr__` 패턴 DeprecationWarning
- [x] `InMemoryCircuitBreaker` → `GateFaultDetector` + `__getattr__` 패턴 DeprecationWarning
- [x] 상수 4개 → `_get_notification_limits()` 사용 + `__getattr__` 패턴 DeprecationWarning

### Phase 3: Re-export 정리 (83_PHASE3_REEXPORT_CLEANUP.md)

- [ ] `shopping/tasks/self_healing_tasks.py` → 직접 import
- [ ] `shopping/tasks/dlq_replay_tasks.py` → 직접 import
- [ ] `shopping/tasks/drift_detection_tasks.py` → 직접 import
- [ ] `shopping/models/point_history.py` → 직접 import
- [ ] `selfhealing.services.dlq_service` wrapper 정리
- [ ] `selfhealing.services.audit_helpers` wrapper 정리
- [ ] `selfhealing.services.circuit_breaker_service` wrapper 정리

### Phase 4: Deprecated API 제거 (84_PHASE4_API_DEPRECATION.md)

- [ ] `DeprecatedMetricSyncView` 제거
- [ ] `DeprecatedDriftReportView` 제거
- [ ] URL 패턴에서 deprecated 엔드포인트 제거
- [ ] 관련 테스트 업데이트/제거

---

## 5. 의존성 그래프

```
Phase 1 (함수/메서드)
    │
    ├─ 독립적 작업 가능
    │
    v
Phase 2 (모듈/클래스)
    │
    ├─ Phase 1 완료 필요 (일부 의존)
    │
    v
Phase 3 (Re-export)
    │
    ├─ Phase 1, 2 완료 권장
    │
    v
Phase 4 (API)
    │
    └─ 독립적 (언제든 진행 가능)
```

---

## 6. 예상 작업량

| Phase | 예상 시간 | 난이도 | 리스크 |
|-------|----------|--------|--------|
| Phase 1 | 4-8시간 | 중 | 낮음 |
| Phase 2 | 2-4시간 | 중 | 낮음 |
| Phase 3 | 8-12시간 | 상 | 중간 |
| Phase 4 | 2-4시간 | 하 | 낮음 |
| **총계** | **16-28시간** | - | - |

---

## 7. 롤백 전략

각 Phase는 독립적 브랜치에서 작업:

```bash
# Phase별 브랜치
git checkout -b refactor/phase1-deprecated-functions
git checkout -b refactor/phase2-deprecated-modules
git checkout -b refactor/phase3-reexport-cleanup
git checkout -b refactor/phase4-deprecated-api
```

문제 발생 시:
```bash
git revert <commit-hash>
```

---

## 8. 성공 기준

- [ ] 모든 테스트 통과 (pytest)
- [ ] DeprecationWarning 0개 (pytest -W error::DeprecationWarning)
- [ ] Import 순환 없음
- [ ] 코드 커버리지 유지 또는 향상

---

## 변경 이력

| 버전 | 날짜 | 작성자 | 내용 |
|------|------|--------|------|
| 1.0 | 2026-01-23 | - | 초안 작성 |
| 1.1 | 2026-01-23 | - | Phase 1 완료 |
| 1.2 | 2026-01-23 | - | Phase 2 완료 - Deprecated 모듈/클래스에 DeprecationWarning 추가 |
