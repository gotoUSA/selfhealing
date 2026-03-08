# 321. Celery Beat Schedule Internalization — Beat 설정 라이브러리 내부화

> **Status**: Planning
> **Severity**: P1 (HIGH) — repo 분리 선행 조건
> **Target**: `selfhealing/adapters/celery/beat_schedule.py` (기존 구현 보강)
> **References**:
> - 319 — Repo Separation Overview (커플링 C2)
> - 317 — Orphan Service Wiring (Beat 태스크 등록)
> - 320 — Auto-Configuration (configure_selfhealing 래퍼)

---

## 1. 현황 및 문제

### 1.1 현재: Consumer가 15+ Beat 태스크를 직접 정의

`myproject/celery.py`에 selfhealing Beat 태스크 15+개가 하드코딩되어 있다.

```python
# myproject/celery.py — 현재 상태 (약 170줄)
app.conf.beat_schedule = {
    # shopping 태스크 (consumer 영역)
    "retry-failed-emails": {...},
    "delete-unverified-users": {...},
    # ... shopping 태스크 7개

    # selfhealing 태스크 (라이브러리 영역 — 여기 있으면 안 됨)
    "check-circuit-breaker-recovery": {
        "task": "selfhealing.celery_tasks.check_circuit_breaker_recovery",
        "schedule": 60.0,
        "options": {"expires": 55},
    },
    # ... selfhealing 태스크 14개 더
}
```

**문제**:
- selfhealing에 새 Beat 태스크 추가 시 consumer celery.py도 수정 필요
- 큐/라우팅 설정도 consumer에 하드코딩 (`selfhealing.critical`, `chaos`, `chaos_monitoring`)

---

## 2. 설계

### 2.1 팩토리 함수 기반 동적 스케줄 생성 (현재 구현)

> **Q1 반영**: 정적 딕셔너리 대신 팩토리 함수 + Pydantic Settings로 동적 구성.
> 설정 객체는 `AdaptersGroup.celery_task` (`cached_property`)를 통해 싱글톤 캐싱되므로
> 매 호출 시 환경변수를 재파싱하지 않는다.

`get_selfhealing_beat_schedule()`는 `_SCHEDULE_MODULES` 테이블에 정의된
레인별 서브모듈을 `importlib.import_module()`로 동적 로딩하고,
각 모듈의 `get_*_beat_schedule()` 함수를 호출하여 스케줄을 수집한다.

```python
# selfhealing/adapters/celery/beat_schedule.py (현재 구현)
_SCHEDULE_MODULES = [
    ("cleanup",      "selfhealing.tasks.cleanup_tasks",      "get_cleanup_beat_schedule",      "cleanup lane"),
    ("intelligence", "selfhealing.tasks.intelligence_tasks",  "get_intelligence_beat_schedule",  "intelligence lane"),
    # ... 9개 모듈
]

def get_selfhealing_beat_schedule(
    include_cleanup: bool = True,
    include_intelligence: bool = True,
    # ... include_* 플래그
) -> dict[str, Any]:
    """모듈별 include/exclude 플래그로 스케줄을 동적 생성."""
    for flag_name, module_path, getter_func, debug_msg in _SCHEDULE_MODULES:
        if include_flags.get(flag_name, False):
            schedule.update(_load_schedule_module(module_path, getter_func, debug_msg))
    ...
```

각 서브모듈의 `get_*_beat_schedule()` 함수가 자체적으로 Settings를 참조하여
환경/부하 상태에 따라 주기를 조정할 수 있다:

```python
# 예시: xtest_cleanup_tasks.py — Settings에서 동적 interval 참조
from selfhealing.settings.xtest import get_xtest_settings
settings = get_xtest_settings()
"schedule": crontab(minute=f"*/{settings.cleanup_interval_minutes}"),
```

### 2.2 Crontab 타임존 명시 (UTC 고정) — 향후 마이그레이션

> **Q2 반영**: Consumer의 `CELERY_TIMEZONE` 설정에 의존하지 않도록
> 모든 crontab에 `nowfun=lambda: datetime.now(timezone.utc)`를 주입하는 것이 목표이다.
>
> **현재 상태**: 기존 레인별 서브모듈(`cleanup_tasks.py`, `intelligence_tasks.py` 등)의
> crontab은 아직 `nowfun`을 사용하지 않는다. 신규 crontab 추가 시부터 적용하고,
> 기존 crontab은 별도 마이그레이션 이슈로 일괄 전환한다.

```python
# 목표 패턴 (신규 crontab 추가 시 적용)
from datetime import datetime, timezone
from celery.schedules import crontab

"schedule": crontab(hour=5, minute=0, nowfun=lambda: datetime.now(timezone.utc)),
```

> **참고**: Celery 5.x의 `crontab`은 `tz` 파라미터를 직접 지원하지 않고 `nowfun`을 통해
> 현재 시각 기준을 주입한다. 이 방식으로 Consumer의 `CELERY_TIMEZONE` 설정과 무관하게
> 항상 UTC 기준으로 crontab이 실행된다.

### 2.3 Queue 네임스페이스 격리 (다중 Consumer 환경)

> **Q3 반영**: 여러 마이크로서비스가 동일한 브로커를 공유할 때
> 메시지 탈취(Message Stealing) 방지를 위해 Queue, Exchange, Routing Key 모두에
> Consumer 앱의 Prefix를 동적으로 적용한다.

```python
# settings/celery_task.py 확장
queue_prefix: str = Field(
    default="",
    description="큐 네임스페이스 접두사 (멀티서비스 격리). "
                "예: 'shopping' → 'shopping.selfhealing.critical'",
)
```

```python
# beat_schedule.py — 네임스페이스 격리 + Settings 와이어링 팩토리
def get_selfhealing_queues(
    prefix: str = "",
    queue_type: str = "quorum",
    enable_dlx: bool = True,
) -> list[Queue]:
    """Consumer에 전달할 kombu.Queue 리스트.

    prefix가 지정되면 큐 이름, Exchange 이름, Routing Key 모두에 적용.
    queue_type/enable_dlx로 CeleryTaskSettings 값을 큐 인자에 반영.
    """
    result: list[Queue] = []
    for q in _QUEUE_DEFINITIONS:
        name = f"{prefix}.{q.name}" if prefix else q.name
        # ... exchange/routing_key prefix 적용

        args = dict(q.queue_arguments or {})
        args["x-queue-type"] = queue_type          # Settings 와이어링
        if not enable_dlx:
            args.pop("x-dead-letter-exchange", None)  # DLX 비활성화

        result.append(Queue(name, exchange=exchange, routing_key=routing_key,
                            queue_arguments=args))
    return result
```

> **Settings 와이어링**: `CeleryTaskSettings.queue_type`과 `enable_dlx` 필드가
> `get_selfhealing_queues()` 파라미터로 전달되어 큐 인자에 반영된다.
> `configure_selfhealing_celery()`가 이 파라미터를 중개한다.

### 2.4 kombu.Queue/Exchange 객체 기반 큐 정의

> **Q4 반영**: 엔터프라이즈 스케일에서 dict 구조는 오타/스키마 누락에 취약하다.
> 초기 전환 비용이 가장 낮은 현 시점에 `kombu.Queue`/`kombu.Exchange` 객체로 전환하여
> 타입 안전성 확보 및 RabbitMQ 고급 기능(Quorum Queue, DLX, Priority) 접근성을 확보한다.
> 현재 코드베이스에 kombu import가 0건이므로 영향 범위는 `beat_schedule.py` 1개 파일.

#### 2.4.1 kombu 객체 정의

```python
# selfhealing/adapters/celery/beat_schedule.py
from kombu import Exchange, Queue

_selfhealing_exchange = Exchange(
    "selfhealing", type="direct", durable=True
)

_selfhealing_dlx = Exchange(
    "selfhealing.dlx", type="direct", durable=True
)

_QUEUE_DEFINITIONS: list[Queue] = [
    # Cleanup Lane
    Queue(
        "maintenance",
        exchange=_selfhealing_exchange,
        routing_key="maintenance",
        queue_arguments={
            "x-max-priority": 3,
            "x-queue-type": "quorum",
        },
    ),
    Queue(
        "critical_maintenance",
        exchange=_selfhealing_exchange,
        routing_key="critical_maintenance",
        queue_arguments={
            "x-max-priority": 10,
            "x-queue-type": "quorum",
            "x-dead-letter-exchange": _selfhealing_dlx.name,  # DLX 객체 참조
        },
    ),
    # ... (11개 큐 전체 — realtime, compliance, reports, metrics,
    #      audit_flush, chaos, chaos_monitoring, selfhealing.critical)
]

# 하위호환: dict 형태도 제공 (점진적 마이그레이션)
SELFHEALING_QUEUE_CONFIG = {
    q.name: {
        "exchange": q.exchange.name,
        "routing_key": q.routing_key,
        "queue_arguments": q.queue_arguments or {},
    }
    for q in _QUEUE_DEFINITIONS
}
```

> **DLX 참조 방식**: `_selfhealing_dlx.name` 으로 참조하여 DLX Exchange 객체와
> `queue_arguments` 문자열의 동기화를 보장한다. 하드코딩 문자열 `"selfhealing.dlx"` 대신
> 객체 참조를 사용하여 이름 변경 시 일관성이 유지된다.

#### 2.4.2 Settings 연동

```python
# settings/celery_task.py 확장 필드
queue_prefix: str = Field(
    default="",
    description="큐 네임스페이스 접두사 (멀티서비스 격리)",
)
queue_type: str = Field(
    default="quorum",
    pattern=r"^(classic|quorum|stream)$",
    description="RabbitMQ 큐 타입 (quorum 권장 — Raft 합의 기반 메시지 유실 방지)",
)
enable_dlx: bool = Field(
    default=True,
    description="Dead Letter Exchange 활성화 (critical 큐에 DLX 바인딩)",
)
```

#### 2.4.3 Kafka와의 관계 (관심사 분리)

`kombu.Queue`/`Exchange`는 **AMQP(RabbitMQ) 전용** 객체이며 Kafka에는 적용되지 않는다.
이 프로젝트의 브로커 아키텍처는 두 레이어로 분리되어 있다:

| 레이어 | 브로커 | 라이브러리 | 용도 |
|--------|--------|-----------|------|
| **Task Queue** | RabbitMQ/Redis | Celery + kombu | Beat 태스크 실행, 큐 라우팅, DLQ 리플레이 |
| **Event Bus** | Kafka | confluent-kafka | 감사 이벤트 스트리밍, 실시간 메트릭, CRDT 복제 |

- **Celery 레이어** (`adapters/celery/`, `adapters/queues/celery_adapter.py`):
  `kombu.Queue` 객체로 RabbitMQ Quorum Queue, DLX, Priority를 엄격하게 제어
- **Kafka 레이어** (`adapters/kafka/`):
  `KafkaAuditProducer`, `KafkaAuditConsumer`, `KafkaEventBus` 등 독립적 어댑터.
  Topic 기반 구조이므로 Exchange/Queue 개념이 없으며, 파티션과 컨슈머 그룹으로 격리.
  `NonBlockingRetryHandler`가 Retry Topic 체인(Main → Retry-1..3 → DLQ)을 자체 관리.

따라서 321의 kombu 전환은 **Celery/RabbitMQ 레이어에만 해당**하며,
Kafka 어댑터에는 영향을 주지 않는다. 두 브로커는 `TaskQueueInterface`와
Kafka 어댑터라는 별도의 추상화 계층을 통해 독립적으로 동작한다.

### 2.5 Opt-out 빌더 패턴 (팩토리 함수)

> **Q5 반영**: `del` 기반 opt-out 대신 `include_*` 불리언 플래그 기반 팩토리 함수를
> 이미 구현 완료 (`beat_schedule.py:136-199`). 타입 안전하고 오타 위험 없음.

```python
# Consumer 사용법 — 모듈 단위 opt-out
from selfhealing.adapters.celery.beat_schedule import get_selfhealing_beat_schedule

schedule = get_selfhealing_beat_schedule(
    include_intelligence=False,  # Chaos/분석 제외
    include_saga=False,          # Saga 미사용
)
app.conf.beat_schedule.update(schedule)
```

> **Deprecated**: 아래 `del` 방식은 더 이상 권장하지 않는다:
> ```python
> # (비권장) 오타 위험, 타입 안전하지 않음
> schedule = dict(SELFHEALING_BEAT_SCHEDULE)
> del schedule["selfhealing-chaos-hunt-zombie-experiments"]
> ```

### 2.6 Consumer 통합 래퍼: `configure_selfhealing_celery(app)`

> **Q6 반영**: 320의 `configure_selfhealing(namespace=globals())` 패턴과 대칭적인
> Celery 전용 래퍼를 제공하여, Consumer celery.py에서 1줄로 모든 설정을 주입한다.

```python
# selfhealing/adapters/celery/beat_schedule.py
_celery_configured = False  # 멱등성 가드 (setup_selfhealing_signals 패턴과 동일)

def configure_selfhealing_celery(
    app,
    *,
    include_cleanup: bool = True,
    include_intelligence: bool = True,
    include_compliance: bool = True,
    include_traffic_aware: bool = True,
    include_canary_watchdog: bool = True,
    include_governance: bool = True,
    include_xtest_cleanup: bool = True,
    include_audit_flush: bool = True,
    include_saga: bool = True,
    include_legacy: bool = True,
    queue_prefix: str = "",
    queue_type: str = "quorum",
    enable_dlx: bool = True,
) -> None:
    """Celery app에 selfhealing Beat 스케줄, 큐, 라우팅을 1줄로 주입.

    configure_selfhealing(namespace=globals()) 과 대칭 구조.
    멱등 — 두 번째 호출 시 경고 로그 후 no-op.

    Args:
        app: Celery application instance
        include_*: 모듈별 Beat 태스크 포함 여부
        queue_prefix: 큐 네임스페이스 접두사 (멀티서비스 격리)
        queue_type: RabbitMQ 큐 타입 (classic/quorum/stream)
        enable_dlx: Dead Letter Exchange 활성화 여부
    """
    global _celery_configured
    if _celery_configured:
        logger.warning("beat_schedule.celery_already_configured")
        return

    # 1. Beat Schedule merge
    schedule = get_selfhealing_beat_schedule(...)

    # 1b. Beat 스케줄 큐 옵션에 prefix 적용
    if queue_prefix:
        for entry in schedule.values():
            opts = entry.get("options", {})
            if "queue" in opts:
                opts["queue"] = f"{queue_prefix}.{opts['queue']}"

    app.conf.beat_schedule.update(schedule)

    # 2. Queue 정의 merge (queue_type/enable_dlx 전달)
    queues = get_selfhealing_queues(
        prefix=queue_prefix, queue_type=queue_type, enable_dlx=enable_dlx,
    )
    existing = list(app.conf.task_queues or [])
    app.conf.task_queues = existing + queues

    # 3. Task Routes merge
    existing_routes = dict(app.conf.task_routes or {})
    existing_routes.update(get_selfhealing_task_routes(prefix=queue_prefix))
    app.conf.task_routes = existing_routes

    # 4. Task 등록
    register_all_tasks_with_celery(app)

    _celery_configured = True
    logger.info("beat_schedule.celery_configured", queue_prefix=queue_prefix or "(none)")
```

> **멱등성 가드**: `setup_selfhealing_signals()`의 `_signals_connected` 패턴과 동일하게
> `_celery_configured` 플래그로 중복 호출을 방지한다. Django reload, 테스트 setUp 등에서
> 안전하다. 테스트용 `_reset_celery_configured()` 함수를 별도 제공한다.
>
> **Beat 스케줄 큐 prefix**: Step 1b에서 `queue_prefix`가 지정된 경우 beat 스케줄 엔트리의
> `options.queue` 값에도 prefix를 적용한다. 이를 통해 Step 2의 kombu Queue 정의
> (`shopping.maintenance`)와 beat 스케줄의 큐 참조(`shopping.maintenance`)가 일치한다.

### 2.7 Consumer 사용법 (최종)

```python
# myproject/celery.py — 최종 형태
from celery import Celery

app = Celery("myproject")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Consumer 자체 Beat 스케줄
app.conf.beat_schedule = {
    "retry-failed-emails": {
        "task": "shopping.tasks.email_tasks.retry_failed_emails_task",
        "schedule": crontab(minute="*/5"),
        "options": {"expires": 300},
    },
    # ... consumer 태스크만
}

# selfhealing 설정 1줄 주입 (Q3: 네임스페이스 격리, Q4: kombu 큐, Q6: 래퍼)
from selfhealing.adapters.celery.beat_schedule import configure_selfhealing_celery
configure_selfhealing_celery(app, include_intelligence=False, queue_prefix="shopping")
```

```python
# myproject/settings.py — 마지막 줄 (320에서 구현)
from selfhealing.adapters.django import configure_selfhealing
configure_selfhealing(namespace=globals())
```

> **대칭 구조 요약**:
>
> | 설정 대상 | 래퍼 함수 | 호출 위치 |
> |----------|----------|----------|
> | Django (MIDDLEWARE, DRF, OTEL) | `configure_selfhealing(namespace=globals())` | `settings.py` 마지막 |
> | Celery (Beat, Queue, Routes) | `configure_selfhealing_celery(app)` | `celery.py` |

### 2.8 AppConfig 자동 등록 (대안)

320에서 구현하는 auto-config와 연동하여, AppConfig.ready()에서 자동으로 Beat schedule을 merge할 수도 있다:

```python
# apps.py — ready()에서 자동 merge (대안)
def _auto_merge_beat_schedule(self):
    """Beat schedule 자동 merge (SELFHEALING_AUTO_BEAT=True일 때)."""
    if not getattr(settings, "SELFHEALING_AUTO_BEAT", False):
        return

    try:
        from celery import current_app
        from selfhealing.adapters.celery.beat_schedule import configure_selfhealing_celery

        configure_selfhealing_celery(current_app)
    except ImportError:
        pass
```

**권장**: 명시적 `configure_selfhealing_celery(app)` 호출 — Celery 설정은 명시적인 것이 디버깅에 유리

---

## 3. 하위 호환성

- 기존 consumer가 Beat 태스크를 직접 정의한 경우 → 중복 키 없도록 selfhealing 키에 `selfhealing-` 접두사 추가
- `SELFHEALING_QUEUE_CONFIG` dict 형태도 계속 제공하여 kombu 전환 이전의 Consumer도 호환 가능
- `include_*` 플래그를 통한 모듈 단위 opt-out으로 `del` 방식 대체

---

## 4. 테스트 계획

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | `get_selfhealing_beat_schedule()` 호출 | 15+ 태스크 키 존재 확인 |
| 2 | `configure_selfhealing_celery(app)` 호출 | beat_schedule, task_queues, task_routes 모두 주입 확인 |
| 3 | `include_*=False` 플래그 | 해당 모듈 태스크 제외 확인 |
| 4 | 큐 정의 완전성 | kombu.Queue 객체로 모든 큐 생성, `x-queue-type: quorum` 확인 |
| 5 | DLX 바인딩 | critical, realtime 큐에 `x-dead-letter-exchange` 존재 확인 |
| 6 | 네임스페이스 격리 | `queue_prefix="shopping"` 시 큐/Exchange/Routing Key 모두 prefix 적용 확인 |
| 7 | 라우팅 정의 완전성 | critical 태스크 4개 라우팅 확인, prefix 적용 시 큐 이름 변환 확인 |
| 8 | crontab UTC 고정 | `nowfun` 기반 UTC 타임존 독립성 검증 |
| 9 | Settings 캐싱 | `get_celery_task_settings()` 반복 호출 시 동일 인스턴스 반환 확인 |
| 10 | dict 하위호환 | `SELFHEALING_QUEUE_CONFIG` dict가 kombu 객체와 동기화 확인 |
| 11 | Kafka 무영향 | `adapters/kafka/` 어댑터가 kombu 전환과 독립적으로 동작 확인 |

---

## 5. 리뷰 반영 이력

| Q | 리뷰 피드백 | 반영 섹션 | 변경 내용 |
|---|-----------|----------|----------|
| Q1 | 스케줄 주기 동적 구성 + Settings 싱글톤 캐싱 | §2.1 | 팩토리 함수 + `cached_property` 캐싱 명시 |
| Q2 | crontab UTC 명시적 고정 | §2.2 | `nowfun=lambda: datetime.now(timezone.utc)` 적용 |
| Q3 | Queue 네임스페이스 격리 (Exchange/RK 포함) | §2.3 | `queue_prefix` 파라미터 + 3요소 모두 prefix 적용 |
| Q4 | kombu.Queue 객체 전환 + Kafka 관계 정리 | §2.4 | kombu 객체 정의 + dict 하위호환 + Kafka 분리 아키텍처 문서화 |
| Q5 | `include_*` 빌더 패턴 (del 방식 비권장) | §2.5 | 팩토리 함수 기반 opt-out, del 방식 deprecated 표시 |
| Q6 | `configure_selfhealing_celery(app)` 래퍼 | §2.6, §2.7 | 320 대칭 구조 래퍼 + Consumer 최종 사용법 |
