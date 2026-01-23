"""
Redis Key Priority Eviction Guard.

Redis maxmemory 상황에서 핵심 거버넌스 키(P0)를 보호합니다.

Features:
- RedisKeyPriority: 키 우선순위 정의 (P0 ~ P4)
- RedisKeyPriorityEviction: 키 우선순위 기반 Eviction 관리자
- 긴급 메모리 정리 (emergency_cleanup)

전략:
1. P0-P2 키: TTL 없음 (영구 보관, volatile-lru로 보호)
2. P3-P4 키: TTL 설정하여 volatile-lru로 자동 삭제 가능
3. 메모리 임계값 경고 시: P4 → P3 순서로 수동 정리

Code reference:
    critical_path_fallback.py (3단계 Fallback 패턴)
    payment_service.py (Redis TTL 관리 패턴)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#11.1
"""

from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================

class RedisKeyPriority(IntEnum):
    """
    Redis 키 우선순위.
    
    낮은 숫자 = 높은 우선순위 = 절대 삭제 금지
    
    Code reference:
        TaskPriority enum 패턴 (celery_adapter.py)
    """
    
    P0_GOVERNANCE = 0
    """거버넌스 키: emergency:level, governance:mode (절대 보호)."""
    
    P1_RECOVERY = 1
    """복구 키: recovery:session, recovery:lock (중요)."""
    
    P2_BUDGET = 2
    """버짓 키: budget:consumed, budget:multiplier (중요)."""
    
    P3_CACHE = 3
    """캐시 키: cache:*, metrics:* (휘발 가능)."""
    
    P4_AUDIT = 4
    """감사 키: audit:7일 경과 데이터 (자동 삭제 가능)."""


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class RedisMemoryInfo:
    """
    Redis 메모리 정보.
    """
    
    used_memory: int = 0
    """사용 중인 메모리 (bytes)."""
    
    max_memory: int = 0
    """최대 메모리 (bytes)."""
    
    used_percent: float = 0.0
    """사용률 (0.0 ~ 100.0)."""
    
    eviction_policy: str = "noeviction"
    """현재 eviction 정책."""
    
    keys_total: int = 0
    """전체 키 개수."""
    
    keys_with_ttl: int = 0
    """TTL이 설정된 키 개수."""
    
    def is_warning(self, threshold: float = 80.0) -> bool:
        """경고 수준인지 확인."""
        return self.used_percent >= threshold
    
    def is_critical(self, threshold: float = 90.0) -> bool:
        """위험 수준인지 확인."""
        return self.used_percent >= threshold


@dataclass
class KeyPatternConfig:
    """
    키 패턴 설정.
    """
    
    pattern: str = ""
    """키 패턴 (glob 형식)."""
    
    priority: RedisKeyPriority = RedisKeyPriority.P4_AUDIT
    """우선순위."""
    
    default_ttl_seconds: Optional[int] = None
    """기본 TTL (초). None이면 TTL 없음."""
    
    description: str = ""
    """설명."""


# =============================================================================
# Redis Key Priority Eviction
# =============================================================================

@dataclass
class RedisKeyPriorityEviction:
    """
    Redis 키 우선순위 기반 Eviction 관리자.
    
    핵심 거버넌스 데이터는 절대 삭제하지 않으면서,
    중요도가 낮은 데이터는 TTL 기반으로 자동 정리합니다.
    
    전략:
    1. P0-P2 키: TTL 없음 (영구 보관, noeviction 보호)
    2. P3-P4 키: volatile-lru + 엄격한 TTL 관리
    3. 메모리 임계 경고 시: P4 → P3 순서로 수동 정리
    
    Code reference:
        CriticalPathFallback 패턴 (critical_path_fallback.py)
        Redis TTL 패턴 (payment_service.py)
    """
    
    # P0: 절대 보호 키 패턴 (TTL 없음)
    protected_key_patterns: List[str] = field(default_factory=lambda: [
        "selfhealing:*:emergency:level",
        "selfhealing:*:emergency:state",
        "selfhealing:*:governance:*",
        "selfhealing:*:recovery:session:*",
        "selfhealing:*:recovery:lock:*",
        "selfhealing:*:recovery:active",
        "selfhealing:*:budget:*",
    ])
    
    # P3-P4: 휘발 가능 키 TTL (초)
    volatile_key_ttl: Dict[str, int] = field(default_factory=lambda: {
        "cache:*": 3600,              # 1시간
        "metrics:realtime:*": 600,    # 10분
        "metrics:aggregate:*": 7200,  # 2시간
        "audit:event:*": 604800,      # 7일
        "temp:*": 300,                # 5분
    })
    
    # 메모리 경고 임계값 (%)
    memory_warning_threshold: float = field(default_factory=lambda: float(
        os.environ.get("REDIS_MEMORY_WARNING_THRESHOLD", "80.0")
    ))
    
    # 메모리 위험 임계값 (%)
    memory_critical_threshold: float = field(default_factory=lambda: float(
        os.environ.get("REDIS_MEMORY_CRITICAL_THRESHOLD", "90.0")
    ))
    
    # 키 패턴별 상세 설정
    key_pattern_configs: List[KeyPatternConfig] = field(default_factory=lambda: [
        # P0: 거버넌스
        KeyPatternConfig(
            pattern="selfhealing:*:emergency:*",
            priority=RedisKeyPriority.P0_GOVERNANCE,
            default_ttl_seconds=None,
            description="Emergency 상태 데이터",
        ),
        KeyPatternConfig(
            pattern="selfhealing:*:governance:*",
            priority=RedisKeyPriority.P0_GOVERNANCE,
            default_ttl_seconds=None,
            description="Governance 모드 데이터",
        ),
        # P1: 복구
        KeyPatternConfig(
            pattern="selfhealing:*:recovery:session:*",
            priority=RedisKeyPriority.P1_RECOVERY,
            default_ttl_seconds=None,
            description="복구 세션 데이터",
        ),
        KeyPatternConfig(
            pattern="selfhealing:*:recovery:lock:*",
            priority=RedisKeyPriority.P1_RECOVERY,
            default_ttl_seconds=None,
            description="복구 분산 락",
        ),
        # P2: 버짓
        KeyPatternConfig(
            pattern="selfhealing:*:budget:*",
            priority=RedisKeyPriority.P2_BUDGET,
            default_ttl_seconds=None,
            description="Error Budget 데이터",
        ),
        # P3: 캐시
        KeyPatternConfig(
            pattern="cache:*",
            priority=RedisKeyPriority.P3_CACHE,
            default_ttl_seconds=3600,
            description="일반 캐시 데이터",
        ),
        KeyPatternConfig(
            pattern="metrics:*",
            priority=RedisKeyPriority.P3_CACHE,
            default_ttl_seconds=7200,
            description="메트릭 데이터",
        ),
        # P4: 감사
        KeyPatternConfig(
            pattern="audit:event:*",
            priority=RedisKeyPriority.P4_AUDIT,
            default_ttl_seconds=604800,
            description="감사 이벤트 (7일 보관)",
        ),
    ])
    
    def get_key_priority(self, key: str) -> RedisKeyPriority:
        """
        키의 우선순위 반환.
        
        Args:
            key: Redis 키
        
        Returns:
            해당 키의 우선순위
        """
        # 패턴 설정에서 먼저 확인
        for config in self.key_pattern_configs:
            if fnmatch.fnmatch(key, config.pattern):
                return config.priority
        
        # 보호 패턴 확인
        for pattern in self.protected_key_patterns:
            if fnmatch.fnmatch(key, pattern):
                if "emergency" in pattern or "governance" in pattern:
                    return RedisKeyPriority.P0_GOVERNANCE
                elif "recovery" in pattern:
                    return RedisKeyPriority.P1_RECOVERY
                elif "budget" in pattern:
                    return RedisKeyPriority.P2_BUDGET
        
        # 기본값
        if key.startswith("cache:") or key.startswith("metrics:"):
            return RedisKeyPriority.P3_CACHE
        
        return RedisKeyPriority.P4_AUDIT
    
    def should_protect_key(self, key: str) -> bool:
        """
        키가 보호 대상인지 확인.
        
        P0, P1, P2 키는 보호 대상입니다.
        
        Args:
            key: Redis 키
        
        Returns:
            보호 여부
        """
        priority = self.get_key_priority(key)
        return priority <= RedisKeyPriority.P2_BUDGET
    
    def get_key_ttl(self, key: str) -> Optional[int]:
        """
        키의 권장 TTL 반환.
        
        Args:
            key: Redis 키
        
        Returns:
            권장 TTL (초). None이면 TTL 없음
        """
        # 패턴 설정에서 확인
        for config in self.key_pattern_configs:
            if fnmatch.fnmatch(key, config.pattern):
                return config.default_ttl_seconds
        
        # 휘발 가능 키 TTL 확인
        for pattern, ttl in self.volatile_key_ttl.items():
            if fnmatch.fnmatch(key, pattern):
                return ttl
        
        # 보호 키는 TTL 없음
        if self.should_protect_key(key):
            return None
        
        # 기본값: 7일
        return 604800
    
    def get_recommended_redis_config(self) -> Dict[str, str]:
        """
        권장 Redis 설정 반환.
        
        Returns:
            redis.conf에 적용할 설정값
        """
        return {
            # volatile-lru: TTL이 설정된 키만 LRU로 삭제
            # → P0-P2 키는 TTL 없으므로 보호됨
            # → P3-P4 키는 TTL 있으므로 메모리 부족 시 삭제
            "maxmemory-policy": "volatile-lru",
            
            # 메모리 샘플링
            "maxmemory-samples": "10",
            
            # 만료 정확도 (값이 높을수록 정확)
            "hz": "10",
        }
    
    def get_memory_info(self, redis_client) -> RedisMemoryInfo:
        """
        Redis 메모리 정보 조회.
        
        Args:
            redis_client: Redis 클라이언트
        
        Returns:
            메모리 정보
        """
        try:
            info = redis_client.info("memory")
            keyspace = redis_client.info("keyspace")
            
            used = info.get("used_memory", 0)
            max_mem = info.get("maxmemory", 0)
            
            # 전체 키 수 계산
            total_keys = 0
            keys_with_ttl = 0
            for db_info in keyspace.values():
                if isinstance(db_info, dict):
                    total_keys += db_info.get("keys", 0)
                    keys_with_ttl += db_info.get("expires", 0)
            
            return RedisMemoryInfo(
                used_memory=used,
                max_memory=max_mem,
                used_percent=(used / max_mem * 100) if max_mem > 0 else 0,
                eviction_policy=info.get("maxmemory_policy", "noeviction"),
                keys_total=total_keys,
                keys_with_ttl=keys_with_ttl,
            )
        except Exception as e:
            logger.error(f"[RedisKeyPriorityEviction] Get memory info failed: {e}")
            return RedisMemoryInfo()
    
    def check_memory_status(self, redis_client) -> Dict[str, Any]:
        """
        메모리 상태 확인.
        
        Args:
            redis_client: Redis 클라이언트
        
        Returns:
            상태 정보
        """
        info = self.get_memory_info(redis_client)
        
        return {
            "used_memory_mb": info.used_memory / (1024 * 1024),
            "max_memory_mb": info.max_memory / (1024 * 1024) if info.max_memory else 0,
            "used_percent": info.used_percent,
            "status": (
                "critical" if info.is_critical(self.memory_critical_threshold)
                else "warning" if info.is_warning(self.memory_warning_threshold)
                else "normal"
            ),
            "eviction_policy": info.eviction_policy,
            "keys_total": info.keys_total,
            "keys_with_ttl": info.keys_with_ttl,
            "keys_without_ttl": info.keys_total - info.keys_with_ttl,
        }
    
    def emergency_cleanup(
        self,
        redis_client,
        target_free_percent: float = 20.0,
    ) -> Dict[str, int]:
        """
        긴급 메모리 정리.
        
        P4 → P3 순서로 키를 삭제하여 목표 여유 공간 확보.
        P0, P1, P2 키는 절대 삭제하지 않습니다.
        
        Args:
            redis_client: Redis 클라이언트
            target_free_percent: 목표 여유 공간 비율
        
        Returns:
            {"deleted_p4": N, "deleted_p3": M}
        """
        result = {"deleted_p4": 0, "deleted_p3": 0}
        
        info = self.get_memory_info(redis_client)
        target_used = 100 - target_free_percent
        
        if info.used_percent <= target_used:
            logger.info(
                f"[RedisKeyPriorityEviction] No cleanup needed: "
                f"used={info.used_percent:.1f}%, target={target_used:.1f}%"
            )
            return result
        
        logger.warning(
            f"[RedisKeyPriorityEviction] Starting emergency cleanup: "
            f"used={info.used_percent:.1f}%, target={target_used:.1f}%"
        )
        
        # P4 키 삭제 (audit 7일 경과)
        try:
            for key in redis_client.scan_iter("audit:event:*"):
                key_str = key.decode() if isinstance(key, bytes) else key
                if not self.should_protect_key(key_str):
                    redis_client.delete(key)
                    result["deleted_p4"] += 1
                    
                    # 중간 체크
                    if result["deleted_p4"] % 100 == 0:
                        info = self.get_memory_info(redis_client)
                        if info.used_percent <= target_used:
                            break
        except Exception as e:
            logger.error(f"[RedisKeyPriorityEviction] P4 cleanup error: {e}")
        
        # 메모리 확인 후 P3도 필요 시 삭제
        info = self.get_memory_info(redis_client)
        
        if info.used_percent > target_used:
            try:
                for pattern in ["cache:*", "metrics:*", "temp:*"]:
                    for key in redis_client.scan_iter(pattern):
                        key_str = key.decode() if isinstance(key, bytes) else key
                        if not self.should_protect_key(key_str):
                            redis_client.delete(key)
                            result["deleted_p3"] += 1
                            
                            if result["deleted_p3"] % 100 == 0:
                                info = self.get_memory_info(redis_client)
                                if info.used_percent <= target_used:
                                    break
            except Exception as e:
                logger.error(f"[RedisKeyPriorityEviction] P3 cleanup error: {e}")
        
        logger.warning(
            f"[RedisKeyPriorityEviction] Emergency cleanup completed: "
            f"P4={result['deleted_p4']}, P3={result['deleted_p3']}"
        )
        
        return result
    
    def ensure_key_ttl(
        self,
        redis_client,
        key: str,
        override_ttl: Optional[int] = None,
    ) -> bool:
        """
        키에 적절한 TTL이 설정되어 있는지 확인하고 설정.
        
        P0, P1, P2 키는 TTL을 설정하지 않습니다.
        
        Args:
            redis_client: Redis 클라이언트
            key: Redis 키
            override_ttl: TTL 덮어쓰기 (초)
        
        Returns:
            TTL 설정 여부
        """
        if self.should_protect_key(key):
            # 보호 키는 TTL 제거 (혹시 설정되어 있다면)
            try:
                current_ttl = redis_client.ttl(key)
                if current_ttl > 0:
                    redis_client.persist(key)
                    logger.debug(
                        f"[RedisKeyPriorityEviction] Removed TTL from protected key: {key}"
                    )
            except Exception:
                pass
            return False
        
        # 휘발 가능 키에 TTL 설정
        ttl = override_ttl or self.get_key_ttl(key)
        if ttl:
            try:
                current_ttl = redis_client.ttl(key)
                if current_ttl < 0:  # -1: TTL 없음, -2: 키 없음
                    redis_client.expire(key, ttl)
                    return True
            except Exception as e:
                logger.warning(f"[RedisKeyPriorityEviction] Set TTL failed: {key}, {e}")
        
        return False
    
    def scan_and_set_ttls(
        self,
        redis_client,
        pattern: str = "*",
        batch_size: int = 100,
    ) -> Dict[str, int]:
        """
        키를 스캔하여 TTL 설정.
        
        Args:
            redis_client: Redis 클라이언트
            pattern: 키 패턴
            batch_size: 배치 크기
        
        Returns:
            {"protected": N, "ttl_set": M, "skipped": K}
        """
        result = {"protected": 0, "ttl_set": 0, "skipped": 0}
        
        try:
            for key in redis_client.scan_iter(pattern, count=batch_size):
                key_str = key.decode() if isinstance(key, bytes) else key
                
                if self.should_protect_key(key_str):
                    result["protected"] += 1
                elif self.ensure_key_ttl(redis_client, key_str):
                    result["ttl_set"] += 1
                else:
                    result["skipped"] += 1
        except Exception as e:
            logger.error(f"[RedisKeyPriorityEviction] Scan and set TTLs failed: {e}")
        
        return result


# =============================================================================
# Singleton Access
# =============================================================================

_redis_key_guard: Optional[RedisKeyPriorityEviction] = None


def get_redis_key_guard() -> RedisKeyPriorityEviction:
    """
    RedisKeyPriorityEviction 싱글톤 반환.
    
    Returns:
        RedisKeyPriorityEviction 인스턴스
    """
    global _redis_key_guard
    
    if _redis_key_guard is None:
        _redis_key_guard = RedisKeyPriorityEviction()
    
    return _redis_key_guard
