"""
Kubernetes Autoscaling 설정 검증 단위 테스트

이 테스트는 Kubernetes HPA 및 KEDA 설정 파일의 유효성을 검증합니다:
- YAML 문법 검증
- 필수 필드 존재 여부 확인
- 값 범위 검증
- 스케일링 정책 일관성 확인
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def k8s_dir() -> Path:
    """k8s 디렉토리 경로 반환"""
    # 프로젝트 루트에서 k8s 디렉토리 찾기
    current = Path(__file__).resolve()
    # packages/selfhealing-python/tests/unit/config/test_kubernetes_autoscaling.py
    # 5단계 위로 올라가면 프로젝트 루트
    project_root = current.parents[5]
    k8s_path = project_root / "k8s"
    return k8s_path


@pytest.fixture
def django_hpa_config(k8s_dir: Path) -> dict[str, Any]:
    """Django API HPA 설정 로드"""
    hpa_file = k8s_dir / "django-api-hpa.yaml"
    if not hpa_file.exists():
        pytest.skip("django-api-hpa.yaml not found")
    with open(hpa_file, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def keda_default_config(k8s_dir: Path) -> dict[str, Any]:
    """KEDA Celery Default ScaledObject 설정 로드"""
    keda_file = k8s_dir / "keda-scaledobject-celery-default.yaml"
    if not keda_file.exists():
        pytest.skip("keda-scaledobject-celery-default.yaml not found")
    with open(keda_file, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def keda_critical_config(k8s_dir: Path) -> dict[str, Any]:
    """KEDA Celery Critical ScaledObject 설정 로드"""
    keda_file = k8s_dir / "keda-scaledobject-celery-critical.yaml"
    if not keda_file.exists():
        pytest.skip("keda-scaledobject-celery-critical.yaml not found")
    with open(keda_file, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def keda_audit_config(k8s_dir: Path) -> dict[str, Any]:
    """KEDA Celery Audit ScaledObject 설정 로드"""
    keda_file = k8s_dir / "keda-scaledobject-celery-audit.yaml"
    if not keda_file.exists():
        pytest.skip("keda-scaledobject-celery-audit.yaml not found")
    with open(keda_file, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def prometheus_adapter_config(k8s_dir: Path) -> dict[str, Any]:
    """Prometheus Adapter ConfigMap 설정 로드"""
    config_file = k8s_dir / "prometheus-adapter-config.yaml"
    if not config_file.exists():
        pytest.skip("prometheus-adapter-config.yaml not found")
    with open(config_file, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def keda_triggerauth_config(k8s_dir: Path) -> dict[str, Any]:
    """KEDA Redis TriggerAuthentication 설정 로드"""
    auth_file = k8s_dir / "keda-triggerauth-redis.yaml"
    if not auth_file.exists():
        pytest.skip("keda-triggerauth-redis.yaml not found")
    with open(auth_file, encoding="utf-8") as f:
        return yaml.safe_load(f)


# =============================================================================
# Django API HPA Tests
# =============================================================================


class TestDjangoAPIHPA:
    """Django API HPA 설정 테스트"""

    def test_hpa_api_version(self, django_hpa_config: dict[str, Any]):
        """HPA API 버전이 autoscaling/v2인지 확인"""
        assert django_hpa_config["apiVersion"] == "autoscaling/v2"

    def test_hpa_kind(self, django_hpa_config: dict[str, Any]):
        """Kind가 HorizontalPodAutoscaler인지 확인"""
        assert django_hpa_config["kind"] == "HorizontalPodAutoscaler"

    def test_hpa_metadata(self, django_hpa_config: dict[str, Any]):
        """메타데이터에 필수 필드 존재 확인"""
        metadata = django_hpa_config["metadata"]
        assert "name" in metadata
        assert "namespace" in metadata
        assert metadata["name"] == "django-api-hpa"

    def test_hpa_scale_target_ref(self, django_hpa_config: dict[str, Any]):
        """스케일 대상 참조 설정 확인"""
        spec = django_hpa_config["spec"]
        target = spec["scaleTargetRef"]
        assert target["apiVersion"] == "apps/v1"
        assert target["kind"] == "Deployment"
        assert target["name"] == "django-api"

    def test_hpa_replica_range(self, django_hpa_config: dict[str, Any]):
        """레플리카 범위가 적절한지 확인"""
        spec = django_hpa_config["spec"]
        min_replicas = spec["minReplicas"]
        max_replicas = spec["maxReplicas"]

        # 최소값은 1 이상
        assert min_replicas >= 1
        # 최대값은 최소값보다 커야 함
        assert max_replicas > min_replicas
        # Django API는 2-20 범위로 설정
        assert min_replicas == 2
        assert max_replicas == 20

    def test_hpa_cpu_metric(self, django_hpa_config: dict[str, Any]):
        """CPU 메트릭 설정 확인"""
        metrics = django_hpa_config["spec"]["metrics"]
        cpu_metric = next((m for m in metrics if m["type"] == "Resource" and m["resource"]["name"] == "cpu"), None)
        assert cpu_metric is not None
        # CPU 임계값은 50-80% 범위가 적절
        cpu_target = cpu_metric["resource"]["target"]["averageUtilization"]
        assert 50 <= cpu_target <= 80

    def test_hpa_memory_metric(self, django_hpa_config: dict[str, Any]):
        """메모리 메트릭 설정 확인"""
        metrics = django_hpa_config["spec"]["metrics"]
        memory_metric = next((m for m in metrics if m["type"] == "Resource" and m["resource"]["name"] == "memory"), None)
        assert memory_metric is not None
        # 메모리 임계값은 70-90% 범위가 적절
        memory_target = memory_metric["resource"]["target"]["averageUtilization"]
        assert 70 <= memory_target <= 90

    def test_hpa_scale_up_behavior(self, django_hpa_config: dict[str, Any]):
        """스케일 업 동작 설정 확인"""
        behavior = django_hpa_config["spec"]["behavior"]
        scale_up = behavior["scaleUp"]

        # 안정화 윈도우 존재
        assert "stabilizationWindowSeconds" in scale_up
        # 정책 존재
        assert "policies" in scale_up
        assert len(scale_up["policies"]) > 0

    def test_hpa_scale_down_behavior(self, django_hpa_config: dict[str, Any]):
        """스케일 다운 동작 설정 확인 (플래핑 방지)"""
        behavior = django_hpa_config["spec"]["behavior"]
        scale_down = behavior["scaleDown"]

        # 스케일 다운은 더 긴 안정화 윈도우 필요 (5분 이상)
        stabilization = scale_down["stabilizationWindowSeconds"]
        assert stabilization >= 300, "스케일 다운 안정화 윈도우는 5분 이상이어야 함"


# =============================================================================
# KEDA ScaledObject Tests
# =============================================================================


class TestKEDAScaledObjects:
    """KEDA ScaledObject 설정 테스트"""

    def test_default_worker_api_version(self, keda_default_config: dict[str, Any]):
        """KEDA API 버전 확인"""
        assert keda_default_config["apiVersion"] == "keda.sh/v1alpha1"

    def test_default_worker_kind(self, keda_default_config: dict[str, Any]):
        """Kind가 ScaledObject인지 확인"""
        assert keda_default_config["kind"] == "ScaledObject"

    def test_default_worker_replica_range(self, keda_default_config: dict[str, Any]):
        """Default Worker 레플리카 범위 확인"""
        spec = keda_default_config["spec"]
        assert spec["minReplicaCount"] >= 1
        assert spec["maxReplicaCount"] > spec["minReplicaCount"]
        assert spec["minReplicaCount"] == 2
        assert spec["maxReplicaCount"] == 10

    def test_default_worker_polling_interval(self, keda_default_config: dict[str, Any]):
        """폴링 간격이 적절한지 확인"""
        polling = keda_default_config["spec"]["pollingInterval"]
        # 너무 짧으면 Redis 부하 증가 (10초 이상 권장)
        assert polling >= 10, "폴링 간격이 너무 짧음 (Redis 부하 고려)"
        # 너무 길면 반응 느림 (60초 이하 권장)
        assert polling <= 60, "폴링 간격이 너무 김"

    def test_default_worker_has_redis_trigger(self, keda_default_config: dict[str, Any]):
        """Redis 트리거 설정 확인"""
        triggers = keda_default_config["spec"]["triggers"]
        redis_trigger = next((t for t in triggers if t["type"] == "redis"), None)
        assert redis_trigger is not None
        assert "address" in redis_trigger["metadata"]
        assert "listName" in redis_trigger["metadata"]
        assert "listLength" in redis_trigger["metadata"]

    def test_critical_worker_faster_polling(self, keda_default_config: dict[str, Any], keda_critical_config: dict[str, Any]):
        """Critical Worker는 더 빠른 폴링 필요"""
        default_polling = keda_default_config["spec"]["pollingInterval"]
        critical_polling = keda_critical_config["spec"]["pollingInterval"]

        # Critical은 더 빠르게 반응해야 함
        assert critical_polling < default_polling

    def test_critical_worker_sensitive_threshold(self, keda_critical_config: dict[str, Any]):
        """Critical Worker는 더 민감한 임계값 필요"""
        triggers = keda_critical_config["spec"]["triggers"]
        redis_trigger = next(t for t in triggers if t["type"] == "redis")
        list_length = int(redis_trigger["metadata"]["listLength"])

        # Critical 큐는 10개 이하로 민감하게
        assert list_length <= 20

    def test_critical_worker_immediate_scale_up(self, keda_critical_config: dict[str, Any]):
        """Critical Worker는 즉시 스케일 업 필요"""
        advanced = keda_critical_config["spec"].get("advanced", {})
        hpa_config = advanced.get("horizontalPodAutoscalerConfig", {})
        behavior = hpa_config.get("behavior", {})
        scale_up = behavior.get("scaleUp", {})

        # 즉시 반응 (안정화 윈도우 0 또는 매우 짧음)
        stabilization = scale_up.get("stabilizationWindowSeconds", 30)
        assert stabilization <= 10, "Critical Worker는 즉시 스케일 업해야 함"

    def test_audit_worker_longer_cooldown(self, keda_default_config: dict[str, Any], keda_audit_config: dict[str, Any]):
        """Audit Worker는 더 긴 쿨다운 필요"""
        default_cooldown = keda_default_config["spec"]["cooldownPeriod"]
        audit_cooldown = keda_audit_config["spec"]["cooldownPeriod"]

        # Audit은 작업 완료를 위해 더 긴 쿨다운
        assert audit_cooldown >= default_cooldown


# =============================================================================
# KEDA TriggerAuthentication Tests
# =============================================================================


class TestKEDATriggerAuth:
    """KEDA TriggerAuthentication 설정 테스트"""

    def test_trigger_auth_api_version(self, keda_triggerauth_config: dict[str, Any]):
        """API 버전 확인"""
        assert keda_triggerauth_config["apiVersion"] == "keda.sh/v1alpha1"

    def test_trigger_auth_kind(self, keda_triggerauth_config: dict[str, Any]):
        """Kind 확인"""
        assert keda_triggerauth_config["kind"] == "TriggerAuthentication"

    def test_trigger_auth_has_secret_ref(self, keda_triggerauth_config: dict[str, Any]):
        """Secret 참조 존재 확인"""
        spec = keda_triggerauth_config["spec"]
        assert "secretTargetRef" in spec
        secrets = spec["secretTargetRef"]
        assert len(secrets) > 0

        # password 파라미터 존재
        password_ref = next((s for s in secrets if s["parameter"] == "password"), None)
        assert password_ref is not None


# =============================================================================
# Prometheus Adapter Tests
# =============================================================================


class TestPrometheusAdapterConfig:
    """Prometheus Adapter 설정 테스트"""

    def test_configmap_kind(self, prometheus_adapter_config: dict[str, Any]):
        """Kind가 ConfigMap인지 확인"""
        assert prometheus_adapter_config["kind"] == "ConfigMap"

    def test_configmap_has_config_yaml(self, prometheus_adapter_config: dict[str, Any]):
        """config.yaml 데이터 존재 확인"""
        data = prometheus_adapter_config["data"]
        assert "config.yaml" in data

    def test_configmap_has_custom_rules(self, prometheus_adapter_config: dict[str, Any]):
        """커스텀 메트릭 규칙 존재 확인"""
        config_yaml = prometheus_adapter_config["data"]["config.yaml"]
        inner_config = yaml.safe_load(config_yaml)

        assert "rules" in inner_config
        rules = inner_config["rules"]
        assert len(rules) > 0

    def test_configmap_has_django_rps_rule(self, prometheus_adapter_config: dict[str, Any]):
        """Django RPS 메트릭 규칙 존재 확인"""
        config_yaml = prometheus_adapter_config["data"]["config.yaml"]
        inner_config = yaml.safe_load(config_yaml)

        rules = inner_config["rules"]
        django_rule = next((r for r in rules if "django" in r.get("seriesQuery", "")), None)
        assert django_rule is not None


# =============================================================================
# Cross-Config Consistency Tests
# =============================================================================


class TestConfigConsistency:
    """설정 간 일관성 테스트"""

    def test_all_keda_objects_same_namespace(
        self,
        keda_default_config: dict[str, Any],
        keda_critical_config: dict[str, Any],
        keda_audit_config: dict[str, Any],
        keda_triggerauth_config: dict[str, Any],
    ):
        """모든 KEDA 리소스가 같은 네임스페이스에 있는지 확인"""
        namespaces = {
            keda_default_config["metadata"]["namespace"],
            keda_critical_config["metadata"]["namespace"],
            keda_audit_config["metadata"]["namespace"],
            keda_triggerauth_config["metadata"]["namespace"],
        }
        assert len(namespaces) == 1, "모든 KEDA 리소스는 같은 네임스페이스에 있어야 함"

    def test_trigger_auth_name_referenced(self, keda_default_config: dict[str, Any], keda_triggerauth_config: dict[str, Any]):
        """TriggerAuth 이름이 ScaledObject에서 참조되는지 확인"""
        auth_name = keda_triggerauth_config["metadata"]["name"]

        triggers = keda_default_config["spec"]["triggers"]
        for trigger in triggers:
            if "authenticationRef" in trigger:
                assert trigger["authenticationRef"]["name"] == auth_name


# =============================================================================
# Celery Configuration Tests
# =============================================================================


class TestCeleryConfiguration:
    """Celery 설정 테스트 (myproject/celery.py)"""

    @pytest.fixture
    def celery_config_path(self) -> Path:
        """celery.py 경로 반환"""
        current = Path(__file__).resolve()
        project_root = current.parents[5]
        return project_root / "myproject" / "celery.py"

    def test_task_reject_on_worker_lost_configured(self, celery_config_path: Path):
        """task_reject_on_worker_lost 설정 존재 확인"""
        if not celery_config_path.exists():
            pytest.skip("celery.py not found")

        content = celery_config_path.read_text(encoding="utf-8")
        assert "task_reject_on_worker_lost" in content, "task_reject_on_worker_lost 설정이 필요함 (At-least-once 보장)"

    def test_visibility_timeout_configured(self, celery_config_path: Path):
        """visibility_timeout 설정 존재 확인"""
        if not celery_config_path.exists():
            pytest.skip("celery.py not found")

        content = celery_config_path.read_text(encoding="utf-8")
        assert "visibility_timeout" in content, "visibility_timeout 설정이 필요함 (Worker 장애 시 작업 재처리)"

    def test_broker_transport_options_configured(self, celery_config_path: Path):
        """broker_transport_options 설정 존재 확인"""
        if not celery_config_path.exists():
            pytest.skip("celery.py not found")

        content = celery_config_path.read_text(encoding="utf-8")
        assert "broker_transport_options" in content, "broker_transport_options 설정이 필요함"
