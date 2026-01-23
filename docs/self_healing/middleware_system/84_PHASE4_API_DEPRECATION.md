# Phase 4: Deprecated API 제거

> **상위 문서**: [80_DEPRECATION_CLEANUP_MASTER_PLAN.md](80_DEPRECATION_CLEANUP_MASTER_PLAN.md)  
> **예상 작업 시간**: 2-4시간  
> **난이도**: 하

---

## 1. 대상 목록

### 1.1 Deprecated API Endpoints

| Endpoint | View Class | 대체 Endpoint |
|----------|------------|--------------|
| `POST /api/self-healing/metrics/sync/` | `DeprecatedMetricSyncView` | `POST /api/self-healing/governance/reconcile/` |
| `GET /api/self-healing/metrics/drift-report/` | `DeprecatedDriftReportView` | `GET /api/self-healing/metrics/status/` |

### 1.2 관련 파일

| 파일 | 역할 |
|------|------|
| `selfhealing/api/django/views/governance/deprecated_views.py` | Deprecated View 구현 |
| `selfhealing/api/django/views/governance/__init__.py` | Re-export |
| `selfhealing/api/django/views/__init__.py` | Lazy import 등록 |
| `selfhealing/api/django/urls.py` | URL 패턴 |
| `tests/api/test_governance_api.py` | 테스트 코드 |

---

## 2. 제거 전 확인사항

### 2.1 사용 현황 로그 확인

Deprecated API가 실제로 호출되는지 확인:

```bash
# 로그에서 Deprecated 경고 검색
grep -rn "\[Deprecated\]" logs/*.log

# Prometheus 메트릭 확인 (있는 경우)
curl localhost:9090/api/v1/query?query=http_requests_total{path="/api/self-healing/metrics/sync/"}
```

### 2.2 클라이언트 확인

Deprecated API를 사용하는 클라이언트 검색:

```bash
# 코드베이스에서 사용처 검색
grep -rn "metrics/sync" --include="*.py" --include="*.js" --include="*.ts"
grep -rn "metrics/drift-report" --include="*.py" --include="*.js" --include="*.ts"

# Load test에서 사용처 검색
grep -rn "metrics/sync\|drift-report" load_tests/
```

---

## 3. 실행 순서

### Step 4.1: 테스트 코드 정리

**파일**: `tests/api/test_governance_api.py`

| 클래스 | 라인 | 작업 |
|--------|------|------|
| `TestDeprecatedMetricSyncView` | L434-470 | 제거 또는 skip 처리 |
| `TestDeprecatedDriftReportView` | L472-500 | 제거 또는 skip 처리 |

**옵션 A**: 테스트 제거
```python
# 해당 테스트 클래스 삭제
```

**옵션 B**: Skip 처리 (점진적 제거)
```python
@pytest.mark.skip(reason="Deprecated API removed in v2.5.0")
class TestDeprecatedMetricSyncView:
    ...
```

### Step 4.2: URL 패턴 제거

**파일**: `selfhealing/api/django/urls.py`

| 라인 | 현재 | 변경 |
|------|------|------|
| L130-131 | Import 문 | 제거 |
| L354-359 | URL 패턴 | 제거 |

**제거할 코드**:
- L130: `DeprecatedMetricSyncView,`
- L131: `DeprecatedDriftReportView,`
- L354-359: Deprecated 엔드포인트 URL 패턴

### Step 4.3: View 클래스 제거

**순서**:
1. `selfhealing/api/django/views/__init__.py` - Lazy import 제거
2. `selfhealing/api/django/views/governance/__init__.py` - Re-export 제거
3. `selfhealing/api/django/views/governance/deprecated_views.py` - 파일 삭제

### Step 4.4: 통합 테스트

```bash
# API 테스트 실행
pytest tests/api/ -v

# 전체 테스트
pytest tests/ -v

# URL 확인
python manage.py show_urls | grep self-healing
```

---

## 4. 상세 파일별 작업

### 4.1 tests/api/test_governance_api.py

| 라인 | 작업 |
|------|------|
| L28-29 | Import 제거 (`DeprecatedMetricSyncView`, `DeprecatedDriftReportView`) |
| L430-500 | 테스트 클래스 제거 |
| L522 | Deprecated API 확인 코드 수정 |

### 4.2 selfhealing/api/django/urls.py

| 라인 | 작업 |
|------|------|
| L130-131 | Import 제거 |
| L354 | 주석 제거 (`# Metric Sync API - DEPRECATED`) |
| L357-359 | URL 패턴 제거 |

### 4.3 selfhealing/api/django/views/__init__.py

| 라인 | 작업 |
|------|------|
| L160-161 | `_LAZY_IMPORTS` 에서 제거 |
| L335-336 | `TYPE_CHECKING` import 제거 |
| L460-461 | `__all__` 에서 제거 |

### 4.4 selfhealing/api/django/views/governance/__init__.py

| 라인 | 작업 |
|------|------|
| L53-56 | Import 제거 |
| L77-79 | `__all__` 에서 제거 |

### 4.5 deprecated_views.py

| 작업 |
|------|
| 파일 전체 삭제 |

---

## 5. Load Test 업데이트

### 5.1 관련 파일

| 파일 | 라인 | 내용 |
|------|------|------|
| `load_tests/utils/selfhealing/governance.py` | L352, L363 | Deprecated 메서드 |
| `load_tests/utils/selfhealing/dashboard.py` | L186-197 | Deprecated 메서드 |

### 5.2 작업

**옵션 A**: 메서드 제거
```python
# metrics_sync(), drift_report() 메서드 삭제
```

**옵션 B**: NotImplementedError 발생
```python
def metrics_sync(self):
    raise NotImplementedError("Deprecated: Use governance.reconcile() instead")
```

---

## 6. 완료 체크리스트

- [ ] 사용 현황 로그 확인 (호출 없음 확인)
- [ ] 클라이언트 사용처 검색 및 마이그레이션
- [ ] 테스트 코드 정리
- [ ] URL 패턴 제거
- [ ] View 클래스 파일 삭제
- [ ] `__init__.py` 정리
- [ ] Load test 업데이트
- [ ] 통합 테스트 통과

---

## 7. 롤백 계획

제거 후 문제 발생 시:

```bash
# Git에서 삭제된 파일 복원
git checkout HEAD~1 -- packages/selfhealing-python/src/selfhealing/api/django/views/governance/deprecated_views.py

# URL 패턴 복원
git checkout HEAD~1 -- packages/selfhealing-python/src/selfhealing/api/django/urls.py
```

---

## 8. 타임라인

| 단계 | 시점 | 작업 |
|------|------|------|
| 현재 | v2.4.x | Deprecated 경고 + Warning 헤더 |
| Phase 4 완료 | v2.5.0 | API 완전 제거 |
| 문서화 | v2.5.0 | CHANGELOG에 Breaking Change 명시 |

---

## 9. CHANGELOG 작성

```markdown
## [2.5.0] - YYYY-MM-DD

### Removed

- **BREAKING**: `POST /api/self-healing/metrics/sync/` 엔드포인트 제거
  - 대체: `POST /api/self-healing/governance/reconcile/` 사용
  
- **BREAKING**: `GET /api/self-healing/metrics/drift-report/` 엔드포인트 제거
  - 대체: `GET /api/self-healing/metrics/status/` 사용

### Migration Guide

기존 API 사용 코드를 다음과 같이 변경하세요:

| Before | After |
|--------|-------|
| `POST /metrics/sync/` | `POST /governance/reconcile/` |
| `GET /metrics/drift-report/` | `GET /metrics/status/` |
```

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| 1.0 | 2026-01-23 | 초안 작성 |
