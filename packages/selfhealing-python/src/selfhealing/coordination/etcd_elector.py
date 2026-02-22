"""
etcd 기반 Leader Election.

etcd의 Lease 및 Election API를 사용한 리더 선출.
Redis 대안으로, 더 강력한 일관성이 필요한 환경에서 사용.

요구사항:
    pip install etcd3

환경변수:
    SELFHEALING_LEADER_BACKEND=etcd
    SELFHEALING_LEADER_ETCD_ENDPOINTS=localhost:2379
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import structlog

from selfhealing.coordination.base import (
    LeaderElector,
    LeaderInfo,
    LeadershipState,
)
from selfhealing.coordination.config import (
    LeaderElectionSettings,
    get_leader_election_settings,
)

logger = structlog.get_logger()

# etcd 가용성 확인
# TypeError: protobuf 버전 불일치 시 Descriptors 오류 발생 가능
try:
    import etcd3

    ETCD_AVAILABLE = True
except (ImportError, TypeError) as e:
    ETCD_AVAILABLE = False
    etcd3 = None  # type: ignore
    logger.debug(
        "라이브러리를_로드할_없습니다",
        error=e,
    )


class EtcdLeaderElector(LeaderElector):
    """
    etcd 기반 Leader Elector.

    etcd3 라이브러리를 사용하여 리더 선출을 수행합니다.
    etcd의 Lease API를 활용하여 자동 만료 및 갱신을 처리합니다.

    알고리즘:
    1. etcd Lease 생성 (TTL 설정)
    2. 리더 키에 대해 트랜잭션으로 획득 시도 (Compare-And-Swap)
    3. 획득 성공 → 리더 (Lease 갱신 스레드 시작)
    4. 획득 실패 → 팔로워 (Watch로 변경 감지)
    5. Lease 만료 → 재선출

    Attributes:
        resource_name: 리소스 이름
        state: 현재 리더십 상태
    """

    def __init__(
        self,
        resource_name: str,
        settings: LeaderElectionSettings | None = None,
        etcd_client: Any | None = None,
    ):
        """
        초기화.

        Args:
            resource_name: 리소스 이름 (예: "dlq-consumer")
            settings: 설정
            etcd_client: etcd3 클라이언트 (None이면 자동 생성)

        Raises:
            ImportError: etcd3 라이브러리가 설치되지 않은 경우
        """
        if not ETCD_AVAILABLE:
            raise ImportError("etcd3 라이브러리가 필요합니다. " "pip install etcd3 로 설치하세요.")

        self._resource_name = resource_name
        self._settings = settings or get_leader_election_settings()
        self._etcd = etcd_client

        self._node_id = self._settings.get_node_id()
        self._key = f"{self._settings.etcd_key_prefix}{resource_name}"
        self._fencing_key = f"{self._settings.etcd_key_prefix}fencing:{resource_name}"

        self._lock = threading.RLock()
        self._state = LeadershipState.NOT_STARTED
        self._running = False
        self._fencing_token: int = 0

        # 스레드
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lease: Any = None
        self._lease_id: int = 0

        # 콜백
        self._on_become_callbacks: list[Callable[[], None]] = []
        self._on_lose_callbacks: list[Callable[[], None]] = []

        # 콜백 실행용 스레드 풀
        from concurrent.futures import ThreadPoolExecutor

        self._callback_executor: ThreadPoolExecutor | None = None

    def _get_etcd(self) -> Any:
        """etcd 클라이언트 반환."""
        if self._etcd is None:
            endpoints = self._settings.etcd_endpoints.split(",")
            host, port = endpoints[0].strip().split(":")
            self._etcd = etcd3.client(host=host, port=int(port))
        return self._etcd

    def _get_callback_executor(self):
        """콜백 실행용 스레드 풀."""
        if self._callback_executor is None:
            from concurrent.futures import ThreadPoolExecutor

            self._callback_executor = ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix=f"EtcdLeaderCallback-{self._resource_name}",
            )
        return self._callback_executor

    @property
    def resource_name(self) -> str:
        """리소스 이름."""
        return self._resource_name

    @property
    def state(self) -> LeadershipState:
        """현재 상태."""
        with self._lock:
            return self._state

    def is_leader(self) -> bool:
        """현재 리더인지 확인."""
        with self._lock:
            return self._state == LeadershipState.LEADER

    def get_fencing_token(self) -> int:
        """현재 Fencing Token 반환."""
        return self._fencing_token

    def get_leader(self) -> LeaderInfo | None:
        """현재 리더 정보 조회."""
        try:
            etcd = self._get_etcd()
            value, metadata = etcd.get(self._key)

            if value is None:
                return None

            data = json.loads(value.decode())
            elected_at = datetime.fromisoformat(data["elected_at"])

            # TTL 조회
            if metadata and metadata.lease_id:
                ttl_response = etcd.get_lease_info(metadata.lease_id)
                ttl = ttl_response.TTL if ttl_response else 0
            else:
                ttl = 0

            expires_at = datetime.now(timezone.utc).replace(microsecond=0) + __import__("datetime").timedelta(seconds=ttl)

            return LeaderInfo(
                node_id=data["node_id"],
                elected_at=elected_at,
                lease_expires_at=expires_at,
                is_self=(data["node_id"] == self._node_id),
            )

        except Exception as e:
            logger.exception(
                "etcd_leader_elector.get_leader_error",
                error=e,
            )
            return None

    def is_lease_valid(self) -> bool:
        """현재 Lease가 유효한지 확인."""
        if not self.is_leader():
            return False

        try:
            leader = self.get_leader()
            return leader is not None and leader.is_self
        except Exception:
            return False

    def _create_lease(self) -> Any:
        """etcd Lease 생성."""
        etcd = self._get_etcd()
        self._lease = etcd.lease(ttl=self._settings.lease_ttl_seconds)
        self._lease_id = self._lease.id
        logger.debug(
            "etcd_leader_elector.lease_생성",
            _self=self._lease_id,
        )
        return self._lease

    def _try_acquire(self) -> bool:
        """리더 획득 시도."""
        try:
            etcd = self._get_etcd()

            # Lease 생성
            lease = self._create_lease()

            value = json.dumps(
                {
                    "node_id": self._node_id,
                    "elected_at": datetime.now(timezone.utc).isoformat(),
                }
            ).encode()

            # Compare-And-Swap: 키가 없을 때만 생성
            success, responses = etcd.transaction(
                compare=[
                    etcd.transactions.create(self._key) == 0,  # 키가 없을 때
                ],
                success=[
                    etcd.transactions.put(self._key, value, lease=lease),
                ],
                failure=[],
            )

            if success:
                # Fencing Token 증가
                self._increment_fencing_token()
                logger.info(
                    "etcd_leader_elector.리더_획득_성공",
                    _self=self._resource_name,
                    self_1=self._fencing_token,
                )
                return True
            else:
                # 획득 실패, Lease 취소
                lease.revoke()
                self._lease = None
                self._lease_id = 0
                return False

        except Exception as e:
            logger.exception(
                "etcd_leader_elector.acquire_error",
                error=e,
            )
            return False

    def _increment_fencing_token(self) -> int:
        """Fencing Token 증가 (원자적)."""
        try:
            etcd = self._get_etcd()

            # 현재 값 조회
            value, _ = etcd.get(self._fencing_key)
            current = int(value.decode()) if value else 0

            # 증가된 값 저장
            new_value = current + 1
            etcd.put(self._fencing_key, str(new_value).encode())

            self._fencing_token = new_value
            return new_value

        except Exception as e:
            logger.exception(
                "etcd_leader_elector.fencing_token_error",
                error=e,
            )
            self._fencing_token += 1
            return self._fencing_token

    def _renew_lease(self) -> bool:
        """Lease 갱신."""
        try:
            if self._lease is None:
                return False

            self._lease.refresh()
            logger.debug(
                "etcd_leader_elector.lease_갱신",
                _self=self._lease_id,
            )
            return True

        except Exception as e:
            logger.exception(
                "etcd_leader_elector.renew_error",
                error=e,
            )
            return False

    def _release_leadership(self) -> None:
        """리더십 반납."""
        try:
            etcd = self._get_etcd()

            # 리더 키 삭제 (조건부)
            current_value, _ = etcd.get(self._key)
            if current_value:
                data = json.loads(current_value.decode())
                if data.get("node_id") == self._node_id:
                    etcd.delete(self._key)

            # Lease 취소
            if self._lease:
                self._lease.revoke()
                self._lease = None
                self._lease_id = 0

            logger.info(
                "etcd_leader_elector.리더십_반납",
                _self=self._resource_name,
            )

        except Exception as e:
            logger.exception(
                "etcd_leader_elector.release_error",
                error=e,
            )

    def _become_leader(self) -> None:
        """리더 됨 (비동기 콜백)."""
        with self._lock:
            if self._state == LeadershipState.LEADER:
                return
            self._state = LeadershipState.LEADER

        logger.info(
            "etcd_leader_elector.리더가_되었습니다",
            _self=self._resource_name,
        )

        # 비동기 콜백 실행
        executor = self._get_callback_executor()
        for callback in self._on_become_callbacks:
            executor.submit(self._safe_callback, callback, "on_become_leader")

    def _lose_leader(self, reason: str = "normal") -> None:
        """리더십 상실 (비동기 콜백)."""
        with self._lock:
            if self._state != LeadershipState.LEADER:
                return
            self._state = LeadershipState.FOLLOWER

        logger.info(
            "etcd_leader_elector.리더십을_잃었습니다",
            _self=self._resource_name,
            reason=reason,
        )

        # 비동기 콜백 실행
        executor = self._get_callback_executor()
        for callback in self._on_lose_callbacks:
            executor.submit(self._safe_callback, callback, "on_lose_leader")

    def _safe_callback(self, callback: Callable, callback_type: str) -> None:
        """안전한 콜백 실행."""
        try:
            callback()
        except Exception as e:
            logger.exception(
                "etcd_leader_elector.callback_error",
                callback_type=callback_type,
                error=e,
            )

    def _run_loop(self) -> None:
        """선출 루프."""
        from selfhealing.utils.jitter import calculate_jitter

        consecutive_failures = 0

        while self._running:
            try:
                with self._lock:
                    current_state = self._state

                if current_state == LeadershipState.LEADER:
                    # 리더: Lease 갱신
                    if self._renew_lease():
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1

                        # Self-Fencing
                        if self._settings.self_fencing_enabled:
                            logger.warning(
                                "etcd_leader_elector.lease_갱신_실패_리더십",
                            )
                            self._lose_leader(reason="self_fencing")
                            consecutive_failures = 0
                        elif consecutive_failures >= self._settings.max_retry_attempts > 0:
                            self._lose_leader()
                            consecutive_failures = 0

                    # 갱신 주기
                    self._stop_event.wait(self._settings.get_effective_renew_interval())
                    if self._stop_event.is_set():
                        break

                else:
                    # 팔로워: 리더 획득 시도
                    if self._try_acquire():
                        self._become_leader()
                        consecutive_failures = 0
                    else:
                        with self._lock:
                            self._state = LeadershipState.FOLLOWER

                    # Jitter 적용
                    jitter = calculate_jitter(
                        max_delay_seconds=self._settings.retry_interval_seconds * self._settings.retry_jitter_factor,
                        min_delay_seconds=0,
                    )
                    self._stop_event.wait(self._settings.retry_interval_seconds + jitter)
                    if self._stop_event.is_set():
                        break

            except Exception as e:
                logger.exception(
                    "etcd_leader_elector.loop_error",
                    error=e,
                )
                self._stop_event.wait(self._settings.retry_interval_seconds)
                if self._stop_event.is_set():
                    break

    def start(self) -> None:
        """리더 선출 시작."""
        if not self._settings.enabled:
            logger.info(
                "etcd_leader_elector.비활성화됨",
                _self=self._resource_name,
            )
            return

        if self._running:
            return

        self._stop_event.clear()
        self._running = True
        with self._lock:
            self._state = LeadershipState.FOLLOWER

        self._worker = threading.Thread(
            target=self._run_loop,
            name=f"EtcdLeaderElector-{self._resource_name}",
            daemon=True,
        )
        self._worker.start()
        logger.info(
            "etcd_leader_elector.시작됨",
            _self=self._resource_name,
        )

    def stop(self) -> None:
        """리더 선출 중지."""
        was_leader = False
        with self._lock:
            was_leader = self._state == LeadershipState.LEADER
            self._state = LeadershipState.STOPPING

        self._running = False
        self._stop_event.set()

        # 리더십 반납
        if was_leader:
            self._release_leadership()

            logger.info(
                "etcd_leader_elector.리더십을_잃었습니다",
                _self=self._resource_name,
            )

            # 비동기 콜백
            executor = self._get_callback_executor()
            for callback in self._on_lose_callbacks:
                executor.submit(self._safe_callback, callback, "on_lose_leader")

        # 워커 스레드 종료 대기
        if self._worker:
            self._worker.join(timeout=2.0)

        # 콜백 스레드 풀 종료
        if self._callback_executor:
            self._callback_executor.shutdown(wait=True, cancel_futures=False)
            self._callback_executor = None

        # etcd 연결 정리
        if self._etcd:
            try:
                self._etcd.close()
            except Exception:
                pass
            self._etcd = None

        with self._lock:
            self._state = LeadershipState.STOPPED

        logger.info(
            "etcd_leader_elector.중지됨",
            _self=self._resource_name,
        )

    def on_become_leader(self, callback: Callable[[], None]) -> Callable[[], None]:
        """리더가 되었을 때 콜백 등록."""
        self._on_become_callbacks.append(callback)
        return callback

    def on_lose_leader(self, callback: Callable[[], None]) -> Callable[[], None]:
        """리더십을 잃었을 때 콜백 등록."""
        self._on_lose_callbacks.append(callback)
        return callback


def is_etcd_available() -> bool:
    """etcd 라이브러리가 사용 가능한지 확인."""
    return ETCD_AVAILABLE
