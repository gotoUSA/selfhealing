# 213. EventBus 핸들러 Celery 위임 구현 계획

> **상태**: 📋 설계 완료 — 구현 대기
> **목적**: 동기 EventBus 핸들러 중 I/O 바운드 작업이 포함된 3개 핸들러를 Celery task로 위임하여 발행자 스레드 차단을 제거한다.
> **기준일**: 2026-02-09

---

## 1. 배경: 현재 EventBus 동작 방식

### 1.1 동기 실행 구조

`SelfHealingEventBus.publish()`는 **완전 동기식**이다.

**파일**: `services/event_bus/bus.py` L361-396

```python
def publish(self, event: SelfHealingEvent) -> int:
    # 히스토리에 기록
    self._record_event(event)

    handlers_called = 0
    with self._subscription_lock:
        subscriptions = list(self._subscriptions.get(event.event_type, []))

    for subscription in subscriptions:
        if not subscription.enabled:
            continue
        try:
            subscription.handler(event)          # ← 동기 직접 호출
            handlers_called += 1
        except Exception as e:
            logger.error(...)                     # ← 예외 catch 후 다음 핸들러 계속

    return handlers_called                        # ← 모든 핸들러 완료 후 리턴
```

**문제**: `publish()` 호출자(CB service, Emergency Manager 등)의 스레드에서 모든 핸들러가 순차 실행된다. 핸들러에 네트워크 I/O나 블로킹 작업이 있으면 발행자의 크리티컬 경로가 지연된다.

### 1.2 이미 올바르게 Celery 위임 중인 핸들러 (참고)

| 핸들러 | Celery 위임 방식 | 코드 위치 |
|--------|-----------------|-----------|
| `_on_circuit_breaker_closed` | `conditional_replay_on_circuit_close.delay(...)` | `bus.py` L832 |
| `_on_circuit_breaker_closed_postmortem` (그룹핑 경로) | `close_incident_group.apply_async(...)` | `bus.py` L922 |

이 핸들러들은 **핸들러 자체는 동기로 빠르게 리턴**하고, 무거운 작업(DLQ Replay, Postmortem 생성)은 Celery worker에서 처리된다. 이것이 목표 패턴이다.

---

## 2. 위임 대상 핸들러 3개 — 현재 코드 분석

### 2.1 `_on_circuit_breaker_opened_notify` (알림 발송)

**파일**: `services/event_bus/bus.py` L580-646

**현재 동작**:
```
_on_circuit_breaker_opened_notify(event)
  ├── get_actionable_alert_url_builder().build_cb_open_urls()    ← URL 조립 (인메모리)
  └── get_unified_notification_manager().notify(payload)          ← 동기 HTTP 호출
        └── _send_to_channels(payload, channels, priority)
              └── service.send_alert(title, message, ...)
                    └── _send_to_slack(formatted_message, channel)
                          └── requests.post(webhook_url, timeout=...)  ← 네트워크 I/O 차단
```

**차단 원인**:
- `requests.post()` — Slack Webhook 동기 HTTP 호출
- **파일**: `services/security_notification/slack_handler.py` L191

```python
response = requests.post(
    self.config.slack_webhook_url,
    json=slack_message,
    timeout=limits.notification_timeout_seconds,  # ← 타임아웃까지 차단
)
```

**이벤트 등록**: `bus.py` L1707-1710
```python
bus.subscribe(
    EventType.CIRCUIT_BREAKER_OPENED,
    _on_circuit_breaker_opened_notify,
    priority=EventPriority.HIGH,
)
```

**차단 시간**: Slack 정상 응답 200~500ms, 장애 시 타임아웃(기본 5~30초)

**발행자 영향**: `circuit_breaker/service.py` L275의 `bus.emit(CIRCUIT_BREAKER_OPENED, ...)` 호출 시 Slack 응답 대기.

---

### 2.2 `_on_circuit_breaker_opened_snapshot` (시스템 스냅샷 + Redis 저장)

**파일**: `services/event_bus/bus.py` L648-700

**현재 동작**:
```
_on_circuit_breaker_opened_snapshot(event)
  ├── collect_system_snapshot()                                      ← psutil + DB + Redis
  │     ├── psutil.cpu_percent(interval=0.1)                         ← 고정 100ms sleep
  │     ├── psutil.virtual_memory()                                  ← 인메모리
  │     ├── repo.get_active_connection_count()                       ← DB 쿼리
  │     ├── error_budget_service.get_status()                        ← 서비스 호출
  │     └── adapter.get_counter_value("selfhealing_http_requests_total")
  ├── cb_service.get_all_services() → get_status() 루프               ← N개 서비스 상태 조회
  └── save_open_snapshot_to_redis(service_name, snapshot)             ← Redis HSET × N필드 + EXPIRE
```

**차단 원인**:
- `psutil.cpu_percent(interval=0.1)` — **파일**: `api/django/views/xtest/base.py` L641

```python
cpu_percent = psutil.cpu_percent(interval=0.1)  # ← 최소 100ms 고정 sleep
```

- `save_open_snapshot_to_redis()` — **파일**: `services/postmortem/snapshot_builder.py` L328-330

```python
for field_name, value in snapshot_data.items():
    redis_client.hset(key, field_name, str(value))  # ← N번 Redis I/O
redis_client.expire(key, SnapshotBuilder.OPEN_SNAPSHOT_TTL)
```

**이벤트 등록**: `bus.py` L1713-1716
```python
bus.subscribe(
    EventType.CIRCUIT_BREAKER_OPENED,
    _on_circuit_breaker_opened_snapshot,
    priority=EventPriority.NORMAL,
)
```

**차단 시간**: 100ms(psutil) + DB 쿼리(수ms~수십ms) + Redis HSET × N(수ms) ≈ **~150ms 이상**

**발행자 영향**: `_on_circuit_breaker_opened_notify`와 같은 이벤트를 구독하므로 **둘 합산**하면 200ms~수초.

---

### 2.3 `_create_individual_postmortem` + `_on_emergency_recovery_completed_postmortem` (Postmortem 동기 생성)

#### 2.3.1 `_on_circuit_breaker_closed_postmortem`의 fallback 경로

**파일**: `services/event_bus/bus.py` L846-890 → L940-1005

**현재 동작** (그룹핑 실패 시 `_create_individual_postmortem` 호출):
```
_on_circuit_breaker_closed_postmortem(event)
  ├── get_postmortem_settings()                                 ← 설정 로드
  ├── _handle_incident_group()                                  ← 시도
  │     └── 실패 시 fallback ↓
  └── _create_individual_postmortem(event, ...)                 ← 전체 동기 실행
        ├── collect_system_snapshot()                            ← 100ms+ (psutil)
        ├── get_circuit_breaker_service() → get_all_services()  ← CB 상태 수집
        ├── _collect_service_states(cb_service)                 ← 서비스 분류
        ├── get_healing_events(20)                              ← 로컬 이벤트 조회
        ├── _build_timeline(history, local_events)              ← CPU 바운드
        ├── _generate_postmortem_data(...)                      ← CPU 바운드
        ├── integrity_sealer.seal(postmortem)                   ← HMAC 계산
        ├── add_healing_incident(postmortem)                    ← DB INSERT
        ├── _write_to_wal(...)                                  ← 파일 I/O
        └── _send_postmortem_notification(...)                  ← HTTP (Slack)
              └── manager.notify(payload) → requests.post(...)
```

**이벤트 등록**: `bus.py` L1700-1703
```python
bus.subscribe(
    EventType.CIRCUIT_BREAKER_CLOSED,
    _on_circuit_breaker_closed_postmortem,
    priority=EventPriority.LOW,
)
```

#### 2.3.2 `_on_emergency_recovery_completed_postmortem`

**파일**: `services/event_bus/bus.py` L1263-1370

**현재 동작** (동일 패턴):
```
_on_emergency_recovery_completed_postmortem(event)
  ├── get_postmortem_settings()
  ├── collect_system_snapshot()                       ← 100ms+
  ├── _generate_emergency_postmortem_data(...)        ← CPU 바운드 + 타임라인 구축
  ├── add_healing_incident(postmortem)                ← DB INSERT
  ├── _write_to_wal(...)                              ← 파일 I/O
  └── _send_postmortem_notification(...)              ← HTTP (Slack)
```

**이벤트 등록**: `bus.py` L1719-1722
```python
bus.subscribe(
    EventType.EMERGENCY_RECOVERY_COMPLETED,
    _on_emergency_recovery_completed_postmortem,
    priority=EventPriority.LOW,
)
```

**차단 시간**: 스냅샷(100ms) + DB(수ms~수십ms) + WAL(수ms) + HTTP(수백ms) ≈ **수백ms ~ 수초**

---

## 3. 구현 방안

### 3.1 전략: 핸들러 → Thin Wrapper + Celery Task

기존의 올바른 패턴(`_on_circuit_breaker_closed`)을 동일하게 적용한다:

```
Before (현재):
  bus.publish(event) → handler(event) → [무거운 I/O 작업 동기 실행]

After (목표):
  bus.publish(event) → handler(event) → celery_task.delay(event_data) → 즉시 리턴
                                              ↓ (Celery worker에서 비동기)
                                         [무거운 I/O 작업 실행]
```

### 3.2 Task 1: CB OPEN 알림 Celery 위임

#### 3.2.1 새 Celery Task — `send_cb_open_notification`

**파일**: `adapters/celery/tasks/circuit_breaker.py`에 추가

```python
@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.send_cb_open_notification",
    queue="selfhealing",
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
    time_limit=60,
    soft_time_limit=55,
)
def send_cb_open_notification(
    self,
    service_name: str,
    trace_id: str | None = None,
    trace_url: str | None = None,
    timestamp: str = "",
) -> dict:
    """
    CB OPEN 알림을 비동기로 발송.

    기존 _on_circuit_breaker_opened_notify의 본체를 Celery task로 이동.
    Slack/Email/PagerDuty HTTP 호출이 발행자 스레드를 차단하지 않도록 한다.

    autoretry_for=(Exception,): Slack 장애 시 30초 간격으로 최대 3회 재시도.
    """
    # 기존 bus.py L600-643의 본체를 여기로 이동
    ...
```

**설계 근거**:
- `autoretry_for=(Exception,)`, `max_retries=3`, `default_retry_delay=30` — 기존 `send_sla_notification` task(`adapters/celery/tasks/sla_notification.py` L20-28)와 동일한 패턴
- `queue="selfhealing"` — 기존 알림 task와 동일 큐
- `acks_late=True` — worker 중단 시 미완료 task가 브로커에 반환

#### 3.2.2 핸들러 변경 — `_on_circuit_breaker_opened_notify`

**파일**: `services/event_bus/bus.py` L580-646

```python
# Before (현재)
def _on_circuit_breaker_opened_notify(event: SelfHealingEvent) -> None:
    try:
        # ... URL 빌드, payload 생성, manager.notify() 동기 호출
        manager = get_unified_notification_manager()
        manager.notify(payload)
    except Exception as e:
        logger.warning(f"[Notification] Failed to send CB notification: {e}")


# After (변경)
def _on_circuit_breaker_opened_notify(event: SelfHealingEvent) -> None:
    try:
        from selfhealing.adapters.celery.tasks import send_cb_open_notification

        send_cb_open_notification.delay(
            service_name=event.data.get("service_name", "unknown"),
            trace_id=event.data.get("trace_id"),
            trace_url=event.data.get("trace_url"),
            timestamp=event.data.get("timestamp", ""),
        )
    except ImportError:
        logger.debug("[EventHandler] Celery tasks not available, skipping CB notification")
    except Exception as e:
        logger.warning(f"[Notification] Failed to enqueue CB notification: {e}")
```

**변경 포인트**:
- URL 빌드 + `manager.notify()` 전체를 Celery task로 이동
- 핸들러는 `.delay()` 호출 후 **즉시 리턴** (Celery broker 연결이 있다면 ~1ms)
- `ImportError` fallback — Celery 미설치 환경에서도 안전 (기존 `_on_circuit_breaker_closed` L840-841과 동일 패턴)

---

### 3.3 Task 2: CB OPEN 스냅샷 Celery 위임

#### 3.3.1 새 Celery Task — `collect_cb_open_snapshot`

**파일**: `adapters/celery/tasks/circuit_breaker.py`에 추가

```python
@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.collect_cb_open_snapshot",
    queue="selfhealing",
    max_retries=1,
    default_retry_delay=10,
    acks_late=True,
    time_limit=30,
    soft_time_limit=25,
)
def collect_cb_open_snapshot(
    self,
    service_name: str,
    event_timestamp: str,
) -> dict:
    """
    CB OPEN 시점 시스템 스냅샷을 비동기로 수집 및 Redis 저장.

    기존 _on_circuit_breaker_opened_snapshot의 본체를 Celery task로 이동.
    psutil.cpu_percent(interval=0.1)의 100ms 블로킹과 Redis HSET를
    발행자 스레드에서 제거한다.

    ⚠️ Worker 실행 환경 주의:
    이 Task는 Celery Worker에서 실행되므로, collect_system_snapshot()이
    수집하는 CPU/Memory 지표는 Worker 노드의 시스템 상태를 반영한다.
    실제 트래픽을 처리하는 Web Server 노드의 상태와 다를 수 있다.
    스냅샷 데이터에 snapshot_source 필드를 추가하여 출처를 명시한다.

    CB 상태 조회는 Redis 기반이므로 Worker에서도 정상 조회 가능하다.
    (factory.py L66: _default_repo = "redis",
     RedisCircuitBreakerStateRepository.get_all_states()가 Redis keys scan 수행)

    max_retries=1: 스냅샷은 최신 데이터가 중요하므로 1회만 재시도.
    time_limit=30: 스냅샷 수집은 30초 이내 완료되어야 함.
    """
    # 기존 bus.py L664-698의 본체를 여기로 이동
    # 스냅샷에 출처 메타데이터 추가:
    #   snapshot["snapshot_source"] = "celery_worker"
    #   snapshot["snapshot_note"] = "Worker 노드의 CPU/Memory. Web Server와 다를 수 있음."
    ...
```

**설계 근거**:
- `max_retries=1` — 스냅샷은 시간에 민감하므로 무한 재시도 불필요 (Postmortem에 사용되는 TTL 30분 참조)
- `time_limit=30` — `psutil` + Redis 작업은 30초면 충분
- Celery task 지연에 의한 스냅샷 시간 차이는 **수 초 이내**이며, TTL 30분인 스냅샷의 정밀도에 영향 없음
- **네이밍 `collect_`**: 기존 `collect_self_healing_metrics` (`adapters/celery/tasks/monitoring.py` L38)과 동일한 `collect_*` 패턴 사용. `capture_`는 기존 코드베이스에 없는 동사이므로 일관성을 위해 `collect_`를 선택

#### 3.3.2 핸들러 변경 — `_on_circuit_breaker_opened_snapshot`

**파일**: `services/event_bus/bus.py` L648-700

```python
# Before (현재)
def _on_circuit_breaker_opened_snapshot(event: SelfHealingEvent) -> None:
    service_name = event.data.get("service_name", "unknown")
    try:
        snapshot = collect_system_snapshot()           # ← 100ms+ 차단
        # ... Redis 저장
        save_open_snapshot_to_redis(service_name, snapshot)  # ← Redis I/O
    except Exception as e:
        logger.warning(...)


# After (변경)
def _on_circuit_breaker_opened_snapshot(event: SelfHealingEvent) -> None:
    service_name = event.data.get("service_name", "unknown")
    try:
        from selfhealing.adapters.celery.tasks import collect_cb_open_snapshot

        collect_cb_open_snapshot.delay(
            service_name=service_name,
            event_timestamp=event.timestamp.isoformat(),
        )
    except ImportError:
        logger.debug("[EventHandler] Celery tasks not available, skipping CB snapshot")
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to enqueue CB snapshot: {e}")
```

---

### 3.4 Task 3: 개별 Postmortem 생성 Celery 위임

#### 3.4.1 새 Celery Task — `process_individual_postmortem`

**파일**: `adapters/celery/tasks/postmortem.py`에 추가

```python
@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.process_individual_postmortem",
    queue="selfhealing",
    max_retries=2,
    time_limit=120,
    soft_time_limit=110,
    acks_late=True,
)
def process_individual_postmortem(
    self,
    service_name: str,
    event_data: dict,
    event_type: str,
    event_bus_history: list[dict] | None = None,
) -> dict:
    """
    개별 Post-mortem을 비동기로 생성.

    _create_individual_postmortem과 _on_emergency_recovery_completed_postmortem의
    본체를 Celery task로 통합.

    스냅샷 수집, Timeline 빌드, DB INSERT, WAL 기록, 알림 발송을
    모두 Celery worker에서 수행하여 EventBus 발행자 스레드를 해방한다.

    Args:
        service_name: 대상 서비스 이름
        event_data: SelfHealingEvent.to_dict()로 직렬화된 이벤트 데이터.
                    Celery JSON serializer 호환을 위해 반드시 dict 타입이어야 한다.
                    (SelfHealingEvent 객체를 직접 전달하면 직렬화 실패)
        event_type: 분기용 이벤트 타입
                    - "circuit_breaker_closed": _create_individual_postmortem 로직
                    - "emergency_recovery_completed": _on_emergency_recovery_completed_postmortem 로직
        event_bus_history: 발행자(Web Server) 프로세스에서 미리 수집한 EventBus 히스토리.
                          bus.get_history()는 프로세스 로컬 인메모리(list[dict])이므로
                          Celery Worker에서는 빈 리스트가 반환된다.
                          따라서 핸들러에서 .delay() 호출 전에 반드시 수집하여 전달해야 한다.
                          (bus.py L243: self._event_history: list[dict[str, Any]] = [])
                          (bus.py L455: history = list(self._event_history))

    ⚠️ Worker 실행 환경 주의:
    - collect_system_snapshot()의 CPU/Memory는 Worker 노드 값 (snapshot_source 표시)
    - get_healing_events(20, use_redis=True)로 Redis에서 힐링 이벤트 조회 (Worker에서도 가능)
      (base.py L730: 기본값 use_redis=True → get_healing_events_redis() 호출)
    - collect_service_states(cb_service)는 cb_service.repository.get_all_states() 호출,
      Redis 기반이므로 Worker에서도 정상 조회 가능
      (store.py L533: all_states = cb_service.repository.get_all_states())

    max_retries=2, time_limit=120 — 기존 close_incident_group task
    (adapters/celery/tasks/postmortem.py L32-38)와 동일한 설정.
    """
    ...
```

**설계 근거**:
- `max_retries=2`, `time_limit=120`, `soft_time_limit=110`, `queue="selfhealing"`, `acks_late=True` — 기존 `close_incident_group` task (`adapters/celery/tasks/postmortem.py` L32-38)와 동일한 설정
- 두 핸들러의 작업이 본질적으로 동일(스냅샷 → 생성 → 저장 → 감사 → 알림)하므로 하나의 task로 통합
- **네이밍 `process_`**: `generate`는 기존 코드베이스에 없는 동사이며, 이 Task는 "생성"만이 아니라 "수집 → 생성 → 저장 → 감사 → 알림"까지 수행하므로 포괄적인 `process_`가 적합. 기존 `_create_individual_postmortems` (`adapters/celery/tasks/postmortem.py` L153)은 `close_incident_group` 내부의 private 함수이므로 Task 이름으로 `create_`를 쓰면 혼동됨

#### 3.4.2 핸들러 변경 — `_on_circuit_breaker_closed_postmortem` (fallback 경로)

**파일**: `services/event_bus/bus.py` L846-890

```python
# Before (현재)
def _on_circuit_breaker_closed_postmortem(event: SelfHealingEvent):
    ...
    if incident_group_enabled:
        try:
            _handle_incident_group(event, service_name, settings)
            return
        except Exception as e:
            logger.warning(...)
    # Fallback: 동기로 전체 실행
    _create_individual_postmortem(event, service_name, settings, min_duration, history_limit)


# After (변경)
def _on_circuit_breaker_closed_postmortem(event: SelfHealingEvent):
    ...
    if incident_group_enabled:
        try:
            _handle_incident_group(event, service_name, settings)
            return  # ← 이 경로는 이미 close_incident_group.apply_async() 사용 (변경 없음)
        except Exception as e:
            logger.warning(...)
    # Fallback: Celery task로 위임
    try:
        from selfhealing.adapters.celery.tasks import process_individual_postmortem

        # ⚠️ bus.get_history()는 프로세스 로컬 인메모리이므로 여기서 수집
        # (bus.py L243: self._event_history: list[dict] — 프로세스별 독립)
        bus = get_event_bus()
        event_bus_history = bus.get_history(limit=history_limit)

        # ⚠️ event.to_dict()로 직렬화 — Celery JSON serializer 호환 필수
        # (bus.py L174-182: to_dict()는 모든 필드를 str/dict/None으로 변환)
        process_individual_postmortem.delay(
            service_name=service_name,
            event_data=event.to_dict(),
            event_type="circuit_breaker_closed",
            event_bus_history=event_bus_history,
        )
    except ImportError:
        # Celery 미설치 환경: 기존 동기 방식 fallback
        _create_individual_postmortem(event, service_name, settings, min_duration, history_limit)
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to enqueue postmortem: {e}")
```

#### 3.4.3 핸들러 변경 — `_on_emergency_recovery_completed_postmortem`

**파일**: `services/event_bus/bus.py` L1263-1370

```python
# Before (현재)
def _on_emergency_recovery_completed_postmortem(event: SelfHealingEvent):
    ...
    # 전체 동기 실행 (스냅샷 + 생성 + 저장 + WAL + 알림)
    snapshot = collect_system_snapshot()
    postmortem = _generate_emergency_postmortem_data(...)
    add_healing_incident(postmortem)
    _write_to_wal(...)
    _send_postmortem_notification(...)


# After (변경)
def _on_emergency_recovery_completed_postmortem(event: SelfHealingEvent):
    ...
    # min_duration 체크만 동기로 수행 (빠름)
    if duration is not None and duration < min_duration:
        logger.debug(...)
        return

    try:
        from selfhealing.adapters.celery.tasks import process_individual_postmortem

        # ⚠️ bus.get_history()는 프로세스 로컬 인메모리이므로 여기서 수집
        bus = get_event_bus()
        event_bus_history = bus.get_history(limit=history_limit)

        process_individual_postmortem.delay(
            service_name=f"emergency-{namespace}",
            event_data=event.to_dict(),
            event_type="emergency_recovery_completed",
            event_bus_history=event_bus_history,
        )
    except ImportError:
        # Celery 미설치 환경: 기존 동기 방식 fallback (현재 코드 유지)
        ...
```

---

## 4. 변경하지 않는 핸들러와 그 근거

### 4.1 Throttle limit 변경 핸들러 (8개) — 동기 유지

| 핸들러 | 작업 | 동기 유지 근거 |
|--------|------|---------------|
| `_on_emergency_level_changed_throttle` | `throttle.adjust_for_emergency(level)` | 인메모리 변수 수정, μs 단위 |
| `_on_emergency_deactivated_throttle` | `throttle.adjust_for_emergency(0)` | 인메모리 |
| `_on_circuit_breaker_opened_throttle` | `throttle.current_limit = config.min_limit` | 인메모리, **즉시 반영 필수** |
| `_on_circuit_breaker_half_opened_throttle` | `throttle.current_limit = half_open_limit` | 인메모리 |
| `_on_circuit_breaker_closed_throttle` | `throttle.current_limit = config.initial_limit` | 인메모리 |
| `_on_error_budget_critical_throttle` | `throttle.current_limit = new_limit` | 인메모리 |
| `_on_error_budget_recovered_throttle` | `throttle.start_recovery_dampening()` | 인메모리 |
| `_on_kill_switch_activated_throttle` | `throttle.current_limit = config.min_limit` | 인메모리 |

**근거**: 이 핸들러들은 모두 `throttle.current_limit` 인메모리 변수를 수정한다. Celery로 위임하면 limit 반영이 수초 지연되어 **CB OPEN인데 트래픽이 계속 들어오는 safety 문제**가 발생한다.

### 4.2 로깅 전용 핸들러 (2개) — 동기 유지

| 핸들러 | 작업 | 동기 유지 근거 |
|--------|------|---------------|
| `_on_emergency_level_changed` | `logger.info()`, `logger.warning()` | I/O 없음, 즉시 완료 |
| `_on_error_budget_critical` | `logger.warning()` | I/O 없음 |

### 4.3 이미 Celery 위임 중인 핸들러 (2개) — 변경 불필요

| 핸들러 | 위임 방식 |
|--------|-----------|
| `_on_circuit_breaker_closed` | `.delay()` (L832) |
| `_on_circuit_breaker_closed_postmortem` (그룹핑 성공 경로) | `.apply_async()` (L922) |

---

## 5. 파일 변경 목록

### 5.1 새로 생성하는 파일

없음 (기존 Celery task 모듈에 추가)

### 5.2 수정하는 파일

| 파일 | 변경 내용 |
|------|-----------|
| `adapters/celery/tasks/circuit_breaker.py` | `send_cb_open_notification`, `collect_cb_open_snapshot` task 추가 |
| `adapters/celery/tasks/postmortem.py` | `process_individual_postmortem` task 추가 |
| `adapters/celery/tasks/__init__.py` | 새 task 3개 import 및 `__all__` 추가 |
| `services/event_bus/bus.py` | 3개 핸들러를 Thin Wrapper로 변경 |

### 5.3 테스트 영향

| 테스트 파일 | 변경 필요 여부 |
|-------------|---------------|
| `tests/integration/selfhealing/test_throttle_eventbus_integration.py` | 변경 없음 (Throttle 핸들러 미변경) |
| `tests/integration/selfhealing/test_throttle_cb_full_scenario.py` | 변경 없음 |
| `tests/integration/selfhealing/test_postmortem_auto_trigger_integration.py` | Celery task mock 추가 필요 |

---

## 6. 구현 순서

| 단계 | 작업 | 의존성 |
|------|------|--------|
| 1 | `send_cb_open_notification` task 작성 | 없음 |
| 2 | `_on_circuit_breaker_opened_notify` 핸들러 변경 | 단계 1 |
| 3 | `collect_cb_open_snapshot` task 작성 | 없음 |
| 4 | `_on_circuit_breaker_opened_snapshot` 핸들러 변경 | 단계 3 |
| 5 | `process_individual_postmortem` task 작성 | 없음 |
| 6 | `_on_circuit_breaker_closed_postmortem` fallback 경로 변경 | 단계 5 |
| 7 | `_on_emergency_recovery_completed_postmortem` 핸들러 변경 | 단계 5 |
| 8 | `__init__.py` import/export 추가 | 단계 1, 3, 5 |
| 9 | 기존 테스트 수정 + 새 task 단위 테스트 | 단계 1-8 |

---

## 7. Celery Task 설정 근거

### 7.1 설정값 비교표

기존 task 설정을 참고하여 일관성을 유지한다.

| 설정 | `send_cb_open_notification` | `collect_cb_open_snapshot` | `process_individual_postmortem` | 참고: `send_sla_notification` | 참고: `close_incident_group` |
|------|---------------------------|--------------------------|-------------------------------|-------------------------------|------------------------------|
| `queue` | `selfhealing` | `selfhealing` | `selfhealing` | `selfhealing` | `selfhealing` |
| `max_retries` | 3 | 1 | 2 | 3 | 2 |
| `autoretry_for` | `(Exception,)` | — | — | `(Exception,)` | — |
| `default_retry_delay` | 30 | 10 | — | 30 | — |
| `time_limit` | 60 | 30 | 120 | 60 | 120 |
| `soft_time_limit` | 55 | 25 | 110 | 55 | 110 |
| `acks_late` | ✅ | ✅ | ✅ | ✅ | ✅ |

**근거**:
- **알림 task** (`send_cb_open_notification`): `send_sla_notification` (L20-28)과 동일한 재시도/타임아웃. 알림은 재시도 가치가 있으므로 `autoretry_for=(Exception,)`
- **스냅샷 task** (`collect_cb_open_snapshot`): 시간에 민감하므로 `time_limit=30`, `max_retries=1`
- **포스트모템 task** (`process_individual_postmortem`): `close_incident_group` (L32-38)과 동일. DB Write + 알림 포함이므로 `time_limit=120`

---

## 8. 예상 효과

### 8.1 `CIRCUIT_BREAKER_OPENED` 이벤트 발행 시 차단 시간

| 단계 | Before | After |
|------|--------|-------|
| `_on_circuit_breaker_opened_notify` | Slack HTTP 200ms~30s | `.delay()` ~1ms |
| `_on_circuit_breaker_opened_snapshot` | psutil 100ms + Redis | `.delay()` ~1ms |
| `_on_circuit_breaker_opened_throttle` | limit 변경 ~μs | 변경 없음 ~μs |
| **합계** | **300ms ~ 30s+** | **~2ms + μs** |

### 8.2 `CIRCUIT_BREAKER_CLOSED` 이벤트 발행 시 차단 시간 (그룹핑 실패 fallback)

| 단계 | Before | After |
|------|--------|-------|
| `_on_circuit_breaker_closed` | `.delay()` ~1ms | 변경 없음 |
| `_on_circuit_breaker_closed_throttle` | limit 변경 ~μs | 변경 없음 |
| `_on_circuit_breaker_closed_postmortem` (fallback) | 스냅샷+생성+저장+알림 수백ms~수초 | `.delay()` ~1ms |
| **합계** | **수백ms ~ 수초** | **~2ms** |

### 8.3 `EMERGENCY_RECOVERY_COMPLETED` 이벤트 발행 시 차단 시간

| 단계 | Before | After |
|------|--------|-------|
| `_on_emergency_recovery_completed_postmortem` | 스냅샷+생성+저장+WAL+알림 수백ms~수초 | `.delay()` ~1ms |
| **합계** | **수백ms ~ 수초** | **~1ms** |

---

## 9. 롤백 전략

모든 변경된 핸들러에 `ImportError` fallback이 있으므로:

1. **Celery worker 중단 시**: task가 브로커에 유지됨 (`acks_late=True`)
2. **Celery 미설치 환경**: `ImportError` → 기존 동기 방식으로 자동 fallback
3. **코드 롤백 필요 시**: 핸들러를 원래 동기 코드로 복원 (Celery task는 미사용 상태로 남겨도 무해)

---

## 10. 참조 문서

| 문서 | 관련 내용 |
|------|-----------|
| 128_POSTMORTEM_AUTO_TRIGGER.md | Postmortem 자동 생성 설계 |
| 147_POSTMORTEM_INCIDENT_GROUP.md | IncidentGroup 설계 |
| 152_ADAPTIVE_THROTTLE_EVENTBUS_INTEGRATION.md | EventBus-Throttle 연동 |
| 153_ADAPTIVE_THROTTLE_CIRCUIT_BREAKER_INTEGRATION.md | CB-Throttle 연동 |
| 23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md | CB 알림 설계 |

---

## 11. 리뷰 반영 사항

> 코드 기반 검증을 통해 확인된 6개 리뷰 항목과 반영 위치.

### 리뷰 1: `SelfHealingEvent.to_dict()` 직렬화

- **지적**: Celery JSON serializer에 SelfHealingEvent 객체를 직접 전달하면 직렬화 실패
- **코드 근거**: `bus.py` L174-182 — `to_dict()`가 모든 필드를 `str`/`dict`/`None` 원시 타입으로 변환
- **반영**: §3.2 알림 task, §3.4.1 postmortem task의 `event_data` 파라미터 docstring에 직렬화 규칙 명시. §3.4.2~3.4.3 핸들러 코드에 `event.to_dict()` 호출과 주석 추가

### 리뷰 2: Worker CPU 측정 한계

- **지적**: `collect_system_snapshot()`의 `psutil.cpu_percent(interval=0.1)`은 로컬 호스트 CPU를 측정하므로 Worker ≠ Web Server
- **코드 근거**: `base.py` L620-699 — psutil은 프로세스가 실행 중인 머신의 리소스만 측정
- **반영**: §3.3.1 `collect_cb_open_snapshot` task docstring에 Worker CPU 한계 경고 추가, `snapshot_source: "celery_worker"` 필드와 `snapshot_note` 주석 추가

### 리뷰 3: `get_healing_events(use_redis=True)` 명시

- **지적**: Redis가 기본값이지만 Worker 환경에서 인메모리 fallback이 아닌 Redis 사용을 명시적으로 보장해야 함
- **코드 근거**: `base.py` L730-762 — `use_redis=True`가 기본값, `get_healing_events_redis()` 호출
- **반영**: §3.4.1 postmortem task의 Worker 환경 주의 docstring에 `use_redis=True` 명시 및 코드 참조 추가

### 리뷰 4: CB 상태 Redis 기반 — Worker 접근 가능

- **지적**: CB 상태를 Worker에서 조회할 수 있는지 확인 필요
- **코드 근거**: `factory.py` L66 — `ProviderRegistry._default_repo = "redis"`, `circuit_breaker.py` L272-313 — `RedisCircuitBreakerStateRepository.get_all_states()` Redis keys scan, `store.py` L531-543 — `cb_service.repository.get_all_states()`
- **반영**: §3.3.1 snapshot task docstring에 Redis CB 접근 가능 확인 주석 추가. §3.4.1 postmortem task docstring에 `collect_service_states()` Redis 기반 설명 추가

### 리뷰 5: 네이밍 — 기존 코드베이스 패턴 기반

- **지적**: task 이름이 기존 시스템 네이밍 패턴과 일관성을 유지해야 함
- **변경 내역**:
  - `capture_cb_open_snapshot` → **`collect_cb_open_snapshot`**: 기존 `collect_self_healing_metrics` 패턴과 일치 (수집 행위)
  - `generate_individual_postmortem` → **`process_individual_postmortem`**: 이 Task는 "생성"뿐 아니라 "수집 → 생성 → 저장 → 감사 → 알림"을 수행하므로 포괄적인 `process_`가 적합. 기존 `_create_individual_postmortems` (postmortem.py L153)은 private 함수이므로 `create_`를 쓰면 혼동
  - `send_cb_open_notification`은 `send_sla_notification` 패턴과 일치하여 그대로 유지
- **반영**: §3.2, §3.3, §3.4.1에 네이밍 근거 주석 추가. §5, §6, §7 전체 이름 변경

### 리뷰 6 (Critical): `bus.get_history()` 인메모리 — Worker 접근 불가

- **지적**: `bus.get_history()`의 데이터 소스가 프로세스 로컬 인메모리(`self._event_history: list[dict]`)이므로 Celery Worker에서 호출하면 빈 리스트 반환
- **코드 근거**: `bus.py` L243 — `self._event_history: list[dict[str, Any]] = []` (프로세스별 독립), `bus.py` L455 — `history = list(self._event_history)`
- **영향**: postmortem의 Timeline 빌드에 EventBus 히스토리가 필요하나, Worker에서는 Web Server의 히스토리를 볼 수 없음
- **해결**: `process_individual_postmortem` task에 `event_bus_history: list[dict] | None = None` 파라미터 추가. 핸들러에서 `.delay()` 호출 전에 `bus.get_history(limit=history_limit)`로 미리 수집하여 전달
- **반영**: §3.4.1 task 시그니처에 `event_bus_history` 파라미터와 상세 docstring 추가. §3.4.2~3.4.3 핸들러 코드에 `get_event_bus().get_history()` 사전 수집 로직과 주석 추가
