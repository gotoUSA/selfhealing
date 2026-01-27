# packages/selfhealing-python/src/selfhealing/adapters/health_checker.py
"""
이식 가능한 고성능 헬스 체커 (Platinum SLA 최적화)

OS별 최적 성능 자동 선택
추상화 레이어 + 자동 Fallback
"""

import logging
import platform
import socket
import struct
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

__all__ = [
    "HealthCheckStrategy",
    "TTLCacheStrategy",
    "LinuxTCPInfoStrategy",
    "PortableHealthChecker",
]

logger = logging.getLogger(__name__)


class HealthCheckStrategy(ABC):
    """헬스 체크 전략 인터페이스"""

    @abstractmethod
    def check(self, target: str) -> bool:
        """
        타겟 서비스 헬스 체크

        Args:
            target: 타겟 주소 (예: "localhost:8080")

        Returns:
            헬스 상태 (True=healthy, False=unhealthy)
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """전략 이름 반환"""
        pass


class TTLCacheStrategy(HealthCheckStrategy):
    """
    TTL 기반 캐시 전략 (범용)

    모든 OS에서 동작, 성능도 충분 (~0.01ms 캐시 히트)
    """

    def __init__(
        self, check_callback: Callable[[str], bool] | None = None, ttl: float = 5.0
    ):
        """
        Args:
            check_callback: 실제 헬스 체크를 수행하는 콜백 함수
            ttl: 캐시 유효 시간 (초)
        """
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._ttl = ttl
        self._check_callback = check_callback

    def configure(
        self, check_callback: Callable[[str], bool], ttl: float = 5.0
    ) -> None:
        """콜백 및 TTL 설정"""
        self._check_callback = check_callback
        self._ttl = ttl

    def check(self, target: str) -> bool:
        with self._lock:
            cached = self._cache.get(target)

            if cached and time.time() - cached["ts"] < self._ttl:
                return cached["healthy"]

        # 캐시 미스
        return self._refresh(target)

    def get_name(self) -> str:
        return "TTLCacheStrategy"

    def invalidate(self, target: str) -> None:
        """특정 타겟 캐시 무효화"""
        with self._lock:
            self._cache.pop(target, None)

    def invalidate_all(self) -> None:
        """전체 캐시 무효화"""
        with self._lock:
            self._cache.clear()

    def _refresh(self, target: str) -> bool:
        if not self._check_callback:
            return True  # 콜백 없으면 healthy 가정

        try:
            healthy = self._check_callback(target)
        except Exception as e:
            logger.debug(f"[TTLCacheStrategy] Health check failed for {target}: {e}")
            healthy = False

        with self._lock:
            self._cache[target] = {"healthy": healthy, "ts": time.time()}

        return healthy


class LinuxTCPInfoStrategy(HealthCheckStrategy):
    """
    Linux 전용 TCP_INFO 전략

    커널에서 직접 소켓 상태 조회 (~0.01ms)
    Linux에서만 동작, 다른 OS에서는 NotImplementedError 발생
    """

    TCP_INFO = 11  # Linux TCP_INFO socket option
    TCP_ESTABLISHED = 1

    def __init__(self, timeout: float = 0.1):
        """
        Args:
            timeout: 연결 타임아웃 (초)
        """
        self._timeout = timeout

        # Linux 여부 확인
        if platform.system() != "Linux":
            raise NotImplementedError("LinuxTCPInfoStrategy is only available on Linux")

    def check(self, target: str) -> bool:
        try:
            host, port_str = target.split(":")
            port = int(port_str)

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._timeout)

            try:
                sock.connect((host, port))

                # TCP_INFO 구조체에서 상태 읽기
                info = sock.getsockopt(socket.IPPROTO_TCP, self.TCP_INFO, 104)
                state = struct.unpack("B", info[0:1])[0]

                return state == self.TCP_ESTABLISHED
            finally:
                sock.close()

        except Exception as e:
            logger.debug(
                f"[LinuxTCPInfoStrategy] Health check failed for {target}: {e}"
            )
            return False

    def get_name(self) -> str:
        return "LinuxTCPInfoStrategy"


class SimpleSocketStrategy(HealthCheckStrategy):
    """
    간단한 소켓 연결 전략 (범용)

    TCP 연결 성공 여부로 헬스 체크
    """

    def __init__(self, timeout: float = 1.0):
        """
        Args:
            timeout: 연결 타임아웃 (초)
        """
        self._timeout = timeout

    def check(self, target: str) -> bool:
        try:
            host, port_str = target.split(":")
            port = int(port_str)

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._timeout)

            try:
                sock.connect((host, port))
                return True
            finally:
                sock.close()

        except Exception as e:
            logger.debug(
                f"[SimpleSocketStrategy] Health check failed for {target}: {e}"
            )
            return False

    def get_name(self) -> str:
        return "SimpleSocketStrategy"


class PortableHealthChecker:
    """
    이식 가능한 고성능 헬스 체커

    OS/환경에 맞는 최적 전략을 자동 선택

    Usage:
        checker = PortableHealthChecker()

        # 헬스 체크
        is_healthy = checker.is_healthy("localhost:8080")

        # 전략 확인
        print(checker.strategy_name)  # "TTLCacheStrategy" or "LinuxTCPInfoStrategy"
    """

    def __init__(
        self,
        check_callback: Callable[[str], bool] | None = None,
        ttl: float = 5.0,
        force_strategy: str | None = None,
    ):
        """
        Args:
            check_callback: TTLCacheStrategy용 콜백 (미제공 시 SimpleSocketStrategy 사용)
            ttl: TTLCacheStrategy의 캐시 TTL
            force_strategy: 특정 전략 강제 사용 ("ttl_cache", "linux_tcp", "simple_socket")
        """
        self._check_callback = check_callback
        self._ttl = ttl
        self._strategy = self._select_strategy(force_strategy)

    def _select_strategy(self, force_strategy: str | None) -> HealthCheckStrategy:
        """환경에 맞는 최적 전략 선택"""

        if force_strategy == "linux_tcp":
            return LinuxTCPInfoStrategy()

        if force_strategy == "simple_socket":
            return SimpleSocketStrategy()

        if force_strategy == "ttl_cache":
            callback = self._check_callback or self._default_check
            return TTLCacheStrategy(check_callback=callback, ttl=self._ttl)

        # 자동 선택
        system = platform.system()

        # Linux: TTL 캐시 + TCP_INFO 시도
        if system == "Linux":
            try:
                strategy = LinuxTCPInfoStrategy()
                # TTL 캐시로 감싸서 반환
                return TTLCacheStrategy(check_callback=strategy.check, ttl=self._ttl)
            except NotImplementedError:
                pass

        # 범용 폴백: TTL 캐시 + 콜백 또는 소켓
        if self._check_callback:
            return TTLCacheStrategy(check_callback=self._check_callback, ttl=self._ttl)

        # 기본: 소켓 전략 + TTL 캐시
        simple_strategy = SimpleSocketStrategy()
        return TTLCacheStrategy(check_callback=simple_strategy.check, ttl=self._ttl)

    def _default_check(self, target: str) -> bool:
        """기본 헬스 체크 (소켓 연결)"""
        return SimpleSocketStrategy().check(target)

    def is_healthy(self, target: str) -> bool:
        """
        타겟 서비스 헬스 체크

        Args:
            target: 타겟 주소 (예: "localhost:8080")

        Returns:
            헬스 상태 (True=healthy, False=unhealthy)
        """
        return self._strategy.check(target)

    @property
    def strategy_name(self) -> str:
        """현재 사용 중인 전략 이름"""
        return self._strategy.get_name()

    def invalidate(self, target: str) -> None:
        """특정 타겟 캐시 무효화 (TTLCacheStrategy인 경우에만)"""
        if isinstance(self._strategy, TTLCacheStrategy):
            self._strategy.invalidate(target)

    def invalidate_all(self) -> None:
        """전체 캐시 무효화 (TTLCacheStrategy인 경우에만)"""
        if isinstance(self._strategy, TTLCacheStrategy):
            self._strategy.invalidate_all()
