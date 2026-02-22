"""
Infrastructure Failure Experiments.

Includes:
- CertificateExpiryExperiment: Simulate certificate expiration
- ClockSkewExperiment: Inject clock synchronization issues
- DNSFailureExperiment: Simulate DNS resolution failures
- SimulatedDiskIOExperiment: Simulate disk I/O latency and errors
- SimulatedTLSFailureExperiment: Simulate TLS handshake failures
"""

from __future__ import annotations

import structlog

from selfhealing.services.chaos.base import (
    ChaosExperiment,
    ExperimentType,
    _apply_chaos_config,
)
from selfhealing.services.chaos.experiments.hypothesis import (
    CERTIFICATE_EXPIRY_HYPOTHESIS,
    CLOCK_SKEW_HYPOTHESIS,
    DNS_FAILURE_HYPOTHESIS,
    SIMULATED_DISK_IO_HYPOTHESIS,
    SIMULATED_TLS_FAILURE_HYPOTHESIS,
)

logger = structlog.get_logger()


class CertificateExpiryExperiment(ChaosExperiment):
    """
    Simulate certificate expiration scenarios.

    Simulates TLS 인증서 만료 상황, 어떻게 시스템이 처리하는지 테스트.

    Config parameters:
        - days_until_expiry: Days until simulated expiry (default: 0, expired)
        - check_mtls: Whether to test mTLS scenarios (default: False)
    """

    experiment_type = ExperimentType.CERTIFICATE_EXPIRY.value
    requires_approval = True  # Can break TLS connections

    # 복구 기대 가설 (클래스 레벨)
    failure_hypothesis = CERTIFICATE_EXPIRY_HYPOTHESIS

    @property
    def days_until_expiry(self) -> int:
        return self.config.parameters.get("days_until_expiry", 0)

    @property
    def check_mtls(self) -> bool:
        return self.config.parameters.get("check_mtls", False)

    def inject_chaos(self) -> bool:
        """Inject certificate expiry simulation."""
        logger.warning(
            "certificate_expiry.simulating_expiry_days_mtls",
            self=self.days_until_expiry,
            self_1=self.check_mtls,
        )

        try:
            _apply_chaos_config(
                {
                    "certificate_expiry": {
                        "enabled": True,
                        "target_service": self.config.target_service,
                        "days_until_expiry": self.days_until_expiry,
                        "check_mtls": self.check_mtls,
                        "experiment_id": self.experiment_id,
                        "expires_at": (
                            self._expires_at.isoformat() if self._expires_at else ""
                        ),
                        "ttl_seconds": self._effective_ttl,
                    }
                }
            )
            return True
        except Exception as e:
            logger.exception(
                "certificate_expiry.failed_inject",
                error=e,
            )
            return False

    def rollback(self) -> None:
        """Remove certificate expiry simulation."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info("certificate_expiry.rollback_already_completed")
                return

            logger.info(
                "certificate_expiry.rolling_back",
                self=self.experiment_id,
            )

            try:
                _apply_chaos_config(
                    {
                        "certificate_expiry": {
                            "enabled": False,
                            "target_service": self.config.target_service,
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
            except Exception as e:
                logger.exception(
                    "certificate_expiry.rollback_failed",
                    error=e,
                )


class ClockSkewExperiment(ChaosExperiment):
    """
    Inject clock synchronization issues.

    시스템 시계가 일정 시간 어긋났을 때 어떤 문제가 발생하는지 테스트.
    특히 JWT 토큰, 캐시 TTL, 분산 시스템 동기화에 영향.

    Config parameters:
        - skew_seconds: Clock skew in seconds (positive or negative, default: 60)
        - affect_jwt: Whether to affect JWT validation (default: True)
        - affect_cache: Whether to affect cache TTL (default: True)
    """

    experiment_type = ExperimentType.CLOCK_SKEW.value
    requires_approval = False  # Low risk, but noticeable impact

    # 복구 기대 가설 (클래스 레벨)
    failure_hypothesis = CLOCK_SKEW_HYPOTHESIS

    @property
    def skew_seconds(self) -> int:
        return self.config.parameters.get("skew_seconds", 60)

    @property
    def affect_jwt(self) -> bool:
        return self.config.parameters.get("affect_jwt", True)

    @property
    def affect_cache(self) -> bool:
        return self.config.parameters.get("affect_cache", True)

    def inject_chaos(self) -> bool:
        """Inject clock skew."""
        logger.info(
            "clock_skew.injecting_skew_jwt_cache",
            self=self.skew_seconds,
            self_1=self.affect_jwt,
            self_2=self.affect_cache,
        )

        try:
            _apply_chaos_config(
                {
                    "clock_skew": {
                        "enabled": True,
                        "target_service": self.config.target_service,
                        "skew_seconds": self.skew_seconds,
                        "affect_jwt": self.affect_jwt,
                        "affect_cache": self.affect_cache,
                        "experiment_id": self.experiment_id,
                        "expires_at": (
                            self._expires_at.isoformat() if self._expires_at else ""
                        ),
                        "ttl_seconds": self._effective_ttl,
                    }
                }
            )
            return True
        except Exception as e:
            logger.exception(
                "clock_skew.failed_inject",
                error=e,
            )
            return False

    def rollback(self) -> None:
        """Remove clock skew injection."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info("clock_skew.rollback_already_completed")
                return

            logger.info(
                "clock_skew.rolling_back",
                self=self.experiment_id,
            )

            try:
                _apply_chaos_config(
                    {
                        "clock_skew": {
                            "enabled": False,
                            "target_service": self.config.target_service,
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
            except Exception as e:
                logger.exception(
                    "clock_skew.rollback_failed",
                    error=e,
                )


class DNSFailureExperiment(ChaosExperiment):
    """
    Simulate DNS resolution failures.

    DNS 조회 실패 시나리오를 시뮬레이션하여 서비스 디스커버리 fallback 테스트.

    Config parameters:
        - failure_rate: Percentage of DNS queries to fail (default: 100%)
        - affected_domains: List of domains to fail (optional, all if empty)
        - delay_seconds: Additional delay before failure (default: 0)
    """

    experiment_type = ExperimentType.DNS_FAILURE.value
    requires_approval = True  # Can break service discovery

    # 복구 기대 가설 (클래스 레벨)
    failure_hypothesis = DNS_FAILURE_HYPOTHESIS

    @property
    def failure_rate(self) -> float:
        return self.config.parameters.get("failure_rate", 1.0)

    @property
    def affected_domains(self) -> list:
        return self.config.parameters.get("affected_domains", [])

    @property
    def delay_seconds(self) -> float:
        return self.config.parameters.get("delay_seconds", 0.0)

    def inject_chaos(self) -> bool:
        """Inject DNS failure simulation."""
        logger.warning(
            "dns_failure.injecting_dns_failure",
            self=self.failure_rate*100,
            self_1=self.config.target_service,
        )

        try:
            _apply_chaos_config(
                {
                    "dns_failure": {
                        "enabled": True,
                        "target_service": self.config.target_service,
                        "failure_rate": self.failure_rate,
                        "affected_domains": self.affected_domains,
                        "delay_seconds": self.delay_seconds,
                        "experiment_id": self.experiment_id,
                        "expires_at": (
                            self._expires_at.isoformat() if self._expires_at else ""
                        ),
                        "ttl_seconds": self._effective_ttl,
                    }
                }
            )
            return True
        except Exception as e:
            logger.exception(
                "dns_failure.failed_inject",
                error=e,
            )
            return False

    def rollback(self) -> None:
        """Remove DNS failure injection."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info("dns_failure.rollback_already_completed")
                return

            logger.info(
                "dns_failure.rolling_back",
                self=self.experiment_id,
            )

            try:
                _apply_chaos_config(
                    {
                        "dns_failure": {
                            "enabled": False,
                            "target_service": self.config.target_service,
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
            except Exception as e:
                logger.exception(
                    "dns_failure.rollback_failed",
                    error=e,
                )


class SimulatedDiskIOExperiment(ChaosExperiment):
    """
    Simulate disk I/O latency and errors.

    디스크 I/O 지연 및 오류를 시뮬레이션하여 시스템 반응 테스트.

    Config parameters:
        - latency_ms: Latency to add to disk operations (default: 100ms)
        - error_rate: Percentage of operations to fail (default: 0%)
        - affected_paths: List of paths to affect (optional, all if empty)
    """

    experiment_type = ExperimentType.SIMULATED_DISK_IO.value
    requires_approval = False

    # 복구 기대 가설 (클래스 레벨)
    failure_hypothesis = SIMULATED_DISK_IO_HYPOTHESIS

    @property
    def latency_ms(self) -> int:
        return self.config.parameters.get("latency_ms", 100)

    @property
    def error_rate(self) -> float:
        return self.config.parameters.get("error_rate", 0.0)

    @property
    def affected_paths(self) -> list:
        return self.config.parameters.get("affected_paths", [])

    def inject_chaos(self) -> bool:
        """Inject disk I/O latency."""
        logger.info(
            "simulated_disk_io.injecting_ms_latency_errors",
            self=self.latency_ms,
            self_1=self.error_rate*100,
            self_2=self.config.target_service,
        )

        try:
            _apply_chaos_config(
                {
                    "simulated_disk_io": {
                        "enabled": True,
                        "target_service": self.config.target_service,
                        "latency_ms": self.latency_ms,
                        "error_rate": self.error_rate,
                        "affected_paths": self.affected_paths,
                        "experiment_id": self.experiment_id,
                        "expires_at": (
                            self._expires_at.isoformat() if self._expires_at else ""
                        ),
                        "ttl_seconds": self._effective_ttl,
                    }
                }
            )
            return True
        except Exception as e:
            logger.exception(
                "simulated_disk_io.failed_inject",
                error=e,
            )
            return False

    def rollback(self) -> None:
        """Remove disk I/O injection."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info("simulated_disk_io.rollback_already_completed")
                return

            logger.info(
                "simulated_disk_io.rolling_back",
                self=self.experiment_id,
            )

            try:
                _apply_chaos_config(
                    {
                        "simulated_disk_io": {
                            "enabled": False,
                            "target_service": self.config.target_service,
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
            except Exception as e:
                logger.exception(
                    "simulated_disk_io.rollback_failed",
                    error=e,
                )


class SimulatedTLSFailureExperiment(ChaosExperiment):
    """
    Simulate TLS handshake failures.

    TLS 핸드셰이크 실패 시뮬레이션. SSL/TLS 연결 문제 테스트.

    Config parameters:
        - failure_rate: Percentage of handshakes to fail (default: 100%)
        - failure_type: Type of failure (handshake_timeout, cert_error, version_mismatch)
        - delay_ms: Delay before failure (default: 0)
    """

    experiment_type = ExperimentType.SIMULATED_TLS_FAILURE.value
    requires_approval = True  # Can break encrypted connections

    # 복구 기대 가설 (클래스 레벨)
    failure_hypothesis = SIMULATED_TLS_FAILURE_HYPOTHESIS

    @property
    def failure_rate(self) -> float:
        return self.config.parameters.get("failure_rate", 1.0)

    @property
    def failure_type(self) -> str:
        return self.config.parameters.get("failure_type", "handshake_timeout")

    @property
    def delay_ms(self) -> int:
        return self.config.parameters.get("delay_ms", 0)

    def inject_chaos(self) -> bool:
        """Inject TLS failure simulation."""
        logger.warning(
            "simulated_tls.injecting_failures_rate",
            self=self.failure_type,
            self_1=self.failure_rate*100,
            self_2=self.config.target_service,
        )

        try:
            _apply_chaos_config(
                {
                    "simulated_tls_failure": {
                        "enabled": True,
                        "target_service": self.config.target_service,
                        "failure_rate": self.failure_rate,
                        "failure_type": self.failure_type,
                        "delay_ms": self.delay_ms,
                        "experiment_id": self.experiment_id,
                        "expires_at": (
                            self._expires_at.isoformat() if self._expires_at else ""
                        ),
                        "ttl_seconds": self._effective_ttl,
                    }
                }
            )
            return True
        except Exception as e:
            logger.exception(
                "simulated_tls.failed_inject",
                error=e,
            )
            return False

    def rollback(self) -> None:
        """Remove TLS failure injection."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info("simulated_tls.rollback_already_completed")
                return

            logger.info(
                "simulated_tls.rolling_back",
                self=self.experiment_id,
            )

            try:
                _apply_chaos_config(
                    {
                        "simulated_tls_failure": {
                            "enabled": False,
                            "target_service": self.config.target_service,
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
            except Exception as e:
                logger.exception(
                    "simulated_tls.rollback_failed",
                    error=e,
                )


__all__ = [
    "CertificateExpiryExperiment",
    "ClockSkewExperiment",
    "DNSFailureExperiment",
    "SimulatedDiskIOExperiment",
    "SimulatedTLSFailureExperiment",
]
