"""
HealthProbeManager 기본 프로브 목록 테스트.

테스트 대상:
- _create_default_probes() AuditSystemProbe 포함 확인
- AuditSystemProbe 인스턴스 타입 확인
"""

import pytest


class TestHealthProbeManagerDefaultProbes:
    """기본 프로브 목록 테스트."""

    def test_default_probes_contains_audit_system_probe(self):
        """AuditSystemProbe가 기본 프로브 목록에 포함되어 있는지 확인."""
        from selfhealing.meta.health_probe import HealthProbeManager
        from selfhealing.meta.audit_probe import AuditSystemProbe

        manager = HealthProbeManager()

        # AuditSystemProbe 인스턴스 확인
        audit_probes = [p for p in manager._probes if isinstance(p, AuditSystemProbe)]

        assert len(audit_probes) == 1

    def test_default_probes_count_includes_audit(self):
        """기본 프로브 목록이 5개인지 확인 (CB, DLQ, Recovery, Redis, Audit)."""
        from selfhealing.meta.health_probe import HealthProbeManager

        manager = HealthProbeManager()

        # CircuitBreakerProbe, DLQProbe, RecoveryPipelineProbe, RedisProbe, AuditSystemProbe
        assert len(manager._probes) == 5


class TestHealthProbeManagerProbeTypes:
    """프로브 타입 확인 테스트."""

    def test_all_default_probes_have_component_name(self):
        """모든 기본 프로브가 component_name 속성을 가지는지 확인."""
        from selfhealing.meta.health_probe import HealthProbeManager

        manager = HealthProbeManager()

        for probe in manager._probes:
            assert hasattr(probe, "component_name")
            assert probe.component_name is not None

    def test_audit_system_probe_component_name(self):
        """AuditSystemProbe의 component_name 확인."""
        from selfhealing.meta.health_probe import HealthProbeManager
        from selfhealing.meta.audit_probe import AuditSystemProbe

        manager = HealthProbeManager()

        audit_probes = [p for p in manager._probes if isinstance(p, AuditSystemProbe)]

        assert len(audit_probes) == 1
        assert audit_probes[0].component_name == "audit_system"
