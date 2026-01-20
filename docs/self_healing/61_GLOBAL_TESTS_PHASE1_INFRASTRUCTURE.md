# 61. Phase 1: 인프라 정리

> **선행 문서**: 60_GLOBAL_TESTS_REFACTORING_PLAN.md  
> **목표**: Docker Compose 정비, 포트 통일, 환경변수 표준화  
> **예상 소요**: 2-3시간

---

## 1. 목표

1. Docker Compose 파일들의 포트 통일
2. Celery worker 서비스 추가 (비동기 테스트 지원)
3. 환경변수 기반 연결 표준화
4. `tests/factories/constants.py` 포트 설정 수정

---

## 2. 작업 목록

### 2.1 docker-compose.test.yml 수정

**위치**: `docker-compose.test.yml`

**현재 상태**:
- Redis 포트: 16379 (외부) → 6379 (내부)
- Celery worker 없음
- test-chaos, test, test-inmemory, test-sqlalchemy 서비스만 존재

**수정 사항**:

| 항목 | 변경 전 | 변경 후 |
|------|--------|--------|
| Redis 포트 | 16379:6379 | 6379:6379 |
| Celery worker | 없음 | 추가 |
| Celery beat | 없음 | 추가 (선택) |

**추가할 서비스**:
- `celery-worker`: 비동기 태스크 실행용
- 환경변수: `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`

---

### 2.2 tests/factories/constants.py 수정

**위치**: `tests/factories/constants.py`

**현재 상태**:
- `RedisTestConfig.DEFAULT_PORT = 6379`
- `RedisTestConfig.TEST_PORT` 없음 (또는 다름)

**수정 사항**:
- 포트 상수 통일
- Docker Compose 환경과 로컬 환경 구분

**추가할 상수**:

| 상수 | 값 | 용도 |
|------|---|------|
| `DOCKER_REDIS_PORT` | 6379 | Docker Compose 내부 |
| `LOCAL_REDIS_PORT` | 6379 | 로컬 Docker 연결 |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Celery 브로커 |

---

### 2.3 tests/conftest.py 수정

**위치**: `tests/conftest.py`

**현재 상태**:
- `redis_url = os.environ.get("REDIS_URL", "redis://localhost:16379/0")`
- 16379 포트 기본값

**수정 사항**:
- 기본 포트를 6379로 변경
- 환경변수 `REDIS_HOST`, `REDIS_PORT` 분리 지원

---

### 2.4 하드코딩된 포트 수정

**대상 파일**:

| 파일 | 현재 | 수정 |
|------|------|------|
| `tests/integration/selfhealing/test_canary_integration.py` | `port=6379` 하드코딩 | `RedisTestConfig` 사용 |
| `tests/integration/test_hybrid_storage_integration.py` | `port=6379` 하드코딩 | `RedisTestConfig` 사용 |
| `tests/self_healing/integration/test_multi_cluster_namespace.py` | URL 하드코딩 | 환경변수 사용 |
| `tests/self_healing/integration/test_resilient_storage_integration.py` | URL 하드코딩 | 환경변수 사용 |

---

## 3. 상세 변경 내용

### 3.1 docker-compose.test.yml 추가 서비스

**celery-worker 서비스 사양**:
- 이미지: 프로젝트 Dockerfile 빌드
- 의존성: db (healthy), redis (healthy)
- 환경변수: DJANGO_SETTINGS_MODULE, DATABASE_URL, CELERY_BROKER_URL
- 커맨드: celery worker 실행

**healthcheck 추가**:
- celery worker 상태 확인
- 테스트 시작 전 worker 준비 보장

---

### 3.2 환경변수 표준화

**필수 환경변수**:

| 변수 | 기본값 | 용도 |
|------|-------|------|
| `REDIS_HOST` | localhost | Redis 호스트 |
| `REDIS_PORT` | 6379 | Redis 포트 |
| `REDIS_URL` | redis://localhost:6379/0 | 전체 URL |
| `DATABASE_URL` | postgres://...@localhost:5432/... | DB URL |
| `CELERY_BROKER_URL` | redis://localhost:6379/0 | Celery 브로커 |

---

## 4. 검증 방법

### 4.1 Docker 서비스 확인

```bash
# 모든 서비스 시작
docker-compose -f docker-compose.test.yml up -d

# 상태 확인
docker-compose -f docker-compose.test.yml ps

# Redis 연결 확인
docker-compose -f docker-compose.test.yml exec redis redis-cli ping

# Celery worker 확인 (추가 후)
docker-compose -f docker-compose.test.yml logs celery-worker
```

### 4.2 테스트 실행

```bash
# 통합 테스트 실행
python -m pytest tests/integration/ -v --override-ini=addopts=

# hybrid 테스트 실행
python -m pytest tests/hybrid/ -v --override-ini=addopts=
```

---

## 5. 롤백 계획

문제 발생 시:
1. `docker-compose.test.yml` 원복
2. `tests/factories/constants.py` 원복
3. Git: `git checkout -- docker-compose.test.yml tests/factories/constants.py`

---

## 6. 완료 기준

- [ ] docker-compose.test.yml Redis 포트 6379로 통일
- [ ] docker-compose.test.yml celery-worker 서비스 추가
- [ ] tests/factories/constants.py 포트 상수 정리
- [ ] tests/conftest.py 기본 포트 6379로 변경
- [ ] 하드코딩된 포트 4개 파일 수정
- [ ] `docker-compose -f docker-compose.test.yml up -d` 정상 작동
- [ ] `pytest tests/integration/ -v` 통과
