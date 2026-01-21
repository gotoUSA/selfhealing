"""
Critical Path Fallback.

연계 레이어 핵심 경로의 로컬 폴백.
Redis/Audit API 장애 시에도 Emergency 상태 변경이 가능하도록
로컬 폴백 경로를 제공합니다.

Code reference:
    audit/graceful_degradation/fallback.py#L24-45 (HashChainFallbackChain)
    services/event_bus_redis.py#L139 (fallback_to_local)
    audit/graceful_degradation/manager.py#L61 (local_fallback_path)

Reference:
    docs/self_healing/middleware_system/72_EMERGENCY_COORDINATION_LAYER.md
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# 기본 폴백 경로 (OS별)
DEFAULT_STATE_PATH = Path("/tmp/emergency_state.json")
DEFAULT_AUDIT_PATH = Path("/tmp/emergency_audit.jsonl")


class CriticalPathFallback:
    """
    연계 레이어 핵심 경로의 로컬 폴백.
    
    Redis/Audit API 장애 시에도 Emergency 상태 변경이 가능하도록
    로컬 폴백 경로를 제공합니다.
    
    Fallback Order (우선순위순):
        1. Redis Primary - 분산 상태 저장소
        2. Local File - 로컬 파일 시스템 (영속)
        3. Memory Buffer - 메모리 버퍼 (휘발성, 최후 수단)
    
    Code reference:
        audit/graceful_degradation/fallback.py#HashChainFallbackChain
    
    Usage:
        fallback = CriticalPathFallback()
        state = fallback.load_state_with_fallback()
        tier = fallback.save_state_with_fallback(new_state)
    """
    
    def __init__(
        self,
        redis_client: Optional[Any] = None,
        local_state_path: Optional[Path] = None,
        local_audit_path: Optional[Path] = None,
    ):
        """
        Args:
            redis_client: Redis 클라이언트 (없으면 로컬 폴백만 사용)
            local_state_path: 로컬 상태 파일 경로
            local_audit_path: 로컬 감사 로그 파일 경로
        """
        self._redis = redis_client
        self._local_state_path = local_state_path or DEFAULT_STATE_PATH
        self._local_audit_path = local_audit_path or DEFAULT_AUDIT_PATH
        
        # 메모리 버퍼 (최후 수단)
        self._memory_state: Optional[Dict[str, Any]] = None
        self._memory_audit_buffer: List[Dict[str, Any]] = []
        
        # 동시성 제어
        self._lock = threading.RLock()
        
        # 통계
        self._stats = {
            "redis_loads": 0,
            "redis_saves": 0,
            "local_loads": 0,
            "local_saves": 0,
            "memory_loads": 0,
            "memory_saves": 0,
            "fallback_events": 0,
        }
        
        # 현재 사용 중인 tier
        self._current_tier: str = "redis" if redis_client else "local"
    
    def load_state_with_fallback(
        self,
        namespace: str = "default",
    ) -> Dict[str, Any]:
        """
        상태 로드 (Redis → Local File → Memory 순 폴백).
        
        Args:
            namespace: 네임스페이스
        
        Returns:
            Emergency 상태 딕셔너리
        
        Code reference:
            fallback.py#L84-130 (add_integrity 폴백 체인)
        """
        with self._lock:
            # 1. Redis Primary 시도
            if self._redis:
                try:
                    key = f"selfhealing:emergency:{namespace}"
                    data = self._redis.hgetall(key)
                    if data:
                        self._stats["redis_loads"] += 1
                        self._current_tier = "redis"
                        logger.debug(
                            f"[CriticalPathFallback] Loaded from Redis: {namespace}"
                        )
                        return self._decode_redis_hash(data)
                except Exception as e:
                    logger.warning(
                        f"[CriticalPathFallback] Redis load failed: {e}"
                    )
                    self._stats["fallback_events"] += 1
            
            # 2. Local File 시도
            try:
                if self._local_state_path.exists():
                    with open(self._local_state_path, "r", encoding="utf-8") as f:
                        all_states = json.load(f)
                        if namespace in all_states:
                            self._stats["local_loads"] += 1
                            self._current_tier = "local"
                            logger.debug(
                                f"[CriticalPathFallback] Loaded from local file: {namespace}"
                            )
                            return all_states[namespace]
            except Exception as e:
                logger.warning(
                    f"[CriticalPathFallback] Local file load failed: {e}"
                )
                self._stats["fallback_events"] += 1
            
            # 3. Memory 폴백
            if self._memory_state and namespace in self._memory_state:
                self._stats["memory_loads"] += 1
                self._current_tier = "memory"
                logger.debug(
                    f"[CriticalPathFallback] Loaded from memory: {namespace}"
                )
                return self._memory_state.get(namespace, {})
            
            # 4. 기본 상태 반환 (최후 수단)
            logger.info(
                f"[CriticalPathFallback] Returning default state for {namespace}"
            )
            return self._get_default_state(namespace)
    
    def save_state_with_fallback(
        self,
        state: Dict[str, Any],
        namespace: str = "default",
    ) -> str:
        """
        상태 저장 (Redis + Local File 동시 저장).
        
        Args:
            state: 저장할 상태
            namespace: 네임스페이스
        
        Returns:
            저장된 tier ('redis', 'local', 'memory')
        """
        with self._lock:
            tier = "memory"
            
            # 항상 메모리에 유지
            if self._memory_state is None:
                self._memory_state = {}
            self._memory_state[namespace] = state
            self._stats["memory_saves"] += 1
            
            # Local File 저장 (항상 시도 - 백업용)
            try:
                self._save_to_local_file(state, namespace)
                tier = "local"
                self._stats["local_saves"] += 1
            except Exception as e:
                logger.warning(
                    f"[CriticalPathFallback] Local save failed: {e}"
                )
                self._stats["fallback_events"] += 1
            
            # Redis 저장 시도
            if self._redis:
                try:
                    key = f"selfhealing:emergency:{namespace}"
                    self._redis.hmset(key, self._encode_for_redis(state))
                    tier = "redis"
                    self._stats["redis_saves"] += 1
                    logger.debug(
                        f"[CriticalPathFallback] Saved to Redis: {namespace}"
                    )
                except Exception as e:
                    logger.warning(
                        f"[CriticalPathFallback] Redis save failed: {e}"
                    )
                    self._stats["fallback_events"] += 1
            
            self._current_tier = tier
            return tier
    
    def append_audit_log(
        self,
        entry: Dict[str, Any],
    ) -> str:
        """
        감사 로그 추가 (로컬 파일 + 메모리 버퍼).
        
        Args:
            entry: 감사 로그 엔트리
        
        Returns:
            저장된 tier ('local', 'memory')
        """
        with self._lock:
            entry["timestamp"] = datetime.now(timezone.utc).isoformat()
            tier = "memory"
            
            # 메모리 버퍼에 추가
            self._memory_audit_buffer.append(entry)
            
            # 버퍼 크기 제한 (최근 1000개만 유지)
            if len(self._memory_audit_buffer) > 1000:
                self._memory_audit_buffer = self._memory_audit_buffer[-1000:]
            
            # 로컬 파일에 추가
            try:
                with open(self._local_audit_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                tier = "local"
            except Exception as e:
                logger.warning(
                    f"[CriticalPathFallback] Audit log write failed: {e}"
                )
                self._stats["fallback_events"] += 1
            
            return tier
    
    def _save_to_local_file(
        self,
        state: Dict[str, Any],
        namespace: str,
    ) -> None:
        """로컬 파일에 상태 저장."""
        # 기존 파일 로드
        all_states = {}
        if self._local_state_path.exists():
            try:
                with open(self._local_state_path, "r", encoding="utf-8") as f:
                    all_states = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        
        # 상태 업데이트
        all_states[namespace] = state
        
        # 파일 저장
        self._local_state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._local_state_path, "w", encoding="utf-8") as f:
            json.dump(all_states, f, ensure_ascii=False, indent=2)
    
    def _encode_for_redis(self, state: Dict[str, Any]) -> Dict[str, str]:
        """Redis HMSET용 인코딩."""
        return {k: json.dumps(v) if not isinstance(v, str) else v 
                for k, v in state.items()}
    
    def _decode_redis_hash(self, data: Dict[bytes, bytes]) -> Dict[str, Any]:
        """Redis HGETALL 결과 디코딩."""
        result = {}
        for k, v in data.items():
            key = k.decode("utf-8") if isinstance(k, bytes) else k
            value = v.decode("utf-8") if isinstance(v, bytes) else v
            try:
                result[key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                result[key] = value
        return result
    
    def _get_default_state(self, namespace: str) -> Dict[str, Any]:
        """기본 상태 반환."""
        return {
            "namespace": namespace,
            "level": "NORMAL",
            "is_active": False,
            "governance_mode": "NORMAL",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": "default",
        }
    
    def get_current_tier(self) -> str:
        """현재 사용 중인 tier 조회."""
        return self._current_tier
    
    def get_stats(self) -> Dict[str, Any]:
        """통계 조회."""
        return {
            **self._stats,
            "current_tier": self._current_tier,
            "memory_state_count": len(self._memory_state or {}),
            "memory_audit_buffer_size": len(self._memory_audit_buffer),
        }
    
    def flush_memory_buffer(self) -> int:
        """
        메모리 버퍼 플러시 (로컬 파일로).
        
        Returns:
            플러시된 엔트리 수
        """
        with self._lock:
            if not self._memory_audit_buffer:
                return 0
            
            count = 0
            try:
                with open(self._local_audit_path, "a", encoding="utf-8") as f:
                    for entry in self._memory_audit_buffer:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                        count += 1
                self._memory_audit_buffer.clear()
            except Exception as e:
                logger.error(
                    f"[CriticalPathFallback] Flush failed: {e}"
                )
            
            return count
    
    def clear_local_state(self, namespace: Optional[str] = None) -> None:
        """
        로컬 상태 삭제 (테스트/복구용).
        
        Args:
            namespace: 삭제할 네임스페이스 (없으면 전체 삭제)
        """
        with self._lock:
            if namespace:
                # 특정 네임스페이스만 삭제
                if self._memory_state and namespace in self._memory_state:
                    del self._memory_state[namespace]
                
                if self._local_state_path.exists():
                    try:
                        with open(self._local_state_path, "r", encoding="utf-8") as f:
                            all_states = json.load(f)
                        if namespace in all_states:
                            del all_states[namespace]
                            with open(self._local_state_path, "w", encoding="utf-8") as f:
                                json.dump(all_states, f, ensure_ascii=False, indent=2)
                    except Exception as e:
                        logger.warning(f"[CriticalPathFallback] Clear failed: {e}")
            else:
                # 전체 삭제
                self._memory_state = None
                if self._local_state_path.exists():
                    self._local_state_path.unlink()
                if self._local_audit_path.exists():
                    self._local_audit_path.unlink()
