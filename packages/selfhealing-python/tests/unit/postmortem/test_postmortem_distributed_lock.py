"""
Postmortem 분산 락 연동 테스트 (문서 146 섹션 12.2, 13.2).

테스트 항목:
1. add_healing_incident_with_lock 락 획득 성공 시 저장
2. add_healing_incident_with_lock 락 획득 실패 시 스킵
3. acquire_group_close_lock 함수 동작 확인
4. 락 키 패턴 상수 확인
5. Redis 미사용 환경에서 fallback 동작
6. IncidentGroupManager.close_group 락 연동
"""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def disable_db_persistence():
    """모든 테스트에서 DB persistence 비활성화."""
    from selfhealing.services.postmortem_store import (
        set_db_persistence_enabled,
        get_db_persistence_enabled,
    )

    original = get_db_persistence_enabled()
    set_db_persistence_enabled(False)
    yield
    set_db_persistence_enabled(original)


class TestPostmortemDistributedLockConstants:
    """분산 락 상수 테스트."""

    def test_lock_key_patterns_defined(self):
        """락 키 패턴 상수가 정의되어 있어야 함."""
        from selfhealing.services.postmortem_store import (
            LOCK_KEY_POSTMORTEM_GENERATE,
            LOCK_KEY_POSTMORTEM_GROUP,
            LOCK_TTL_POSTMORTEM_GENERATE,
            LOCK_TTL_POSTMORTEM_GROUP,
        )

        # 락 키 패턴 확인
        assert "postmortem:generate:" in LOCK_KEY_POSTMORTEM_GENERATE
        assert "{incident_id}" in LOCK_KEY_POSTMORTEM_GENERATE
        assert "postmortem:group:" in LOCK_KEY_POSTMORTEM_GROUP
        assert "{group_id}" in LOCK_KEY_POSTMORTEM_GROUP

        # TTL 확인 (문서 12.2 기준)
        assert LOCK_TTL_POSTMORTEM_GENERATE == 30
        assert LOCK_TTL_POSTMORTEM_GROUP == 60

    def test_lock_key_format(self):
        """락 키 포맷이 올바르게 동작해야 함."""
        from selfhealing.services.postmortem_store import (
            LOCK_KEY_POSTMORTEM_GENERATE,
            LOCK_KEY_POSTMORTEM_GROUP,
        )

        incident_id = "AUTO-test-20260201-120000"
        group_id = "INCGRP-1706803200"

        generate_key = LOCK_KEY_POSTMORTEM_GENERATE.format(incident_id=incident_id)
        group_key = LOCK_KEY_POSTMORTEM_GROUP.format(group_id=group_id)

        assert generate_key == "postmortem:generate:AUTO-test-20260201-120000"
        assert group_key == "postmortem:group:INCGRP-1706803200"


class TestAddHealingIncidentWithLock:
    """add_healing_incident_with_lock 함수 테스트."""

    def test_save_with_lock_success(self):
        """락 획득 성공 시 저장이 진행되어야 함."""
        from selfhealing.services.postmortem_store import (
            add_healing_incident_with_lock,
            clear_healing_incidents,
            get_healing_incidents,
        )

        clear_healing_incidents()

        # Mock lock이 성공적으로 획득됨
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        with patch(
            "selfhealing.services.postmortem_store._acquire_postmortem_lock",
            return_value=mock_lock,
        ):
            incident = {
                "incident_id": "TEST-001",
                "summary": {"affected_services": ["test-service"]},
            }
            result = add_healing_incident_with_lock(incident)

        assert result is True
        mock_lock.acquire.assert_called_once()
        mock_lock.release.assert_called_once()

        # 저장 확인
        incidents = get_healing_incidents(limit=10, use_db=False)
        assert len(incidents) >= 1
        assert any(i.get("incident_id") == "TEST-001" for i in incidents)

        clear_healing_incidents()

    def test_save_with_lock_failure_skips(self):
        """락 획득 실패 시 저장이 스킵되어야 함."""
        from selfhealing.services.postmortem_store import (
            add_healing_incident_with_lock,
            clear_healing_incidents,
            get_healing_incidents,
        )

        clear_healing_incidents()

        # Mock lock 획득 실패
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False

        with patch(
            "selfhealing.services.postmortem_store._acquire_postmortem_lock",
            return_value=mock_lock,
        ):
            incident = {
                "incident_id": "TEST-002",
                "summary": {"affected_services": ["test-service"]},
            }
            result = add_healing_incident_with_lock(incident)

        assert result is False
        mock_lock.acquire.assert_called_once()
        mock_lock.release.assert_not_called()

        # 저장되지 않음 확인
        incidents = get_healing_incidents(limit=10, use_db=False)
        assert not any(i.get("incident_id") == "TEST-002" for i in incidents)

        clear_healing_incidents()

    def test_save_without_redis_fallback(self):
        """Redis 미사용 환경에서 락 없이 저장이 진행되어야 함."""
        from selfhealing.services.postmortem_store import (
            add_healing_incident_with_lock,
            clear_healing_incidents,
            get_healing_incidents,
        )

        clear_healing_incidents()

        # Redis 클라이언트 없음
        with patch(
            "selfhealing.services.postmortem_store._acquire_postmortem_lock",
            return_value=None,
        ):
            incident = {
                "incident_id": "TEST-003",
                "summary": {"affected_services": ["test-service"]},
            }
            result = add_healing_incident_with_lock(incident)

        assert result is True

        # 저장 확인
        incidents = get_healing_incidents(limit=10, use_db=False)
        assert any(i.get("incident_id") == "TEST-003" for i in incidents)

        clear_healing_incidents()

    def test_save_without_incident_id_fallback(self):
        """incident_id가 없으면 락 없이 저장되어야 함."""
        from selfhealing.services.postmortem_store import (
            add_healing_incident_with_lock,
            clear_healing_incidents,
            get_healing_incidents,
        )

        clear_healing_incidents()

        # incident_id 없음
        incident = {
            "summary": {"affected_services": ["test-service"]},
        }
        result = add_healing_incident_with_lock(incident)

        assert result is True

        # 저장 확인
        incidents = get_healing_incidents(limit=10, use_db=False)
        assert len(incidents) >= 1

        clear_healing_incidents()

    def test_lock_released_on_exception(self):
        """저장 중 예외 발생 시에도 락이 해제되어야 함."""
        from selfhealing.services.postmortem_store import add_healing_incident_with_lock

        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        with patch(
            "selfhealing.services.postmortem_store._acquire_postmortem_lock",
            return_value=mock_lock,
        ):
            with patch(
                "selfhealing.services.postmortem_store.add_healing_incident",
                side_effect=Exception("DB Error"),
            ):
                try:
                    add_healing_incident_with_lock({"incident_id": "TEST-ERR"})
                except Exception:
                    pass

        # 예외 발생 시에도 release 호출됨
        mock_lock.release.assert_called_once()


class TestAcquireGroupCloseLock:
    """acquire_group_close_lock 함수 테스트."""

    def test_acquire_group_close_lock_with_redis(self):
        """Redis 사용 시 락 객체가 생성되어야 함."""
        from selfhealing.services.postmortem_store import acquire_group_close_lock

        mock_redis = MagicMock()

        with patch(
            "selfhealing.services.postmortem_store._get_redis_client",
            return_value=mock_redis,
        ):
            lock = acquire_group_close_lock("INCGRP-123")

        # 락 객체가 반환됨
        assert lock is not None

    def test_acquire_group_close_lock_without_redis(self):
        """Redis 미사용 시 None이 반환되어야 함."""
        from selfhealing.services.postmortem_store import acquire_group_close_lock

        with patch(
            "selfhealing.services.postmortem_store._get_redis_client",
            return_value=None,
        ):
            lock = acquire_group_close_lock("INCGRP-123")

        assert lock is None


class TestIncidentGroupManagerCloseGroupLock:
    """IncidentGroupManager.close_group 락 연동 테스트."""

    def test_close_group_acquires_lock(self):
        """close_group 호출 시 분산 락을 획득해야 함."""
        from selfhealing.services.postmortem.incident_group import IncidentGroupManager

        manager = IncidentGroupManager()

        # Mock lock
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        with patch.object(
            manager,
            "_acquire_group_close_lock",
            return_value=mock_lock,
        ):
            with patch.object(manager, "_close_group_memory", return_value=None):
                manager.close_group("INCGRP-123", "default")

        mock_lock.acquire.assert_called_once()
        mock_lock.release.assert_called_once()

    def test_close_group_skips_on_lock_failure(self):
        """락 획득 실패 시 close_group이 스킵되어야 함."""
        from selfhealing.services.postmortem.incident_group import IncidentGroupManager

        manager = IncidentGroupManager()

        # Mock lock 획득 실패
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False

        with patch.object(
            manager,
            "_acquire_group_close_lock",
            return_value=mock_lock,
        ):
            with patch.object(manager, "_close_group_memory") as mock_close:
                result = manager.close_group("INCGRP-123", "default")

        # close가 호출되지 않음
        mock_close.assert_not_called()
        assert result is None

    def test_close_group_without_lock_proceeds(self):
        """락 없이도 close_group이 진행되어야 함 (Redis 미사용)."""
        from selfhealing.services.postmortem.incident_group import IncidentGroupManager

        manager = IncidentGroupManager()

        with patch.object(
            manager,
            "_acquire_group_close_lock",
            return_value=None,
        ):
            with patch.object(manager, "_close_group_memory", return_value=MagicMock()) as mock_close:
                manager.close_group("INCGRP-123", "default")

        # 락 없이도 close 호출됨
        mock_close.assert_called_once()

    def test_close_group_releases_lock_on_exception(self):
        """예외 발생 시에도 락이 해제되어야 함."""
        from selfhealing.services.postmortem.incident_group import IncidentGroupManager

        manager = IncidentGroupManager()

        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        with patch.object(
            manager,
            "_acquire_group_close_lock",
            return_value=mock_lock,
        ):
            with patch.object(manager, "_close_group_memory", side_effect=Exception("Error")):
                try:
                    manager.close_group("INCGRP-123", "default")
                except Exception:
                    pass

        # 예외 발생 시에도 release 호출됨
        mock_lock.release.assert_called_once()


class TestGetRedisClient:
    """_get_redis_client 함수 테스트."""

    def test_get_redis_client_from_provider_registry(self):
        """ProviderRegistry에서 Redis 클라이언트를 가져와야 함."""
        from selfhealing.services.postmortem_store import _get_redis_client

        mock_redis = MagicMock()

        with patch("selfhealing.factory.ProviderRegistry") as mock_registry:
            mock_registry.get_cache_provider.return_value = mock_redis
            result = _get_redis_client()

        assert result == mock_redis

    def test_get_redis_client_returns_none_when_unavailable(self):
        """ProviderRegistry에서 None 반환 시 None 반환."""
        from selfhealing.services.postmortem_store import _get_redis_client

        with patch("selfhealing.factory.ProviderRegistry") as mock_registry:
            mock_registry.get_cache_provider.return_value = None
            result = _get_redis_client()

        assert result is None


class TestIntegrationScenario:
    """통합 시나리오 테스트."""

    def test_concurrent_save_prevention(self):
        """동시 저장 요청 시 첫 번째만 성공해야 함."""
        from selfhealing.services.postmortem_store import (
            add_healing_incident_with_lock,
            clear_healing_incidents,
            get_healing_incidents,
        )

        clear_healing_incidents()

        # 첫 번째 요청: 락 획득 성공
        mock_lock_1 = MagicMock()
        mock_lock_1.acquire.return_value = True

        # 두 번째 요청: 락 획득 실패 (이미 획득됨)
        mock_lock_2 = MagicMock()
        mock_lock_2.acquire.return_value = False

        incident = {
            "incident_id": "CONCURRENT-001",
            "summary": {"affected_services": ["service-a"]},
        }

        # 첫 번째 저장
        with patch(
            "selfhealing.services.postmortem_store._acquire_postmortem_lock",
            return_value=mock_lock_1,
        ):
            result1 = add_healing_incident_with_lock(incident.copy())

        # 두 번째 저장 (동일 ID)
        with patch(
            "selfhealing.services.postmortem_store._acquire_postmortem_lock",
            return_value=mock_lock_2,
        ):
            result2 = add_healing_incident_with_lock(incident.copy())

        assert result1 is True
        assert result2 is False

        # 저장은 한 번만 됨
        incidents = get_healing_incidents(limit=10, use_db=False)
        matching = [i for i in incidents if i.get("incident_id") == "CONCURRENT-001"]
        assert len(matching) == 1

        clear_healing_incidents()
