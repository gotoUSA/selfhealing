"""
Deployment Adapters Package.

외부 배포 시스템(Kubernetes, ArgoCD, Helm 등)과 연동하여
배포 이력을 수집하는 어댑터를 제공합니다.

Components:
- DeploymentEvent: 배포 이벤트 데이터 모델
- DeploymentConfigChange: 설정 변경 이벤트 데이터 모델
- ExternalDeploymentAdapter: 외부 배포 시스템 어댑터 인터페이스
- MockDeploymentAdapter: 테스트용 Mock 어댑터
- KubernetesDeploymentAdapter: Kubernetes API 연동 어댑터
"""

from __future__ import annotations

from .base import (
    DeploymentConfigChange,
    DeploymentEvent,
    DeploymentType,
    DeploymentSource,
    ExternalDeploymentAdapter,
)
from .mock import MockDeploymentAdapter
from .kubernetes import KubernetesDeploymentAdapter

__all__ = [
    # Models
    "DeploymentEvent",
    "DeploymentConfigChange",
    "DeploymentType",
    "DeploymentSource",
    # Interfaces
    "ExternalDeploymentAdapter",
    # Adapters
    "MockDeploymentAdapter",
    "KubernetesDeploymentAdapter",
]

# 하위 호환 alias (deprecated)
ConfigChangeEvent = DeploymentConfigChange
