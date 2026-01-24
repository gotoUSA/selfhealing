# Configuration Migration Checklist

> 마이그레이션 진행 상황 추적을 위한 체크리스트

## 진행 상황 요약

| Phase | 상태 | 완료일 | 담당자 |
|-------|------|--------|--------|
| Phase 1 | ⬜ 대기 | - | - |
| Phase 2 | ⬜ 대기 | - | - |
| Phase 3 | ⬜ 대기 | - | - |
| Phase 4.1 | ⬜ 대기 | - | - |
| Phase 4.2 | ⬜ 대기 | - | - |
| Phase 4.3 | ⬜ 대기 | - | - |
| Phase 4.4 | ⬜ 대기 | - | - |
| Phase 5 | ⬜ 대기 | - | - |
| Phase 6 | ⬜ 대기 | - | - |

**상태**: ⬜ 대기 | 🔄 진행중 | ✅ 완료 | ❌ 보류

---

## Phase 1: 기존 인프라 확인

### 체크리스트

- [ ] `STORAGE_KEYS` 현재 목록 확인
- [ ] `CONFIG_CLASSES` 현재 목록 확인
- [ ] 새로 추가할 설정 카테고리 최종 결정
- [ ] 네이밍 규칙 문서화

### 산출물

- [ ] 현재 등록된 설정 타입 목록
- [ ] 추가할 설정 타입 최종 목록

---

## Phase 2: 새 설정 타입 정의

### Settings 클래스 생성

| 클래스 | 파일 | 상태 | 비고 |
|--------|------|------|------|
| `DashboardSettings` | `settings/dashboard.py` | ⬜ | |
| `RecoverySettings` | `settings/recovery.py` | ⬜ | |
| `BatchSettings` | `settings/batch.py` | ⬜ | |
| `AuditSettings` | `settings/audit_settings.py` | ⬜ | |
| `CeleryTaskSettings` | `settings/celery_task.py` | ⬜ | |
| `ApiViewSettings` | `settings/api_view.py` | ⬜ | |

### 필수 작업

- [ ] 각 클래스에 필드 정의 완료
- [ ] Field validation 규칙 추가
- [ ] 환경변수 prefix 설정
- [ ] `__init__.py` 내보내기 추가
- [ ] 단위 테스트 작성

---

## Phase 3: RuntimeConfigManager 등록

### constants.py 수정

| 작업 | 상태 | 비고 |
|------|------|------|
| import 문 추가 | ⬜ | |
| `STORAGE_KEYS` 추가 | ⬜ | 6개 키 |
| `CONFIG_CLASSES` 추가 | ⬜ | 6개 매핑 |

### 검증

- [ ] 기존 테스트 통과
- [ ] 새 설정 타입 조회 테스트
- [ ] 새 설정 타입 저장/로드 테스트

---

## Phase 4.1: CRITICAL 설정 마이그레이션

### 대상 파일

| 파일 | 설정 | 상태 | 비고 |
|------|------|------|------|
| `anti_flapping.py` | `anti_flapping_window` | ⬜ | |
| `anti_flapping.py` | `min_stability_period` | ⬜ | |
| `recovery_coordinator.py` | `max_recovery_attempts` | ⬜ | |
| `recovery_coordinator.py` | `recovery_cooldown` | ⬜ | |
| `escalation.py` | `escalation_threshold` | ⬜ | |

### 검증

- [ ] 각 파일 단위 테스트 통과
- [ ] 통합 테스트 통과
- [ ] 기존 동작과 동일 확인

---

## Phase 4.2: HIGH 설정 마이그레이션

### 대상 파일

| 파일 | 설정 | 상태 | 비고 |
|------|------|------|------|
| `dashboard_service.py` | `CACHE_TTL_SECONDS` | ⬜ | |
| `dashboard_service.py` | `CACHE_TTL_STATUS` | ⬜ | |
| `dashboard_service.py` | `CACHE_TTL_ACTIVITY` | ⬜ | |
| `health_penalty.py` | `_cache_ttl_seconds` | ⬜ | |
| `tracker.py` | `CACHE_TTL_SECONDS` | ⬜ | |
| 다수 파일 | `batch_size=100` | ⬜ | |
| 다수 task 파일 | `max_retries=3` | ⬜ | |

### 검증

- [ ] 각 파일 단위 테스트 통과
- [ ] Dashboard 기능 테스트
- [ ] Recovery 기능 테스트

---

## Phase 4.3: MEDIUM 설정 마이그레이션

### 대상 파일

| 파일 | 설정 | 상태 | 비고 |
|------|------|------|------|
| `async_logger.py` | `BATCH_SIZE` | ⬜ | |
| `async_logger.py` | `FLUSH_INTERVAL` | ⬜ | |
| API 뷰 파일들 | `default_limit` | ⬜ | |
| API 뷰 파일들 | `default_offset` | ⬜ | |
| 네트워크 어댑터 | `connection_timeout` | ⬜ | |

### 검증

- [ ] 로깅 기능 테스트
- [ ] API 페이징 테스트
- [ ] 네트워크 타임아웃 테스트

---

## Phase 4.4: LOW 설정 마이그레이션

### 대상 파일

| 파일 | 설정 | 상태 | 비고 |
|------|------|------|------|
| `pending_config.py` | `MAX_HISTORY` | ⬜ | |
| audit 관련 파일 | `retention_days` | ⬜ | |
| WAL 관련 파일 | `wal_max_size` | ⬜ | |

### 검증

- [ ] 설정 이력 기능 테스트
- [ ] 감사 로그 기능 테스트
- [ ] WAL 기능 테스트

---

## Phase 5: API 엔드포인트 추가

### Serializer 생성

| 클래스 | 상태 | 비고 |
|--------|------|------|
| `DashboardConfigSerializer` | ⬜ | |
| `RecoveryConfigSerializer` | ⬜ | |
| `BatchConfigSerializer` | ⬜ | |
| `AuditConfigSerializer` | ⬜ | |
| `TaskConfigSerializer` | ⬜ | |
| `ApiViewConfigSerializer` | ⬜ | |

### ViewSet 생성

| 클래스 | 엔드포인트 | 상태 | 비고 |
|--------|-----------|------|------|
| `DashboardConfigViewSet` | `/api/v1/config/dashboard/` | ⬜ | |
| `RecoveryConfigViewSet` | `/api/v1/config/recovery/` | ⬜ | |
| `BatchConfigViewSet` | `/api/v1/config/batch/` | ⬜ | |
| `AuditConfigViewSet` | `/api/v1/config/audit/` | ⬜ | |
| `TaskConfigViewSet` | `/api/v1/config/task/` | ⬜ | |
| `ApiViewConfigViewSet` | `/api/v1/config/api-view/` | ⬜ | |

### 검증

- [ ] 각 엔드포인트 CRUD 테스트
- [ ] 권한 검증 테스트
- [ ] OpenAPI 스키마 생성 확인

---

## Phase 6: 테스트 및 검증

### 단위 테스트

| 테스트 | 상태 | 비고 |
|--------|------|------|
| Settings 클래스 테스트 | ⬜ | |
| RuntimeConfigManager 테스트 | ⬜ | |
| LayeredProvider 테스트 | ⬜ | |
| API 엔드포인트 테스트 | ⬜ | |

### 통합 테스트

| 시나리오 | 상태 | 비고 |
|----------|------|------|
| 설정 변경 → 즉시 반영 | ⬜ | |
| ENV 오버라이드 | ⬜ | |
| Request 오버라이드 | ⬜ | |
| 재시작 후 설정 유지 | ⬜ | |

### 부하 테스트

| 시나리오 | 상태 | 비고 |
|----------|------|------|
| 설정 읽기 성능 | ⬜ | |
| 동시 변경 | ⬜ | |
| 대량 설정 | ⬜ | |

---

## 배포 체크리스트

### Staging 배포

- [ ] 모든 테스트 통과
- [ ] Staging 환경 배포
- [ ] Staging 환경 검증 (24시간)
- [ ] 성능 메트릭 확인
- [ ] 로그 이상 없음 확인

### Production 배포

- [ ] 롤백 절차 확인
- [ ] Production 배포
- [ ] 모니터링 (1시간)
- [ ] 기능 정상 동작 확인
- [ ] 메트릭 정상 확인

---

## 이슈 트래킹

| 이슈 번호 | 설명 | 상태 | 해결일 |
|-----------|------|------|--------|
| - | - | - | - |

---

## 변경 이력

| 날짜 | 변경 내용 | 작성자 |
|------|----------|--------|
| 2026-01-24 | 초안 작성 | - |
