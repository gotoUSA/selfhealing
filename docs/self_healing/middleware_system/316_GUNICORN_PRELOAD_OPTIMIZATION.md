# 316. Gunicorn Preload Optimization — 프로세스 모델 기반 Cold Start 최적화

> **Status**: Planned
> **Severity**: P3 (LOW)
> **Target**: 배포 설정 (docker-compose.yml, k8s/, gunicorn.conf.py)
> **References**:
> - 313 — Settings & Configuration Consistency
> - 315 — Settings SSOT Migration
> - 220 — System Metrics Cache Layer (preload 관련 기존 논의)

---

## 1. 현황 및 문제

### 1.1 현재 배포 구성

| 컴포넌트 | 구성 | 코드 위치 |
|---------|------|----------|
| Django API | `gunicorn --workers=4 --threads=4 --worker-class=gthread` | `docker-compose.yml:38` |
| K8s | `gunicorn --workers=4` (preload 미사용) | `k8s/django-api-deployment.yaml:71-74` |
| Celery Worker | `--concurrency=8`, 6개 큐 | `docker-compose.yml:107` |
| Celery Beat | 단일 프로세스 | `docker-compose.yml:143` |

### 1.2 문제: Worker별 독립 초기화

현재 `--preload` 미사용 상태이므로 각 Gunicorn worker가 앱을 독립적으로 초기화한다:

```
Master process → fork() → Worker 1 → Django init + Settings init
                       → Worker 2 → Django init + Settings init
                       → Worker 3 → Django init + Settings init
                       → Worker 4 → Django init + Settings init
```

**비용**: Settings 초기화 × 4회 + Django ORM/middleware 초기화 × 4회

### 1.3 Preload 적용 시

```
Master process → Django init + Settings init → fork() → Worker 1 (CoW 메모리 공유)
                                                      → Worker 2 (CoW 메모리 공유)
                                                      → Worker 3 (CoW 메모리 공유)
                                                      → Worker 4 (CoW 메모리 공유)
```

**비용**: Settings 초기화 × 1회 + Django ORM/middleware 초기화 × 1회

---

## 2. 패턴 B vs D 비교 (315와의 관계)

315(SharedEnvSource)와 316(Gunicorn Preload)은 서로 다른 레이어의 문제를 해결한다:

| 기준 | 315: SharedEnvSource | 316: Gunicorn Preload |
|------|---------------------|----------------------|
| 해결 레이어 | Settings 내부 (I/O 최적화) | 프로세스 모델 (fork 최적화) |
| 적용 범위 | Gunicorn + Celery + 테스트 | **Gunicorn만** |
| Celery 효과 | O | X (별도 프로세스 모델) |
| 테스트 효과 | O | X (테스트에 gunicorn 없음) |
| 메모리 효율 | 변화 없음 | CoW로 메모리 공유 |
| 코드 변경량 | 108개 settings 수정 | 설정 파일 2-3줄 |
| Hot reload 영향 | 없음 | `--preload`와 `--reload` 병용 불가 |
| DB connection | 영향 없음 | `post_fork` 훅 필요 |

**결론**: 315와 316은 상호 배타적이 아니며, **둘 다 적용 시 최대 효과**를 얻는다.

---

## 3. 개선 계획

### 3.1 Gunicorn 설정 파일 생성

```python
# gunicorn.conf.py (신규)
import multiprocessing

# Worker 설정
workers = 4
worker_class = "gthread"
threads = 4
timeout = 60

# Preload 활성화 — Master에서 앱 초기화 후 Worker fork
preload_app = True

# Access/Error 로그
accesslog = "-"
errorlog = "-"

# Bind
bind = "0.0.0.0:8000"


def post_fork(server, worker):
    """Worker fork 후 DB connection 재초기화.

    Master에서 열린 DB connection이 fork 후 공유되면
    connection pool 충돌이 발생한다.
    post_fork에서 모든 connection을 닫아 Worker가
    독립적으로 새 connection을 생성하도록 한다.
    """
    from django.db import connections

    for conn in connections.all():
        conn.close()


def post_worker_init(worker):
    """Worker 초기화 완료 후 로깅."""
    worker.log.info(f"Worker {worker.pid} initialized (preload mode)")
```

### 3.2 docker-compose.yml 변경

```yaml
# BEFORE
command: >
  sh -c "python manage.py migrate --noinput &&
         python manage.py collectstatic --noinput &&
         gunicorn myproject.wsgi:application --bind 0.0.0.0:8000 --workers 4 --threads 4 --timeout 60 --worker-class gthread --access-logfile - --error-logfile -"

# AFTER
command: >
  sh -c "python manage.py migrate --noinput &&
         python manage.py collectstatic --noinput &&
         gunicorn myproject.wsgi:application -c gunicorn.conf.py"
```

### 3.3 K8s Deployment 변경

```yaml
# k8s/django-api-deployment.yaml
command:
  - gunicorn
  - myproject.wsgi:application
  - -c
  - gunicorn.conf.py
```

### 3.4 wsgi.py에서 Settings 선행 초기화 (선택적)

```python
# myproject/wsgi.py
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

application = get_wsgi_application()

# Preload 모드에서 Master가 settings를 미리 초기화
# Worker fork 시 CoW로 메모리 공유
from selfhealing.settings import get_config
get_config()
```

---

## 4. 주의 사항

### 4.1 Hot Reload 비활성화

`--preload`와 `--reload`는 함께 사용할 수 없다.

| 환경 | preload | reload | 비고 |
|------|---------|--------|------|
| 개발 (로컬) | OFF | ON | 코드 변경 시 자동 재시작 |
| 스테이징 | ON | OFF | 프로덕션과 동일 구성 |
| 프로덕션 | ON | OFF | Cold Start 최적화 |

**구현**: `gunicorn.conf.py`에서 환경변수로 분기.

```python
import os

env = os.environ.get("DEPLOYMENT_ENV", "development")

if env == "development":
    preload_app = False
    reload = True
else:
    preload_app = True
    reload = False
```

### 4.2 DB Connection Pool 관리

`--preload` 사용 시 Master에서 생성된 DB connection이 fork 후 Worker에 복제된다.
이 connection은 **동일한 소켓 FD를 공유**하므로 데이터 손상이 발생할 수 있다.

**해결**: `post_fork` 훅에서 모든 connection을 닫는다 (3.1절 참조).

### 4.3 Signal Handling

`--preload` 모드에서 graceful shutdown은 다음 순서로 동작한다:

1. Master가 SIGTERM 수신
2. Master가 각 Worker에 SIGTERM 전송
3. Worker가 현재 요청 처리 완료 후 종료
4. Master 종료

현재 프로젝트의 Graceful Shutdown 코드 (`core/graceful_shutdown.py`)와의
호환성 확인이 필요하다.

### 4.4 Celery에는 효과 없음

Celery는 자체 프로세스 모델(`prefork`, `eventlet`, `gevent`)을 사용하므로
Gunicorn `--preload`의 혜택을 받지 않는다.

Celery의 Cold Start 최적화는 별도 접근이 필요하다:
- `celery worker --pool=prefork`는 자체 fork를 수행
- Settings 최적화는 315(SharedEnvSource)로 대응

---

## 5. 검증 계획

### 5.1 Cold Start 측정

```bash
# Preload OFF (현재)
time gunicorn myproject.wsgi:application --workers=4 --worker-class=gthread

# Preload ON (변경 후)
time gunicorn myproject.wsgi:application -c gunicorn.conf.py
```

### 5.2 DB Connection 검증

```python
# 각 Worker에서 독립 connection 확인
def post_fork(server, worker):
    from django.db import connections
    for conn in connections.all():
        conn.close()
    # 검증: 새 connection 생성 확인
    from django.db import connection
    connection.ensure_connection()
    worker.log.info(f"Worker {worker.pid}: DB connection OK (pid-isolated)")
```

### 5.3 Graceful Shutdown 호환성

```bash
# Graceful shutdown 테스트
kill -SIGTERM <master_pid>
# Worker가 현재 요청 처리 완료 후 종료되는지 확인
```

---

## 6. 구현 순서

| Phase | 작업 | 우선순위 | 의존성 |
|-------|------|----------|--------|
| 1 | `gunicorn.conf.py` 생성 (preload + post_fork) | P3 | 없음 |
| 2 | `docker-compose.yml` 명령어 변경 | P3 | Phase 1 |
| 3 | `k8s/django-api-deployment.yaml` 명령어 변경 | P3 | Phase 1 |
| 4 | `wsgi.py`에 settings 선행 초기화 추가 (선택적) | P3 | 315 완료 후 |
| 5 | Cold Start 측정 + DB connection 검증 | P3 | Phase 2 |
| 6 | Graceful Shutdown 호환성 테스트 | P3 | Phase 1 |

---

## 7. 관련 문서

| 문서 | 관계 |
|------|------|
| 220 | System Metrics Cache Layer — `AppConfig.ready()`와 preload 상호작용 기존 논의 |
| 313 | Settings Configuration Consistency — 하드코딩 제거 (선행 완료) |
| 315 | Settings SSOT Migration — SharedEnvSource (같은 레이어가 아닌 보완 관계) |

---

## 8. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2026-03-07 | 1.0.0 | 초안 작성 (313 Q2 리뷰에서 분리) |
