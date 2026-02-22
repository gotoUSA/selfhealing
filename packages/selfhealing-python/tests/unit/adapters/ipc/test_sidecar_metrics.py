"""
SidecarMetrics 단위 테스트.

테스트 항목:
- Prometheus 메트릭 정의
- 메트릭 기록
- 컨텍스트 매니저
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from selfhealing.adapters.ipc.sidecar_metrics import (
    PROMETHEUS_AVAILABLE,
    SidecarMetrics,
    record_cache_access,
    record_ipc_request,
    sidecar_metrics,
)


class TestSidecarMetrics:
    """SidecarMetrics 테스트."""

    def test_init_custom_prefix(self):
        """커스텀 접두사 (Prometheus 없이)."""
        with patch("selfhealing.adapters.ipc.sidecar_metrics.PROMETHEUS_AVAILABLE", False):
            # Prometheus 없이 테스트
            metrics = SidecarMetrics.__new__(SidecarMetrics)
            metrics.prefix = "custom_sidecar"
            metrics._initialized = False

            assert metrics.prefix == "custom_sidecar"

    def test_singleton_prefix(self):
        """싱글톤 접두사 확인."""
        assert sidecar_metrics.prefix == "selfhealing_sidecar"

    @pytest.mark.skipif(not PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
    def test_metrics_defined_on_singleton(self):
        """싱글톤 메트릭 정의 확인."""
        assert hasattr(sidecar_metrics, "ipc_active_connections")
        assert hasattr(sidecar_metrics, "uds_queue_depth")
        assert hasattr(sidecar_metrics, "request_latency_seconds")
        assert hasattr(sidecar_metrics, "errors_total")
        assert hasattr(sidecar_metrics, "buffer_entries")

    def test_update_connections_no_error(self):
        """연결 수 업데이트 - 에러 없이 실행."""
        # 싱글톤 사용
        sidecar_metrics.update_connections("uds", 5)
        sidecar_metrics.update_connections("grpc", 3)

    def test_record_connection_no_error(self):
        """새 연결 기록 - 에러 없이 실행."""
        sidecar_metrics.record_connection("uds")
        sidecar_metrics.record_connection("grpc")

    def test_record_error_no_error(self):
        """에러 기록 - 에러 없이 실행."""
        sidecar_metrics.record_error("circuit_breaker.should_allow", "connection_error")

    def test_record_cache_hit_no_error(self):
        """캐시 히트 기록 - 에러 없이 실행."""
        sidecar_metrics.record_cache_hit("cb_state")

    def test_record_cache_miss_no_error(self):
        """캐시 미스 기록 - 에러 없이 실행."""
        sidecar_metrics.record_cache_miss("cb_state")

    def test_update_buffer_entries_no_error(self):
        """버퍼 엔트리 수 업데이트 - 에러 없이 실행."""
        sidecar_metrics.update_buffer_entries("dlq", 100)
        sidecar_metrics.update_buffer_entries("audit", 50)

    def test_update_queue_depth_no_error(self):
        """UDS 큐 깊이 업데이트 - 에러 없이 실행."""
        sidecar_metrics.update_queue_depth(10)

    def test_update_event_streams_no_error(self):
        """이벤트 스트림 수 업데이트 - 에러 없이 실행."""
        sidecar_metrics.update_event_streams(3)

    def test_record_event_proxied_no_error(self):
        """이벤트 프록시 기록 - 에러 없이 실행."""
        sidecar_metrics.record_event_proxied("circuit_breaker.state_changed")

    def test_record_shm_update_no_error(self):
        """Shared Memory 업데이트 기록 - 에러 없이 실행."""
        sidecar_metrics.record_shm_update()

    def test_update_shm_age_no_error(self):
        """Shared Memory 나이 업데이트 - 에러 없이 실행."""
        sidecar_metrics.update_shm_age(5.0)

    def test_record_auth_failure_no_error(self):
        """인증 실패 기록 - 에러 없이 실행."""
        sidecar_metrics.record_auth_failure("uds")

    def test_record_cache_invalidation_no_error(self):
        """캐시 무효화 기록 - 에러 없이 실행."""
        sidecar_metrics.record_cache_invalidation("cb_state")

    def test_update_buffer_bytes_no_error(self):
        """버퍼 바이트 업데이트 - 에러 없이 실행."""
        sidecar_metrics.update_buffer_bytes(1024)

    def test_record_event_dropped_no_error(self):
        """이벤트 드롭 기록 - 에러 없이 실행."""
        sidecar_metrics.record_event_dropped()

    def test_record_request_latency_no_error(self):
        """요청 지연 시간 기록 - 에러 없이 실행."""
        sidecar_metrics.record_request_latency("test.method", "uds", 0.01)


class TestRecordIPCRequest:
    """record_ipc_request 컨텍스트 매니저 테스트."""

    def test_context_manager_success(self):
        """성공 케이스."""
        with record_ipc_request("test.method", "uds"):
            time.sleep(0.01)  # 약간의 지연

        # 컨텍스트 매니저가 에러 없이 완료되어야 함
        assert True

    def test_context_manager_with_exception(self):
        """예외 케이스."""
        with pytest.raises(ValueError):
            with record_ipc_request("test.method", "grpc"):
                raise ValueError("Test error")

        # 예외가 전파되어야 함


class TestRecordCacheAccess:
    """record_cache_access 헬퍼 테스트."""

    def test_cache_hit(self):
        """캐시 히트 기록."""
        # 에러 없이 실행되어야 함
        record_cache_access("cb_state", hit=True)

    def test_cache_miss(self):
        """캐시 미스 기록."""
        # 에러 없이 실행되어야 함
        record_cache_access("cb_state", hit=False)


class TestGlobalSidecarMetrics:
    """싱글톤 인스턴스 테스트."""

    def test_singleton_instance(self):
        """싱글톤 인스턴스 존재 확인."""
        assert sidecar_metrics is not None
        assert isinstance(sidecar_metrics, SidecarMetrics)

    def test_singleton_prefix(self):
        """싱글톤 접두사 확인."""
        assert sidecar_metrics.prefix == "selfhealing_sidecar"


class TestMetricsWithoutPrometheus:
    """Prometheus 없이 테스트."""

    def test_no_op_when_prometheus_unavailable(self):
        """prometheus_client 없을 때 no-op."""
        with patch("selfhealing.adapters.ipc.sidecar_metrics.PROMETHEUS_AVAILABLE", False):
            metrics = SidecarMetrics()
            metrics._initialized = False  # 강제로 비초기화 상태로

            # 에러 없이 실행되어야 함
            metrics.update_connections("uds", 10)
            metrics.record_error("method", "error")
            metrics.record_cache_hit("cb_state")
            metrics.update_queue_depth(5)
