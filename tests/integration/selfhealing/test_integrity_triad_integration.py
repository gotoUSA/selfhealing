"""
Hash Chain Integrity Triad 통합 테스트 — Mock 없이 실제 인프라 사용.

실제 Redis + 실제 WAL + 실제 HashChainVerifier + 실제 EventBus를 연결하여
전체 무결성 검증 흐름을 end-to-end로 테스트합니다.

검증 항목:
- CB CLOSED 이벤트 → IntegrityGate CRITICAL 핸들러 → WAL 데이터 수집 →
  HashChainVerifier 검증 → event.data[integrity_failed] 설정 → Replay 허용/차단
- 실제 Redis IntegrityHealthScore 업데이트 확인
- 실제 WAL 파일 I/O 기반 엔트리 복구 확인

Requirements:
- Docker Compose (Redis + Postgres):
    docker-compose -f docker-compose.test.yml up -d db redis
- Run:
    docker-compose -f docker-compose.test.yml run test-integrity-triad
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django  # noqa: E402

django.setup()

from selfhealing.audit.integrity.models import compute_hash  # noqa: E402
from selfhealing.services.event_bus.integrity_gate import (  # noqa: E402
    INTEGRITY_FAILED_KEY,
    INTEGRITY_GATE_KEY,
)


# =============================================================================
# Helpers — 실제 해시 체인 엔트리 빌드
# =============================================================================

GENESIS_HASH = "GENESIS"


def _build_valid_chain(n: int = 3) -> list[dict]:
    """HashChainVerifier.verify_chain()이 (True, None)을 반환하는 정상 체인 생성."""
    entries: list[dict] = []
    previous_hash = GENESIS_HASH
    for seq in range(1, n + 1):
        entry = {
            "event_type": "TEST_AUDIT",
            "source": "integration_test",
            "details": {"index": seq},
            "integrity": {
                "sequence": seq,
                "previous_hash": previous_hash,
                "timestamp": f"2026-02-12T00:00:{seq:02d}Z",
            },
        }
        current_hash = compute_hash(entry)
        entry["integrity"]["current_hash"] = current_hash
        entries.append(entry)
        previous_hash = current_hash
    return entries


def _build_broken_chain(n: int = 3, tamper_at: int = 2) -> list[dict]:
    """
    tamper_at 시퀀스의 데이터를 변조하여 해시 불일치를 유발하는 체인 생성.
    HashChainVerifier.verify_chain()이 (False, ...) 를 반환합니다.
    """
    entries = _build_valid_chain(n)
    # tamper_at 번째 엔트리의 데이터를 변조 (해시 재계산 없이)
    idx = tamper_at - 1
    entries[idx]["details"]["tampered"] = True  # 해시와 불일치
    return entries


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_all_singletons():
    """각 테스트 전/후 EventBus, AuditIntegritySettings, HealthScore, WAL 초기화."""
    from selfhealing.audit.integrity.health_score import reset_integrity_health_score
    from selfhealing.services.event_bus import get_event_bus
    from selfhealing.settings.audit_integrity import reset_audit_integrity_settings
    import selfhealing.services.audit.base as audit_base

    get_event_bus().reset()
    reset_audit_integrity_settings()
    reset_integrity_health_score()

    yield

    get_event_bus().reset()
    reset_audit_integrity_settings()
    reset_integrity_health_score()
    audit_base._wal_instance = None
    audit_base._wal_enabled = True


@pytest.fixture()
def real_wal(tmp_path):
    """
    실제 파일 기반 WAL을 생성하고 _get_wal() 싱글톤으로 주입.

    AUDIT_WAL_DIR 환경변수를 tmp_path로 설정한 후 _get_wal()을 호출해
    게이트가 사용하는 것과 동일한 싱글톤 인스턴스를 반환합니다.
    이렇게 하면 fixture와 게이트가 동일한 WAL 인스턴스를 공유합니다.
    """
    import selfhealing.services.audit.base as audit_base

    # 기존 싱글톤 강제 제거 → 재생성 유도
    audit_base._wal_instance = None
    audit_base._wal_enabled = True

    # 임시 디렉토리를 WAL 경로로 설정
    old_dir = os.environ.get("AUDIT_WAL_DIR")
    os.environ["AUDIT_WAL_DIR"] = str(tmp_path)

    # _get_wal()을 호출하여 게이트와 동일한 싱글톤 생성
    wal = audit_base._get_wal()
    assert wal is not None, "WAL should be created at tmp_path"

    yield wal

    # 정리
    audit_base._wal_instance = None
    audit_base._wal_enabled = True
    if old_dir is not None:
        os.environ["AUDIT_WAL_DIR"] = old_dir
    else:
        os.environ.pop("AUDIT_WAL_DIR", None)


@pytest.fixture()
def redis_client():
    """실제 Redis 클라이언트 (Docker Compose redis 서비스)."""
    from selfhealing.adapters.redis import get_redis_client

    client = get_redis_client()
    assert client is not None, "Redis must be available (docker-compose up -d redis)"
    client.ping()
    # 테스트 전 관련 키 정리
    for key in client.scan_iter("selfhealing:audit:hash_chain:*"):
        client.delete(key)
    yield client
    # 테스트 후 정리
    for key in client.scan_iter("selfhealing:audit:hash_chain:*"):
        client.delete(key)


# =============================================================================
# Tests — 실제 인프라 기반 통합 테스트
# =============================================================================


class TestIntegrityGateEventBusIntegration:
    """
    IntegrityGate ↔ EventBus ↔ WAL ↔ HashChainVerifier 통합 테스트.

    Mock 0개. 실제 Redis + 실제 WAL 파일 + 실제 Verifier.
    """

    def test_integrity_gate_registered_as_critical_priority(self):
        """register_default_handlers() 호출 시 IntegrityGate가 CRITICAL로 등록된다."""
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.bus import register_default_handlers

        bus = SelfHealingEventBus()
        bus.reset()
        register_default_handlers()

        subscriptions = bus.get_subscriptions(EventType.CIRCUIT_BREAKER_CLOSED)
        has_critical = any(sub["priority"] == EventPriority.CRITICAL.name for sub in subscriptions)
        assert has_critical, "IntegrityGate must be registered with CRITICAL priority"

    def test_valid_chain_allows_replay(self, real_wal, redis_client):
        """
        정상 해시체인 WAL → IntegrityGate 통과 → 리플레이 허용.

        실제 WAL에 정상 체인 엔트리를 기록하고,
        EventBus를 통해 CB CLOSED 이벤트를 발행하면
        IntegrityGate가 무결성을 검증하여 리플레이를 허용한다.
        """
        from selfhealing.audit.integrity.health_score import (
            get_integrity_health_score,
            reset_integrity_health_score,
        )
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            SelfHealingEvent,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.integrity_gate import (
            on_circuit_breaker_closed_integrity_gate,
        )

        # 1) 실제 HealthScore 싱글톤에 Redis 클라이언트 주입
        reset_integrity_health_score()
        health = get_integrity_health_score(redis_client=redis_client)

        # 2) 실제 WAL에 정상 해시 체인 기록
        chain = _build_valid_chain(5)
        for entry in chain:
            real_wal.write(entry)

        # 3) EventBus 구성: IntegrityGate (CRITICAL) + Replay (NORMAL)
        bus = SelfHealingEventBus()
        bus.reset()

        replay_called = []

        def replay_handler(event):
            if not event.data.get(INTEGRITY_FAILED_KEY, False):
                replay_called.append(True)

        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            on_circuit_breaker_closed_integrity_gate,
            priority=EventPriority.CRITICAL,
        )
        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            replay_handler,
            priority=EventPriority.NORMAL,
        )

        # 4) 이벤트 발행
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="integration_test",
        )
        bus.publish(event)

        # 5) 검증
        assert event.data.get(INTEGRITY_FAILED_KEY) is False
        assert len(replay_called) == 1, "Replay should be allowed on valid chain"

        gate = event.data.get(INTEGRITY_GATE_KEY, {})
        assert gate["valid"] is True
        assert gate["checked"] == 5
        assert gate["strategy"] == "wal_chain_verify"
        assert "duration_ms" in gate

    def test_broken_chain_blocks_replay(self, real_wal, redis_client):
        """
        위반 해시체인 WAL → IntegrityGate 차단 → 리플레이 블록.

        변조된 엔트리가 포함된 WAL을 IntegrityGate가 감지하여
        integrity_failed=True를 설정하고, Replay 핸들러가 실행되지 않는다.
        """
        from selfhealing.audit.integrity.health_score import (
            get_integrity_health_score,
            reset_integrity_health_score,
        )
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            SelfHealingEvent,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.integrity_gate import (
            on_circuit_breaker_closed_integrity_gate,
        )

        reset_integrity_health_score()
        get_integrity_health_score(redis_client=redis_client)

        # 변조된 체인 WAL에 기록
        chain = _build_broken_chain(5, tamper_at=3)
        for entry in chain:
            real_wal.write(entry)

        bus = SelfHealingEventBus()
        bus.reset()

        replay_called = []

        def replay_handler(event):
            if not event.data.get(INTEGRITY_FAILED_KEY, False):
                replay_called.append(True)

        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            on_circuit_breaker_closed_integrity_gate,
            priority=EventPriority.CRITICAL,
        )
        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            replay_handler,
            priority=EventPriority.NORMAL,
        )

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="integration_test",
        )
        bus.publish(event)

        assert event.data.get(INTEGRITY_FAILED_KEY) is True
        assert len(replay_called) == 0, "Replay must be BLOCKED on broken chain"

        gate = event.data.get(INTEGRITY_GATE_KEY, {})
        assert gate["valid"] is False
        assert gate["checked"] == 5
        assert gate["strategy"] == "wal_chain_verify"

    def test_critical_priority_executes_before_normal(self, real_wal, redis_client):
        """CRITICAL → NORMAL → LOW 순서로 핸들러가 실행된다."""
        from selfhealing.audit.integrity.health_score import (
            get_integrity_health_score,
            reset_integrity_health_score,
        )
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            SelfHealingEvent,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.integrity_gate import (
            on_circuit_breaker_closed_integrity_gate,
        )

        reset_integrity_health_score()
        get_integrity_health_score(redis_client=redis_client)

        # 빈 WAL → 게이트 통과
        bus = SelfHealingEventBus()
        bus.reset()

        execution_order = []

        def critical_handler(event):
            execution_order.append("critical")
            on_circuit_breaker_closed_integrity_gate(event)

        def normal_handler(event):
            execution_order.append("normal")

        def low_handler(event):
            execution_order.append("low")

        bus.subscribe(EventType.CIRCUIT_BREAKER_CLOSED, low_handler, priority=EventPriority.LOW)
        bus.subscribe(EventType.CIRCUIT_BREAKER_CLOSED, normal_handler, priority=EventPriority.NORMAL)
        bus.subscribe(EventType.CIRCUIT_BREAKER_CLOSED, critical_handler, priority=EventPriority.CRITICAL)

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="integration_test",
        )
        bus.publish(event)

        assert execution_order == ["critical", "normal", "low"], f"Expected [critical, normal, low], got {execution_order}"

    def test_empty_wal_passes_gate(self, real_wal, redis_client):
        """WAL에 엔트리가 없으면 게이트를 통과한다 (strategy=no_entries)."""
        from selfhealing.audit.integrity.health_score import (
            get_integrity_health_score,
            reset_integrity_health_score,
        )
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            SelfHealingEvent,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.integrity_gate import (
            on_circuit_breaker_closed_integrity_gate,
        )

        reset_integrity_health_score()
        get_integrity_health_score(redis_client=redis_client)

        # WAL은 비어있음 — 기록 없이 테스트
        bus = SelfHealingEventBus()
        bus.reset()

        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            on_circuit_breaker_closed_integrity_gate,
            priority=EventPriority.CRITICAL,
        )

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="integration_test",
        )
        bus.publish(event)

        assert event.data.get(INTEGRITY_FAILED_KEY) is False
        gate = event.data.get(INTEGRITY_GATE_KEY, {})
        assert gate["valid"] is True
        assert gate["checked"] == 0
        assert gate["strategy"] == "no_entries"

    def test_health_score_updated_after_valid_check(self, real_wal, redis_client):
        """
        정상 체인 검증 후 IntegrityHealthScore.record_recovery()가 호출된다.

        실제 Redis 기반 HealthScore에 recovery 이벤트가 기록되는지 확인.
        """
        from selfhealing.audit.integrity.health_score import (
            get_integrity_health_score,
            reset_integrity_health_score,
        )
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            SelfHealingEvent,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.integrity_gate import (
            on_circuit_breaker_closed_integrity_gate,
        )

        reset_integrity_health_score()
        health = get_integrity_health_score(redis_client=redis_client)
        initial_events = len(health._recovery_events)

        chain = _build_valid_chain(3)
        for entry in chain:
            real_wal.write(entry)

        bus = SelfHealingEventBus()
        bus.reset()
        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            on_circuit_breaker_closed_integrity_gate,
            priority=EventPriority.CRITICAL,
        )

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="integration_test",
        )
        bus.publish(event)

        # HealthScore에 recovery 이벤트가 추가되었는지 확인
        assert len(health._recovery_events) > initial_events, "record_recovery() should add an event to _recovery_events"

    def test_wal_violation_alert_written(self, real_wal, redis_client):
        """
        위반 체인 감지 시 _send_integrity_violation_alert()이 WAL에 기록한다.

        실제 WAL에 INTEGRITY_VIOLATION 이벤트가 추가로 기록되는지 확인.
        """
        from selfhealing.audit.integrity.health_score import (
            get_integrity_health_score,
            reset_integrity_health_score,
        )
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            SelfHealingEvent,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.integrity_gate import (
            on_circuit_breaker_closed_integrity_gate,
        )

        reset_integrity_health_score()
        get_integrity_health_score(redis_client=redis_client)

        chain = _build_broken_chain(5, tamper_at=2)
        for entry in chain:
            real_wal.write(entry)

        # 변조 체인 기록 후 WAL 엔트리 수
        before_count = len(real_wal.recover_unprocessed(last_processed_seq=0))

        bus = SelfHealingEventBus()
        bus.reset()
        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            on_circuit_breaker_closed_integrity_gate,
            priority=EventPriority.CRITICAL,
        )

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="integration_test",
        )
        bus.publish(event)

        # 위반 감지 → WAL에 INTEGRITY_VIOLATION 이벤트가 추가 기록되었는지
        after_entries = real_wal.recover_unprocessed(last_processed_seq=0)
        after_count = len(after_entries)
        assert after_count > before_count, (
            f"INTEGRITY_VIOLATION should be written to WAL " f"(before={before_count}, after={after_count})"
        )

        # 추가된 엔트리가 INTEGRITY_VIOLATION 타입인지 확인
        violation_entries = [e for e in after_entries if e.data.get("event_type") == "INTEGRITY_VIOLATION"]
        assert len(violation_entries) >= 1, "WAL should contain INTEGRITY_VIOLATION entry"

    def test_fail_open_policy_on_wal_disabled(self, redis_client):
        """WAL 비활성화 상태 → 빈 엔트리 → gate 통과 (fail_open 정책)."""
        import selfhealing.services.audit.base as audit_base
        from selfhealing.audit.integrity.health_score import (
            get_integrity_health_score,
            reset_integrity_health_score,
        )
        from selfhealing.services.event_bus import (
            SelfHealingEventBus,
            SelfHealingEvent,
            EventType,
            EventPriority,
        )
        from selfhealing.services.event_bus.integrity_gate import (
            on_circuit_breaker_closed_integrity_gate,
        )

        # WAL 비활성화 — _get_wal() → None
        audit_base._wal_instance = None
        audit_base._wal_enabled = False

        reset_integrity_health_score()
        get_integrity_health_score(redis_client=redis_client)

        bus = SelfHealingEventBus()
        bus.reset()

        replay_called = []

        def replay_handler(event):
            if not event.data.get(INTEGRITY_FAILED_KEY, False):
                replay_called.append(True)

        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            on_circuit_breaker_closed_integrity_gate,
            priority=EventPriority.CRITICAL,
        )
        bus.subscribe(
            EventType.CIRCUIT_BREAKER_CLOSED,
            replay_handler,
            priority=EventPriority.NORMAL,
        )

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="integration_test",
        )
        bus.publish(event)

        # WAL 빈 = no_entries = valid → replay 허용
        assert event.data.get(INTEGRITY_FAILED_KEY) is False
        assert len(replay_called) == 1
