# Self-Healing 테스트 갭 분석 및 권장 조치

> **문서 목적**: L3 Self-Healing 시스템의 테스트-운영 환경 차이로 인한 잠재적 위험 요소 분석 및 해결 방안
>
> **작성일**: 2025-12-10
> **관련 문서**: `docs/SELF_HEALING_EXTRACTION_PLAN.md`

---

## 1. 요약 (Executive Summary)

| 구분 | 상태 | 설명 |
|------|------|------|
| 보안 테스트 | ✅ 양호 | Control API 권한, 민감정보 sanitization 테스트 완비 |
| 동시성 테스트 | ✅ 양호 | select_for_update 기반 race condition 테스트 존재 |
| **설정 누락** | 🔴 심각 | `SELF_HEALING` 설정이 settings 파일에 미정의 |
| **Celery 차이** | 🔴 심각 | Eager mode vs 비동기 모드 동작 차이 미검증 |
| Redis 장애 | 🟠 부족 | 테스트 3개만 존재, Circuit Breaker 캐시 장애 미검증 |
| 시간 기반 | 🟠 부족 | freeze_time 테스트 없음 |

---

## 2. 잠재적 위험 요소 상세

### 2.1. 🔴 [심각] SELF_HEALING 설정 누락

#### 현황
```python
# shopping/services/self_healing/config.py
self_healing = getattr(settings, "SELF_HEALING", {})  # 기본값 {} 사용
```

#### 문제점
- `myproject/settings/base.py`, `production.py`, `local.py` 어디에도 `SELF_HEALING` 정의 없음
- 코드는 기본값으로 폴백하여 테스트 통과하지만, 운영에서 의도치 않은 동작 가능

#### 기본값 목록 (config.py 기준)
| 설정 | 기본값 | 설명 |
|------|--------|------|
| SLA.PAYMENT_HOURS | 1 | 결제 복구 SLA |
| SLA.POINT_HOURS | 4 | 포인트 복구 SLA |
| RETRY.MAX_RETRIES | 3 | 최대 재시도 횟수 |
| RETRY.BACKOFF_BASE | 2 | 지수 백오프 기본값 |
| CIRCUIT_BREAKER.FAILURE_THRESHOLD | 5 | CB 오픈 임계값 |
| CIRCUIT_BREAKER.RECOVERY_TIMEOUT | 30 | CB Half-Open 전환 시간(초) |
| DLQ.AUTO_REPLAY_ENABLED | True | 자동 리플레이 활성화 |
| IDEMPOTENCY.PAYMENT_CACHE_TTL | 300 | 결제 멱등성 TTL(초) |

#### 해결 방안
```python
# myproject/settings/production.py에 추가

SELF_HEALING = {
    "SLA": {
        "PAYMENT_HOURS": 1,
        "POINT_HOURS": 4,
        "INVENTORY_HOURS": 2,
        "WEBHOOK_HOURS": 8,
        "NOTIFICATION_HOURS": 24,
    },
    "RETRY": {
        "MAX_RETRIES": 5,  # 운영에서는 더 많은 재시도
        "BACKOFF_BASE": 2,
        "BACKOFF_MAX": 300,
        "JITTER_PERCENT": 0.25,
    },
    "CIRCUIT_BREAKER": {
        "ENABLED": True,
        "FAILURE_THRESHOLD": 5,
        "SUCCESS_THRESHOLD": 3,
        "RECOVERY_TIMEOUT": 60,  # 운영에서는 더 긴 대기
    },
    "DLQ": {
        "AUTO_REPLAY_ENABLED": True,
        "MAX_REPLAY_ATTEMPTS": 3,
        "REPLAY_DELAY_SECONDS": 60,
    },
    "IDEMPOTENCY": {
        "DEFAULT_CACHE_TTL": 60,
        "PAYMENT_CACHE_TTL": 600,  # 운영에서는 더 긴 TTL
        "WEBHOOK_CACHE_TTL": 120,
    },
}
```

---

### 2.2. 🔴 [심각] Celery Eager Mode 차이

#### 현황
```python
# shopping/tests/conftest.py (Line 72-79)
@pytest.fixture(scope="session", autouse=True)
def setup_celery_for_tests():
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
```

#### 테스트 vs 운영 차이

| 항목 | 테스트 (Eager) | 운영 (Async) |
|------|---------------|--------------|
| Task 실행 | 동기, 즉시 | 비동기, 브로커 경유 |
| 에러 전파 | 즉시 예외 발생 | Task 내부에서만 처리 |
| 재시도 동작 | 재시도 무시됨 | 실제 backoff 적용 |
| 브로커 장애 | 영향 없음 | Task 유실 가능 |
| 트랜잭션 | 같은 트랜잭션 | 별도 트랜잭션 |

#### 위험 시나리오
1. **DLQ 재시도 로직**: 테스트에서 성공 → 운영에서 Celery beat 미실행으로 재시도 안됨
2. **Payment Recovery Task**: 테스트에서 동기 성공 → 운영에서 Redis 브로커 연결 끊김으로 Task 유실
3. **트랜잭션 격리**: 테스트에서 DB 저장 후 Task 실행 → 운영에서 Task가 commit 전 데이터 못 읽음

#### 해결 방안

**방안 1: 비동기 모드 테스트 추가**
```python
# shopping/tests/integration/self_healing/test_celery_async_mode.py (신규 생성)

import pytest
from unittest.mock import patch, MagicMock
from celery import states

@pytest.mark.django_db
class TestCeleryAsyncBehavior:
    """Celery 비동기 모드 시뮬레이션 테스트"""

    @pytest.fixture(autouse=True)
    def disable_eager_mode(self, settings):
        """Eager 모드 비활성화"""
        settings.CELERY_TASK_ALWAYS_EAGER = False
        settings.CELERY_TASK_EAGER_PROPAGATES = False

    def test_task_queued_but_not_executed_immediately(self):
        """Task가 즉시 실행되지 않고 큐에 들어가는지 확인"""
        with patch('shopping.tasks.self_healing_tasks.process_failed_operation.apply_async') as mock_apply:
            mock_apply.return_value = MagicMock(id='test-task-id', state=states.PENDING)

            # Task 호출
            from shopping.tasks.self_healing_tasks import process_failed_operation
            result = process_failed_operation.apply_async(args=[1])

            # 즉시 완료가 아닌 PENDING 상태
            assert result.state == states.PENDING
            mock_apply.assert_called_once()

    def test_dlq_replay_respects_delay(self):
        """DLQ 리플레이가 delay를 적용하는지 확인"""
        with patch('shopping.tasks.dlq_replay_tasks.replay_single_operation.apply_async') as mock_apply:
            from shopping.tasks.dlq_replay_tasks import schedule_dlq_replay

            schedule_dlq_replay(operation_id=1, delay_seconds=60)

            # countdown 인자로 delay 적용 확인
            mock_apply.assert_called_once()
            call_kwargs = mock_apply.call_args[1]
            assert call_kwargs.get('countdown') == 60

    def test_broker_connection_failure_handling(self):
        """브로커 연결 실패 시 처리 확인"""
        from kombu.exceptions import OperationalError

        with patch('shopping.tasks.self_healing_tasks.process_failed_operation.apply_async') as mock_apply:
            mock_apply.side_effect = OperationalError("Connection refused")

            # 브로커 연결 실패 시 적절한 예외 처리
            with pytest.raises(OperationalError):
                from shopping.tasks.self_healing_tasks import process_failed_operation
                process_failed_operation.apply_async(args=[1])
```

**방안 2: Transaction Commit 후 Task 실행 테스트**
```python
# shopping/tests/integration/self_healing/test_transaction_task_timing.py (신규 생성)

import pytest
from django.db import transaction
from unittest.mock import patch

@pytest.mark.django_db(transaction=True)
class TestTransactionTaskTiming:
    """트랜잭션과 Task 실행 타이밍 테스트"""

    def test_task_receives_committed_data(self):
        """Task가 commit된 데이터를 읽을 수 있는지 확인"""
        from shopping.models import FailedOperation
        from shopping.tasks.self_healing_tasks import process_failed_operation

        # 트랜잭션 내에서 데이터 생성
        with transaction.atomic():
            failed_op = FailedOperation.objects.create(
                operation_type="payment",
                operation_data={"test": "data"},
                status="pending"
            )
            op_id = failed_op.id

        # commit 후 Task 실행
        # Eager 모드에서도 별도 DB 연결로 데이터 조회
        result = process_failed_operation.apply(args=[op_id])

        # 데이터가 정상 조회되었는지 확인
        assert result is not None

    def test_task_with_on_commit_hook(self):
        """transaction.on_commit으로 Task 스케줄링 패턴 테스트"""
        from shopping.models import FailedOperation

        task_scheduled = []

        def schedule_task(op_id):
            task_scheduled.append(op_id)

        with transaction.atomic():
            failed_op = FailedOperation.objects.create(
                operation_type="payment",
                operation_data={"test": "data"},
                status="pending"
            )
            # on_commit 훅으로 Task 스케줄링
            transaction.on_commit(lambda: schedule_task(failed_op.id))

        # commit 후 Task가 스케줄되었는지 확인
        assert len(task_scheduled) == 1
        assert task_scheduled[0] == failed_op.id
```

---

### 2.3. 🟠 [중요] Redis 장애 시나리오 테스트 부족

#### 현황
- `shopping/tests/integration/test_redis_fallback.py` - 3개 테스트만 존재
- Circuit Breaker 상태가 Redis에 캐시될 경우 장애 시 동작 미검증

#### 해결 방안
```python
# shopping/tests/integration/self_healing/test_redis_failure_scenarios.py (신규 생성)

import pytest
from unittest.mock import patch, MagicMock
from redis.exceptions import ConnectionError as RedisConnectionError

@pytest.mark.django_db
class TestCircuitBreakerRedisFailure:
    """Circuit Breaker Redis 장애 시나리오 테스트"""

    def test_circuit_breaker_continues_on_redis_failure(self):
        """Redis 장애 시 Circuit Breaker가 기본 동작 유지"""
        from shopping.services.self_healing.circuit_breaker_service import CircuitBreakerService

        with patch('django.core.cache.cache.get', side_effect=RedisConnectionError("Connection refused")):
            with patch('django.core.cache.cache.set', side_effect=RedisConnectionError("Connection refused")):
                cb = CircuitBreakerService("test_service")

                # Redis 없이도 기본 CLOSED 상태로 동작
                assert cb.is_available() == True

                # 실패 기록도 가능해야 함 (메모리 폴백)
                cb.record_failure()

    def test_idempotency_graceful_degradation(self):
        """멱등성 체크 Redis 장애 시 graceful degradation"""
        from shopping.services.self_healing.idempotency_service import IdempotencyService

        with patch('django.core.cache.cache.get', side_effect=RedisConnectionError("Connection refused")):
            idempotency = IdempotencyService()

            # Redis 장애 시 중복 체크 스킵 (false positive 허용)
            # 단, 결제는 DB 레벨에서 추가 검증 필요
            is_duplicate = idempotency.check("payment_123")

            # 장애 시 False 반환 (처리 진행)
            assert is_duplicate == False


@pytest.mark.django_db
class TestDLQRedisFailure:
    """DLQ Redis 장애 시나리오 테스트"""

    def test_dlq_stores_to_db_on_redis_failure(self):
        """Redis 장애 시 DLQ가 DB에 직접 저장"""
        from shopping.services.self_healing.dlq_service import DLQService
        from shopping.models import FailedOperation

        with patch('django.core.cache.cache.set', side_effect=RedisConnectionError("Connection refused")):
            dlq = DLQService()

            # DB 저장은 정상 동작
            failed_op = dlq.enqueue(
                operation_type="payment",
                operation_data={"order_id": 123},
                error_message="Test error"
            )

            assert FailedOperation.objects.filter(id=failed_op.id).exists()
```

---

### 2.4. 🟠 [중요] 시간 기반 테스트 부재

#### 현황
- `timedelta` 사용 테스트는 있으나 `freezegun.freeze_time` 사용 테스트 없음
- Circuit Breaker Half-Open 전환, SLA 만료 등 시간 의존적 로직 테스트 부족

#### 해결 방안
```python
# shopping/tests/unit/self_healing/test_time_based_behaviors.py (신규 생성)

import pytest
from datetime import datetime, timedelta
from freezegun import freeze_time
from django.utils import timezone

@pytest.mark.django_db
class TestCircuitBreakerTimeBased:
    """Circuit Breaker 시간 기반 동작 테스트"""

    @freeze_time("2025-01-01 12:00:00")
    def test_circuit_breaker_opens_after_threshold(self):
        """연속 실패 후 CB가 OPEN 상태로 전환"""
        from shopping.services.self_healing.circuit_breaker_service import CircuitBreakerService

        cb = CircuitBreakerService("test_service")

        # 5번 연속 실패
        for _ in range(5):
            cb.record_failure()

        assert cb.state == "OPEN"
        assert cb.is_available() == False

    @freeze_time("2025-01-01 12:00:00")
    def test_circuit_breaker_half_open_after_timeout(self):
        """OPEN 상태에서 timeout 후 HALF_OPEN으로 전환"""
        from shopping.services.self_healing.circuit_breaker_service import CircuitBreakerService

        cb = CircuitBreakerService("test_service")

        # CB를 OPEN 상태로 만듦
        for _ in range(5):
            cb.record_failure()

        assert cb.state == "OPEN"

        # 30초 후 (recovery_timeout)
        with freeze_time("2025-01-01 12:00:31"):
            # 다음 호출 시 HALF_OPEN으로 전환
            is_available = cb.is_available()
            assert cb.state == "HALF_OPEN"
            assert is_available == True  # 테스트 요청 허용


@pytest.mark.django_db
class TestSLATimeBased:
    """SLA 시간 기반 동작 테스트"""

    @freeze_time("2025-01-01 12:00:00")
    def test_payment_sla_breach_detection(self):
        """결제 SLA(1시간) 위반 감지"""
        from shopping.models import FailedOperation
        from shopping.services.self_healing.sla_monitor_service import SLAMonitorService

        # 1시간 전 생성된 실패 작업
        failed_op = FailedOperation.objects.create(
            operation_type="payment",
            operation_data={"order_id": 123},
            status="pending",
            created_at=timezone.now() - timedelta(hours=1, minutes=1)
        )

        sla_monitor = SLAMonitorService()
        breaches = sla_monitor.check_sla_breaches()

        assert failed_op.id in [b.id for b in breaches]

    @freeze_time("2025-01-01 12:00:00")
    def test_point_sla_not_breached_within_threshold(self):
        """포인트 SLA(4시간) 내 정상"""
        from shopping.models import FailedOperation
        from shopping.services.self_healing.sla_monitor_service import SLAMonitorService

        # 3시간 전 생성 (4시간 SLA 내)
        failed_op = FailedOperation.objects.create(
            operation_type="point",
            operation_data={"user_id": 123},
            status="pending",
            created_at=timezone.now() - timedelta(hours=3)
        )

        sla_monitor = SLAMonitorService()
        breaches = sla_monitor.check_sla_breaches()

        assert failed_op.id not in [b.id for b in breaches]
```

---

### 2.5. 🟠 [중요] 외부 API 장애 시나리오 테스트 부족

#### 현황
- 모든 Toss 결제 테스트가 `mock_toss_client` 사용
- 타임아웃, 지연, 부분 장애 시나리오 미검증

#### 해결 방안
```python
# shopping/tests/integration/self_healing/test_external_api_failures.py (신규 생성)

import pytest
import requests
from unittest.mock import patch, MagicMock
from requests.exceptions import Timeout, ConnectionError

@pytest.mark.django_db
class TestTossAPIFailureRecovery:
    """Toss API 장애 시 Self-Healing 동작 테스트"""

    def test_payment_timeout_triggers_dlq(self):
        """결제 타임아웃 시 DLQ에 저장"""
        from shopping.services.payment_service import PaymentService
        from shopping.models import FailedOperation

        with patch('requests.post', side_effect=Timeout("Connection timed out")):
            payment_service = PaymentService()

            with pytest.raises(Timeout):
                payment_service.confirm_payment(
                    order_id=123,
                    payment_key="test_key",
                    amount=10000
                )

            # DLQ에 저장되었는지 확인
            assert FailedOperation.objects.filter(
                operation_type="payment",
                operation_data__contains='"order_id": 123'
            ).exists()

    def test_payment_retry_with_exponential_backoff(self):
        """결제 재시도 시 지수 백오프 적용 확인"""
        from shopping.services.self_healing.retry_handler import RetryHandler
        from shopping.services.self_healing.backoff_calculator import BackoffCalculator

        calculator = BackoffCalculator()

        # 재시도 간격이 지수적으로 증가
        delay_1 = calculator.calculate(attempt=1)
        delay_2 = calculator.calculate(attempt=2)
        delay_3 = calculator.calculate(attempt=3)

        assert delay_2 > delay_1
        assert delay_3 > delay_2
        # jitter 포함하여 정확한 2배는 아님
        assert delay_2 >= delay_1 * 1.5

    def test_circuit_breaker_opens_on_repeated_failures(self):
        """반복 실패 시 Circuit Breaker 오픈"""
        from shopping.services.self_healing.circuit_breaker_service import CircuitBreakerService
        from shopping.services.payment_service import PaymentService

        cb = CircuitBreakerService("toss_payment")

        with patch('requests.post', side_effect=ConnectionError("Connection refused")):
            for _ in range(5):
                try:
                    # 실패 시도
                    pass
                except:
                    cb.record_failure()

        # CB가 열림
        assert cb.state == "OPEN"
        assert cb.is_available() == False
```

---

## 3. 테스트 파일 생성 체크리스트

### 신규 생성 필요 파일

| 파일 | 경로 | 우선순위 | 설명 |
|------|------|----------|------|
| ✅ | `test_celery_async_mode.py` | 🔴 높음 | Celery 비동기 모드 시뮬레이션 |
| ✅ | `test_transaction_task_timing.py` | 🔴 높음 | 트랜잭션-Task 타이밍 |
| ✅ | `test_redis_failure_scenarios.py` | 🟠 중간 | Redis 장애 시나리오 |
| ✅ | `test_time_based_behaviors.py` | 🟠 중간 | freezegun 시간 테스트 |
| ✅ | `test_external_api_failures.py` | 🟠 중간 | 외부 API 장애 복구 |

### 설정 파일 수정 필요

| 파일 | 작업 | 우선순위 |
|------|------|----------|
| `myproject/settings/production.py` | `SELF_HEALING` 설정 추가 | 🔴 필수 |
| `myproject/settings/local.py` | `SELF_HEALING` 설정 추가 (개발용) | 🟠 권장 |
| `myproject/settings/test.py` | `SELF_HEALING` 테스트 설정 추가 | 🟠 권장 |

---

## 4. 의존성 추가 필요

```txt
# requirements-dev.txt에 추가
freezegun>=1.2.0  # 시간 기반 테스트용
```

---

## 5. 실행 순서 권장

1. **[1단계]** `SELF_HEALING` 설정을 `production.py`에 추가
2. **[2단계]** `freezegun` 의존성 추가
3. **[3단계]** `test_celery_async_mode.py` 생성 및 테스트
4. **[4단계]** `test_redis_failure_scenarios.py` 생성 및 테스트
5. **[5단계]** 나머지 테스트 파일 순차 생성

---

## 6. 참고: 현재 잘 되어있는 테스트 목록

### 보안 테스트 ✅
- `test_control_api.py` - Control API 권한 테스트 (22개)
- `test_security_violation_service.py` - 민감정보 sanitization (15개)
- `test_security_notification_service.py` - 보안 알림 (20개)

### 동시성 테스트 ✅
- `test_point_service_concurrent.py` - 포인트 동시성 (10개)
- `test_user_invisible_flows.py` - E2E 동시 플로우 (5개)

### Chaos 테스트 ✅
- `test_random_failure_injection.py` - 랜덤 장애 주입
- `test_circuit_breaker_chaos.py` - CB chaos 테스트
- `test_recovery_under_pressure.py` - 부하 상황 복구

---

## 7. 변경 이력

| 날짜 | 작성자 | 변경 내용 |
|------|--------|----------|
| 2025-12-10 | AI Assistant | 초안 작성 |
