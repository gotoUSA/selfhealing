"""
Self-Healing 사이드카 진입점.

타언어 애플리케이션에서 Self-Healing 시스템을 사용할 수 있도록
UDS 서버 및 gRPC 서버를 시작합니다.

지원 모드:
- uds: Unix Domain Socket 서버만 시작
- grpc: gRPC 서버만 시작
- both: 양쪽 모두 시작 (기본값)

환경 변수:
- SELFHEALING_MODE: 실행 모드 (uds, grpc, both)
- SELFHEALING_SOCKET: UDS 소켓 경로 (기본: /tmp/selfhealing.sock)
- SELFHEALING_GRPC_PORT: gRPC 포트 (기본: 50051)
- SELFHEALING_GRPC_HOST: gRPC 바인딩 호스트 (기본: 0.0.0.0)
- SELFHEALING_AUTH_TOKEN: 인증 토큰 (선택)
- REDIS_URL: Redis 연결 URL
- DATABASE_URL: 데이터베이스 연결 URL

Usage:
    # UDS + gRPC 모두 실행
    python entrypoint.py --mode both

    # UDS만 실행
    python entrypoint.py --mode uds

    # gRPC만 실행
    python entrypoint.py --mode grpc --port 50052
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from typing import Any

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("selfhealing.sidecar")


class SidecarEntrypoint:
    """
    사이드카 진입점.

    UDS 서버와 gRPC 서버를 관리하고,
    정상 종료(graceful shutdown)를 처리합니다.
    """

    def __init__(
        self,
        mode: str = "both",
        socket_path: str | None = None,
        grpc_host: str = "0.0.0.0",
        grpc_port: int = 50051,
        auth_token: str | None = None,
    ):
        """
        사이드카 초기화.

        Args:
            mode: 실행 모드 (uds, grpc, both)
            socket_path: UDS 소켓 경로
            grpc_host: gRPC 바인딩 호스트
            grpc_port: gRPC 포트
            auth_token: 인증 토큰
        """
        self._mode = mode
        self._socket_path = socket_path or os.getenv("SELFHEALING_SOCKET", "/tmp/selfhealing.sock")
        self._grpc_host = grpc_host
        self._grpc_port = grpc_port
        self._auth_token = auth_token or os.getenv("SELFHEALING_AUTH_TOKEN")

        self._uds_server: Any = None
        self._grpc_server: Any = None
        self._running = False
        self._shutdown_event = threading.Event()

    def start(self) -> None:
        """사이드카 시작."""
        logger.info(f"Starting Self-Healing Sidecar (mode={self._mode})")

        # 시그널 핸들러 등록
        self._setup_signal_handlers()

        self._running = True

        # UDS 서버 시작
        if self._mode in ("uds", "both"):
            self._start_uds_server()

        # gRPC 서버 시작
        if self._mode in ("grpc", "both"):
            self._start_grpc_server()

        # 메인 루프
        self._main_loop()

    def stop(self) -> None:
        """사이드카 종료."""
        logger.info("Stopping Self-Healing Sidecar...")
        self._running = False
        self._shutdown_event.set()

        # UDS 서버 종료
        if self._uds_server:
            try:
                self._uds_server.stop()
                logger.info("UDS server stopped")
            except Exception as e:
                logger.error(f"Error stopping UDS server: {e}")

        # gRPC 서버 종료
        if self._grpc_server:
            try:
                self._grpc_server.stop(grace=5.0)
                logger.info("gRPC server stopped")
            except Exception as e:
                logger.error(f"Error stopping gRPC server: {e}")

        logger.info("Sidecar shutdown complete")

    def _setup_signal_handlers(self) -> None:
        """시그널 핸들러 설정."""
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        """시그널 처리."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received signal {sig_name}, initiating shutdown...")
        self.stop()

    def _start_uds_server(self) -> None:
        """UDS 서버 시작."""
        try:
            from selfhealing.adapters.ipc import UDSServer

            self._uds_server = UDSServer(socket_path=self._socket_path)
            self._uds_server.start(background=True)
            logger.info(f"UDS server started on {self._socket_path}")
        except ImportError as e:
            logger.error(f"Failed to import UDS server: {e}")
        except Exception as e:
            logger.error(f"Failed to start UDS server: {e}")

    def _start_grpc_server(self) -> None:
        """gRPC 서버 시작."""
        try:
            from selfhealing.adapters.ipc import SidecarGRPCServer

            self._grpc_server = SidecarGRPCServer(
                host=self._grpc_host,
                port=self._grpc_port,
            )
            self._grpc_server.start(blocking=False)
            logger.info(f"gRPC server started on {self._grpc_host}:{self._grpc_port}")
        except ImportError as e:
            logger.warning(f"gRPC not available: {e}")
        except RuntimeError as e:
            logger.warning(f"gRPC server could not start: {e}")
        except Exception as e:
            logger.error(f"Failed to start gRPC server: {e}")

    def _main_loop(self) -> None:
        """메인 이벤트 루프."""
        logger.info("Sidecar is running. Press Ctrl+C to stop.")

        while self._running:
            # 셧다운 이벤트 대기 (1초 타임아웃)
            if self._shutdown_event.wait(timeout=1.0):
                break

            # 헬스 체크 로깅 (60초마다)
            self._periodic_health_log()

    def _periodic_health_log(self) -> None:
        """주기적 헬스 로그 출력."""
        # 간단한 상태 확인
        status = {
            "uds": self._uds_server is not None and getattr(self._uds_server, "_running", False),
            "grpc": self._grpc_server is not None and getattr(self._grpc_server, "_running", False),
        }
        logger.debug(f"Health status: {status}")


def parse_args() -> argparse.Namespace:
    """커맨드라인 인자 파싱."""
    parser = argparse.ArgumentParser(
        description="Self-Healing Sidecar Server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--mode",
        choices=["uds", "grpc", "both"],
        default=os.getenv("SELFHEALING_MODE", "both"),
        help="Server mode",
    )

    parser.add_argument(
        "--socket",
        default=os.getenv("SELFHEALING_SOCKET", "/tmp/selfhealing.sock"),
        help="UDS socket path",
    )

    parser.add_argument(
        "--host",
        default=os.getenv("SELFHEALING_GRPC_HOST", "0.0.0.0"),
        help="gRPC bind host",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("SELFHEALING_GRPC_PORT", "50051")),
        help="gRPC port",
    )

    parser.add_argument(
        "--auth-token",
        default=os.getenv("SELFHEALING_AUTH_TOKEN"),
        help="Authentication token",
    )

    return parser.parse_args()


def main() -> None:
    """메인 진입점."""
    args = parse_args()

    sidecar = SidecarEntrypoint(
        mode=args.mode,
        socket_path=args.socket,
        grpc_host=args.host,
        grpc_port=args.port,
        auth_token=args.auth_token,
    )

    try:
        sidecar.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Sidecar error: {e}")
        sys.exit(1)
    finally:
        sidecar.stop()


if __name__ == "__main__":
    main()
