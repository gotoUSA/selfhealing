# 107. 하드코딩된 설정값 리팩토링 전체 구현 로드맵

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: 마스터 플랜
- **관련 문서**: 102-106번 문서

---

## 1. 개요

`packages/selfhealing-python/src/selfhealing/` 패키지 전반에서 발견된 하드코딩된 설정값들을 Pydantic Settings 체계로 마이그레이션하는 전체 로드맵.

---

## 2. 리팩토링 대상 요약

| 모듈 | 파일 수 | 설정값 수 | 상세 문서 |
|-----|--------|----------|----------|
| Core | 8 | 약 45개 | 103번 문서 |
| Coordination | 6+ | 약 50개 | 104번 문서 |
| Audit | 12 | 약 35개 | 105번 문서 |
| API | 5 | 약 25개 | 106번 문서 |
| Chaos (실험적) | 10+ | 약 60개 | 저우선순위 |
| **총계** | **40+** | **약 215개** | - |

---

## 3. 전체 구현 순서

### Phase 1: 기반 작업 (1주차)

#### Week 1, Day 1-2: Settings 인프라 확장
1. 기존 `settings/` 모듈 구조 분석
2. 환경변수 네이밍 규칙 표준화
3. 공통 validator 및 유틸리티 추가

#### Week 1, Day 3-5: Core 모듈 리팩토링
4. `settings/runtime_feedback.py` 확장/생성
5. `settings/auto_rollback.py` 확장/생성
6. `settings/adaptive_jitter.py` 확장/생성
7. `settings/safety_bounds.py` 확장/생성
8. Core 모듈 각 파일 settings 연동

---

### Phase 2: 서비스 레이어 (2주차)

#### Week 2, Day 1-3: Coordination 서비스 리팩토링
9. `settings/recovery_tasks.py` 확장
10. `settings/critical_worker.py` 확장
11. `settings/regional_policy.py` 확장
12. Coordination 모듈 각 파일 settings 연동

#### Week 2, Day 4-5: Namespace Emergency 리팩토링
13. `settings/namespace_emergency.py` 확장
14. Namespace Emergency 모듈 settings 연동

---

### Phase 3: Audit 시스템 (3주차)

#### Week 3, Day 1-2: Audit Settings 확장
15. `settings/audit_integrity.py` 확장 (major)
16. `settings/hash_chain.py` 생성
17. 기타 audit 관련 settings 확장

#### Week 3, Day 3-5: Audit 모듈 리팩토링
18. `audit/integrity/` 모듈 settings 연동
19. `audit/resilience/` 모듈 settings 연동
20. `audit/` 루트 레벨 모듈 settings 연동

---

### Phase 4: API 레이어 (4주차 전반)

#### Week 4, Day 1-2: API Settings 생성
21. `settings/api_rate_limit.py` 생성
22. `settings/api_middleware.py` 생성

#### Week 4, Day 3-4: API 모듈 리팩토링
23. `api/django/` 모듈 settings 연동
24. `api/fastapi/` 모듈 검토 및 적용 (해당 시)

---

### Phase 5: 마무리 (4주차 후반)

#### Week 4, Day 5: 테스트 및 문서화
25. 전체 단위 테스트 업데이트
26. 통합 테스트 검증
27. 환경변수 문서 업데이트
28. 마이그레이션 가이드 완성

---

## 4. 우선순위 기준

### P0 - 즉시 필요 (1-2주차)
- 운영 환경에서 자주 변경이 필요한 설정
- 장애 대응 시 조정이 필요한 임계값
- 환경별로 다른 값이 필요한 설정

### P1 - 중요 (3주차)
- 규제 준수 관련 설정 (Audit 모듈)
- 보안 관련 설정 (Rate Limit 등)

### P2 - 보통 (4주차)
- 튜닝 옵션으로 유용한 설정
- 드물게 변경되는 설정

### P3 - 낮음 (향후)
- 실험적 기능 설정 (Chaos 모듈)
- 개발 전용 설정

---

## 5. 파일별 구현 순서 상세

### Core 모듈 (103번 문서 참조)
```
1. core/runtime_feedback.py
2. core/auto_rollback_guard.py
3. core/adaptive_jitter.py
4. core/safety_bounds.py
5. core/state_cache.py
6. core/canary_slot_manager.py
7. core/circuit_breaker_integration.py
8. core/error_rate_calculator.py
```

### Coordination 모듈 (104번 문서 참조)
```
1. services/coordination/recovery_tasks.py
2. services/coordination/critical_worker.py
3. services/coordination/regional_recovery_policy.py
4. services/coordination/redis_key_guard.py
5. services/coordination/scheduled_task_registry.py
6. services/coordination/leader_election.py
```

### Audit 모듈 (105번 문서 참조)
```
1. audit/integrity/sequence.py
2. audit/integrity/cold_storage.py
3. audit/hash_chain_safety.py
4. audit/cascade_auditor.py
5. audit/integrity/anchor.py
6. audit/resilience/buffer.py
7. audit/integrity/cross_cluster_linker.py
8. audit/integrity/health_score.py
9. audit/audit_watchdog.py
10. audit/cascade_load_shedding.py
11. audit/config.py
12. audit/backends/s3_worm.py
```

### API 모듈 (106번 문서 참조)
```
1. api/django/rate_limit.py
2. api/django/middleware_health.py
3. api/django/circuit_breaker_middleware.py
4. api/django/timeout_middleware.py
5. api/fastapi/dependencies.py (해당 시)
```

---

## 6. 환경변수 네이밍 규칙

### 표준 Prefix
```
SELFHEALING_               # 최상위
SELFHEALING_CORE_          # Core 모듈
SELFHEALING_COORD_         # Coordination 서비스
SELFHEALING_AUDIT_         # Audit 모듈
SELFHEALING_API_           # API 모듈
```

### 명명 패턴
```
SELFHEALING_{모듈}_{기능}_{설정명}

예시:
SELFHEALING_CORE_ROLLBACK_ERROR_RATE_THRESHOLD
SELFHEALING_AUDIT_INTEGRITY_PENDING_TTL
SELFHEALING_API_RATE_DEFAULT_PER_MINUTE
```

---

## 7. 예상 총 소요 시간

| Phase | 기간 | 핵심 작업 |
|-------|-----|----------|
| Phase 1 | 5일 | 기반 작업 + Core |
| Phase 2 | 5일 | Coordination + Namespace |
| Phase 3 | 5일 | Audit 시스템 |
| Phase 4 | 3일 | API 레이어 |
| Phase 5 | 2일 | 마무리 |
| **총계** | **20일 (약 4주)** | - |

### 병렬 작업 시
- 2명 동시 작업: 약 2.5주
- 3명 동시 작업: 약 2주

---

## 8. 위험 관리

### 고위험 작업
1. **Audit 모듈 변경**: 규제 준수 영향 → 철저한 테스트 필요
2. **Rate Limit 변경**: 서비스 가용성 영향 → 단계적 롤아웃
3. **Circuit Breaker 변경**: 장애 전파 위험 → 스테이징 충분한 검증

### 롤백 계획
- 각 Phase 완료 시 릴리스 태그 생성
- 환경변수 미설정 시 기본값으로 기존 동작 보장
- 문제 발생 시 해당 Phase 이전으로 롤백 가능

---

## 9. 테스트 전략

### 단위 테스트
- 각 settings 모듈 환경변수 파싱 테스트
- 값 범위 검증 테스트
- 기본값 동작 테스트

### 통합 테스트
- 환경변수 조합 시나리오 테스트
- 모듈 간 설정 일관성 테스트

### E2E 테스트
- 스테이징 환경 전체 시나리오 테스트
- 환경변수 변경 시 동작 검증

---

## 10. 문서화 계획

### 생성할 문서
1. **환경변수 레퍼런스**: 전체 환경변수 목록 및 설명
2. **마이그레이션 가이드**: 버전 업그레이드 시 주의사항
3. **환경별 설정 예시**: 개발/스테이징/프로덕션 설정 템플릿
4. **트러블슈팅 가이드**: 흔한 설정 오류 및 해결 방법

### 업데이트할 문서
1. `README.md` - 설정 섹션 추가
2. `docs/` 하위 기존 문서 업데이트
3. 코드 내 docstring 업데이트

---

## 11. 완료 기준

### 기술적 완료
- [ ] 모든 하드코딩된 설정값이 Pydantic Settings로 마이그레이션
- [ ] 환경변수 없이 기본값으로 기존 동작 100% 유지
- [ ] 모든 단위 테스트 통과
- [ ] 모든 통합 테스트 통과

### 문서화 완료
- [ ] 환경변수 레퍼런스 문서 완성
- [ ] 마이그레이션 가이드 완성
- [ ] 환경별 설정 예시 완성

### 운영 준비 완료
- [ ] 스테이징 환경 검증 완료
- [ ] 모니터링 대시보드 업데이트
- [ ] 알림 규칙 업데이트
- [ ] 롤백 계획 문서화

---

## 12. 관련 문서 인덱스

| 문서 번호 | 문서명 | 내용 |
|----------|-------|------|
| 94 | HARDCODED_CONFIG_REFACTORING_OVERVIEW | 초기 분석 개요 |
| 95-101 | (기존 문서들) | 초기 분석 상세 |
| 102 | HARDCODED_CONFIG_FINAL_AUDIT | 최종 감사 보고서 |
| 103 | HARDCODED_CONFIG_CORE_REFACTORING | Core 모듈 계획 |
| 104 | HARDCODED_CONFIG_COORDINATION_REFACTORING | Coordination 계획 |
| 105 | HARDCODED_CONFIG_AUDIT_REFACTORING | Audit 모듈 계획 |
| 106 | HARDCODED_CONFIG_API_REFACTORING | API 모듈 계획 |
| **107** | **HARDCODED_CONFIG_MASTER_ROADMAP** | **전체 로드맵 (본 문서)** |
