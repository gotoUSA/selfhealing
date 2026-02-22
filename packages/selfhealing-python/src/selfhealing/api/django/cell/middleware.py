"""
Cell Tagging & Baggage Sync Django Middlewares.

CellTaggingMiddleware:
    HTTP 요청에 cell_id 어트리뷰트를 추가하고,
    ContextVar에 설정하여 서비스 레이어에서도 접근 가능하게 합니다.
    수신 측 cell_id 전파 수용 시 Trust Boundary(CIDR) 검증 및
    Topology Mismatch(Registry 유효성) 검증을 수행합니다.

BaggageSyncMiddleware:
    ContextVar ↔ OTel Baggage 양방향 동기화.
    수신: Baggage → ContextVar 복원
    송신: ContextVar → Baggage 동기화 (outgoing 요청에 자동 전파)

활성화:
    SELFHEALING_CELL_TOPOLOGY_ENABLED=true
    SELFHEALING_CELL_TAGGING_ENABLED=true

MIDDLEWARE 설정:
    "selfhealing.api.django.cell.middleware.CellTaggingMiddleware"
    → AuthenticationMiddleware 이후, BaggageSyncMiddleware 이전 배치
    "selfhealing.api.django.cell.middleware.BaggageSyncMiddleware"
    → CellTaggingMiddleware 직후 배치
"""

from __future__ import annotations

import logging
from ipaddress import ip_address, ip_network
from typing import Any

from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


class CellTaggingMiddleware:
    """
    요청에 cell_id를 태깅하는 Django 미들웨어.

    토글 패턴: TieringMiddleware와 동일
    - SELFHEALING_CELL_TOPOLOGY_ENABLED=false → 즉시 패스스루
    - SELFHEALING_CELL_TAGGING_ENABLED=false → 즉시 패스스루

    수신 cell_id 전파 수용:
    - BaggageSyncMiddleware가 복원한 ContextVar에서 incoming cell_id를 읽음
    - CIDR 기반 Trust 검증 후, CellRegistry Topology 검증 통과 시 수용
    - 검증 실패 시 로컬 해싱으로 폴백

    ContextVar 전파:
    - request.cell_id 어트리뷰트 + _current_cell_id ContextVar 동시 설정
    - 요청 종료 시 ContextVar 자동 복원 (token.reset)
    """

    def __init__(self, get_response: Any):
        self.get_response = get_response
        self._tagger = None
        self._trusted_cidrs: list[str] | None = None

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not self._check_enabled():
            return self.get_response(request)

        from selfhealing.context.cell_context import _current_cell_id

        tagger = self._get_tagger()

        # 수신 측 cell_id 전파 수용 (Trust 검증 + Topology 검증)
        cell_id = self._accept_incoming_cell_id(request)

        if cell_id is None:
            # 수신 전파 없음 또는 검증 실패 → 로컬 해싱
            cell_id = tagger.resolve_cell_id_from_request(request)

        # 요청에 cell_id 어트리뷰트 추가
        request.cell_id = cell_id  # type: ignore[attr-defined]

        # ContextVar 설정 — 서비스 레이어 및 Celery 발행 시점에서 접근 가능
        token = _current_cell_id.set(cell_id)

        try:
            response = self.get_response(request)
        finally:
            # 요청 종료 시 ContextVar 복원 (actor_context.py 패턴)
            _current_cell_id.reset(token)

        # 응답 헤더에 Cell 정보 추가 (디버깅용)
        response["X-Cell-Id"] = cell_id

        return response

    # =========================================================================
    # 수신 측 cell_id 전파 수용
    # =========================================================================

    def _accept_incoming_cell_id(self, request: HttpRequest) -> str | None:
        """
        수신된 cell_id를 Trust 검증 + Topology 검증 후 수용 또는 거부.

        Returns:
            유효한 cell_id 또는 None (로컬 해싱으로 폴백)
        """
        from selfhealing.context.cell_context import get_current_cell_id

        incoming_cell_id = get_current_cell_id()

        if not incoming_cell_id:
            return None

        # CIDR Trust 검증 — 신뢰할 수 있는 내부 네트워크인지 확인
        if not self._is_trusted_source(request):
            logger.debug(
                "Untrusted source attempted cell_id propagation: %s",
                incoming_cell_id,
            )
            return None

        # Topology Mismatch 검증 — 로컬 Registry에 유효한 Cell인지 확인
        return self._validate_cell_id(incoming_cell_id)

    def _is_trusted_source(self, request: HttpRequest) -> bool:
        """
        요청 소스가 신뢰할 수 있는 내부 네트워크인지 CIDR로 검증.

        IP 추출은 extract_client_ip()를 사용하여 X-Forwarded-For,
        X-Real-IP, REMOTE_ADDR 순서로 해석한다.
        """
        from selfhealing.utils.network import extract_client_ip

        client_ip = extract_client_ip(request)
        if not client_ip:
            return False

        try:
            addr = ip_address(client_ip)
            return any(addr in ip_network(cidr, strict=False) for cidr in self._get_trusted_cidrs())
        except ValueError:
            return False

    def _get_trusted_cidrs(self) -> list[str]:
        """trusted_source_cidrs 지연 로딩."""
        if self._trusted_cidrs is None:
            from selfhealing.settings.cell_topology import get_cell_topology_settings

            settings = get_cell_topology_settings()
            self._trusted_cidrs = settings.trusted_source_cidrs
        return self._trusted_cidrs

    def _validate_cell_id(self, incoming_cell_id: str) -> str | None:
        """
        수신된 cell_id가 로컬 CellRegistry에서 유효한지 검증.

        검증 기준:
        1. Registry에 존재하는 Cell인지 (get_cell_info != None)
        2. ACTIVE 또는 WARMUP 상태인지 (DRAINING/ISOLATED은 격리 정책 우회 방지)

        검증 실패 시 topology_mismatch 메트릭 증가 + 로깅.
        """
        from selfhealing.services.cell_topology.models import CellState
        from selfhealing.services.cell_topology.registry import get_cell_registry

        registry = get_cell_registry()
        cell_info = registry.get_cell_info(incoming_cell_id)

        if cell_info is None:
            self._record_topology_mismatch(incoming_cell_id, "cell_not_found")
            logger.warning(
                "Topology mismatch: received '%s' not in local registry " "(local cell_count=%d)",
                incoming_cell_id,
                len(registry.get_all_cells()),
            )
            return None

        if cell_info.state not in (CellState.ACTIVE, CellState.WARMUP):
            self._record_topology_mismatch(incoming_cell_id, "cell_not_active")
            logger.warning(
                "Topology mismatch: received '%s' is %s (not ACTIVE/WARMUP)",
                incoming_cell_id,
                cell_info.state.value,
            )
            return None

        return incoming_cell_id

    @staticmethod
    def _record_topology_mismatch(incoming_cell_id: str, reason: str) -> None:
        """Topology Mismatch Prometheus 카운터 기록."""
        try:
            from prometheus_client import Counter

            counter = Counter(
                "selfhealing_cell_topology_mismatch_total",
                "Cell topology mismatch between upstream and local registry",
                ["incoming_cell_id", "reason"],
            )
            counter.labels(
                incoming_cell_id=incoming_cell_id,
                reason=reason,
            ).inc()
        except Exception:
            pass  # 메트릭 실패가 요청을 중단하지 않음

    # =========================================================================
    # 기존 헬퍼
    # =========================================================================

    def _check_enabled(self) -> bool:
        """토글 확인."""
        try:
            from django.conf import settings as django_settings

            if not getattr(django_settings, "SELFHEALING_CELL_TOPOLOGY_ENABLED", False):
                return False
            if not getattr(django_settings, "SELFHEALING_CELL_TAGGING_ENABLED", False):
                return False
            return True
        except Exception:
            return False

    def _get_tagger(self) -> Any:
        """CellTagger 지연 로딩."""
        if self._tagger is None:
            from selfhealing.services.cell_topology.tagger import CellTagger

            self._tagger = CellTagger()
        return self._tagger


class BaggageSyncMiddleware:
    """
    ContextVar ↔ OTel Baggage 양방향 동기화 미들웨어.

    실행 순서:
    1. 수신 측: DjangoInstrumentor가 파싱한 Baggage → ContextVar 복원
    2. 송신 측: ContextVar 값 → OTel Baggage 동기화

    배치: CellTaggingMiddleware 직후
    - 모든 ContextVar가 설정된 후 실행되어야 Baggage에 최신값이 반영됨
    - try/finally로 OTel Context token의 격리를 보장

    패턴 참조: services/http_client.py suppress_otel_instrumentation()
    """

    def __init__(self, get_response: Any):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        from selfhealing.observability.baggage import (
            detach_baggage_token,
            restore_contextvars_from_baggage,
            sync_contextvars_to_baggage,
        )

        # 수신 측: Baggage → ContextVar 복원
        restore_contextvars_from_baggage()

        # 송신 측: ContextVar → Baggage 동기화
        token = sync_contextvars_to_baggage()
        try:
            response = self.get_response(request)
        finally:
            # 요청 종료 시 OTel Context 복원 — 누수 방지
            detach_baggage_token(token)
        return response
