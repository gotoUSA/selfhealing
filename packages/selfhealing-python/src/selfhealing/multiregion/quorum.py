"""
Quorum Witness - Split-brain 방지.

DynamoDB Global Table을 사용하여 리전 간 Quorum을 확인합니다.
Primary 승격 전 반드시 Witness 락을 획득해야 합니다.

동작 원리:
1. Primary가 되려는 리전이 DynamoDB에 조건부 쓰기 시도
2. 이미 다른 리전이 락을 보유하고 있으면 실패
3. 성공한 리전만 Primary가 됨
4. 락은 TTL 기반으로 자동 만료 (장애 시 자동 해제)
"""

from __future__ import annotations

import structlog
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

logger = structlog.get_logger()


@dataclass
class QuorumLease:
    """
    Quorum 리스 정보.

    리스를 보유한 동안 해당 리전이 Primary 역할을 수행합니다.

    Attributes:
        region: 리스를 보유한 리전
        acquired_at: 획득 시각 (Unix timestamp)
        expires_at: 만료 시각 (Unix timestamp)
        lease_id: 리스 ID (재획득 시 검증용)
    """

    region: str
    """리스를 보유한 리전."""

    acquired_at: float
    """획득 시각 (Unix timestamp)."""

    expires_at: float
    """만료 시각 (Unix timestamp)."""

    lease_id: str
    """리스 ID (재획득 시 검증용)."""

    def is_valid(self) -> bool:
        """리스가 유효한지 확인."""
        return time.time() < self.expires_at


class DynamoDBClient(Protocol):
    """DynamoDB 클라이언트 프로토콜."""

    def put_item(self, **kwargs: Any) -> Any:
        """아이템 저장."""
        ...

    def get_item(self, **kwargs: Any) -> Any:
        """아이템 조회."""
        ...

    def update_item(self, **kwargs: Any) -> Any:
        """아이템 업데이트."""
        ...

    def delete_item(self, **kwargs: Any) -> Any:
        """아이템 삭제."""
        ...

    @property
    def exceptions(self) -> Any:
        """예외 클래스."""
        ...


class QuorumWitness:
    """
    DynamoDB Global Table 기반 Quorum Witness.

    Split-brain 방지를 위해 Primary 승격 전 Witness 락 획득이 필수입니다.

    동작 원리:
    1. Primary가 되려는 리전이 DynamoDB에 조건부 쓰기 시도
    2. 이미 다른 리전이 락을 보유하고 있으면 실패
    3. 성공한 리전만 Primary가 됨
    4. 락은 TTL 기반으로 자동 만료 (장애 시 자동 해제)

    사용 예:
        import boto3
        dynamodb = boto3.client("dynamodb")

        witness = QuorumWitness(
            dynamodb_client=dynamodb,
            region="ap-northeast-2",
            cluster_id="prod-kr-1",
        )

        # Primary 락 획득 시도
        if witness.try_acquire_primary():
            print("I am now the Primary!")
            # Primary 작업 수행...

            # 주기적 갱신
            witness.renew_lease()
        else:
            print("Another region is the Primary")

        # 종료 시 해제
        witness.release_lease()
    """

    TABLE_NAME = "selfhealing-quorum-witness"
    """DynamoDB 테이블 이름."""

    LEASE_TTL_SECONDS = 60
    """리스 유효 기간 (초)."""

    RENEW_INTERVAL_SECONDS = 20
    """리스 갱신 주기 (초)."""

    def __init__(
        self,
        dynamodb_client: Any,
        region: str,
        cluster_id: str,
        table_name: str | None = None,
        lease_ttl_seconds: int | None = None,
    ):
        """
        초기화.

        Args:
            dynamodb_client: boto3 DynamoDB 클라이언트
            region: 현재 리전 이름
            cluster_id: 클러스터 ID
            table_name: 테이블 이름 (기본값 사용 시 None)
            lease_ttl_seconds: 리스 TTL (초, 기본값 사용 시 None)
        """
        self._dynamodb = dynamodb_client
        self._region = region
        self._cluster_id = cluster_id
        self._table_name = table_name or self.TABLE_NAME
        self._lease_ttl = lease_ttl_seconds or self.LEASE_TTL_SECONDS
        self._current_lease: QuorumLease | None = None
        self._lock = threading.RLock()

        # 자동 갱신 스레드
        self._renew_running = False
        self._renew_thread: threading.Thread | None = None

    def try_acquire_primary(self) -> bool:
        """
        Primary 락 획득 시도.

        조건부 쓰기로 락 획득을 시도합니다.
        이미 다른 리전이 락을 보유하고 있으면 실패합니다.

        Returns:
            True: 락 획득 성공 → Primary 가능
            False: 락 획득 실패 → 다른 리전이 Primary
        """
        now = time.time()
        lease_id = f"{self._region}:{self._cluster_id}:{uuid.uuid4().hex[:8]}"
        expires_at = now + self._lease_ttl

        try:
            self._dynamodb.put_item(
                TableName=self._table_name,
                Item={
                    "pk": {"S": "primary_lease"},
                    "region": {"S": self._region},
                    "cluster_id": {"S": self._cluster_id},
                    "lease_id": {"S": lease_id},
                    "acquired_at": {"N": str(now)},
                    "expires_at": {"N": str(expires_at)},
                    "ttl": {"N": str(int(expires_at))},
                },
                # 조건: 키가 없거나 TTL이 만료된 경우에만 쓰기
                ConditionExpression="attribute_not_exists(pk) OR expires_at < :now",
                ExpressionAttributeValues={
                    ":now": {"N": str(now)},
                },
            )

            with self._lock:
                self._current_lease = QuorumLease(
                    region=self._region,
                    acquired_at=now,
                    expires_at=expires_at,
                    lease_id=lease_id,
                )

            logger.info(
                "quorum.primary_lease_acquired",
                self=self._region,
                expires_at=expires_at,
            )
            return True

        except Exception as e:
            # ConditionalCheckFailedException 처리
            error_name = type(e).__name__
            if "ConditionalCheckFailedException" in error_name or "ConditionalCheckFailed" in str(e):
                logger.warning(
                    "quorum.primary_lease_denied_another",
                    self=self._region,
                )
            else:
                logger.error(
                    "quorum.lease_acquisition_error",
                    error=e,
                )
            return False

    def renew_lease(self) -> bool:
        """
        리스 갱신.

        현재 보유한 리스의 만료 시간을 연장합니다.

        Returns:
            True if 갱신 성공
        """
        with self._lock:
            if self._current_lease is None:
                return False

            now = time.time()
            new_expires_at = now + self._lease_ttl

            try:
                self._dynamodb.update_item(
                    TableName=self._table_name,
                    Key={"pk": {"S": "primary_lease"}},
                    UpdateExpression="SET expires_at = :exp, #ttl = :ttl",
                    ConditionExpression="lease_id = :lid",
                    ExpressionAttributeNames={"#ttl": "ttl"},
                    ExpressionAttributeValues={
                        ":exp": {"N": str(new_expires_at)},
                        ":ttl": {"N": str(int(new_expires_at))},
                        ":lid": {"S": self._current_lease.lease_id},
                    },
                )

                self._current_lease.expires_at = new_expires_at
                logger.debug(
                    "quorum.lease_renewed",
                    new_expires_at=new_expires_at,
                )
                return True

            except Exception as e:
                logger.error(
                    "quorum.lease_renewal_failed",
                    error=e,
                )
                self._current_lease = None
                return False

    def release_lease(self) -> None:
        """
        리스 해제.

        현재 보유한 리스를 해제하여 다른 리전이 Primary가 될 수 있게 합니다.
        """
        with self._lock:
            if self._current_lease is None:
                return

            try:
                self._dynamodb.delete_item(
                    TableName=self._table_name,
                    Key={"pk": {"S": "primary_lease"}},
                    ConditionExpression="lease_id = :lid",
                    ExpressionAttributeValues={
                        ":lid": {"S": self._current_lease.lease_id},
                    },
                )
                logger.info(
                    "quorum.lease_released",
                    self=self._region,
                )
            except Exception as e:
                logger.warning(
                    "quorum.lease_release_failed",
                    error=e,
                )
            finally:
                self._current_lease = None

    def get_current_primary(self) -> str | None:
        """
        현재 Primary 리전 조회.

        Returns:
            Primary 리전 이름 또는 None
        """
        try:
            response = self._dynamodb.get_item(
                TableName=self._table_name,
                Key={"pk": {"S": "primary_lease"}},
            )

            item = response.get("Item")
            if item is None:
                return None

            expires_at = float(item["expires_at"]["N"])
            if time.time() > expires_at:
                return None  # 만료됨

            return item["region"]["S"]

        except Exception as e:
            logger.error(
                "quorum.get_primary_failed",
                error=e,
            )
            return None

    def is_primary(self) -> bool:
        """
        현재 리전이 Primary인지 확인.

        Returns:
            True if Primary
        """
        with self._lock:
            return self._current_lease is not None and self._current_lease.is_valid()

    def get_lease(self) -> QuorumLease | None:
        """현재 리스 반환."""
        with self._lock:
            return self._current_lease

    def _renew_loop(self) -> None:
        """자동 갱신 루프."""
        while self._renew_running:
            time.sleep(self.RENEW_INTERVAL_SECONDS)
            if self._renew_running and self.is_primary():
                self.renew_lease()

    def start_auto_renew(self) -> None:
        """자동 갱신 시작."""
        if self._renew_running:
            return

        self._renew_running = True
        self._renew_thread = threading.Thread(
            target=self._renew_loop,
            name="QuorumWitnessRenew",
            daemon=True,
        )
        self._renew_thread.start()
        logger.info("quorum.auto_renew_started")

    def stop_auto_renew(self) -> None:
        """자동 갱신 중지."""
        self._renew_running = False
        if self._renew_thread:
            self._renew_thread.join(timeout=5.0)
        logger.info("quorum.auto_renew_stopped")


class InMemoryQuorumWitness:
    """
    인메모리 Quorum Witness (테스트용).

    DynamoDB 없이 로컬에서 테스트할 때 사용합니다.
    단일 프로세스 내에서만 동작합니다.
    """

    # 클래스 레벨 상태 (싱글톤 패턴)
    _global_lease: QuorumLease | None = None
    _global_lock = threading.RLock()

    def __init__(self, region: str, cluster_id: str):
        """
        초기화.

        Args:
            region: 현재 리전 이름
            cluster_id: 클러스터 ID
        """
        self._region = region
        self._cluster_id = cluster_id
        self._lease_ttl = QuorumWitness.LEASE_TTL_SECONDS

    def try_acquire_primary(self) -> bool:
        """Primary 락 획득 시도."""
        now = time.time()
        lease_id = f"{self._region}:{self._cluster_id}:{uuid.uuid4().hex[:8]}"
        expires_at = now + self._lease_ttl

        with InMemoryQuorumWitness._global_lock:
            # 기존 리스가 없거나 만료됨
            if InMemoryQuorumWitness._global_lease is None or not InMemoryQuorumWitness._global_lease.is_valid():
                InMemoryQuorumWitness._global_lease = QuorumLease(
                    region=self._region,
                    acquired_at=now,
                    expires_at=expires_at,
                    lease_id=lease_id,
                )
                logger.info(
                    "lease_acquired",
                    self=self._region,
                )
                return True
            else:
                logger.warning(
                    f"[Quorum/InMemory] Lease denied: {self._region} "
                    f"(held by {InMemoryQuorumWitness._global_lease.region})"
                )
                return False

    def renew_lease(self) -> bool:
        """리스 갱신."""
        with InMemoryQuorumWitness._global_lock:
            if InMemoryQuorumWitness._global_lease is None:
                return False

            if InMemoryQuorumWitness._global_lease.region != self._region:
                return False

            now = time.time()
            InMemoryQuorumWitness._global_lease.expires_at = now + self._lease_ttl
            return True

    def release_lease(self) -> None:
        """리스 해제."""
        with InMemoryQuorumWitness._global_lock:
            if InMemoryQuorumWitness._global_lease and InMemoryQuorumWitness._global_lease.region == self._region:
                InMemoryQuorumWitness._global_lease = None
                logger.info(
                    "lease_released",
                    self=self._region,
                )

    def get_current_primary(self) -> str | None:
        """현재 Primary 리전 조회."""
        with InMemoryQuorumWitness._global_lock:
            if InMemoryQuorumWitness._global_lease and InMemoryQuorumWitness._global_lease.is_valid():
                return InMemoryQuorumWitness._global_lease.region
            return None

    def is_primary(self) -> bool:
        """현재 리전이 Primary인지 확인."""
        with InMemoryQuorumWitness._global_lock:
            return (
                InMemoryQuorumWitness._global_lease is not None
                and InMemoryQuorumWitness._global_lease.is_valid()
                and InMemoryQuorumWitness._global_lease.region == self._region
            )

    @classmethod
    def reset(cls) -> None:
        """전역 상태 리셋 (테스트용)."""
        with cls._global_lock:
            cls._global_lease = None
