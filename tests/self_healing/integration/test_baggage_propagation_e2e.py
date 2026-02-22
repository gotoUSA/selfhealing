"""
OTel Baggage 통합 전파 — ContextVar ↔ Baggage E2E 테스트.

검증 흐름:
1. ContextVar 설정 → Baggage 동기화 → Baggage에서 값 확인
2. Baggage 설정 → ContextVar 복원 → ContextVar에서 값 확인
3. BaggageSyncMiddleware 양방향 흐름

인프라 의존: 없음 (OTel API in-process, Mock 기반)
"""

import pytest

from selfhealing.context.cell_context import _current_cell_id, get_current_cell_id
from selfhealing.decorators.domain_tag import _current_domain, get_current_domain
from selfhealing.observability.baggage import (
    BAGGAGE_PREFIX,
    detach_baggage_token,
    restore_contextvars_from_baggage,
    sync_contextvars_to_baggage,
)


class TestBaggageRoundTripBehavior:
    """ContextVar → Baggage → ContextVar 왕복 전파 검증."""

    def setup_method(self):
        """ContextVar 초기화."""
        self._cell_token = _current_cell_id.set(None)
        self._domain_token = _current_domain.set(None)

    def teardown_method(self):
        """ContextVar 복원."""
        _current_cell_id.reset(self._cell_token)
        _current_domain.reset(self._domain_token)

    def test_cell_id_round_trip(self):
        """cell_id: ContextVar → Baggage 동기화 → ContextVar 복원."""
        from opentelemetry import baggage, context

        # 1. ContextVar에 cell_id 설정
        _current_cell_id.set("cell-42")

        # 2. ContextVar → Baggage 동기화
        token = sync_contextvars_to_baggage()
        try:
            # 3. Baggage에서 값 확인
            value = baggage.get_baggage(f"{BAGGAGE_PREFIX}.cell_id")
            assert value == "cell-42"

            # 4. ContextVar 초기화 후 Baggage에서 복원
            _current_cell_id.set(None)
            assert get_current_cell_id() is None

            restore_contextvars_from_baggage()
            assert get_current_cell_id() == "cell-42"
        finally:
            detach_baggage_token(token)

    def test_domain_round_trip(self):
        """domain: ContextVar → Baggage 동기화 → ContextVar 복원."""
        from opentelemetry import baggage

        _current_domain.set("payment")

        token = sync_contextvars_to_baggage()
        try:
            value = baggage.get_baggage(f"{BAGGAGE_PREFIX}.domain")
            assert value == "payment"

            _current_domain.set(None)
            assert get_current_domain() is None

            restore_contextvars_from_baggage()
            assert get_current_domain() == "payment"
        finally:
            detach_baggage_token(token)

    def test_multiple_contextvars_synced_simultaneously(self):
        """cell_id + domain이 동시에 Baggage로 동기화된다."""
        from opentelemetry import baggage

        _current_cell_id.set("cell-99")
        _current_domain.set("order")

        token = sync_contextvars_to_baggage()
        try:
            assert baggage.get_baggage(f"{BAGGAGE_PREFIX}.cell_id") == "cell-99"
            assert baggage.get_baggage(f"{BAGGAGE_PREFIX}.domain") == "order"
        finally:
            detach_baggage_token(token)

    def test_none_values_not_propagated(self):
        """None인 ContextVar는 Baggage에 전파되지 않는다."""
        from opentelemetry import baggage

        # 모든 ContextVar가 None인 상태
        token = sync_contextvars_to_baggage()
        try:
            assert baggage.get_baggage(f"{BAGGAGE_PREFIX}.cell_id") is None
            assert baggage.get_baggage(f"{BAGGAGE_PREFIX}.domain") is None
        finally:
            detach_baggage_token(token)


class TestBaggageSyncMiddlewareIntegrationBehavior:
    """BaggageSyncMiddleware를 포함한 요청 흐름 통합 검증.

    Note: BaggageSyncMiddleware는 Django API 모듈 의존으로 인해
    Django 설정이 필요하다. 여기서는 baggage 모듈의 함수만으로
    미들웨어 흐름을 재현한다.
    """

    def setup_method(self):
        self._cell_token = _current_cell_id.set(None)
        self._domain_token = _current_domain.set(None)

    def teardown_method(self):
        _current_cell_id.reset(self._cell_token)
        _current_domain.reset(self._domain_token)

    def test_sync_then_response_captures_baggage(self):
        """ContextVar 설정 → Baggage 동기화 → 응답 핸들러에서 Baggage 접근 가능."""
        from opentelemetry import baggage

        # CellTaggingMiddleware가 이미 설정한 상태 시뮬레이션
        _current_cell_id.set("cell-5")
        _current_domain.set("inventory")

        # BaggageSyncMiddleware.__call__() 흐름 재현
        restore_contextvars_from_baggage()

        token = sync_contextvars_to_baggage()
        try:
            # 응답 핸들러 (get_response) 내부에서 Baggage 접근
            baggage_cell_id = baggage.get_baggage(f"{BAGGAGE_PREFIX}.cell_id")
            baggage_domain = baggage.get_baggage(f"{BAGGAGE_PREFIX}.domain")
            assert baggage_cell_id == "cell-5"
            assert baggage_domain == "inventory"
        finally:
            detach_baggage_token(token)

    def test_detach_isolates_baggage_context_per_request(self):
        """detach 후 Baggage 컨텍스트가 격리(복원)된다."""
        from opentelemetry import baggage

        _current_cell_id.set("cell-1")

        token = sync_contextvars_to_baggage()
        detach_baggage_token(token)

        # detach 후 Baggage 컨텍스트가 복원되어야 함
        value_after = baggage.get_baggage(f"{BAGGAGE_PREFIX}.cell_id")
        assert value_after is None
