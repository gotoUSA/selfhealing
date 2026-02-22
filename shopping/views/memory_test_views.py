"""
Memory Pressure Test Endpoints

실제 메모리 압박 테스트를 위한 엔드포인트.
Stage 36 Real 테스트에서 사용.

WARNING: 프로덕션에서는 비활성화해야 함!
환경변수 MEMORY_TEST_ENABLED=true 일 때만 동작.

엔드포인트:
  - /api/test/memory/status/ - 현재 메모리 상태
  - /api/test/memory/allocate/ - 메모리 할당 (지정 크기)
  - /api/test/memory/large-response/ - 대용량 JSON 응답
  - /api/test/memory/streaming-response/ - 스트리밍 응답
  - /api/test/memory/leak-simulate/ - 메모리 누수 시뮬레이션
  - /api/test/memory/gc-trigger/ - GC 강제 실행
  - /api/test/memory/release-all/ - 모든 할당 메모리 해제

=============================================================================
업계 표준 메모리 모니터링 4종 세트 (Stage 36 개선)
=============================================================================

1순위: cgroup 메모리 (컨테이너 '진짜' 한도 기준) - SSoT
   - /sys/fs/cgroup/memory.current (v2) 또는 memory.usage_in_bytes (v1)
   - /sys/fs/cgroup/memory.max (v2) 또는 memory.limit_in_bytes (v1)
   - /sys/fs/cgroup/memory.events (v2) - oom_kill 카운트

2순위: per-process RSS/USS (원인 분해용)
   - psutil로 각 워커의 RSS 측정
   - cgroup 전체와 RSS 합계 차이 = 공유메모리/캐시/오버헤드

3순위: OOM Kill/pressure 신호 (조기 경보)
   - memory.events의 oom_kill 카운터
   - PSI (Pressure Stall Information): /proc/pressure/memory

4순위: 런타임/서빙 계층 지표 (증상 감지)
   - gunicorn worker exits / timeouts
   - request queue length, 2xx/429/503 비율
=============================================================================
"""

import gc
import os
import time
import json
import threading
from typing import Dict, List, Any, Optional
from datetime import datetime

from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

# psutil for actual memory tracking
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


# =============================================================================
# Configuration
# =============================================================================

MEMORY_TEST_ENABLED = os.environ.get("MEMORY_TEST_ENABLED", "false").lower() == "true"

# Memory thresholds (from environment or defaults)
MEMORY_WARNING_THRESHOLD = float(os.environ.get("MEMORY_WARNING_THRESHOLD", "0.70"))
MEMORY_THROTTLE_THRESHOLD = float(os.environ.get("MEMORY_THROTTLE_THRESHOLD", "0.85"))
MEMORY_CRITICAL_THRESHOLD = float(os.environ.get("MEMORY_CRITICAL_THRESHOLD", "0.95"))
MEMORY_MAX_MB = int(os.environ.get("MEMORY_MAX_MB", "1024"))

# Streaming chunk size
STREAMING_CHUNK_SIZE = int(os.environ.get("STREAMING_CHUNK_SIZE_KB", "64")) * 1024

# =============================================================================
# 피드백 2: 백프레셔 구성 (Throttle 단계 개선)
# =============================================================================

# 세마포어: 동시 요청 제한 (throttle 상태에서)
THROTTLE_MAX_CONCURRENT = int(os.environ.get("THROTTLE_MAX_CONCURRENT", "5"))
THROTTLE_SLEEP_MS = int(os.environ.get("THROTTLE_SLEEP_MS", "200"))  # ms
THROTTLE_RETRY_AFTER_SEC = int(os.environ.get("THROTTLE_RETRY_AFTER_SEC", "2"))

# 429 백프레셔 개선: Throttle 상태에서 일정 비율 요청 거부
# 세마포어만으로는 동시성이 낮으면 429가 발생하지 않음
THROTTLE_REJECT_RATE = float(os.environ.get("THROTTLE_REJECT_RATE", "0.3"))  # 30% 거부

# C3 테스트: 강제 throttle 모드 (테스트용)
FORCE_THROTTLE_MODE = os.environ.get("FORCE_THROTTLE_MODE", "false").lower() == "true"

# =============================================================================
# 피드백 5: 히스테리시스 구성 (플래핑 방지)
# =============================================================================

# Throttle 진입: N회 연속 초과 시 상태 전환
HYSTERESIS_ENTER_COUNT = int(os.environ.get("HYSTERESIS_ENTER_COUNT", "2"))
# Throttle 해제: N회 연속 미만 시 상태 해제
HYSTERESIS_EXIT_COUNT = int(os.environ.get("HYSTERESIS_EXIT_COUNT", "3"))
# 해제 임계치 (진입보다 5% 낮게)
HYSTERESIS_EXIT_OFFSET = float(os.environ.get("HYSTERESIS_EXIT_OFFSET", "0.05"))


# =============================================================================
# 1순위: CgroupMemoryMonitor - 컨테이너 '진짜' 한도 기준 (SSoT)
# =============================================================================

class CgroupMemoryMonitor:
    """
    업계 표준 cgroup 기반 메모리 모니터링.
    
    Netflix, Google, Uber 등 대규모 컨테이너 운영사들이 사용하는 패턴:
    - cgroup memory.current를 SSoT(Single Source of Truth)로 사용
    - psutil RSS는 원인 분해용 보조 지표로만 사용
    
    cgroups v2 (modern Docker, Kubernetes 1.25+):
        /sys/fs/cgroup/memory.current - 현재 사용량
        /sys/fs/cgroup/memory.max - 제한
        /sys/fs/cgroup/memory.events - OOM 이벤트
    
    cgroups v1 (legacy):
        /sys/fs/cgroup/memory/memory.usage_in_bytes
        /sys/fs/cgroup/memory/memory.limit_in_bytes
        /sys/fs/cgroup/memory/memory.oom_control
    """
    
    # cgroups v2 paths
    CGROUP_V2_CURRENT = "/sys/fs/cgroup/memory.current"
    CGROUP_V2_MAX = "/sys/fs/cgroup/memory.max"
    CGROUP_V2_EVENTS = "/sys/fs/cgroup/memory.events"
    
    # cgroups v1 paths
    CGROUP_V1_USAGE = "/sys/fs/cgroup/memory/memory.usage_in_bytes"
    CGROUP_V1_LIMIT = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
    CGROUP_V1_OOM = "/sys/fs/cgroup/memory/memory.oom_control"
    
    # PSI (Pressure Stall Information)
    PSI_MEMORY = "/proc/pressure/memory"
    
    # 캐시 (매 요청마다 파일 읽기 방지)
    _cache = {}
    _cache_ttl_ms = 100  # 100ms 캐시
    _last_cache_time = 0
    
    @classmethod
    def _read_file_safe(cls, path: str) -> Optional[str]:
        """파일을 안전하게 읽기"""
        try:
            with open(path, 'r') as f:
                return f.read().strip()
        except (FileNotFoundError, IOError, PermissionError):
            return None
    
    @classmethod
    def _parse_events(cls, content: str) -> Dict[str, int]:
        """memory.events 파싱 (key value 형식)"""
        result = {}
        for line in content.split('\n'):
            parts = line.split()
            if len(parts) == 2:
                try:
                    result[parts[0]] = int(parts[1])
                except ValueError:
                    pass
        return result
    
    @classmethod
    def _parse_psi(cls, content: str) -> Dict[str, Any]:
        """PSI 파싱 (some/full avg10/avg60/avg300/total 형식)"""
        result = {}
        for line in content.split('\n'):
            if line.startswith('some ') or line.startswith('full '):
                parts = line.split()
                prefix = parts[0]  # 'some' or 'full'
                metrics = {}
                for part in parts[1:]:
                    if '=' in part:
                        key, value = part.split('=')
                        try:
                            metrics[key] = float(value)
                        except ValueError:
                            pass
                result[prefix] = metrics
        return result
    
    @classmethod
    def get_cgroup_version(cls) -> int:
        """cgroups 버전 감지 (2 또는 1)"""
        if os.path.exists(cls.CGROUP_V2_CURRENT):
            return 2
        elif os.path.exists(cls.CGROUP_V1_USAGE):
            return 1
        return 0  # 컨테이너 아님
    
    @classmethod
    def get_current_bytes(cls) -> Optional[int]:
        """현재 컨테이너 메모리 사용량 (bytes)"""
        version = cls.get_cgroup_version()
        
        if version == 2:
            content = cls._read_file_safe(cls.CGROUP_V2_CURRENT)
            if content:
                try:
                    return int(content)
                except ValueError:
                    pass
        elif version == 1:
            content = cls._read_file_safe(cls.CGROUP_V1_USAGE)
            if content:
                try:
                    return int(content)
                except ValueError:
                    pass
        return None
    
    @classmethod
    def get_max_bytes(cls) -> Optional[int]:
        """컨테이너 메모리 제한 (bytes)"""
        version = cls.get_cgroup_version()
        
        if version == 2:
            content = cls._read_file_safe(cls.CGROUP_V2_MAX)
            if content:
                if content == "max":
                    return None  # 무제한
                try:
                    value = int(content)
                    # 매우 큰 값은 무제한으로 간주 (> 100GB)
                    if value > 100 * 1024 * 1024 * 1024:
                        return None
                    return value
                except ValueError:
                    pass
        elif version == 1:
            content = cls._read_file_safe(cls.CGROUP_V1_LIMIT)
            if content:
                try:
                    value = int(content)
                    if value > 100 * 1024 * 1024 * 1024:
                        return None
                    return value
                except ValueError:
                    pass
        return None
    
    @classmethod
    def get_memory_percent(cls) -> float:
        """
        컨테이너 메모리 사용률 (0.0 ~ 1.0).
        
        ★ 핵심 변경: 이제 cgroup을 SSoT로 사용 ★
        기존: psutil.Process().memory_info().rss (단일 워커만 봄)
        변경: /sys/fs/cgroup/memory.current (컨테이너 전체)
        """
        current = cls.get_current_bytes()
        max_bytes = cls.get_max_bytes()
        
        if current is None or max_bytes is None:
            # 컨테이너가 아니거나 무제한인 경우 psutil fallback
            if PSUTIL_AVAILABLE:
                return psutil.Process().memory_percent() / 100.0
            return 0.0
        
        return min(current / max_bytes, 1.0)
    
    @classmethod
    def get_oom_events(cls) -> Dict[str, int]:
        """
        OOM 이벤트 카운터 읽기.
        
        Returns:
            {
                'oom': int,       # OOM 발생 횟수 (cgroup에 의해 제한됨)
                'oom_kill': int,  # 실제 프로세스 kill 횟수
                'oom_group_kill': int  # 그룹 kill 횟수 (cgroups v2)
            }
        """
        result = {'oom': 0, 'oom_kill': 0, 'oom_group_kill': 0}
        
        if cls.get_cgroup_version() == 2:
            content = cls._read_file_safe(cls.CGROUP_V2_EVENTS)
            if content:
                events = cls._parse_events(content)
                result['oom'] = events.get('oom', 0)
                result['oom_kill'] = events.get('oom_kill', 0)
                result['oom_group_kill'] = events.get('oom_group_kill', 0)
        
        return result
    
    @classmethod
    def get_psi(cls) -> Optional[Dict[str, Any]]:
        """
        PSI (Pressure Stall Information) 읽기.
        메모리 압박으로 인해 CPU가 stall된 시간 비율.
        
        Returns:
            {
                'some': {'avg10': 0.0, 'avg60': 0.0, 'avg300': 0.0, 'total': 0},
                'full': {'avg10': 0.0, 'avg60': 0.0, 'avg300': 0.0, 'total': 0}
            }
        """
        content = cls._read_file_safe(cls.PSI_MEMORY)
        if content:
            return cls._parse_psi(content)
        return None
    
    @classmethod
    def get_full_status(cls) -> Dict[str, Any]:
        """
        전체 메모리 상태 (4종 세트 통합).
        
        Returns:
            종합 메모리 상태 딕셔너리
        """
        current_bytes = cls.get_current_bytes()
        max_bytes = cls.get_max_bytes()
        cgroup_version = cls.get_cgroup_version()
        
        # 1순위: cgroup 메모리 (SSoT)
        cgroup_status = {
            'version': cgroup_version,
            'current_bytes': current_bytes,
            'max_bytes': max_bytes,
            'current_mb': round(current_bytes / (1024 * 1024), 2) if current_bytes else None,
            'max_mb': round(max_bytes / (1024 * 1024), 2) if max_bytes else None,
            'percent': round(cls.get_memory_percent() * 100, 2),
            'is_containerized': cgroup_version > 0 and max_bytes is not None
        }
        
        # 2순위: per-process RSS (원인 분해용)
        process_status = None
        if PSUTIL_AVAILABLE:
            try:
                proc = psutil.Process()
                mem_info = proc.memory_info()
                process_status = {
                    'rss_mb': round(mem_info.rss / (1024 * 1024), 2),
                    'vms_mb': round(mem_info.vms / (1024 * 1024), 2),
                    'percent_of_system': round(proc.memory_percent(), 2),
                }
                # USS (Unique Set Size) - 공유 메모리 제외
                try:
                    full_info = proc.memory_full_info()
                    process_status['uss_mb'] = round(full_info.uss / (1024 * 1024), 2)
                    process_status['pss_mb'] = round(full_info.pss / (1024 * 1024), 2)
                except (psutil.AccessDenied, AttributeError):
                    pass
            except Exception:
                pass
        
        # 3순위: OOM/Pressure 조기 경보
        oom_events = cls.get_oom_events()
        psi = cls.get_psi()
        
        # 메모리 압박 상태 판단
        pressure_status = {
            'oom_events': oom_events,
            'psi': psi,
            'under_pressure': False,
            'pressure_level': 'none'
        }
        
        if psi:
            # some.avg10 > 10%면 경미한 압박, > 50%면 심각
            some_avg10 = psi.get('some', {}).get('avg10', 0)
            if some_avg10 > 50:
                pressure_status['under_pressure'] = True
                pressure_status['pressure_level'] = 'severe'
            elif some_avg10 > 10:
                pressure_status['under_pressure'] = True
                pressure_status['pressure_level'] = 'moderate'
        
        return {
            'cgroup': cgroup_status,
            'process': process_status,
            'pressure': pressure_status,
            'timestamp': datetime.now().isoformat()
        }


# =============================================================================
# Global Memory Pool (for controlled allocation)
# =============================================================================

class MemoryPool:
    """
    실제 메모리를 할당하고 추적하는 메모리 풀.
    테스트 목적으로만 사용.
    
    피드백 2: 세마포어 기반 백프레셔
    피드백 5: 히스테리시스 상태 관리
    """
    
    # ==========================================================
    # 디버깅 카운터 (429가 왜 안 나는지 추적)
    # ==========================================================
    throttle_state_seen_count = 0      # throttle 상태로 진입한 횟수
    semaphore_acquire_failed_count = 0 # 세마포어 획득 실패 횟수
    returned_429_count = 0             # 429 반환한 횟수
    returned_503_count = 0             # 503 반환한 횟수 (critical)
    probability_reject_count = 0       # 확률 기반 거부 횟수
    total_backpressure_checks = 0      # 총 백프레셔 체크 횟수
    peak_memory_percent_seen = 0.0     # 테스트 중 최고 메모리 %
    
    def __init__(self):
        self._allocations: Dict[str, bytes] = {}
        self._lock = threading.RLock()
        self._leak_storage: List[bytes] = []  # 누수 시뮬레이션용
        
        # 피드백 2: 세마포어 (동시 요청 제한)
        self._throttle_semaphore = threading.Semaphore(THROTTLE_MAX_CONCURRENT)
        
        # 피드백 5: 히스테리시스 상태
        self._current_state = "normal"  # normal, warning, throttled, critical
        self._state_counter = 0  # 연속 횟수 카운터
        self._last_check_above_threshold = False
        
    def allocate(self, block_id: str, size_mb: float) -> Dict[str, Any]:
        """
        실제 메모리 할당.
        
        Args:
            block_id: 블록 식별자
            size_mb: 할당할 크기 (MB)
            
        Returns:
            할당 결과 딕셔너리
        """
        with self._lock:
            size_bytes = int(size_mb * 1024 * 1024)
            
            result = {
                "block_id": block_id,
                "requested_mb": size_mb,
                "success": False,
                "memory_before": self.get_current_memory_mb(),
                "memory_after": 0,
                "error": None
            }
            
            try:
                # 실제 바이트 할당 (0으로 채움)
                self._allocations[block_id] = b'\x00' * size_bytes
                result["success"] = True
                result["memory_after"] = self.get_current_memory_mb()
            except MemoryError as e:
                result["error"] = f"MemoryError: {str(e)}"
            except Exception as e:
                result["error"] = f"Error: {str(e)}"
                
            return result
    
    def deallocate(self, block_id: str) -> Dict[str, Any]:
        """메모리 블록 해제"""
        with self._lock:
            result = {
                "block_id": block_id,
                "success": False,
                "freed_mb": 0,
                "memory_after": 0
            }
            
            if block_id in self._allocations:
                freed_bytes = len(self._allocations[block_id])
                del self._allocations[block_id]
                result["success"] = True
                result["freed_mb"] = freed_bytes / (1024 * 1024)
                
            result["memory_after"] = self.get_current_memory_mb()
            return result
    
    def release_all(self) -> Dict[str, Any]:
        """모든 할당 메모리 해제"""
        with self._lock:
            count = len(self._allocations)
            total_mb = sum(len(b) for b in self._allocations.values()) / (1024 * 1024)
            
            self._allocations.clear()
            self._leak_storage.clear()
            
            # 강제 GC
            gc.collect()
            
            return {
                "released_blocks": count,
                "released_mb": total_mb,
                "memory_after": self.get_current_memory_mb()
            }
    
    def simulate_leak(self, size_mb: float) -> Dict[str, Any]:
        """
        메모리 누수 시뮬레이션.
        해제되지 않는 메모리를 할당.
        """
        with self._lock:
            size_bytes = int(size_mb * 1024 * 1024)
            memory_before = self.get_current_memory_mb()
            
            try:
                # 누수 저장소에 추가 (해제 안됨)
                self._leak_storage.append(b'\x00' * size_bytes)
                
                return {
                    "success": True,
                    "leaked_mb": size_mb,
                    "total_leaked_mb": sum(len(b) for b in self._leak_storage) / (1024 * 1024),
                    "memory_before": memory_before,
                    "memory_after": self.get_current_memory_mb()
                }
            except MemoryError as e:
                return {
                    "success": False,
                    "error": f"MemoryError: {str(e)}",
                    "memory_after": self.get_current_memory_mb()
                }
    
    def get_allocated_mb(self) -> float:
        """현재 풀에서 할당된 총 메모리"""
        with self._lock:
            return sum(len(b) for b in self._allocations.values()) / (1024 * 1024)
    
    def get_leaked_mb(self) -> float:
        """누수된 메모리 총량"""
        with self._lock:
            return sum(len(b) for b in self._leak_storage) / (1024 * 1024)
    
    @staticmethod
    def get_current_memory_mb() -> float:
        """현재 프로세스의 실제 메모리 사용량 (RSS)"""
        if PSUTIL_AVAILABLE:
            process = psutil.Process()
            return process.memory_info().rss / (1024 * 1024)
        else:
            # psutil 없으면 sys.getsizeof 기반 추정
            return 0.0
    
    @staticmethod
    def get_container_memory_limit_mb() -> Optional[float]:
        """
        Docker 컨테이너 메모리 제한 감지 (cgroups v2).
        업계 표준 패턴: Netflix, Google, Uber 등이 사용하는 방식.
        
        Returns:
            컨테이너 메모리 제한 (MB) 또는 None (제한 없음)
        """
        # cgroups v2 (modern Docker)
        cgroup_v2_path = "/sys/fs/cgroup/memory.max"
        # cgroups v1 (legacy Docker)
        cgroup_v1_path = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
        
        for path in [cgroup_v2_path, cgroup_v1_path]:
            try:
                with open(path, 'r') as f:
                    value = f.read().strip()
                    if value == "max":  # cgroups v2: 무제한
                        continue
                    limit_bytes = int(value)
                    # 매우 큰 값은 무제한으로 간주 (> 100GB)
                    if limit_bytes > 100 * 1024 * 1024 * 1024:
                        continue
                    return limit_bytes / (1024 * 1024)
            except (FileNotFoundError, IOError, ValueError):
                continue
        
        return None  # 컨테이너 제한 없음

    @staticmethod
    def get_memory_percent() -> float:
        """
        현재 메모리 사용률 (0.0 ~ 1.0).
        
        ★ Stage 36 핵심 개선: cgroup을 SSoT로 사용 ★
        
        기존 문제:
            psutil.Process().memory_info().rss는 '단일 워커'만 봄
            → 4개 워커 환경에서 25%만 보고 나머지 75%는 blind spot
            → 컨테이너 OOM 전에 429 발생 불가
        
        개선:
            CgroupMemoryMonitor.get_memory_percent()를 SSoT로 사용
            → /sys/fs/cgroup/memory.current / memory.max
            → 컨테이너 전체 메모리를 정확히 관측
        """
        # 1순위: cgroup 기반 (컨테이너 전체)
        return CgroupMemoryMonitor.get_memory_percent()
    
    def _update_state_with_hysteresis(self, percent: float) -> str:
        """
        피드백 5: 히스테리시스 기반 상태 전이
        - 상태 진입: N회 연속 임계값 초과
        - 상태 해제: N회 연속 임계값 - offset 미만
        """
        # 현재 가능한 상태 계산 (raw)
        if percent >= MEMORY_CRITICAL_THRESHOLD:
            raw_state = "critical"
        elif percent >= MEMORY_THROTTLE_THRESHOLD:
            raw_state = "throttled"
        elif percent >= MEMORY_WARNING_THRESHOLD:
            raw_state = "warning"
        else:
            raw_state = "normal"
        
        state_order = ["normal", "warning", "throttled", "critical"]
        current_idx = state_order.index(self._current_state)
        raw_idx = state_order.index(raw_state)
        
        # 상승 전이 (more severe)
        if raw_idx > current_idx:
            if self._last_check_above_threshold:
                self._state_counter += 1
            else:
                self._state_counter = 1
                self._last_check_above_threshold = True
            
            # N회 연속 넘으면 상태 전환
            if self._state_counter >= HYSTERESIS_ENTER_COUNT:
                self._current_state = raw_state
                self._state_counter = 0
        
        # 하강 전이 (less severe)
        elif raw_idx < current_idx:
            # 해제 임계치 확인 (offset 적용)
            exit_thresholds = {
                "critical": MEMORY_CRITICAL_THRESHOLD - HYSTERESIS_EXIT_OFFSET,
                "throttled": MEMORY_THROTTLE_THRESHOLD - HYSTERESIS_EXIT_OFFSET,
                "warning": MEMORY_WARNING_THRESHOLD - HYSTERESIS_EXIT_OFFSET
            }
            
            exit_threshold = exit_thresholds.get(self._current_state, 0)
            
            if percent < exit_threshold:
                if not self._last_check_above_threshold:
                    self._state_counter += 1
                else:
                    self._state_counter = 1
                    self._last_check_above_threshold = False
                
                # N회 연속 미만이면 상태 해제
                if self._state_counter >= HYSTERESIS_EXIT_COUNT:
                    self._current_state = raw_state
                    self._state_counter = 0
        else:
            # 동일 상태 - 카운터 유지
            pass
        
        return self._current_state
    
    def get_status(self) -> Dict[str, Any]:
        """
        전체 메모리 상태 반환.
        
        업계 표준 4종 세트 포함:
        1순위: cgroup 메모리 (SSoT)
        2순위: per-process RSS (원인 분해용)
        3순위: OOM/Pressure 조기 경보
        4순위: 런타임 지표 (pool 상태)
        """
        with self._lock:
            # 1순위: cgroup 기반 메모리 (SSoT)
            percent = self.get_memory_percent()  # 이제 cgroup 기반
            container_limit_mb = self.get_container_memory_limit_mb()
            
            # cgroup에서 현재 사용량 직접 읽기
            cgroup_current = CgroupMemoryMonitor.get_current_bytes()
            cgroup_current_mb = cgroup_current / (1024 * 1024) if cgroup_current else None
            
            # 2순위: per-process RSS (원인 분해용)
            process_rss_mb = self.get_current_memory_mb()  # psutil RSS
            
            # 히스테리시스 적용한 상태 결정
            state = self._update_state_with_hysteresis(percent)
            
            # Raw 상태도 반환 (디버깅용)
            if percent >= MEMORY_CRITICAL_THRESHOLD:
                raw_state = "critical"
            elif percent >= MEMORY_THROTTLE_THRESHOLD:
                raw_state = "throttled"
            elif percent >= MEMORY_WARNING_THRESHOLD:
                raw_state = "warning"
            else:
                raw_state = "normal"
            
            # 3순위: OOM/Pressure 조기 경보
            oom_events = CgroupMemoryMonitor.get_oom_events()
            psi = CgroupMemoryMonitor.get_psi()
            
            return {
                # 기본 상태 (하위 호환성)
                "current_mb": round(cgroup_current_mb, 2) if cgroup_current_mb else round(process_rss_mb, 2),
                "percent": round(percent * 100, 2),
                "state": state,
                "raw_state": raw_state,
                "memory_state": state,  # 레거시 호환
                "current_percent": round(percent * 100, 2),  # 레거시 호환
                
                # 1순위: cgroup (SSoT)
                "cgroup": {
                    "current_mb": round(cgroup_current_mb, 2) if cgroup_current_mb else None,
                    "limit_mb": round(container_limit_mb, 2) if container_limit_mb else None,
                    "percent": round(percent * 100, 2),
                    "version": CgroupMemoryMonitor.get_cgroup_version()
                },
                
                # 2순위: per-process (원인 분해용)
                "process": {
                    "rss_mb": round(process_rss_mb, 2),
                    "rss_percent_of_limit": round((process_rss_mb / container_limit_mb) * 100, 2) if container_limit_mb else None,
                    "note": "Single worker RSS. Use to analyze 'who is eating memory'"
                },
                
                # 3순위: OOM/Pressure 조기 경보
                "pressure": {
                    "oom_events": oom_events,
                    "psi": psi,
                    "oom_kill_count": oom_events.get('oom_kill', 0)
                },
                
                "hysteresis": {
                    "counter": self._state_counter,
                    "enter_count": HYSTERESIS_ENTER_COUNT,
                    "exit_count": HYSTERESIS_EXIT_COUNT
                },
                "container_limit_mb": round(container_limit_mb, 2) if container_limit_mb else None,
                "is_containerized": container_limit_mb is not None,
                "thresholds": {
                    "warning": MEMORY_WARNING_THRESHOLD * 100,
                    "throttle": MEMORY_THROTTLE_THRESHOLD * 100,
                    "critical": MEMORY_CRITICAL_THRESHOLD * 100
                },
                
                # 4순위: 런타임 지표 (pool 상태)
                "pool": {
                    "allocated_mb": round(self.get_allocated_mb(), 2),
                    "leaked_mb": round(self.get_leaked_mb(), 2),
                    "block_count": len(self._allocations)
                },
                "psutil_available": PSUTIL_AVAILABLE,
                "timestamp": datetime.now().isoformat()
            }


# Global memory pool instance
_memory_pool = MemoryPool()

import random
import logging

# 로깅 설정
logger = logging.getLogger(__name__)

def check_backpressure(endpoint_name: str = "unknown") -> Optional[Response]:
    """
    백프레셔 공통 체크 함수.
    모든 메모리 관련 엔드포인트에서 사용.
    
    Returns:
        None: 정상 처리 가능
        Response: 429 또는 503 응답 (즉시 반환해야 함)
    """
    # 디버깅: 총 체크 수 증가
    MemoryPool.total_backpressure_checks += 1
    
    current_status = _memory_pool.get_status()
    state = current_status["state"]
    percent = current_status["percent"]
    
    # C3 테스트: 강제 throttle 모드
    if FORCE_THROTTLE_MODE:
        state = "throttled"
    
    # 디버깅: 최고 메모리 % 추적
    if percent > MemoryPool.peak_memory_percent_seen:
        MemoryPool.peak_memory_percent_seen = percent
    
    # Critical = 최후의 안전장치 (즉시 거부)
    if state == "critical":
        MemoryPool.returned_503_count += 1
        logger.warning(f"[BACKPRESSURE] 503 CRITICAL - endpoint={endpoint_name}, percent={percent}%")
        response = Response({
            "error": "Memory critical - request rejected",
            "endpoint": endpoint_name,
            "status": current_status,
            "action": "reject"
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        response["Retry-After"] = str(THROTTLE_RETRY_AFTER_SEC * 2)
        return response
    
    # Throttle = 백프레셔 (확률 기반 거부)
    if state == "throttled":
        # 디버깅: throttle 상태 진입 카운터
        MemoryPool.throttle_state_seen_count += 1
        
        # 1) 확률 기반 거부 (세마포어와 무관)
        if random.random() < THROTTLE_REJECT_RATE:
            MemoryPool.probability_reject_count += 1
            MemoryPool.returned_429_count += 1
            logger.info(f"[BACKPRESSURE] 429 THROTTLE (probability) - endpoint={endpoint_name}, percent={percent}%")
            response = Response({
                "error": "Memory throttled - backpressure active",
                "endpoint": endpoint_name,
                "status": current_status,
                "action": "throttle",
                "hint": f"Retry after {THROTTLE_RETRY_AFTER_SEC} seconds"
            }, status=status.HTTP_429_TOO_MANY_REQUESTS)
            response["Retry-After"] = str(THROTTLE_RETRY_AFTER_SEC)
            return response
        
        # 2) 세마포어 체크 (추가 보호)
        acquired = _memory_pool._throttle_semaphore.acquire(blocking=False)
        if not acquired:
            # 디버깅: 세마포어 획득 실패 카운터
            MemoryPool.semaphore_acquire_failed_count += 1
            MemoryPool.returned_429_count += 1
            logger.info(f"[BACKPRESSURE] 429 THROTTLE (semaphore) - endpoint={endpoint_name}, percent={percent}%")
            response = Response({
                "error": "Memory throttled - too many concurrent requests",
                "endpoint": endpoint_name,
                "status": current_status,
                "action": "throttle",
                "hint": f"Retry after {THROTTLE_RETRY_AFTER_SEC} seconds"
            }, status=status.HTTP_429_TOO_MANY_REQUESTS)
            response["Retry-After"] = str(THROTTLE_RETRY_AFTER_SEC)
            return response
        
        # 세마포어 획득 성공 - 나중에 해제 필요
        # (호출자가 _memory_pool._throttle_semaphore.release() 호출해야 함)
    
    return None  # 정상 처리 가능


# =============================================================================
# Decorator for enabling/disabling test endpoints
# =============================================================================

def memory_test_enabled(view_func):
    """테스트 엔드포인트 활성화 체크 데코레이터"""
    def wrapper(request, *args, **kwargs):
        if not MEMORY_TEST_ENABLED:
            return JsonResponse({
                "error": "Memory test endpoints are disabled",
                "hint": "Set MEMORY_TEST_ENABLED=true to enable"
            }, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


# =============================================================================
# API Endpoints
# =============================================================================

@csrf_exempt
@api_view(['GET'])
@permission_classes([AllowAny])
def memory_status(request):
    """
    현재 메모리 상태 반환.
    
    Response:
        - current_mb: 현재 RSS (MB)
        - percent: 메모리 사용률 (%)
        - state: normal/warning/throttled/critical
        - pool: 풀 할당 상태
    """
    if not MEMORY_TEST_ENABLED:
        return Response({
            "error": "Memory test endpoints are disabled",
            "hint": "Set MEMORY_TEST_ENABLED=true to enable"
        }, status=status.HTTP_403_FORBIDDEN)
    
    return Response(_memory_pool.get_status())


@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def memory_allocate(request):
    """
    지정 크기의 메모리 할당.
    
    피드백 2 반영:
    - Throttle 상태: 세마포어 + 429 + Retry-After 헤더
    - Critical 상태: 즉시 503 거부 (최후의 안전장치)
    
    Request Body:
        - size_mb: 할당할 크기 (MB)
        - block_id: 블록 식별자 (선택, 기본값 자동생성)
        
    Response:
        - success: 성공 여부
        - memory_before/after: 할당 전후 메모리
    """
    if not MEMORY_TEST_ENABLED:
        return Response({
            "error": "Memory test endpoints are disabled"
        }, status=status.HTTP_403_FORBIDDEN)
    
    size_mb = float(request.data.get("size_mb", 10))
    block_id = request.data.get("block_id", f"block_{time.time()}")
    
    # ==========================================================
    # 공통 백프레셔 체크 (Critical → 503, Throttle → 429)
    # ==========================================================
    backpressure_response = check_backpressure("allocate")
    if backpressure_response:
        return backpressure_response
    
    # Throttle 상태에서 세마포어 획득했으면 처리 후 해제 필요
    current_status = _memory_pool.get_status()
    needs_semaphore_release = current_status["state"] == "throttled"
    
    try:
        if needs_semaphore_release:
            time.sleep(THROTTLE_SLEEP_MS / 1000.0)
        
        result = _memory_pool.allocate(block_id, size_mb)
    finally:
        if needs_semaphore_release:
            _memory_pool._throttle_semaphore.release()
    
    if result["success"]:
        return Response(result)
    else:
        return Response(result, status=status.HTTP_507_INSUFFICIENT_STORAGE)


@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def memory_deallocate(request):
    """
    메모리 블록 해제.
    
    Request Body:
        - block_id: 해제할 블록 식별자
    """
    if not MEMORY_TEST_ENABLED:
        return Response({
            "error": "Memory test endpoints are disabled"
        }, status=status.HTTP_403_FORBIDDEN)
    
    block_id = request.data.get("block_id")
    if not block_id:
        return Response({"error": "block_id required"}, status=status.HTTP_400_BAD_REQUEST)
    
    result = _memory_pool.deallocate(block_id)
    return Response(result)


@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def memory_release_all(request):
    """모든 할당 메모리 해제"""
    if not MEMORY_TEST_ENABLED:
        return Response({
            "error": "Memory test endpoints are disabled"
        }, status=status.HTTP_403_FORBIDDEN)
    
    result = _memory_pool.release_all()
    return Response(result)


@csrf_exempt
@api_view(['GET'])
@permission_classes([AllowAny])
def large_response(request):
    """
    대용량 JSON 응답 생성 (한 번에 메모리 할당).
    
    Query Params:
        - size_mb: 응답 크기 (MB), 기본값 10
    """
    if not MEMORY_TEST_ENABLED:
        return Response({
            "error": "Memory test endpoints are disabled"
        }, status=status.HTTP_403_FORBIDDEN)
    
    size_mb = float(request.query_params.get("size_mb", 10))
    
    # 공통 백프레셔 체크
    backpressure_response = check_backpressure("large_response")
    if backpressure_response:
        return backpressure_response
    
    current_status = _memory_pool.get_status()
    needs_semaphore_release = current_status["state"] == "throttled"
    
    start_time = time.time()
    memory_before = _memory_pool.get_current_memory_mb()
    
    try:
        # 대용량 데이터 생성 (한 번에)
        # 1 문자 = 1 byte (ASCII)
        data_size = int(size_mb * 1024 * 1024)
        large_data = "x" * data_size
        
        response_data = {
            "success": True,
            "size_mb": size_mb,
            "actual_size_bytes": len(large_data),
            "memory_before_mb": round(memory_before, 2),
            "memory_after_mb": round(_memory_pool.get_current_memory_mb(), 2),
            "generation_time_ms": round((time.time() - start_time) * 1000, 2),
            "data_preview": large_data[:100] + "...[truncated]",
            "timestamp": datetime.now().isoformat()
        }
        
        return Response(response_data)
        
    except MemoryError as e:
        return Response({
            "success": False,
            "error": f"MemoryError: {str(e)}",
            "size_mb": size_mb,
            "memory_before_mb": round(memory_before, 2)
        }, status=status.HTTP_507_INSUFFICIENT_STORAGE)


@csrf_exempt
@api_view(['GET'])
@permission_classes([AllowAny])
def streaming_response(request):
    """
    스트리밍 응답 (청크 단위 처리).
    
    Query Params:
        - size_mb: 총 응답 크기 (MB), 기본값 10
        - chunk_kb: 청크 크기 (KB), 기본값 64
    """
    if not MEMORY_TEST_ENABLED:
        return JsonResponse({
            "error": "Memory test endpoints are disabled"
        }, status=403)
    
    size_mb = float(request.GET.get("size_mb", 10))
    chunk_kb = int(request.GET.get("chunk_kb", 64))
    
    # 공통 백프레셔 체크
    backpressure_response = check_backpressure("streaming_response")
    if backpressure_response:
        return backpressure_response
    
    def generate_chunks():
        """청크 제너레이터"""
        total_bytes = int(size_mb * 1024 * 1024)
        chunk_size = chunk_kb * 1024
        chunks_sent = 0
        bytes_sent = 0
        
        while bytes_sent < total_bytes:
            remaining = total_bytes - bytes_sent
            current_chunk_size = min(chunk_size, remaining)
            
            # 청크 생성
            chunk = "x" * current_chunk_size
            yield chunk
            
            bytes_sent += current_chunk_size
            chunks_sent += 1
        
        # 마지막에 메타데이터 청크
        yield json.dumps({
            "__meta__": {
                "total_mb": size_mb,
                "chunks_sent": chunks_sent,
                "chunk_kb": chunk_kb
            }
        })
    
    response = StreamingHttpResponse(
        generate_chunks(),
        content_type="application/octet-stream"
    )
    response["X-Streaming"] = "true"
    response["X-Total-Size-MB"] = str(size_mb)
    return response


@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def leak_simulate(request):
    """
    메모리 누수 시뮬레이션.
    
    Request Body:
        - size_mb: 누수시킬 크기 (MB)
    """
    if not MEMORY_TEST_ENABLED:
        return Response({
            "error": "Memory test endpoints are disabled"
        }, status=status.HTTP_403_FORBIDDEN)
    
    size_mb = float(request.data.get("size_mb", 1))
    result = _memory_pool.simulate_leak(size_mb)
    
    if result["success"]:
        return Response(result)
    else:
        return Response(result, status=status.HTTP_507_INSUFFICIENT_STORAGE)


@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def gc_trigger(request):
    """
    강제 GC 실행.
    
    Request Body:
        - generation: GC 세대 (0, 1, 2), 기본값 2 (full)
    """
    if not MEMORY_TEST_ENABLED:
        return Response({
            "error": "Memory test endpoints are disabled"
        }, status=status.HTTP_403_FORBIDDEN)
    
    generation = int(request.data.get("generation", 2))
    
    memory_before = _memory_pool.get_current_memory_mb()
    start_time = time.time()
    
    # GC 실행
    collected = gc.collect(generation)
    
    gc_time_ms = (time.time() - start_time) * 1000
    memory_after = _memory_pool.get_current_memory_mb()
    
    return Response({
        "success": True,
        "generation": generation,
        "objects_collected": collected,
        "gc_time_ms": round(gc_time_ms, 2),
        "memory_before_mb": round(memory_before, 2),
        "memory_after_mb": round(memory_after, 2),
        "memory_reclaimed_mb": round(memory_before - memory_after, 2)
    })


@csrf_exempt
@api_view(['GET'])
@permission_classes([AllowAny])
def debug_counters(request):
    """
    디버깅 카운터 조회.
    429가 왜 발생하지 않는지 추적.
    """
    if not MEMORY_TEST_ENABLED:
        return Response({
            "error": "Memory test endpoints are disabled"
        }, status=status.HTTP_403_FORBIDDEN)
    
    current_status = _memory_pool.get_status()
    
    return Response({
        "counters": {
            "total_backpressure_checks": MemoryPool.total_backpressure_checks,
            "throttle_state_seen_count": MemoryPool.throttle_state_seen_count,
            "semaphore_acquire_failed_count": MemoryPool.semaphore_acquire_failed_count,
            "probability_reject_count": MemoryPool.probability_reject_count,
            "returned_429_count": MemoryPool.returned_429_count,
            "returned_503_count": MemoryPool.returned_503_count,
            "peak_memory_percent_seen": round(MemoryPool.peak_memory_percent_seen, 2)
        },
        "current_state": current_status["state"],
        "current_percent": current_status["percent"],
        "thresholds": current_status["thresholds"],
        "analysis": {
            "reason_no_429": _analyze_no_429()
        }
    })


def _analyze_no_429() -> str:
    """429가 안 나오는 이유 분석"""
    if MemoryPool.total_backpressure_checks == 0:
        return "No backpressure checks yet"
    
    if MemoryPool.throttle_state_seen_count == 0:
        return f"Memory never reached throttle threshold. Peak: {MemoryPool.peak_memory_percent_seen:.1f}% (threshold: 85%)"
    
    if MemoryPool.returned_429_count > 0:
        return f"429 was returned {MemoryPool.returned_429_count} times"
    
    return "Unknown - throttle state seen but no 429 returned"


@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def reset_debug_counters(request):
    """디버깅 카운터 초기화"""
    if not MEMORY_TEST_ENABLED:
        return Response({
            "error": "Memory test endpoints are disabled"
        }, status=status.HTTP_403_FORBIDDEN)
    
    MemoryPool.total_backpressure_checks = 0
    MemoryPool.throttle_state_seen_count = 0
    MemoryPool.semaphore_acquire_failed_count = 0
    MemoryPool.probability_reject_count = 0
    MemoryPool.returned_429_count = 0
    MemoryPool.returned_503_count = 0
    MemoryPool.peak_memory_percent_seen = 0.0
    
    return Response({"success": True, "message": "Debug counters reset"})


# =============================================================================
# URL Patterns (to be included in urls.py)
# =============================================================================

from django.urls import path

memory_test_urlpatterns = [
    path('status/', memory_status, name='memory-test-status'),
    path('allocate/', memory_allocate, name='memory-test-allocate'),
    path('deallocate/', memory_deallocate, name='memory-test-deallocate'),
    path('release-all/', memory_release_all, name='memory-test-release-all'),
    path('large-response/', large_response, name='memory-test-large-response'),
    path('streaming-response/', streaming_response, name='memory-test-streaming-response'),
    path('leak-simulate/', leak_simulate, name='memory-test-leak-simulate'),
    path('gc-trigger/', gc_trigger, name='memory-test-gc-trigger'),
    path('debug-counters/', debug_counters, name='memory-test-debug-counters'),
    path('reset-debug-counters/', reset_debug_counters, name='memory-test-reset-debug-counters'),
]
