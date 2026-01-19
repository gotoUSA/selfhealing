# Multi-Cluster Architecture

> 다중 클러스터/리전 지원을 위한 아키텍처 설계 및 구현 계획

---

## 📋 목차

1. [개요](#1-개요)
2. [다중 클러스터 기준 옵션](#2-다중-클러스터-기준-옵션)
3. [아키텍처 설계](#3-아키텍처-설계)
4. [구현 계획](#4-구현-계획)
5. [마이그레이션 가이드](#5-마이그레이션-가이드)
6. [배포 시나리오](#6-배포-시나리오)
7. [테스트 계획](#7-테스트-계획)
8. [핵심 컴포넌트 설계](#8-핵심-컴포넌트-설계) 🆕
9. [4대 허점 보완](#9-4대-허점-보완) 🆕
10. [Cross-Cluster 기능](#10-cross-cluster-기능) 🆕
11. [관련 문서](#11-관련-문서)
12. [체크리스트](#12-체크리스트)

---

## 1. 개요

### 1.1 목적

Self-Healing 시스템을 다중 클러스터/리전 환경에서 운영할 수 있도록 확장합니다.

### 1.2 현재 상태 분석

#### 현재 Redis 키 구조 (코드 기반)

| 파일 | 현재 KEY_PREFIX | 용도 |
|------|----------------|------|
| `adapters/redis/circuit_breaker.py#L40` | `cb:` | Circuit Breaker 상태 |
| `adapters/redis/dlq.py#L47` | `dlq:` | DLQ 엔트리 |
| `adapters/resilient/backend.py#L43` | `selfhealing:` | 범용 스토리지 |
| `core/state_backend.py#L170` | `selfhealing:state:` | 시스템 상태 |
| `services/config_history.py#L45-47` | `selfhealing:config:*` | 설정 버전 관리 |
| `audit/integrity/redis_manager.py#L57` | `selfhealing:` | Hash Chain |

#### 현재 구조의 특징

```python
# adapters/redis/circuit_breaker.py#L40-41
class RedisCircuitBreakerRepository:
    KEY_PREFIX = "cb:"  # ← 하드코딩된 상수
```

```python
# core/state_backend.py#L169-171
class RedisStateBackend:
    def __init__(
        self,
        key_prefix: str = "selfhealing:state:",  # ← 파라미터로 받음
    ):
        self._key_prefix = key_prefix
```

**결론**: 일부는 하드코딩, 일부는 파라미터화 → **통일 필요**

---

## 2. 다중 클러스터 기준 옵션

### 2.1 지원 가능한 기준

| 기준 | 사용 사례 | 키 패턴 예시 |
|------|----------|-------------|
| 리전 (Region) | 글로벌 서비스, DR | `selfhealing:seoul:cb:*` |
| 환경 (Environment) | dev/staging/prod 분리 | `selfhealing:prod:cb:*` |
| 테넌트 (Tenant) | SaaS 멀티테넌시 | `selfhealing:tenant123:cb:*` |
| 네임스페이스 (Namespace) | K8s 환경 | `selfhealing:ns-payments:cb:*` |

### 2.2 구현 방식 비교

**모든 기준은 동일한 방식으로 구현됩니다:**

```python
# 변경 전
KEY_PREFIX = "cb:"

# 변경 후 (모든 기준 동일)
KEY_PREFIX = f"{namespace}:cb:"  # namespace = region, tenant, env 등
```

**결론: 기준마다 구현이 다르지 않습니다. 환경변수 하나만 바꾸면 됩니다.**

---

## 3. 아키텍처 설계

### 3.1 Namespace Provider

```
┌──────────────────────────────────────────────────────────────────┐
│                      NamespaceProvider                           │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  SELFHEALING_NAMESPACE=seoul                                │ │
│  │  또는                                                       │ │
│  │  SELFHEALING_REGION=seoul                                   │ │
│  │  SELFHEALING_TENANT=customer123                             │ │
│  │  SELFHEALING_ENV=production                                 │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                              ↓                                   │
│                   namespace = "seoul"                            │
│                              ↓                                   │
│              key_prefix = "selfhealing:seoul:"                   │
└──────────────────────────────────────────────────────────────────┘
                               ↓
    ┌──────────────────────────┼──────────────────────────┐
    ↓                          ↓                          ↓
┌─────────┐            ┌─────────────┐            ┌──────────┐
│ CB Repo │            │  DLQ Repo   │            │ StateBackend │
│ seoul:cb:│            │ seoul:dlq:  │            │ seoul:state: │
└─────────┘            └─────────────┘            └──────────┘
```

### 3.2 변경 영향 범위

#### 변경 필요 (연결 부분)

| 파일 | 변경 내용 |
|------|----------|
| `settings/namespace.py` (신규) | NamespaceSettings 정의 |
| `core/namespace.py` (신규) | NamespaceProvider 구현 |
| `adapters/redis/circuit_breaker.py` | KEY_PREFIX 동적화 |
| `adapters/redis/dlq.py` | KEY_PREFIX 동적화 |
| `adapters/resilient/backend.py` | key_prefix 주입 |
| `services/config_history.py` | CONFIG_*_KEY 동적화 |

#### 변경 불필요 (비즈니스 로직)

- Circuit Breaker 로직 (`services/circuit_breaker/`)
- DLQ 처리 로직 (`services/dlq/`)
- Forensic 로직 (`services/forensic/`)
- Chaos Engineering (`services/chaos/`)
- 모든 비즈니스 로직

---

## 4. 구현 계획

### 4.1 Phase 1: Namespace Settings

**파일**: `packages/selfhealing-python/src/selfhealing/settings/namespace.py`

```python
"""
Namespace Settings - Multi-Cluster Support.

환경변수로 클러스터/리전/테넌트 등의 네임스페이스를 설정합니다.

Usage:
    # 방법 1: 통합 네임스페이스
    SELFHEALING_NAMESPACE=seoul
    
    # 방법 2: 개별 설정 (우선순위: NAMESPACE > REGION > TENANT > ENV)
    SELFHEALING_REGION=seoul
    SELFHEALING_TENANT=customer123
    SELFHEALING_ENV=production
"""
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class NamespaceSettings(BaseSettings):
    """
    네임스페이스 설정.
    
    다중 클러스터/리전/테넌트 환경에서 Redis 키를 분리합니다.
    """
    
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # 통합 네임스페이스 (최우선)
    namespace: Optional[str] = Field(
        default=None,
        description="Unified namespace (highest priority)",
    )
    
    # 개별 설정 (우선순위 순)
    region: Optional[str] = Field(
        default=None,
        description="Region identifier (e.g., seoul, tokyo)",
    )
    tenant: Optional[str] = Field(
        default=None,
        description="Tenant identifier for SaaS multi-tenancy",
    )
    env: Optional[str] = Field(
        default=None,
        description="Environment (dev, staging, production)",
    )
    
    # 기본값 (아무것도 설정 안 된 경우)
    default_namespace: str = Field(
        default="default",
        description="Fallback namespace when nothing is set",
    )
    
    # 네임스페이스 활성화 여부
    namespace_enabled: bool = Field(
        default=False,
        description="Enable namespace-based key prefixing",
    )
    
    def get_effective_namespace(self) -> str:
        """
        유효 네임스페이스 반환.
        
        우선순위: namespace > region > tenant > env > default
        
        Returns:
            유효한 네임스페이스 문자열
        """
        if not self.namespace_enabled:
            return ""  # 비활성화 시 빈 문자열 (기존 동작 유지)
        
        return (
            self.namespace or
            self.region or
            self.tenant or
            self.env or
            self.default_namespace
        )
    
    def get_key_prefix(self, base_prefix: str = "selfhealing") -> str:
        """
        Redis 키 프리픽스 생성.
        
        Args:
            base_prefix: 기본 프리픽스
            
        Returns:
            완전한 키 프리픽스 (예: "selfhealing:seoul:")
        """
        ns = self.get_effective_namespace()
        if ns:
            return f"{base_prefix}:{ns}:"
        return f"{base_prefix}:"


# Singleton
_settings: Optional[NamespaceSettings] = None


def get_namespace_settings() -> NamespaceSettings:
    """NamespaceSettings 싱글톤 반환."""
    global _settings
    if _settings is None:
        _settings = NamespaceSettings()
    return _settings


def get_key_prefix(base_prefix: str = "selfhealing") -> str:
    """
    현재 네임스페이스 기반 키 프리픽스 반환.
    
    편의 함수로, 어디서든 호출 가능.
    
    Args:
        base_prefix: 기본 프리픽스
        
    Returns:
        완전한 키 프리픽스
    """
    return get_namespace_settings().get_key_prefix(base_prefix)
```

### 4.2 Phase 2: Adapter 수정

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/redis/circuit_breaker.py`

```python
# 변경 전 (L40-41)
class RedisCircuitBreakerRepository:
    KEY_PREFIX = "cb:"

# 변경 후
class RedisCircuitBreakerRepository:
    _BASE_PREFIX = "cb"
    
    def __init__(self, backend: "ResilientStorageBackend"):
        self._backend = backend
        self._key_prefix = self._build_key_prefix()
    
    def _build_key_prefix(self) -> str:
        """네임스페이스 기반 키 프리픽스 생성."""
        from selfhealing.settings.namespace import get_key_prefix
        return f"{get_key_prefix()}{self._BASE_PREFIX}:"
    
    def _make_key(self, service_name: str) -> str:
        return f"{self._key_prefix}{service_name}"
```

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/redis/dlq.py`

```python
# 변경 전 (L47-50)
class RedisDLQRepository:
    KEY_PREFIX = "dlq:"
    PENDING_KEY = "dlq:pending"
    ID_SEQ_KEY = "dlq:id_seq"

# 변경 후
class RedisDLQRepository:
    _BASE_PREFIX = "dlq"
    
    def __init__(self, backend: "ResilientStorageBackend"):
        self._backend = backend
        self._key_prefix = self._build_key_prefix()
        self._pending_key = f"{self._key_prefix}pending"
        self._id_seq_key = f"{self._key_prefix}id_seq"
    
    def _build_key_prefix(self) -> str:
        from selfhealing.settings.namespace import get_key_prefix
        return f"{get_key_prefix()}{self._BASE_PREFIX}:"
```

**파일**: `packages/selfhealing-python/src/selfhealing/services/config_history.py`

```python
# 변경 전 (L45-47)
CONFIG_HISTORY_KEY = "selfhealing:config:history:{config_type}"
CONFIG_VERSION_COUNTER_KEY = "selfhealing:config:version:{config_type}"
CONFIG_CURRENT_KEY = "selfhealing:config:current:{config_type}"

# 변경 후
def _get_config_history_key(config_type: str) -> str:
    from selfhealing.settings.namespace import get_key_prefix
    return f"{get_key_prefix()}config:history:{config_type}"

def _get_config_version_key(config_type: str) -> str:
    from selfhealing.settings.namespace import get_key_prefix
    return f"{get_key_prefix()}config:version:{config_type}"

def _get_config_current_key(config_type: str) -> str:
    from selfhealing.settings.namespace import get_key_prefix
    return f"{get_key_prefix()}config:current:{config_type}"
```

### 4.3 Phase 3: 기존 파라미터 통합

이미 `key_prefix`를 파라미터로 받는 클래스들은 기본값만 변경:

```python
# 변경 전 (audit/integrity/redis_manager.py#L57)
def __init__(
    self,
    redis_client: Any,
    key_prefix: str = "selfhealing:",
):

# 변경 후
def __init__(
    self,
    redis_client: Any,
    key_prefix: Optional[str] = None,
):
    if key_prefix is None:
        from selfhealing.settings.namespace import get_key_prefix
        key_prefix = get_key_prefix()
    self._key_prefix = key_prefix
```

---

## 5. 마이그레이션 가이드

### 5.1 기존 시스템 (변경 없음)

```bash
# 환경변수 없음 → 기존과 동일하게 동작
# SELFHEALING_NAMESPACE_ENABLED=false (기본값)
```

Redis 키: `selfhealing:cb:payment-api`

### 5.2 다중 클러스터 활성화

```bash
# 방법 1: 통합 네임스페이스
SELFHEALING_NAMESPACE_ENABLED=true
SELFHEALING_NAMESPACE=seoul

# 방법 2: 리전 기반
SELFHEALING_NAMESPACE_ENABLED=true
SELFHEALING_REGION=seoul

# 방법 3: 테넌트 기반
SELFHEALING_NAMESPACE_ENABLED=true
SELFHEALING_TENANT=customer123
```

Redis 키: `selfhealing:seoul:cb:payment-api`

### 5.3 기존 데이터 마이그레이션

```python
# scripts/migrate_namespace.py
"""
기존 Redis 데이터를 새 네임스페이스로 마이그레이션.

Usage:
    python scripts/migrate_namespace.py --target-namespace=seoul
"""
import redis

def migrate_keys(source_prefix: str, target_prefix: str):
    r = redis.from_url("redis://localhost:6379/0")
    
    for key in r.scan_iter(f"{source_prefix}*"):
        new_key = key.replace(source_prefix, target_prefix, 1)
        r.rename(key, new_key)
        print(f"Migrated: {key} → {new_key}")
```

---

## 6. 배포 시나리오

### 6.1 단일 → 다중 클러스터 전환

```
Step 1: 새 클러스터에 배포
┌─────────────────────────────┐
│ 기존 클러스터 (seoul)        │  ← SELFHEALING_NAMESPACE=seoul
│ Redis: selfhealing:seoul:*  │
└─────────────────────────────┘

Step 2: 추가 클러스터 배포
┌─────────────────────────────┐  ┌─────────────────────────────┐
│ 서울 클러스터               │  │ 도쿄 클러스터               │
│ SELFHEALING_NAMESPACE=seoul │  │ SELFHEALING_NAMESPACE=tokyo │
│ Redis A                     │  │ Redis B                     │
└─────────────────────────────┘  └─────────────────────────────┘
```

### 6.2 동일 Redis에서 분리 운영

```
┌─────────────────────────────────────────────────────┐
│                    공유 Redis                        │
│  selfhealing:seoul:cb:*  │  selfhealing:tokyo:cb:*  │
│  selfhealing:seoul:dlq:* │  selfhealing:tokyo:dlq:* │
└─────────────────────────────────────────────────────┘
         ↑                           ↑
         │                           │
┌─────────────────┐         ┌─────────────────┐
│ 서울 클러스터    │         │ 도쿄 클러스터    │
│ NAMESPACE=seoul │         │ NAMESPACE=tokyo │
└─────────────────┘         └─────────────────┘
```

---

## 7. 테스트 계획

### 7.1 단위 테스트

```python
# tests/unit/settings/test_namespace.py

class TestNamespaceSettings:
    def test_namespace_disabled_returns_empty(self):
        """비활성화 시 빈 문자열 반환."""
        settings = NamespaceSettings(namespace_enabled=False)
        assert settings.get_effective_namespace() == ""
    
    def test_namespace_priority(self):
        """우선순위: namespace > region > tenant > env."""
        settings = NamespaceSettings(
            namespace_enabled=True,
            namespace="ns1",
            region="seoul",
        )
        assert settings.get_effective_namespace() == "ns1"
    
    def test_key_prefix_with_namespace(self):
        """네임스페이스 포함 키 프리픽스."""
        settings = NamespaceSettings(
            namespace_enabled=True,
            region="seoul",
        )
        assert settings.get_key_prefix() == "selfhealing:seoul:"
    
    def test_key_prefix_without_namespace(self):
        """네임스페이스 없을 때 기존 형식."""
        settings = NamespaceSettings(namespace_enabled=False)
        assert settings.get_key_prefix() == "selfhealing:"
```

### 7.2 통합 테스트

```python
# tests/integration/test_multi_cluster.py

class TestMultiClusterIsolation:
    def test_different_namespaces_are_isolated(self, redis_client):
        """다른 네임스페이스는 서로 격리됨."""
        # Seoul 클러스터
        with namespace_context("seoul"):
            cb_seoul = get_circuit_breaker("payment-api")
            cb_seoul.record_failure()
        
        # Tokyo 클러스터
        with namespace_context("tokyo"):
            cb_tokyo = get_circuit_breaker("payment-api")
            # Seoul의 실패가 Tokyo에 영향 없음
            assert cb_tokyo.failure_count == 0
```

---

## 8. 핵심 컴포넌트 설계 🆕

### 8.1 ClusterIdentity (SSOT 싱글톤)

현재 Pod의 클러스터 정보를 담는 **Single Source of Truth** 클래스입니다.

> **네이밍 결정**: `ClusterContext` 대신 `ClusterIdentity` 사용
> - `Context`는 Python의 `contextvars.ContextVar`와 혼동 우려
> - `Identity`가 "정체성/식별자" 의미를 더 명확히 전달

**파일**: `packages/selfhealing-python/src/selfhealing/core/cluster_identity.py`

```python
"""
Cluster Identity - Multi-Cluster SSOT.

각 Pod가 자신의 클러스터 정보를 인지하는 단일 진실 소스(SSOT).

코드 근거:
- redis_manager.py#L146-147: pod_id = os.environ.get("HOSTNAME", ...)
- 기존에 Pod ID만 인식, 클러스터/리전 정보 없음

Usage:
    from selfhealing.core.cluster_identity import get_cluster_identity
    
    identity = get_cluster_identity()
    print(identity.cluster_id)    # "seoul-prod-01"
    print(identity.region)        # "seoul"
    print(identity.full_prefix)   # "selfhealing:seoul:prod:"
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClusterIdentity:
    """
    클러스터 식별 정보 (Immutable).
    
    Attributes:
        cluster_id: 클러스터 고유 ID (필수)
        region: 리전 식별자 (예: seoul, tokyo)
        environment: 환경 (dev, staging, prod)
        tenant: SaaS 테넌트 ID (옵션)
        pod_id: 현재 Pod ID
    """
    
    cluster_id: str
    region: Optional[str] = None
    environment: str = "production"
    tenant: Optional[str] = None
    pod_id: str = field(default_factory=lambda: os.environ.get("HOSTNAME", "unknown"))
    
    @property
    def namespace(self) -> str:
        """Redis 키 네임스페이스 반환."""
        # 우선순위: region > tenant > environment
        return self.region or self.tenant or self.environment
    
    @property
    def full_prefix(self) -> str:
        """완전한 Redis 키 프리픽스 반환."""
        return f"selfhealing:{self.namespace}:"
    
    @property
    def trace_id_prefix(self) -> str:
        """Trace ID용 클러스터 접두사."""
        # 짧게: 리전 앞 3글자 + 환경 앞 1글자
        region_short = (self.region or "unk")[:3]
        env_short = self.environment[0] if self.environment else "u"
        return f"{region_short}{env_short}"
    
    def validate(self, fail_fast: bool = None) -> bool:
        """
        클러스터 ID 유효성 검증.
        
        Fail-Fast 강화:
        - SELFHEALING_CLUSTER_ID 누락 시 프로세스 즉시 중단 옵션
        - 잘못된 네임스페이스 건드리는 것을 원천 방지
        
        코드 근거:
        - tools/hold_row_lock.py#L160-162: Fail-Fast 패턴 존재
        - apps.py#L287-295: Quarantine Mode 패턴 존재
        
        Args:
            fail_fast: True면 sys.exit(1), False면 Quarantine Mode
                       None이면 환경변수 SELFHEALING_FAIL_FAST 참조 (기본: True)
        """
        # 환경변수에서 fail_fast 설정 읽기
        if fail_fast is None:
            fail_fast = os.environ.get("SELFHEALING_FAIL_FAST", "true").lower() == "true"
        
        if not self.cluster_id or self.cluster_id in ("unknown", "default"):
            error_msg = (
                "❌ [FATAL] SELFHEALING_CLUSTER_ID not set or invalid. "
                "Refusing to start to prevent namespace collision. "
                f"Current value: '{self.cluster_id}'"
            )
            
            if fail_fast:
                logger.critical(error_msg)
                import sys
                sys.exit(1)  # Fail-Fast: 즉시 종료
            else:
                logger.error(
                    f"{error_msg} "
                    "Running in Quarantine Mode (SELFHEALING_FAIL_FAST=false)"
                )
                return False
        
        logger.info(
            f"✅ [ClusterIdentity] Cluster: {self.cluster_id}, "
            f"Region: {self.region}, Env: {self.environment}, Pod: {self.pod_id}"
        )
        return True


# =============================================================================
# Factory & Singleton
# =============================================================================

_identity: Optional[ClusterIdentity] = None


def get_cluster_identity() -> ClusterIdentity:
    """ClusterIdentity 싱글톤 반환."""
    global _identity
    if _identity is None:
        _identity = ClusterIdentity(
            cluster_id=os.environ.get("SELFHEALING_CLUSTER_ID", "default"),
            region=os.environ.get("SELFHEALING_REGION"),
            environment=os.environ.get("SELFHEALING_ENV", "production"),
            tenant=os.environ.get("SELFHEALING_TENANT"),
        )
        _identity.validate()
    return _identity


def reset_cluster_identity() -> None:
    """테스트용 리셋."""
    global _identity
    _identity = None
```

### 8.2 GlobalConfigPropagator (설정 전파)

Redis Pub/Sub을 활용해 글로벌 설정 변경을 모든 클러스터에 강제 푸시합니다.

> **네이밍 결정**: `GlobalConfigSynchronizer` 대신 `GlobalConfigPropagator` 사용
> - `Synchronizer`는 양방향 동기화 의미
> - 실제로는 Global → Local **단방향 전파**이므로 `Propagator`가 정확

**코드 근거**:
```python
# event_bus_redis.py#L31 - 현재 Chaos 전용 채널만 존재
CHAOS_EVENT_CHANNEL = "selfhealing:chaos:events"

# 필요한 변경: 모든 이벤트 타입 + Config 전파 채널 추가
```

**파일**: `packages/selfhealing-python/src/selfhealing/services/config/propagator.py`

```python
"""
Global Config Propagator.

글로벌 네임스페이스 설정 변경을 모든 클러스터에 전파.

코드 근거:
- event_bus_redis.py: RedisEventBus 존재, Chaos 전용
- event_bus.py#L77-78: CONFIG_UPDATED 이벤트 타입 이미 정의

확장 방향:
- RedisEventBus를 Config 전파에도 활용
- 글로벌 채널과 로컬 채널 분리
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ConfigScope(Enum):
    """설정 적용 범위."""
    
    LOCAL = "local"       # 현재 클러스터만
    REGIONAL = "regional"  # 같은 리전 내 모든 클러스터
    GLOBAL = "global"     # 모든 클러스터


class PropagationTier(Enum):
    """전파 일관성 등급 (SLA 기반)."""
    
    TIER_1_IMMEDIATE = "tier_1"  # 1초 내 전파 보장 (Audit/Governance)
    TIER_2_EVENTUAL = "tier_2"   # 30초 내 전파 허용 (Metrics/Stats)


@dataclass
class GlobalConfigChange:
    """글로벌 설정 변경 이벤트."""
    
    config_type: str           # circuit_breaker, dlq, emergency 등
    config_key: str            # 설정 키
    new_value: Any             # 새 값
    previous_value: Any        # 이전 값
    scope: ConfigScope         # 적용 범위
    tier: PropagationTier      # 전파 등급
    source_cluster: str        # 변경 발생 클러스터
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "config_type": self.config_type,
            "config_key": self.config_key,
            "new_value": self.new_value,
            "previous_value": self.previous_value,
            "scope": self.scope.value,
            "tier": self.tier.value,
            "source_cluster": self.source_cluster,
            "timestamp": self.timestamp.isoformat(),
        }


class GlobalConfigPropagator:
    """
    글로벌 설정 전파기.
    
    RedisEventBus를 확장하여 Config 변경을 모든 클러스터에 전파.
    """
    
    # 채널 정의
    GLOBAL_CONFIG_CHANNEL = "selfhealing:global:config"
    REGIONAL_CONFIG_CHANNEL = "selfhealing:{region}:config"
    
    def __init__(
        self,
        redis_client: Any = None,
        cluster_identity: "ClusterIdentity" = None,
    ):
        self._redis = redis_client
        self._identity = cluster_identity
        self._handlers: Dict[str, List[Callable]] = {}
        self._running = False
    
    def propagate(self, change: GlobalConfigChange) -> bool:
        """
        설정 변경 전파.
        
        Args:
            change: 설정 변경 이벤트
            
        Returns:
            전파 성공 여부
        """
        if not self._redis:
            logger.warning("[GlobalConfigPropagator] Redis not available, skipping propagation")
            return False
        
        try:
            # 범위에 따른 채널 선택
            if change.scope == ConfigScope.GLOBAL:
                channel = self.GLOBAL_CONFIG_CHANNEL
            elif change.scope == ConfigScope.REGIONAL:
                channel = self.REGIONAL_CONFIG_CHANNEL.format(
                    region=self._identity.region if self._identity else "default"
                )
            else:
                return True  # LOCAL은 전파 불필요
            
            # 전파
            payload = json.dumps(change.to_dict(), default=str)
            subscribers = self._redis.publish(channel, payload)
            
            logger.info(
                f"[GlobalConfigPropagator] Propagated {change.config_type}.{change.config_key} "
                f"to {subscribers} subscribers via {channel}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[GlobalConfigPropagator] Propagation failed: {e}")
            return False
    
    def subscribe(self, config_type: str, handler: Callable[[GlobalConfigChange], None]) -> None:
        """설정 변경 구독."""
        if config_type not in self._handlers:
            self._handlers[config_type] = []
        self._handlers[config_type].append(handler)
    
    def _handle_message(self, data: str) -> None:
        """Redis 메시지 처리."""
        try:
            change_dict = json.loads(data)
            change = GlobalConfigChange(
                config_type=change_dict["config_type"],
                config_key=change_dict["config_key"],
                new_value=change_dict["new_value"],
                previous_value=change_dict["previous_value"],
                scope=ConfigScope(change_dict["scope"]),
                tier=PropagationTier(change_dict["tier"]),
                source_cluster=change_dict["source_cluster"],
                timestamp=datetime.fromisoformat(change_dict["timestamp"]),
            )
            
            # 핸들러 호출
            handlers = self._handlers.get(change.config_type, [])
            for handler in handlers:
                try:
                    handler(change)
                except Exception as e:
                    logger.error(f"[GlobalConfigPropagator] Handler error: {e}")
                    
        except Exception as e:
            logger.error(f"[GlobalConfigPropagator] Message parsing failed: {e}")
```

### 8.3 CrossClusterAuditLinker (글로벌 앵커)

각 클러스터의 로컬 해시 체인을 글로벌 앵커로 연결하여 '전사 무결성'을 증명합니다.

**코드 근거**:
```python
# audit/integrity/redis_manager.py#L48-50 - 현재 단일 체인만 지원
SEQUENCE_KEY = "audit:hash_chain:seq"
STATE_KEY = "audit:hash_chain:state"
LOCK_KEY = "audit:hash_chain:lock"

# 43_DISTRIBUTED_HASH_CHAIN_ENHANCED.md#L722 - DailyHashAnchor 문서만 존재
# 실제 코드 미구현 상태
```

**파일**: `packages/selfhealing-python/src/selfhealing/audit/integrity/cross_cluster_linker.py`

```python
"""
Cross-Cluster Audit Linker.

각 클러스터의 로컬 해시 체인을 글로벌 앵커로 연결.

설계 원칙:
- 체인은 클러스터별로 독립 (Local Chain) - 성능 보장
- 일일 앵커만 글로벌 저장소에 통합 (Global Anchoring) - 전사 무결성

코드 근거:
- redis_manager.py: RedisHashChainManager 존재 (단일 클러스터)
- 43번 문서: DailyHashAnchor 설계 존재 (미구현)
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ClusterDailyAnchor:
    """클러스터별 일일 앵커."""
    
    cluster_id: str
    date: date
    final_sequence: int
    final_hash: str
    entry_count: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "date": self.date.isoformat(),
            "final_sequence": self.final_sequence,
            "final_hash": self.final_hash,
            "entry_count": self.entry_count,
            "created_at": self.created_at.isoformat(),
        }
    
    def compute_anchor_hash(self) -> str:
        """앵커 해시 계산."""
        data = f"{self.cluster_id}:{self.date}:{self.final_sequence}:{self.final_hash}"
        return hashlib.sha256(data.encode()).hexdigest()


@dataclass
class GlobalDailyAnchor:
    """글로벌 일일 앵커 (모든 클러스터 통합)."""
    
    date: date
    cluster_anchors: List[ClusterDailyAnchor]
    global_hash: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def __post_init__(self):
        if not self.global_hash:
            self.global_hash = self._compute_global_hash()
    
    def _compute_global_hash(self) -> str:
        """모든 클러스터 앵커를 결합한 글로벌 해시."""
        # 클러스터 ID 순으로 정렬하여 결정론적 해시 보장
        sorted_anchors = sorted(self.cluster_anchors, key=lambda a: a.cluster_id)
        combined = ":".join(a.compute_anchor_hash() for a in sorted_anchors)
        return hashlib.sha256(combined.encode()).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "cluster_count": len(self.cluster_anchors),
            "cluster_anchors": [a.to_dict() for a in self.cluster_anchors],
            "global_hash": self.global_hash,
            "created_at": self.created_at.isoformat(),
        }


class CrossClusterAuditLinker:
    """
    클러스터 간 Audit 체인 연결기.
    
    하이브리드 전략:
    - Local: 각 클러스터가 독립적인 해시 체인 유지 (성능)
    - Global: 일일 앵커만 글로벌 저장소에 통합 (무결성 증명)
    """
    
    # Redis 키 패턴
    LOCAL_ANCHOR_KEY = "{prefix}audit:anchor:{date}"
    GLOBAL_ANCHOR_KEY = "selfhealing:global:anchor:{date}"
    GLOBAL_ANCHOR_LIST_KEY = "selfhealing:global:anchor:list"
    
    def __init__(
        self,
        local_redis: Any,
        global_redis: Any = None,  # 없으면 local 사용
        cluster_identity: "ClusterIdentity" = None,
        key_prefix: str = "selfhealing:",
    ):
        self._local_redis = local_redis
        self._global_redis = global_redis or local_redis
        self._identity = cluster_identity
        self._key_prefix = key_prefix
    
    def create_local_anchor(self, target_date: date = None) -> Optional[ClusterDailyAnchor]:
        """
        로컬 클러스터의 일일 앵커 생성.
        
        Args:
            target_date: 대상 날짜 (기본: 어제)
            
        Returns:
            생성된 앵커 또는 None
        """
        if target_date is None:
            from datetime import timedelta
            target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        
        try:
            # 로컬 체인에서 해당 날짜의 마지막 엔트리 조회
            state_key = f"{self._key_prefix}audit:hash_chain:state"
            state = self._local_redis.hgetall(state_key)
            
            if not state:
                logger.warning(f"[CrossClusterAuditLinker] No hash chain state found")
                return None
            
            # 앵커 생성
            cluster_id = self._identity.cluster_id if self._identity else "unknown"
            anchor = ClusterDailyAnchor(
                cluster_id=cluster_id,
                date=target_date,
                final_sequence=int(state.get(b"sequence", state.get("sequence", 0))),
                final_hash=state.get(b"previous_hash", state.get("previous_hash", "")).decode()
                    if isinstance(state.get(b"previous_hash", state.get("previous_hash", "")), bytes)
                    else state.get("previous_hash", ""),
                entry_count=int(state.get(b"sequence", state.get("sequence", 0))),
            )
            
            # 로컬 저장
            anchor_key = self.LOCAL_ANCHOR_KEY.format(
                prefix=self._key_prefix,
                date=target_date.isoformat()
            )
            self._local_redis.set(anchor_key, json.dumps(anchor.to_dict()))
            self._local_redis.expire(anchor_key, 90 * 86400)  # 90일 보관
            
            logger.info(f"[CrossClusterAuditLinker] Created local anchor for {target_date}: {anchor.compute_anchor_hash()[:16]}...")
            return anchor
            
        except Exception as e:
            logger.error(f"[CrossClusterAuditLinker] Failed to create local anchor: {e}")
            return None
    
    def submit_to_global(self, anchor: ClusterDailyAnchor) -> bool:
        """
        로컬 앵커를 글로벌 저장소에 제출.
        
        Args:
            anchor: 로컬 앵커
            
        Returns:
            제출 성공 여부
        """
        try:
            # 글로벌 앵커 키
            global_key = self.GLOBAL_ANCHOR_KEY.format(date=anchor.date.isoformat())
            
            # 기존 글로벌 앵커 조회
            existing = self._global_redis.get(global_key)
            if existing:
                global_data = json.loads(existing)
                cluster_anchors = [
                    ClusterDailyAnchor(**ca) for ca in global_data.get("cluster_anchors", [])
                ]
                # 중복 체크
                if any(ca.cluster_id == anchor.cluster_id for ca in cluster_anchors):
                    logger.info(f"[CrossClusterAuditLinker] Anchor already submitted for {anchor.cluster_id}")
                    return True
                cluster_anchors.append(anchor)
            else:
                cluster_anchors = [anchor]
            
            # 글로벌 앵커 생성/갱신
            global_anchor = GlobalDailyAnchor(
                date=anchor.date,
                cluster_anchors=cluster_anchors,
            )
            
            self._global_redis.set(global_key, json.dumps(global_anchor.to_dict()))
            self._global_redis.expire(global_key, 365 * 86400)  # 1년 보관
            
            # 앵커 목록에 추가
            self._global_redis.zadd(
                self.GLOBAL_ANCHOR_LIST_KEY,
                {anchor.date.isoformat(): anchor.date.toordinal()}
            )
            
            logger.info(
                f"[CrossClusterAuditLinker] Submitted to global: {anchor.cluster_id} / {anchor.date} "
                f"(Global hash: {global_anchor.global_hash[:16]}...)"
            )
            return True
            
        except Exception as e:
            logger.error(f"[CrossClusterAuditLinker] Failed to submit to global: {e}")
            return False
    
    def verify_global_integrity(self, target_date: date) -> Dict[str, Any]:
        """
        글로벌 앵커 무결성 검증.
        
        Args:
            target_date: 검증 대상 날짜
            
        Returns:
            검증 결과
        """
        try:
            global_key = self.GLOBAL_ANCHOR_KEY.format(date=target_date.isoformat())
            data = self._global_redis.get(global_key)
            
            if not data:
                return {"valid": False, "error": "Global anchor not found"}
            
            global_data = json.loads(data)
            
            # 글로벌 해시 재계산
            cluster_anchors = [
                ClusterDailyAnchor(
                    cluster_id=ca["cluster_id"],
                    date=date.fromisoformat(ca["date"]),
                    final_sequence=ca["final_sequence"],
                    final_hash=ca["final_hash"],
                    entry_count=ca["entry_count"],
                )
                for ca in global_data["cluster_anchors"]
            ]
            
            recomputed = GlobalDailyAnchor(
                date=target_date,
                cluster_anchors=cluster_anchors,
            )
            
            stored_hash = global_data["global_hash"]
            computed_hash = recomputed.global_hash
            
            return {
                "valid": stored_hash == computed_hash,
                "date": target_date.isoformat(),
                "cluster_count": len(cluster_anchors),
                "clusters": [ca.cluster_id for ca in cluster_anchors],
                "stored_hash": stored_hash[:16] + "...",
                "computed_hash": computed_hash[:16] + "...",
            }
            
        except Exception as e:
            return {"valid": False, "error": str(e)}
```

---

## 9. 4대 허점 보완 🆕

### 9.1 TieredRedisProvider (Redis 토폴로지)

> **네이밍 결정**: `MultiLayerRedisProvider` 대신 `TieredRedisProvider` 사용
> - 기존 L1/L2 Storage 개념과 일관성 유지 (settings/l2_storage.py 참조)

**문제**: 단일 Redis는 전 세계 클러스터가 하나의 가용 영역에 종속

**코드 근거**:
```python
# adapters/resilient/backend.py#L39
redis_url: str = "redis://localhost:6379/0"  # 단일 URL만 지원
```

**설계**:

```
┌─────────────────────────────────────────────────────────────────┐
│                    TieredRedisProvider                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────┐      ┌─────────────────────────────────┐  │
│  │ LOCAL Redis     │      │ GLOBAL Redis                     │  │
│  │ (Cluster-내부)   │      │ (Cross-Region Replication)       │  │
│  ├─────────────────┤      ├─────────────────────────────────┤  │
│  │ • CB 상태       │      │ • Governance 설정               │  │
│  │ • 실시간 메트릭 │      │ • Error Budget                   │  │
│  │ • DLQ 엔트리    │      │ • Global Anchors                 │  │
│  │ • Local Hash Chain │   │ • Cross-Cluster Config          │  │
│  └─────────────────┘      └─────────────────────────────────┘  │
│                                                                  │
│  get_redis(scope: RedisScope) → Redis                           │
└─────────────────────────────────────────────────────────────────┘
```

```python
# core/tiered_redis.py

from enum import Enum
from typing import Any, Optional


class RedisScope(Enum):
    """Redis 접근 범위."""
    
    LOCAL = "local"    # 클러스터 내부 (고속, 실시간)
    GLOBAL = "global"  # 리전 간 (설정, 앵커)


class TieredRedisProvider:
    """
    계층화된 Redis 제공자.
    
    LOCAL: 각 클러스터 내부 Redis (CB, 메트릭, DLQ)
    GLOBAL: 리전 간 복제 Redis (설정, 앵커, Error Budget)
    """
    
    def __init__(
        self,
        local_url: str = None,
        global_url: str = None,
    ):
        import os
        self._local_url = local_url or os.environ.get("REDIS_URL")
        self._global_url = global_url or os.environ.get("REDIS_GLOBAL_URL") or self._local_url
        self._local_client: Optional[Any] = None
        self._global_client: Optional[Any] = None
    
    def get_redis(self, scope: RedisScope = RedisScope.LOCAL) -> Any:
        """범위에 맞는 Redis 클라이언트 반환."""
        if scope == RedisScope.LOCAL:
            if self._local_client is None:
                import redis
                self._local_client = redis.from_url(self._local_url)
            return self._local_client
        else:
            if self._global_client is None:
                import redis
                self._global_client = redis.from_url(self._global_url)
            return self._global_client
```

### 9.2 클러스터 식별 필수화

**코드 근거**:
```python
# redis_manager.py#L146-147
pod_id = os.environ.get("HOSTNAME", os.environ.get("POD_NAME", "unknown"))
# → 클러스터/리전 정보 없음
```

**처방**: `SELFHEALING_CLUSTER_ID`를 **필수 환경변수**로 정의

```python
# settings/root.py에 추가

class SelfHealingSettings(BaseSettings):
    # 기존 설정들...
    
    # 🆕 클러스터 식별 (필수)
    cluster_id: str = Field(
        default="default",
        description="Cluster identifier (REQUIRED for multi-cluster)",
    )
    
    @model_validator(mode="after")
    def warn_default_cluster_id(self) -> "SelfHealingSettings":
        """기본값 사용 시 경고."""
        if self.cluster_id == "default":
            import logging
            logging.getLogger(__name__).warning(
                "⚠️ SELFHEALING_CLUSTER_ID not set. "
                "Using 'default' - this may cause data conflicts in multi-cluster."
            )
        return self
```

### 9.3 SLA 기반 일관성 등급

**코드 근거**:
```python
# settings/sla.py#L27 - 이미 SLA 설정 존재
class SLASettings(BaseSettings):
    ...
```

**처방**: 데이터 성격별 일관성 등급 정의

| Tier | 데이터 유형 | 전파 지연 허용 | 예시 |
|------|-------------|----------------|------|
| **Tier 1** | Audit, Governance, Emergency | 1초 내 | Emergency Level 변경, Kill Switch |
| **Tier 2** | Metrics, Stats, Cache | 30초 내 | 메트릭 집계, 캐시 무효화 |

```python
# settings/propagation.py

class PropagationSettings(BaseSettings):
    """전파 일관성 설정."""
    
    model_config = SettingsConfigDict(env_prefix="SELFHEALING_PROPAGATION_")
    
    tier1_max_latency_ms: int = Field(
        default=1000,
        description="Tier 1 (Audit/Governance) 최대 지연 (ms)",
    )
    tier2_max_latency_ms: int = Field(
        default=30000,
        description="Tier 2 (Metrics/Stats) 최대 지연 (ms)",
    )
```

### 9.4 Trace ID 클러스터 접두사

**코드 근거**:
```python
# audit/trace.py#L24-28
def generate_trace_id() -> str:
    return f"req-{uuid.uuid4().hex[:8]}"  # 충돌 확률 0.73%/년
```

**처방**: 즉시 형식 변경

```python
# audit/trace.py - 수정 버전

def generate_trace_id() -> str:
    """
    Generate a new trace ID with cluster prefix.
    
    Format: "req-{cluster_prefix}-{uuid4_short}"
    Example: "req-seop-a1b2c3d4" (seoul + production)
    
    충돌 확률: 클러스터별로 분리되므로 사실상 0%
    """
    from selfhealing.core.cluster_identity import get_cluster_identity
    
    identity = get_cluster_identity()
    prefix = identity.trace_id_prefix  # e.g., "seop" (seoul+prod)
    
    return f"req-{prefix}-{uuid.uuid4().hex[:8]}"
```

**결과**: `req-seop-a1b2c3d4` (서울 프로덕션에서 시작된 요청)

---

## 10. Cross-Cluster 기능 🆕

### 10.1 Regional Isolation Gate (리전 차단)

> **네이밍 결정**: `Cluster-Level Circuit Breaker` 대신 `RegionalIsolationGate` 사용
> - CB와 혼동 방지 (CB는 서비스 단위)
> - Gate는 On/Off 특성을 더 명확히 전달

**코드 근거**:
```python
# blast_radius.py#L40 - REGION 레벨 존재
class BlastRadius(str, Enum):
    INSTANCE = "instance"
    SERVICE = "service"
    REGION = "region"  # ← 이미 개념 존재

# guard.py#L827-836 - 전역 차단 패턴 존재
def block_globally(self, reason: str) -> None:
    self._global_block = True
```

**설계**:

```python
# services/isolation/regional_gate.py

class RegionalIsolationGate:
    """
    리전 단위 트래픽 차단 게이트.
    
    특정 리전(클러스터 그룹)이 불안정할 때 
    해당 리전으로의 트래픽을 전역적으로 차단.
    """
    
    GATE_KEY = "selfhealing:global:isolation:{region}"
    
    def __init__(self, global_redis: Any):
        self._redis = global_redis
    
    def isolate_region(self, region: str, reason: str, duration_seconds: int = 300) -> bool:
        """리전 격리 활성화."""
        key = self.GATE_KEY.format(region=region)
        data = {
            "isolated": True,
            "reason": reason,
            "isolated_at": datetime.now(timezone.utc).isoformat(),
            "isolated_by": get_cluster_identity().cluster_id,
        }
        self._redis.set(key, json.dumps(data), ex=duration_seconds)
        
        # 글로벌 이벤트 발행
        # ... EventBus 연동
        
        return True
    
    def is_region_isolated(self, region: str) -> tuple[bool, Optional[str]]:
        """리전 격리 상태 확인."""
        key = self.GATE_KEY.format(region=region)
        data = self._redis.get(key)
        if data:
            parsed = json.loads(data)
            return parsed.get("isolated", False), parsed.get("reason")
        return False, None
    
    def restore_region(self, region: str) -> bool:
        """리전 격리 해제."""
        key = self.GATE_KEY.format(region=region)
        return self._redis.delete(key) > 0
```

### 10.2 RedisEventBus 범용화

**현재 문제**:
```python
# event_bus_redis.py#L31
CHAOS_EVENT_CHANNEL = "selfhealing:chaos:events"  # Chaos 전용
```

**필요한 변경**:
```python
# event_bus_redis.py - 수정 방향

# 채널 정의 확장
SELFHEALING_EVENT_CHANNELS = {
    "chaos": "selfhealing:events:chaos",
    "emergency": "selfhealing:events:emergency",
    "config": "selfhealing:events:config",
    "circuit_breaker": "selfhealing:events:cb",
    "global": "selfhealing:global:events",  # 🆕 글로벌 전파용
}


class RedisEventBus:
    """모든 이벤트 타입 지원으로 확장."""
    
    def __init__(
        self,
        redis_url: Optional[str] = None,
        channels: Dict[str, str] = None,  # 🆕 다중 채널 지원
        fallback_to_local: bool = True,
    ):
        self._channels = channels or SELFHEALING_EVENT_CHANNELS
        # ...
```

### 10.3 PropagationHealthMonitor (전파 지연 모니터링) 🆕

글로벌 설정 전파 시간을 측정하여 HealthScore에 반영합니다.

> **네이밍 결정**: `Cross-Cluster Latency` 대신 `PropagationHealthMonitor` 사용
> - `IntegrityHealthScore`와 네이밍 일관성 유지
> - "Propagation"이 단방향 전파 의미 명확

**코드 근거**:
```python
# audit/integrity/health_score.py#L43-46 - IntegrityHealthMetrics 구조 참조
@dataclass
class IntegrityHealthMetrics:
    health_score: float = 100.0
    avg_recovery_time_ms: float = 0.0  # ← 시간 메트릭 패턴 존재
```

**파일**: `packages/selfhealing-python/src/selfhealing/services/config/propagation_health.py`

```python
"""
Propagation Health Monitor.

글로벌 설정 전파 건강 모니터링.

코드 근거:
- audit/integrity/health_score.py: IntegrityHealthScore 패턴
- settings/propagation.py: Tier 1/2 SLA 정의

"글로벌 정책 정합성" 자체가 시스템의 건강 지표가 됩니다.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional

from selfhealing.services.config.propagator import PropagationTier

logger = logging.getLogger(__name__)


@dataclass
class PropagationHealthMetrics:
    """글로벌 설정 전파 건강 지표."""
    
    # Latency (ms)
    last_propagation_latency_ms: float = 0.0
    avg_propagation_latency_ms: float = 0.0
    p50_propagation_latency_ms: float = 0.0
    p99_propagation_latency_ms: float = 0.0
    
    # SLA 준수
    tier1_sla_violations: int = 0  # 1초 초과 횟수 (Audit/Governance)
    tier2_sla_violations: int = 0  # 30초 초과 횟수 (Metrics/Stats)
    total_propagations: int = 0
    
    # 계산된 점수
    propagation_health_score: float = 100.0
    
    # Timestamps
    calculated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_propagation_at: Optional[str] = None


class PropagationHealthMonitor:
    """
    글로벌 설정 전파 건강 모니터링.
    
    IntegrityHealthScore와 통합하여 종합 HealthScore 제공.
    
    감점 기준:
    - Tier 1 SLA 위반 (>1초): -5점/회
    - Tier 2 SLA 위반 (>30초): -1점/회
    
    Prometheus 메트릭:
    - selfhealing_propagation_latency_ms (Histogram)
    - selfhealing_propagation_health_score (Gauge)
    - selfhealing_propagation_sla_violations_total (Counter)
    """
    
    # Prometheus 메트릭 이름
    HISTOGRAM_LATENCY = "selfhealing_propagation_latency_ms"
    GAUGE_HEALTH_SCORE = "selfhealing_propagation_health_score"
    COUNTER_SLA_VIOLATIONS = "selfhealing_propagation_sla_violations_total"
    
    # SLA 임계값 (ms)
    TIER1_SLA_THRESHOLD_MS = 1000   # 1초
    TIER2_SLA_THRESHOLD_MS = 30000  # 30초
    
    # 감점 가중치
    TIER1_PENALTY_POINTS = 5
    TIER2_PENALTY_POINTS = 1
    
    def __init__(
        self,
        max_history: int = 1000,
        prometheus_registry: Optional[Any] = None,
    ):
        self._lock = threading.Lock()
        self._latency_history: Deque[float] = deque(maxlen=max_history)
        self._tier1_violations = 0
        self._tier2_violations = 0
        self._total_propagations = 0
        self._last_propagation_at: Optional[datetime] = None
        self._prometheus_registry = prometheus_registry
    
    def record_propagation(
        self,
        config_type: str,
        latency_ms: float,
        tier: PropagationTier,
        source_cluster: str,
        target_cluster: str,
    ) -> None:
        """
        전파 완료 기록.
        
        Args:
            config_type: 설정 타입 (circuit_breaker, dlq 등)
            latency_ms: 전파 지연 시간 (ms)
            tier: 전파 등급 (Tier 1 또는 Tier 2)
            source_cluster: 소스 클러스터 ID
            target_cluster: 타겟 클러스터 ID
        """
        with self._lock:
            self._latency_history.append(latency_ms)
            self._total_propagations += 1
            self._last_propagation_at = datetime.now(timezone.utc)
            
            # SLA 위반 체크
            if tier == PropagationTier.TIER_1_IMMEDIATE:
                if latency_ms > self.TIER1_SLA_THRESHOLD_MS:
                    self._tier1_violations += 1
                    logger.warning(
                        f"[PropagationHealth] Tier 1 SLA violation: "
                        f"{config_type} propagation took {latency_ms:.1f}ms "
                        f"(threshold: {self.TIER1_SLA_THRESHOLD_MS}ms) "
                        f"[{source_cluster} → {target_cluster}]"
                    )
            elif tier == PropagationTier.TIER_2_EVENTUAL:
                if latency_ms > self.TIER2_SLA_THRESHOLD_MS:
                    self._tier2_violations += 1
                    logger.warning(
                        f"[PropagationHealth] Tier 2 SLA violation: "
                        f"{config_type} propagation took {latency_ms:.1f}ms "
                        f"(threshold: {self.TIER2_SLA_THRESHOLD_MS}ms)"
                    )
    
    def get_current_metrics(self) -> PropagationHealthMetrics:
        """현재 전파 건강 메트릭 반환."""
        with self._lock:
            if not self._latency_history:
                return PropagationHealthMetrics()
            
            # 통계 계산
            latencies = sorted(self._latency_history)
            avg_latency = sum(latencies) / len(latencies)
            p50_idx = int(len(latencies) * 0.50)
            p99_idx = int(len(latencies) * 0.99)
            
            # HealthScore 계산
            health_score = self._calculate_health_score()
            
            return PropagationHealthMetrics(
                last_propagation_latency_ms=latencies[-1] if latencies else 0.0,
                avg_propagation_latency_ms=avg_latency,
                p50_propagation_latency_ms=latencies[p50_idx] if latencies else 0.0,
                p99_propagation_latency_ms=latencies[p99_idx] if latencies else 0.0,
                tier1_sla_violations=self._tier1_violations,
                tier2_sla_violations=self._tier2_violations,
                total_propagations=self._total_propagations,
                propagation_health_score=health_score,
                last_propagation_at=self._last_propagation_at.isoformat() 
                    if self._last_propagation_at else None,
            )
    
    def _calculate_health_score(self) -> float:
        """
        HealthScore 계산.
        
        감점 기준:
        - Tier 1 SLA 위반: -5점/회
        - Tier 2 SLA 위반: -1점/회
        """
        score = 100.0
        score -= self._tier1_violations * self.TIER1_PENALTY_POINTS
        score -= self._tier2_violations * self.TIER2_PENALTY_POINTS
        return max(0.0, min(100.0, score))
    
    def get_combined_health_score(
        self,
        integrity_score: float,
        propagation_weight: float = 0.3,
    ) -> float:
        """
        IntegrityHealthScore와 결합한 종합 점수.
        
        Args:
            integrity_score: IntegrityHealthScore (0-100)
            propagation_weight: Propagation 가중치 (기본 30%)
            
        Returns:
            종합 HealthScore (0-100)
        """
        propagation_score = self._calculate_health_score()
        integrity_weight = 1.0 - propagation_weight
        
        return (integrity_score * integrity_weight) + (propagation_score * propagation_weight)


# =============================================================================
# Singleton
# =============================================================================

_monitor: Optional[PropagationHealthMonitor] = None


def get_propagation_health_monitor() -> PropagationHealthMonitor:
    """PropagationHealthMonitor 싱글톤 반환."""
    global _monitor
    if _monitor is None:
        _monitor = PropagationHealthMonitor()
    return _monitor


def reset_propagation_health_monitor() -> None:
    """테스트용 리셋."""
    global _monitor
    _monitor = None
```

---

## 11. 관련 문서

- [71_CANARY_CONFIG_ROLLOUT.md](71_CANARY_CONFIG_ROLLOUT.md) - Canary Config Rollout 구현
- [05_RESILIENT_STORAGE_BACKEND.md](05_RESILIENT_STORAGE_BACKEND.md) - 스토리지 백엔드
- [42_DISTRIBUTED_HASH_CHAIN_REDIS.md](42_DISTRIBUTED_HASH_CHAIN_REDIS.md) - Redis 분산 처리
- [43_DISTRIBUTED_HASH_CHAIN_ENHANCED.md](43_DISTRIBUTED_HASH_CHAIN_ENHANCED.md) - DailyHashAnchor 설계

---

## 12. 체크리스트

### Phase 1: 핵심 인프라 ✅ COMPLETED (2026-01-19)
- [x] `core/cluster_identity.py` - ClusterIdentity 싱글톤 생성
- [x] `core/tiered_redis.py` - TieredRedisProvider 생성
- [x] `settings/namespace.py` - NamespaceSettings 생성
- [x] 단위 테스트 작성

### Phase 2: Adapter 수정 ✅ COMPLETED (2026-01-19)
- [x] `adapters/redis/circuit_breaker.py` - KEY_PREFIX 동적화
- [x] `adapters/redis/dlq.py` - KEY_PREFIX 동적화
- [x] `services/config_history.py` - CONFIG_*_KEY 동적화
- [x] 통합 테스트 작성

**구현 노트:**
- Namespace prefixing은 `ResilientStorageBackend.config.key_prefix`에서 처리
- CB/DLQ는 component prefix(`cb:`, `dlq:`)만 반환
- 최종 키 형식: `{backend.key_prefix}{component.prefix}{key}`
  - 예: `selfhealing:seoul:cb:payment-api`

### Phase 3: Cross-Cluster 기능
- [ ] `services/config/propagator.py` - GlobalConfigPropagator 생성
- [ ] `audit/integrity/cross_cluster_linker.py` - CrossClusterAuditLinker 생성
- [ ] `services/isolation/regional_gate.py` - RegionalIsolationGate 생성
- [ ] `event_bus_redis.py` - 범용화 (다중 채널)
- [ ] `services/config/propagation_health.py` - PropagationHealthMonitor 생성

### Phase 4: Trace ID 개선
- [ ] `audit/trace.py` - 클러스터 접두사 추가
- [ ] 기존 로그 호환성 테스트

### Phase 5: 마이그레이션 도구
- [ ] `scripts/migrate_namespace.py` 작성
- [ ] `scripts/verify_global_anchors.py` 작성
- [ ] 마이그레이션 가이드 문서화

### Phase 6: 설정 필수화 및 Fail-Fast
- [ ] `settings/root.py` - cluster_id 경고 추가
- [ ] `settings/propagation.py` - Tier 설정 추가
- [ ] HealthCheck 로그에 cluster_id 출력
- [ ] `SELFHEALING_FAIL_FAST` 환경변수 지원 (기본: true)
- [ ] Quarantine Mode 폴백 옵션
