"""
K8sIngressTrafficRoutingAdapter 단위 테스트.

테스트 대상:
- K8s 클라이언트 미가용 시 동작
- switch_primary(): 서비스 매핑 검증, Ingress 패치 동작
- rollback(): 역방향 전환
- get_current_routing(): 상태 조회
- _publish_routing_event(): 이벤트 발행 예외 격리
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfhealing.adapters.traffic_routing.k8s_ingress_adapter import (
    K8sIngressTrafficRoutingAdapter,
)
from selfhealing.interfaces.traffic_routing import (
    RoutingChange,
    TrafficRoutingAdapter,
)

# =============================================================================
# K8sIngressTrafficRoutingAdapter 계약 검증
# =============================================================================


class TestK8sIngressAdapterContract:
    """K8sIngressTrafficRoutingAdapter 계약 검증."""

    def test_is_subclass_of_traffic_routing_adapter(self) -> None:
        """TrafficRoutingAdapter의 하위 클래스이다."""
        assert issubclass(K8sIngressTrafficRoutingAdapter, TrafficRoutingAdapter)


# =============================================================================
# K8sIngressTrafficRoutingAdapter 동작 검증
# =============================================================================


class TestK8sIngressAdapterBehavior:
    """K8sIngressTrafficRoutingAdapter 동작 검증."""

    def _make_adapter(
        self,
        is_available: bool = True,
        region_service_map: dict[str, str] | None = None,
    ) -> K8sIngressTrafficRoutingAdapter:
        """테스트용 어댑터 생성 (K8s 클라이언트 Mock)."""
        with patch.object(K8sIngressTrafficRoutingAdapter, "_initialize_client"):
            adapter = K8sIngressTrafficRoutingAdapter(
                ingress_name="test-ingress",
                namespace="test-ns",
                region_service_map=region_service_map or {"ap-northeast-2": "app-apne2", "us-west-2": "app-usw2"},
            )
            adapter._is_available = is_available
            adapter._networking_v1 = MagicMock() if is_available else None
        return adapter

    def _make_ingress_mock(self, service_name: str = "app-apne2") -> MagicMock:
        """Mock Ingress 객체 생성."""
        path_entry = MagicMock()
        path_entry.backend.service.name = service_name

        rule = MagicMock()
        rule.http.paths = [path_entry]

        ingress = MagicMock()
        ingress.metadata.annotations = {"selfhealing.io/primary-region": "ap-northeast-2"}
        ingress.spec.rules = [rule]

        return ingress

    # --- switch_primary ---

    def test_switch_fails_when_client_unavailable(self) -> None:
        """K8s 클라이언트 미가용 시 success=False를 반환한다."""
        adapter = self._make_adapter(is_available=False)
        result = adapter.switch_primary("ap-northeast-2", "us-west-2")

        assert result.success is False
        assert "K8s client not available" in result.details["error"]

    def test_switch_fails_when_no_service_mapping(self) -> None:
        """매핑되지 않은 리전이면 success=False를 반환한다."""
        adapter = self._make_adapter(region_service_map={"ap-northeast-2": "app-apne2"})
        result = adapter.switch_primary("ap-northeast-2", "eu-west-1")

        assert result.success is False
        assert "No service mapped" in result.details["error"]

    def test_switch_primary_patches_ingress(self) -> None:
        """switch_primary()는 Ingress를 패치한다."""
        adapter = self._make_adapter()
        mock_ingress = self._make_ingress_mock("app-apne2")
        adapter._networking_v1.read_namespaced_ingress.return_value = mock_ingress

        with patch.object(adapter, "_publish_routing_event"):
            result = adapter.switch_primary("ap-northeast-2", "us-west-2")

        assert result.success is True
        adapter._networking_v1.patch_namespaced_ingress.assert_called_once()

    def test_switch_primary_returns_correct_details(self) -> None:
        """switch_primary() 성공 시 details에 인프라 정보가 포함된다."""
        adapter = self._make_adapter()
        mock_ingress = self._make_ingress_mock("app-apne2")
        adapter._networking_v1.read_namespaced_ingress.return_value = mock_ingress

        with patch.object(adapter, "_publish_routing_event"):
            result = adapter.switch_primary("ap-northeast-2", "us-west-2")

        assert result.details["level"] == "infrastructure"
        assert result.details["dns_updated"] is False
        assert result.details["ingress_updated"] is True
        assert result.details["target_service"] == "app-usw2"
        assert result.details["replaced_backends"] >= 1

    def test_switch_primary_stores_rollback_info(self) -> None:
        """switch_primary() 성공 시 rollback_info에 이전 상태가 저장된다."""
        adapter = self._make_adapter()
        mock_ingress = self._make_ingress_mock("app-apne2")
        adapter._networking_v1.read_namespaced_ingress.return_value = mock_ingress

        with patch.object(adapter, "_publish_routing_event"):
            result = adapter.switch_primary("ap-northeast-2", "us-west-2")

        assert result.rollback_info is not None
        assert result.rollback_info["previous_region"] == "ap-northeast-2"

    def test_switch_fails_when_no_matching_backend(self) -> None:
        """Ingress에 매칭되는 backend가 없으면 success=False."""
        adapter = self._make_adapter()
        mock_ingress = self._make_ingress_mock("unrelated-service")
        adapter._networking_v1.read_namespaced_ingress.return_value = mock_ingress

        result = adapter.switch_primary("ap-northeast-2", "us-west-2")

        assert result.success is False
        assert "No matching backend" in result.details["error"]

    def test_switch_handles_k8s_api_exception(self) -> None:
        """K8s API 예외 시 success=False를 반환한다."""
        adapter = self._make_adapter()
        adapter._networking_v1.read_namespaced_ingress.side_effect = Exception("API server unavailable")

        result = adapter.switch_primary("ap-northeast-2", "us-west-2")

        assert result.success is False
        assert "API server unavailable" in result.details["error"]

    def test_switch_updates_current_primary(self) -> None:
        """switch_primary() 성공 시 _current_primary가 갱신된다."""
        adapter = self._make_adapter()
        mock_ingress = self._make_ingress_mock("app-apne2")
        adapter._networking_v1.read_namespaced_ingress.return_value = mock_ingress

        with patch.object(adapter, "_publish_routing_event"):
            adapter.switch_primary("ap-northeast-2", "us-west-2")

        assert adapter._current_primary == "us-west-2"

    # --- 이벤트 발행 예외 격리 ---

    def test_event_publish_failure_does_not_affect_result(self) -> None:
        """이벤트 발행 실패해도 RoutingChange는 success=True를 반환한다."""
        adapter = self._make_adapter()
        mock_ingress = self._make_ingress_mock("app-apne2")
        adapter._networking_v1.read_namespaced_ingress.return_value = mock_ingress

        with patch.object(
            adapter,
            "_publish_routing_event",
            side_effect=Exception("Event bus down"),
        ):
            # _publish_routing_event은 switch_primary 내에서 이미 try/except 처리됨
            # 하지만 우리 mock은 patch.object로 메서드 자체를 교체하므로
            # side_effect 대신 직접 검증
            pass

        # 실제 테스트: _publish_routing_event 내부의 예외 격리
        with patch(
            "selfhealing.services.event_bus.redis_bus.get_event_bus",
            side_effect=Exception("Event bus down"),
        ):
            result = adapter.switch_primary("ap-northeast-2", "us-west-2")

        assert result.success is True

    # --- rollback ---

    def test_rollback_returns_false_without_rollback_info(self) -> None:
        """rollback_info가 없으면 False를 반환한다."""
        adapter = self._make_adapter()
        change = RoutingChange(
            success=True,
            from_region="ap-northeast-2",
            to_region="us-west-2",
        )
        result = adapter.rollback(change)
        assert result is False

    def test_rollback_calls_switch_primary_reversed(self) -> None:
        """rollback()은 switch_primary()를 역방향으로 호출한다."""
        adapter = self._make_adapter()
        change = RoutingChange(
            success=True,
            from_region="ap-northeast-2",
            to_region="us-west-2",
            rollback_info={"previous_region": "ap-northeast-2"},
        )

        with patch.object(adapter, "switch_primary") as mock_switch:
            mock_switch.return_value = RoutingChange(
                success=True,
                from_region="us-west-2",
                to_region="ap-northeast-2",
            )
            result = adapter.rollback(change)

        assert result is True
        mock_switch.assert_called_once_with("us-west-2", "ap-northeast-2")

    # --- get_current_routing ---

    def test_get_current_routing_unavailable(self) -> None:
        """K8s 미가용 시 available=False를 반환한다."""
        adapter = self._make_adapter(is_available=False)
        result = adapter.get_current_routing()

        assert result["available"] is False

    def test_get_current_routing_returns_ingress_info(self) -> None:
        """정상 시 Ingress 정보를 반환한다."""
        adapter = self._make_adapter()
        mock_ingress = MagicMock()
        mock_ingress.metadata.annotations = {
            "selfhealing.io/primary-region": "us-west-2",
            "selfhealing.io/failover-timestamp": "2026-02-22T10:00:00Z",
        }
        adapter._networking_v1.read_namespaced_ingress.return_value = mock_ingress

        result = adapter.get_current_routing()

        assert result["available"] is True
        assert result["primary_region"] == "us-west-2"
        assert result["last_failover"] == "2026-02-22T10:00:00Z"
