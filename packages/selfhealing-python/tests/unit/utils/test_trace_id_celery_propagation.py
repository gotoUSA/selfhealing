"""
Phase 25: trace_id 일관성 확보 단위 테스트 (Phase 5)

테스트 시나리오:
1. get_trace_for_celery()가 현재 trace_id를 반환하는지 검증
2. restore_trace_from_celery()가 전파된 trace_id를 복원하는지 검증
3. restore_trace_from_celery()가 trace_info 없을 때 INTERNAL_BEAT_xxx 생성하는지 검증
4. _write_to_wal()이 trace_id를 WAL에 기록하는지 검증
"""

import pytest
from unittest.mock import MagicMock, patch

from selfhealing.audit.trace import (
    TraceContext,
    get_trace_id,
    set_trace_id,
    clear_trace_id,
    generate_trace_id,
    get_trace_for_celery,
    restore_trace_from_celery,
)


class TestGetTraceForCelery:
    """get_trace_for_celery() 함수 테스트."""

    def test_returns_current_trace_id(self):
        """현재 설정된 trace_id를 반환하는지 검증."""
        # Given
        test_trace_id = "req-test1234"
        with TraceContext(test_trace_id):
            # When
            result = get_trace_for_celery()
        
        # Then
        assert result["trace_id"] == test_trace_id
        assert result["source"] == "celery_propagated"

    def test_returns_none_when_no_trace_context(self):
        """trace_id가 없을 때 None을 반환하는지 검증."""
        # Given: trace_id 없는 상태
        clear_trace_id()
        
        # When
        result = get_trace_for_celery()
        
        # Then
        assert result["trace_id"] is None
        assert result["source"] == "celery_propagated"


class TestRestoreTraceFromCelery:
    """restore_trace_from_celery() 컨텍스트 매니저 테스트."""

    def test_restores_propagated_trace_id(self):
        """전파된 trace_id가 복원되는지 검증."""
        # Given
        trace_info = {"trace_id": "req-propagated", "source": "celery_propagated"}
        
        # When
        with restore_trace_from_celery(trace_info) as trace_id:
            current_trace_id = get_trace_id()
        
        # Then
        assert trace_id == "req-propagated"
        assert current_trace_id == "req-propagated"

    def test_generates_internal_beat_when_no_trace_info(self):
        """trace_info가 없을 때 INTERNAL_BEAT_xxx 형식으로 생성하는지 검증."""
        # Given: trace_info 없음
        
        # When
        with restore_trace_from_celery(None) as trace_id:
            current_trace_id = get_trace_id()
        
        # Then
        assert trace_id.startswith("INTERNAL_BEAT_req-")
        assert current_trace_id == trace_id

    def test_generates_internal_beat_when_empty_trace_info(self):
        """빈 trace_info일 때 INTERNAL_BEAT_xxx 형식으로 생성하는지 검증."""
        # Given: 빈 dict
        trace_info = {}
        
        # When
        with restore_trace_from_celery(trace_info) as trace_id:
            current_trace_id = get_trace_id()
        
        # Then
        assert trace_id.startswith("INTERNAL_BEAT_req-")
        assert current_trace_id == trace_id

    def test_generates_internal_beat_when_trace_id_is_none(self):
        """trace_id가 None인 trace_info일 때 INTERNAL_BEAT_xxx 생성하는지 검증."""
        # Given
        trace_info = {"trace_id": None, "source": "celery_propagated"}
        
        # When
        with restore_trace_from_celery(trace_info) as trace_id:
            current_trace_id = get_trace_id()
        
        # Then
        assert trace_id.startswith("INTERNAL_BEAT_req-")
        assert current_trace_id == trace_id

    def test_context_is_restored_after_exit(self):
        """컨텍스트 종료 후 이전 trace_id가 복원되는지 검증."""
        # Given: 원래 trace_id 설정
        original_trace_id = "req-original"
        with TraceContext(original_trace_id):
            # When: 중첩된 trace_info 복원
            trace_info = {"trace_id": "req-nested"}
            with restore_trace_from_celery(trace_info):
                nested_trace_id = get_trace_id()
                assert nested_trace_id == "req-nested"
            
            # Then: 원래 trace_id 복원됨
            restored_trace_id = get_trace_id()
            assert restored_trace_id == original_trace_id


class TestWriteToWalWithTraceId:
    """_write_to_wal()의 trace_id 기록 테스트."""

    @patch("selfhealing.services.audit.base._get_wal")
    def test_wal_includes_trace_id_from_context(self, mock_get_wal):
        """TraceContext의 trace_id가 WAL에 기록되는지 검증."""
        from selfhealing.services.audit.base import _write_to_wal
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.return_value = 1
        mock_get_wal.return_value = mock_wal
        
        # Given: TraceContext 설정
        with TraceContext("req-waltest"):
            # When
            seq = _write_to_wal(
                event_type="TEST_EVENT",
                source="test",
                details={"test": "data"},
            )
        
        # Then
        assert seq == 1
        mock_wal.write.assert_called_once()
        wal_entry = mock_wal.write.call_args[0][0]
        assert wal_entry["trace_id"] == "req-waltest"

    @patch("selfhealing.services.audit.base._get_wal")
    def test_wal_includes_explicit_trace_id(self, mock_get_wal):
        """명시적으로 전달된 trace_id가 WAL에 기록되는지 검증."""
        from selfhealing.services.audit.base import _write_to_wal
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.return_value = 2
        mock_get_wal.return_value = mock_wal
        
        # When: trace_id를 명시적으로 전달
        seq = _write_to_wal(
            event_type="TEST_EVENT",
            source="test",
            details={"test": "data"},
            trace_id="req-explicit",
        )
        
        # Then
        assert seq == 2
        wal_entry = mock_wal.write.call_args[0][0]
        assert wal_entry["trace_id"] == "req-explicit"

    @patch("selfhealing.services.audit.base._get_wal")
    def test_wal_includes_internal_beat_trace_id(self, mock_get_wal):
        """INTERNAL_BEAT trace_id가 WAL에 기록되는지 검증."""
        from selfhealing.services.audit.base import _write_to_wal
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.return_value = 3
        mock_get_wal.return_value = mock_wal
        
        # Given: restore_trace_from_celery로 INTERNAL_BEAT trace_id 생성
        with restore_trace_from_celery(None):
            # When
            seq = _write_to_wal(
                event_type="DLQ_REPLAY",
                source="beat",
                details={"dlq_id": 123},
            )
            
            # Then
            wal_entry = mock_wal.write.call_args[0][0]
            assert wal_entry["trace_id"].startswith("INTERNAL_BEAT_req-")


class TestDLQReplayTaskWithTraceInfo:
    """Celery Task의 trace_info 파라미터 테스트."""

    @patch("selfhealing.services.audit.base._get_wal")
    @patch("selfhealing.services.replay_service.ReplayService")
    def test_replay_single_with_trace_info_propagates_trace_id(self, mock_service_cls, mock_get_wal):
        """replay_single_dlq_entry가 trace_info를 전파하는지 검증."""
        from selfhealing.adapters.celery.tasks.dlq_replay import replay_single_dlq_entry
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.return_value = 1
        mock_get_wal.return_value = mock_wal
        
        # Given: ReplayService mock
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message = "OK"
        mock_result.error = None
        mock_result.data = {}
        mock_service.replay_single.return_value = mock_result
        mock_service_cls.return_value = mock_service
        
        # When: trace_info 전달
        trace_info = {"trace_id": "req-fromapi"}
        result = replay_single_dlq_entry(
            dlq_id=123,
            actor_info={"actor_id": "test@example.com", "roles": []},
            trace_info=trace_info,
        )
        
        # Then
        assert result["success"] is True
        # trace_id는 restore_trace_from_celery 내부에서 설정됨

    @patch("selfhealing.services.audit.base._get_wal")
    @patch("selfhealing.services.replay_service.ReplayService")
    def test_replay_single_without_trace_info_generates_internal_beat(self, mock_service_cls, mock_get_wal):
        """trace_info 없이 호출 시 INTERNAL_BEAT가 생성되는지 검증."""
        from selfhealing.adapters.celery.tasks.dlq_replay import replay_single_dlq_entry
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.return_value = 1
        mock_get_wal.return_value = mock_wal
        
        # Given: ReplayService mock
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message = "OK"
        mock_result.error = None
        mock_result.data = {}
        mock_service.replay_single.return_value = mock_result
        mock_service_cls.return_value = mock_service
        
        # When: trace_info 없이 호출 (Beat 시뮬레이션)
        result = replay_single_dlq_entry(dlq_id=456)
        
        # Then
        assert result["success"] is True


class TestTraceIdConsistencyE2E:
    """trace_id 일관성 E2E 테스트."""

    @patch("selfhealing.services.audit.base._get_wal")
    def test_http_to_celery_trace_propagation(self, mock_get_wal):
        """HTTP 요청 → Celery Task → WAL까지 trace_id 전파 검증."""
        from selfhealing.context.actor_context import ActorContext
        from selfhealing.services.audit.dlq_audit import log_dlq_replay_audit
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.return_value = 1
        mock_get_wal.return_value = mock_wal
        
        # Step 1: HTTP 요청 시뮬레이션 - trace_id 설정
        http_trace_id = "req-httporigin"
        with TraceContext(http_trace_id):
            # Step 2: Celery에 전달할 trace_info 추출
            trace_info = get_trace_for_celery()
            assert trace_info["trace_id"] == http_trace_id
        
        # Step 3: Celery Task에서 trace_info 복원
        with restore_trace_from_celery(trace_info):
            with ActorContext.set_actor(
                actor_id="admin@example.com",
                actor_type="selfhealing_admin",
                roles=["selfhealing_admin"],
            ):
                # Step 4: Audit 기록
                seq = log_dlq_replay_audit(
                    dlq_id=999,
                    domain="payment",
                    success=True,
                )
        
        # Then: WAL에 원본 trace_id가 기록됨
        assert seq == 1
        wal_entry = mock_wal.write.call_args[0][0]
        assert wal_entry["trace_id"] == http_trace_id
        assert wal_entry["actor_roles"] == ["selfhealing_admin"]

    @patch("selfhealing.services.audit.base._get_wal")
    def test_beat_auto_generates_internal_trace_id(self, mock_get_wal):
        """Beat 자동 호출 시 INTERNAL_BEAT trace_id 자동 생성 검증."""
        from selfhealing.context.actor_context import ActorContext, SYSTEM_ACTOR
        from selfhealing.services.audit.dlq_audit import log_dlq_replay_audit
        
        # Given: Mock WAL
        mock_wal = MagicMock()
        mock_wal.write.return_value = 1
        mock_get_wal.return_value = mock_wal
        
        # Beat 시뮬레이션: trace_info 없음
        with restore_trace_from_celery(None) as trace_id:
            with ActorContext.set_actor(
                actor_id=SYSTEM_ACTOR.actor_id,
                actor_type=SYSTEM_ACTOR.actor_type,
                roles=[],
            ):
                # Audit 기록
                seq = log_dlq_replay_audit(
                    dlq_id=888,
                    domain="point",
                    success=True,
                )
        
        # Then: WAL에 INTERNAL_BEAT trace_id가 기록됨
        assert seq == 1
        wal_entry = mock_wal.write.call_args[0][0]
        assert wal_entry["trace_id"].startswith("INTERNAL_BEAT_req-")
        assert wal_entry["actor_type"] == "system"
        assert wal_entry["actor_roles"] == []
