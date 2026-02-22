"""
Stage 36 Real: 실제 HTTP 메모리 압박 테스트

Purpose: 실제 HTTP 요청을 통한 메모리 압박 테스트
- Docker 컨테이너 메모리 제한 환경에서 실행
- 실제 Django 엔드포인트로 HTTP 요청
- cAdvisor/docker stats로 실제 RSS 모니터링

테스트 시나리오:
  SC-36R-1: 점진적 메모리 할당 (실제 HTTP)
  SC-36R-2: 대용량 응답 (한방 vs 스트리밍)
  SC-36R-3: 메모리 누수 시뮬레이션
  SC-36R-4: GC 트리거 및 회복

검증:
  - [ ] OOM Kill 발생 0회 (컨테이너 재시작 없음)
  - [ ] 70% 경고 정확히 ±2%에서 발생
  - [ ] 85% 스로틀링 정확히 ±2%에서 발생
  - [ ] 스트리밍 성공 / 한방 할당 실패 (대용량)
  - [ ] GC 후 메모리 회수 확인

실행:
    # Docker Compose로 실행
    docker-compose -f docker-compose.stage36-real.yml up

    # 또는 로컬에서 Locust 실행
    locust -f load_tests/scenarios/stage36_real_http.py \\
        --host=http://localhost:8000 \\
        --users=20 --spawn-rate=5 --run-time=5m \\
        --headless --html=stage36_real_report.html
"""

import os
import sys
import time
import random
import json
import threading
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    from locust import HttpUser, task, between, tag, events, LoadTestShape
    LOCUST_AVAILABLE = True
except ImportError:
    LOCUST_AVAILABLE = False
    HttpUser = object
    task = lambda weight=1: lambda f: f
    between = lambda a, b: None
    tag = lambda *args: lambda f: f
    events = None
    LoadTestShape = object

# requests for standalone tests
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


STAGE_NAME = "[Stage36-Real]"

# =============================================================================
# Configuration
# =============================================================================

# Test duration scaling
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "300"))

# Memory thresholds (matching the server side)
MEMORY_WARNING_THRESHOLD = 0.70
MEMORY_THROTTLE_THRESHOLD = 0.85
MEMORY_CRITICAL_THRESHOLD = 0.95

# Tolerance for threshold triggers (±2%)
THRESHOLD_TOLERANCE = 0.02

# Large payload sizes
SMALL_PAYLOAD_MB = 1
MEDIUM_PAYLOAD_MB = 5
LARGE_PAYLOAD_MB = 20
HUGE_PAYLOAD_MB = 50  # Should fail with non-streaming


# =============================================================================
# Statistics Tracking
# =============================================================================

@dataclass
class RealMemoryTestStats:
    """실제 HTTP 테스트 통계"""
    
    start_time: Optional[float] = None
    
    # Memory status tracking
    memory_samples: List[Dict[str, Any]] = field(default_factory=list)
    peak_memory_mb: float = 0.0
    peak_percent: float = 0.0
    
    # Threshold events
    warning_triggered: bool = False
    warning_percent: float = 0.0
    warning_time: Optional[float] = None
    
    throttle_triggered: bool = False
    throttle_percent: float = 0.0
    throttle_time: Optional[float] = None
    
    critical_triggered: bool = False
    critical_percent: float = 0.0
    critical_time: Optional[float] = None
    
    # =============================================
    # 피드백 1,3: 실패율 3개 분리
    # - Expected Reject: critical 상태에서 의도된 거부 (정상 동작)
    # - Expected 503: 메모리 부족으로 의도된 실패 (정상 동작)
    # - Unexpected Error: 네트워크/시스템 오류 (0% 목표)
    # =============================================
    
    # Request counts
    total_requests: int = 0
    successful_requests: int = 0
    
    # Expected failures (정상 동작)
    expected_reject_count: int = 0     # critical 상태 거부 (의도됨)
    expected_throttle_count: int = 0   # throttle 상태 429 응답 (의도됨)
    expected_503_count: int = 0        # 메모리 부족 503 (의도됨)
    
    # Unexpected errors (0% 목표)
    unexpected_network_errors: int = 0  # 네트워크 오류
    unexpected_timeout_errors: int = 0  # 타임아웃 오류
    unexpected_other_errors: int = 0    # 기타 예상치 못한 오류
    
    # =============================================
    # Phase 1: Unexpected Error 상세 분류
    # 원인별 버킷으로 나눠서 "고쳐야 할지/튜닝인지" 판단
    # =============================================
    unexpected_by_type: Dict[str, int] = field(default_factory=lambda: {
        "timeout": 0,           # 타임아웃 (connect/read)
        "connection_reset": 0,   # 연결 리셋 (서버가 끊음)
        "connection_refused": 0, # 연결 거부 (포트 안열림)
        "broken_pipe": 0,        # 파이프 끊김 (write 중 끊김)
        "json_decode": 0,        # JSON 파싱 실패
        "http_5xx_unknown": 0,   # 500/502 등 예상 외 5xx
        "http_4xx_unknown": 0,   # 예상 외 4xx
        "ssl_error": 0,          # SSL/TLS 에러
        "other": 0               # 분류 안된 기타
    })
    unexpected_details: List[Dict[str, Any]] = field(default_factory=list)  # 상세 로그
    
    # Legacy (호환성)
    throttled_responses: int = 0  # deprecated: expected_throttle_count 사용
    rejected_responses: int = 0   # deprecated: expected_reject_count 사용
    oom_errors: int = 0           # 507 Insufficient Storage
    other_errors: int = 0         # deprecated: unexpected_* 사용
    
    # =============================================
    # 피드백 4: 응답시간 분리 추적
    # 2xx/throttle/reject 각각 별도 추적
    # =============================================
    response_times_2xx: List[float] = field(default_factory=list)
    response_times_throttle: List[float] = field(default_factory=list)
    response_times_reject: List[float] = field(default_factory=list)
    
    # Large payload tests
    large_payload_streaming_success: int = 0
    large_payload_streaming_fail: int = 0
    large_payload_nonstreaming_success: int = 0
    large_payload_nonstreaming_fail: int = 0
    
    # GC tests
    gc_triggers: int = 0
    gc_total_reclaimed_mb: float = 0.0
    gc_max_pause_ms: float = 0.0
    
    # Leak simulation
    leaks_created: int = 0
    total_leaked_mb: float = 0.0
    
    def reset(self):
        """Reset statistics"""
        self.start_time = time.time()
        self.memory_samples.clear()
        self.peak_memory_mb = 0.0
        self.peak_percent = 0.0
        self.warning_triggered = False
        self.warning_percent = 0.0
        self.warning_time = None
        self.throttle_triggered = False
        self.throttle_percent = 0.0
        self.throttle_time = None
        self.critical_triggered = False
        self.critical_percent = 0.0
        self.critical_time = None
        self.total_requests = 0
        self.successful_requests = 0
        # New: Expected failures
        self.expected_reject_count = 0
        self.expected_throttle_count = 0
        self.expected_503_count = 0
        # New: Unexpected errors
        self.unexpected_network_errors = 0
        self.unexpected_timeout_errors = 0
        self.unexpected_other_errors = 0
        # Phase 1: 상세 분류 초기화
        self.unexpected_by_type = {
            "timeout": 0,
            "connection_reset": 0,
            "connection_refused": 0,
            "broken_pipe": 0,
            "json_decode": 0,
            "http_5xx_unknown": 0,
            "http_4xx_unknown": 0,
            "ssl_error": 0,
            "other": 0
        }
        self.unexpected_details = []
        # Legacy
        self.throttled_responses = 0
        self.rejected_responses = 0
        self.oom_errors = 0
        self.other_errors = 0
        # New: Response time lists
        self.response_times_2xx = []
        self.response_times_throttle = []
        self.response_times_reject = []
        # Payload tests
        self.large_payload_streaming_success = 0
        self.large_payload_streaming_fail = 0
        self.large_payload_nonstreaming_success = 0
        self.large_payload_nonstreaming_fail = 0
        self.gc_triggers = 0
        self.gc_total_reclaimed_mb = 0.0
        self.gc_max_pause_ms = 0.0
        self.leaks_created = 0
        self.total_leaked_mb = 0.0
    
    def get_error_rate_breakdown(self) -> Dict[str, Any]:
        """
        피드백 1: 실패율을 3개로 분리해서 반환
        - Expected Reject Rate: 의도된 거부 (critical 상태)
        - Expected 503 Rate: 의도된 503 (메모리 부족)
        - Unexpected Error Rate: 예상치 못한 오류 (0% 목표)
        """
        total = self.total_requests or 1
        
        expected_reject = self.expected_reject_count
        expected_throttle = self.expected_throttle_count
        expected_503 = self.expected_503_count
        unexpected = (self.unexpected_network_errors + 
                      self.unexpected_timeout_errors + 
                      self.unexpected_other_errors)
        
        return {
            "total_requests": self.total_requests,
            "successful_2xx": self.successful_requests,
            "success_rate": round(self.successful_requests / total * 100, 2),
            
            # Expected (의도된 실패 = 정상 동작)
            "expected_reject_count": expected_reject,
            "expected_reject_rate": round(expected_reject / total * 100, 2),
            
            "expected_throttle_count": expected_throttle,
            "expected_throttle_rate": round(expected_throttle / total * 100, 2),
            
            "expected_503_count": expected_503,
            "expected_503_rate": round(expected_503 / total * 100, 2),
            
            # Unexpected (0% 목표)
            "unexpected_network_errors": self.unexpected_network_errors,
            "unexpected_timeout_errors": self.unexpected_timeout_errors,
            "unexpected_other_errors": self.unexpected_other_errors,
            "unexpected_total": unexpected,
            "unexpected_error_rate": round(unexpected / total * 100, 2),
            
            # PASS 조건: unexpected_error_rate < 1%
            "unexpected_error_pass": unexpected / total < 0.01
        }
    
    def get_response_time_breakdown(self) -> Dict[str, Any]:
        """
        피드백 4: 응답시간을 요청 유형별로 분리
        - 2xx: 정상 응답
        - throttle: 429 응답 (백프레셔)
        - reject: 503 거부 응답 (빠르게 떨어져야 함)
        """
        import statistics
        
        def calc_percentiles(times: List[float]) -> Dict[str, float]:
            if not times:
                return {"count": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0}
            sorted_times = sorted(times)
            n = len(sorted_times)
            return {
                "count": n,
                "avg": round(statistics.mean(sorted_times), 2),
                "p50": round(sorted_times[int(n * 0.50)] if n > 0 else 0, 2),
                "p95": round(sorted_times[int(n * 0.95)] if n > 0 else 0, 2),
                "p99": round(sorted_times[int(n * 0.99)] if n > 0 else 0, 2)
            }
        
        return {
            "2xx_responses": calc_percentiles(self.response_times_2xx),
            "throttle_429": calc_percentiles(self.response_times_throttle),
            "reject_503": calc_percentiles(self.response_times_reject)
        }
    
    def classify_error(self, exception: Optional[Exception], status_code: int, 
                       endpoint: str, response_text: str = "") -> str:
        """
        Phase 1: 에러를 상세 유형으로 분류
        반환값: 에러 유형 키
        """
        error_type = "other"
        error_msg = str(exception) if exception else ""
        error_class = type(exception).__name__ if exception else "None"
        
        # 예외 기반 분류
        if exception:
            error_lower = error_msg.lower()
            class_lower = error_class.lower()
            
            if "timeout" in error_lower or "timed out" in error_lower:
                error_type = "timeout"
            elif "connection reset" in error_lower or "reset by peer" in error_lower:
                error_type = "connection_reset"
            elif "connection refused" in error_lower or "errno 111" in error_lower:
                error_type = "connection_refused"
            elif "broken pipe" in error_lower or "errno 32" in error_lower:
                error_type = "broken_pipe"
            elif "json" in class_lower or "decode" in error_lower:
                error_type = "json_decode"
            elif "ssl" in error_lower or "certificate" in error_lower:
                error_type = "ssl_error"
        
        # HTTP 상태 코드 기반 분류 (예외가 없거나 분류 안된 경우)
        if error_type == "other" and status_code > 0:
            if 500 <= status_code < 600:
                error_type = "http_5xx_unknown"
            elif 400 <= status_code < 500:
                error_type = "http_4xx_unknown"
        
        # 카운터 증가
        self.unexpected_by_type[error_type] = self.unexpected_by_type.get(error_type, 0) + 1
        
        # 상세 로그 (최대 100개까지만)
        if len(self.unexpected_details) < 100:
            self.unexpected_details.append({
                "type": error_type,
                "exception_class": error_class,
                "message": error_msg[:200],  # 메시지 잘라서 저장
                "status_code": status_code,
                "endpoint": endpoint,
                "timestamp": time.time()
            })
        
        return error_type
    
    def get_unexpected_error_breakdown(self) -> Dict[str, Any]:
        """
        Phase 1: Unexpected Error Top N 분석
        """
        total_unexpected = sum(self.unexpected_by_type.values())
        
        # 유형별 정렬 (많은 순)
        sorted_types = sorted(
            self.unexpected_by_type.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        # Top 3 원인
        top3 = sorted_types[:3]
        
        return {
            "total_unexpected": total_unexpected,
            "by_type": dict(sorted_types),
            "top3_causes": [
                {"type": t, "count": c, "percent": round(c / max(total_unexpected, 1) * 100, 1)}
                for t, c in top3 if c > 0
            ],
            "sample_errors": self.unexpected_details[:10],  # 샘플 10개
            "tuning_recommendation": self._get_tuning_recommendation(sorted_types)
        }
    
    def _get_tuning_recommendation(self, sorted_types: List[tuple]) -> str:
        """
        상위 에러 유형에 따른 튜닝 권장사항
        """
        if not sorted_types or sorted_types[0][1] == 0:
            return "No unexpected errors - excellent!"
        
        top_type = sorted_types[0][0]
        recommendations = {
            "timeout": "Increase gunicorn timeout, Locust read_timeout. Check slow endpoints.",
            "connection_reset": "Increase somaxconn, check ulimit -n, add keep-alive.",
            "connection_refused": "Check if server is running, port binding, firewall.",
            "broken_pipe": "Client disconnected early. Increase client timeout or check streaming.",
            "json_decode": "Server returned invalid JSON. Check error response format.",
            "http_5xx_unknown": "Unhandled server error. Check application logs.",
            "http_4xx_unknown": "Unexpected client error. Check request format.",
            "ssl_error": "SSL/TLS configuration issue. Check certificates.",
            "other": "Unknown error. Check detailed logs."
        }
        return recommendations.get(top_type, "Review error details.")
    
    def record_memory_sample(self, status: Dict[str, Any]):
        """Record a memory status sample"""
        self.memory_samples.append({
            "time": time.time(),
            **status
        })
        
        current_mb = status.get("current_mb", 0)
        current_percent = status.get("percent", 0)
        
        if current_mb > self.peak_memory_mb:
            self.peak_memory_mb = current_mb
        if current_percent > self.peak_percent:
            self.peak_percent = current_percent
        
        # Check threshold transitions
        state = status.get("state", "normal")
        
        if state == "warning" and not self.warning_triggered:
            self.warning_triggered = True
            self.warning_percent = current_percent
            self.warning_time = time.time()
        
        if state == "throttled" and not self.throttle_triggered:
            self.throttle_triggered = True
            self.throttle_percent = current_percent
            self.throttle_time = time.time()
        
        if state == "critical" and not self.critical_triggered:
            self.critical_triggered = True
            self.critical_percent = current_percent
            self.critical_time = time.time()
    
    def get_verification_results(self) -> Dict[str, Any]:
        """Get verification results against criteria"""
        return {
            # OOM Prevention
            "oom_prevented": self.oom_errors == 0,
            "oom_count": self.oom_errors,
            
            # Warning threshold (70% ±2%)
            "warning_triggered": self.warning_triggered,
            "warning_percent": self.warning_percent,
            "warning_accurate": (
                self.warning_triggered and 
                abs(self.warning_percent - MEMORY_WARNING_THRESHOLD * 100) <= THRESHOLD_TOLERANCE * 100
            ),
            
            # Throttle threshold (85% ±2%)
            "throttle_triggered": self.throttle_triggered,
            "throttle_percent": self.throttle_percent,
            "throttle_accurate": (
                self.throttle_triggered and
                abs(self.throttle_percent - MEMORY_THROTTLE_THRESHOLD * 100) <= THRESHOLD_TOLERANCE * 100
            ),
            
            # Large payload: streaming should succeed, non-streaming should fail for huge
            "streaming_works": self.large_payload_streaming_success > 0,
            "nonstreaming_fails_on_huge": self.large_payload_nonstreaming_fail > 0,
            
            # GC recovery
            "gc_effective": self.gc_total_reclaimed_mb > 0,
            
            # Overall
            "pass": (
                self.oom_errors == 0 and
                self.rejected_responses == 0  # No critical rejections
            )
        }


# Global stats
_stats = RealMemoryTestStats()
_stats_lock = threading.Lock()


def get_stats() -> RealMemoryTestStats:
    return _stats


def reset_stats():
    with _stats_lock:
        _stats.reset()


# =============================================================================
# Memory Monitor Thread
# =============================================================================

class MemoryMonitor:
    """백그라운드에서 메모리 상태를 주기적으로 모니터링"""
    
    def __init__(self, base_url: str, interval: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.interval = interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
    
    def start(self):
        """Start monitoring"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop monitoring"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
    
    def _monitor_loop(self):
        """Monitoring loop"""
        while self._running:
            try:
                response = requests.get(
                    f"{self.base_url}/api/test/memory/status/",
                    timeout=5
                )
                if response.status_code == 200:
                    status = response.json()
                    with _stats_lock:
                        _stats.record_memory_sample(status)
            except Exception as e:
                logging.warning(f"Memory monitor error: {e}")
            
            time.sleep(self.interval)


# =============================================================================
# Locust User Classes
# =============================================================================

if LOCUST_AVAILABLE:
    
    class RealMemoryPressureUser(HttpUser):
        """실제 HTTP 기반 메모리 압박 테스트 사용자"""
        
        wait_time = between(0.5, 1.5)
        
        def on_start(self):
            """사용자 시작 시 초기화"""
            self.request_count = 0
            self.allocated_blocks: List[str] = []
        
        def on_stop(self):
            """사용자 종료 시 정리"""
            # 할당한 블록 해제
            for block_id in self.allocated_blocks:
                try:
                    self.client.post(
                        "/api/test/memory/deallocate/",
                        json={"block_id": block_id}
                    )
                except:
                    pass
        
        @task(5)
        @tag("allocate")
        def allocate_memory(self):
            """
            메모리 할당 테스트
            
            피드백 1,2 반영:
            - 429 (throttle) vs 503 (reject) 구분
            - Expected vs Unexpected 에러 분리
            - 응답시간 유형별 기록
            """
            block_id = f"user_{id(self)}_{self.request_count}"
            self.request_count += 1
            
            # 랜덤 크기 (1-10MB)
            size_mb = random.uniform(1, 10)
            
            start_time = time.time()
            
            with self.client.post(
                "/api/test/memory/allocate/",
                json={"block_id": block_id, "size_mb": size_mb},
                catch_response=True
            ) as response:
                elapsed_ms = (time.time() - start_time) * 1000
                
                with _stats_lock:
                    _stats.total_requests += 1
                
                if response.status_code == 200:
                    # 성공
                    self.allocated_blocks.append(block_id)
                    with _stats_lock:
                        _stats.successful_requests += 1
                        _stats.response_times_2xx.append(elapsed_ms)
                    response.success()
                
                elif response.status_code == 429:
                    # 피드백 2: Throttle (429 + Retry-After)
                    # Expected failure = 정상 동작
                    retry_after = response.headers.get("Retry-After", "?")
                    with _stats_lock:
                        _stats.expected_throttle_count += 1
                        _stats.throttled_responses += 1  # legacy
                        _stats.response_times_throttle.append(elapsed_ms)
                    response.failure(f"Throttled (429, retry: {retry_after}s)")
                
                elif response.status_code == 503:
                    # Critical rejection (503)
                    # Expected failure = 정상 동작 (최후의 안전장치)
                    try:
                        data = response.json()
                        action = data.get("action", "")
                        
                        if action == "reject":
                            with _stats_lock:
                                _stats.expected_reject_count += 1
                                _stats.rejected_responses += 1  # legacy
                                _stats.response_times_reject.append(elapsed_ms)
                            response.failure("Rejected (critical)")
                        else:
                            # 기타 503 (예상치 못한) - Phase 1: 상세 분류
                            with _stats_lock:
                                _stats.unexpected_other_errors += 1
                                _stats.classify_error(None, 503, "/api/test/memory/allocate/", 
                                                      response.text[:200] if response.text else "")
                            response.failure("503 unknown")
                    except Exception as e:
                        with _stats_lock:
                            _stats.unexpected_other_errors += 1
                            _stats.classify_error(e, 503, "/api/test/memory/allocate/")
                        response.failure("503 parse error")
                
                elif response.status_code == 507:
                    # OOM - Expected failure
                    with _stats_lock:
                        _stats.oom_errors += 1
                        _stats.expected_503_count += 1
                    response.failure("OOM 507")
                
                elif response.status_code == 0:
                    # 네트워크 에러 (피드백 3) - Phase 1: 상세 분류
                    with _stats_lock:
                        _stats.unexpected_network_errors += 1
                        # response.error에서 예외 정보 추출 시도
                        exc = getattr(response, 'error', None)
                        _stats.classify_error(exc, 0, "/api/test/memory/allocate/")
                    response.failure("Network error")
                
                else:
                    # 기타 예상치 못한 에러 - Phase 1: 상세 분류
                    with _stats_lock:
                        _stats.unexpected_other_errors += 1
                        _stats.classify_error(None, response.status_code, "/api/test/memory/allocate/",
                                             response.text[:200] if response.text else "")
                    response.failure(f"Unexpected {response.status_code}")
        
        @task(3)
        @tag("deallocate")
        def deallocate_memory(self):
            """메모리 해제 테스트"""
            if not self.allocated_blocks:
                return
            
            block_id = self.allocated_blocks.pop()
            
            with self.client.post(
                "/api/test/memory/deallocate/",
                json={"block_id": block_id},
                catch_response=True
            ) as response:
                with _stats_lock:
                    _stats.total_requests += 1
                
                if response.status_code == 200:
                    with _stats_lock:
                        _stats.successful_requests += 1
                    response.success()
                else:
                    response.failure(f"Error {response.status_code}")
        
        @task(2)
        @tag("large_payload", "streaming")
        def large_payload_streaming(self):
            """스트리밍 대용량 응답 테스트"""
            size_mb = random.choice([SMALL_PAYLOAD_MB, MEDIUM_PAYLOAD_MB, LARGE_PAYLOAD_MB])
            
            with self.client.get(
                f"/api/test/memory/streaming-response/?size_mb={size_mb}",
                catch_response=True,
                stream=True  # 스트리밍 모드
            ) as response:
                with _stats_lock:
                    _stats.total_requests += 1
                
                if response.status_code == 200:
                    # 스트리밍으로 읽기 (메모리 효율적)
                    total_read = 0
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        total_read += len(chunk)
                    
                    with _stats_lock:
                        _stats.successful_requests += 1
                        _stats.large_payload_streaming_success += 1
                    response.success()
                else:
                    with _stats_lock:
                        _stats.large_payload_streaming_fail += 1
                    response.failure(f"Error {response.status_code}")
        
        @task(1)
        @tag("large_payload", "nonstreaming")
        def large_payload_nonstreaming(self):
            """비스트리밍 대용량 응답 테스트 (실패 예상)"""
            # 큰 사이즈는 실패해야 정상
            size_mb = random.choice([MEDIUM_PAYLOAD_MB, LARGE_PAYLOAD_MB])
            
            with self.client.get(
                f"/api/test/memory/large-response/?size_mb={size_mb}",
                catch_response=True
            ) as response:
                with _stats_lock:
                    _stats.total_requests += 1
                
                if response.status_code == 200:
                    with _stats_lock:
                        _stats.successful_requests += 1
                        _stats.large_payload_nonstreaming_success += 1
                    response.success()
                elif response.status_code in [503, 507]:
                    # 메모리 부족으로 실패 = 의도된 동작
                    with _stats_lock:
                        _stats.large_payload_nonstreaming_fail += 1
                    # 이건 "정상 실패"라서 failure로 기록하지만 예상된 동작
                    response.failure(f"Expected failure {response.status_code}")
                else:
                    response.failure(f"Unexpected error {response.status_code}")
        
        @task(1)
        @tag("gc")
        def trigger_gc(self):
            """GC 트리거 테스트"""
            with self.client.post(
                "/api/test/memory/gc-trigger/",
                json={"generation": 2},
                catch_response=True
            ) as response:
                with _stats_lock:
                    _stats.total_requests += 1
                
                if response.status_code == 200:
                    data = response.json()
                    with _stats_lock:
                        _stats.successful_requests += 1
                        _stats.gc_triggers += 1
                        reclaimed = data.get("memory_reclaimed_mb", 0)
                        _stats.gc_total_reclaimed_mb += reclaimed
                        gc_time = data.get("gc_time_ms", 0)
                        if gc_time > _stats.gc_max_pause_ms:
                            _stats.gc_max_pause_ms = gc_time
                    response.success()
                else:
                    response.failure(f"Error {response.status_code}")
        
        @task(1)
        @tag("status")
        def check_status(self):
            """메모리 상태 확인"""
            with self.client.get(
                "/api/test/memory/status/",
                catch_response=True
            ) as response:
                with _stats_lock:
                    _stats.total_requests += 1
                
                if response.status_code == 200:
                    status = response.json()
                    with _stats_lock:
                        _stats.record_memory_sample(status)
                        _stats.successful_requests += 1
                    response.success()
                else:
                    response.failure(f"Error {response.status_code}")
    
    
    class MemoryPressureLoadShape(LoadTestShape):
        """메모리 압박 테스트용 로드 쉐이프"""
        
        # 단계별 부하 패턴
        # 1. Baseline (낮은 부하로 시작)
        # 2. Gradual increase (메모리 점진 증가)
        # 3. High pressure (높은 부하)
        # 4. Recovery (부하 감소, GC 동작 확인)
        # 5. Cooldown
        
        stages = [
            {"duration": 30, "users": 5, "spawn_rate": 2},   # Baseline
            {"duration": 90, "users": 20, "spawn_rate": 5},  # Gradual increase
            {"duration": 150, "users": 30, "spawn_rate": 5}, # High pressure
            {"duration": 240, "users": 15, "spawn_rate": 3}, # Recovery
            {"duration": 300, "users": 5, "spawn_rate": 2},  # Cooldown
        ]
        
        def tick(self) -> Optional[Tuple[int, float]]:
            run_time = self.get_run_time()
            
            for stage in self.stages:
                if run_time < stage["duration"]:
                    return (stage["users"], stage["spawn_rate"])
            
            return None


# =============================================================================
# Standalone Test Functions (for non-Locust execution)
# =============================================================================

def run_gradual_increase_test(base_url: str, target_percent: float = 90) -> Dict[str, Any]:
    """점진적 메모리 증가 테스트"""
    print(f"\n{STAGE_NAME} Running gradual memory increase test...")
    
    results = {
        "target_percent": target_percent,
        "allocations": [],
        "final_status": None,
        "warning_at": None,
        "throttle_at": None,
        "error": None
    }
    
    try:
        block_count = 0
        
        while True:
            # 현재 상태 확인
            status_resp = requests.get(f"{base_url}/api/test/memory/status/")
            if status_resp.status_code != 200:
                results["error"] = f"Status check failed: {status_resp.status_code}"
                break
            
            status = status_resp.json()
            current_percent = status.get("percent", 0)
            
            print(f"  Memory: {current_percent:.1f}% ({status.get('state', 'unknown')})")
            
            # 목표 도달 확인
            if current_percent >= target_percent:
                print(f"  Target {target_percent}% reached!")
                break
            
            # Warning 확인
            if status.get("state") == "warning" and not results["warning_at"]:
                results["warning_at"] = current_percent
                print(f"  ⚠️ Warning triggered at {current_percent:.1f}%")
            
            # Throttle 확인
            if status.get("state") == "throttled" and not results["throttle_at"]:
                results["throttle_at"] = current_percent
                print(f"  🔶 Throttle triggered at {current_percent:.1f}%")
            
            # Critical이면 중단
            if status.get("state") == "critical":
                print("  🔴 Critical - stopping allocation")
                break
            
            # 메모리 할당 (10MB씩)
            alloc_resp = requests.post(
                f"{base_url}/api/test/memory/allocate/",
                json={"block_id": f"test_block_{block_count}", "size_mb": 10}
            )
            
            if alloc_resp.status_code == 200:
                block_count += 1
                results["allocations"].append({
                    "block": block_count,
                    "percent": current_percent
                })
            elif alloc_resp.status_code == 507:
                print(f"  OOM at {current_percent:.1f}%")
                results["error"] = "OOM occurred"
                break
            else:
                print(f"  Allocation failed: {alloc_resp.status_code}")
                break
            
            time.sleep(0.5)
        
        # 최종 상태
        status_resp = requests.get(f"{base_url}/api/test/memory/status/")
        if status_resp.status_code == 200:
            results["final_status"] = status_resp.json()
        
    except Exception as e:
        results["error"] = str(e)
    
    return results


def run_large_payload_comparison_test(base_url: str) -> Dict[str, Any]:
    """대용량 페이로드: 스트리밍 vs 비스트리밍 비교"""
    print(f"\n{STAGE_NAME} Running large payload comparison test...")
    
    results = {
        "streaming": {"success": [], "fail": []},
        "nonstreaming": {"success": [], "fail": []}
    }
    
    sizes = [5, 10, 20, 30]  # MB
    
    for size in sizes:
        print(f"  Testing {size}MB payload...")
        
        # 비스트리밍 먼저
        try:
            resp = requests.get(
                f"{base_url}/api/test/memory/large-response/?size_mb={size}",
                timeout=30
            )
            if resp.status_code == 200:
                results["nonstreaming"]["success"].append(size)
                print(f"    Non-streaming {size}MB: ✅ Success")
            else:
                results["nonstreaming"]["fail"].append(size)
                print(f"    Non-streaming {size}MB: ❌ Failed ({resp.status_code})")
        except Exception as e:
            results["nonstreaming"]["fail"].append(size)
            print(f"    Non-streaming {size}MB: ❌ Error ({e})")
        
        # 스트리밍
        try:
            resp = requests.get(
                f"{base_url}/api/test/memory/streaming-response/?size_mb={size}",
                stream=True,
                timeout=60
            )
            if resp.status_code == 200:
                # 스트리밍으로 읽기
                total = 0
                for chunk in resp.iter_content(chunk_size=64*1024):
                    total += len(chunk)
                results["streaming"]["success"].append(size)
                print(f"    Streaming {size}MB: ✅ Success ({total} bytes)")
            else:
                results["streaming"]["fail"].append(size)
                print(f"    Streaming {size}MB: ❌ Failed ({resp.status_code})")
        except Exception as e:
            results["streaming"]["fail"].append(size)
            print(f"    Streaming {size}MB: ❌ Error ({e})")
        
        # 정리
        requests.post(f"{base_url}/api/test/memory/release-all/")
        time.sleep(1)
    
    return results


def run_gc_recovery_test(base_url: str) -> Dict[str, Any]:
    """GC 회복 테스트"""
    print(f"\n{STAGE_NAME} Running GC recovery test...")
    
    results = {
        "allocations": 0,
        "memory_before_gc": 0,
        "memory_after_gc": 0,
        "gc_reclaimed": 0,
        "gc_time_ms": 0
    }
    
    try:
        # 메모리 할당
        print("  Allocating memory...")
        for i in range(10):
            requests.post(
                f"{base_url}/api/test/memory/allocate/",
                json={"block_id": f"gc_test_{i}", "size_mb": 20}
            )
            results["allocations"] += 1
        
        # GC 전 상태
        status = requests.get(f"{base_url}/api/test/memory/status/").json()
        results["memory_before_gc"] = status.get("current_mb", 0)
        print(f"  Memory before GC: {results['memory_before_gc']:.1f}MB")
        
        # 일부 해제 (GC 대상 생성)
        for i in range(5):
            requests.post(
                f"{base_url}/api/test/memory/deallocate/",
                json={"block_id": f"gc_test_{i}"}
            )
        
        # GC 트리거
        print("  Triggering GC...")
        gc_resp = requests.post(
            f"{base_url}/api/test/memory/gc-trigger/",
            json={"generation": 2}
        ).json()
        
        results["gc_reclaimed"] = gc_resp.get("memory_reclaimed_mb", 0)
        results["gc_time_ms"] = gc_resp.get("gc_time_ms", 0)
        
        # GC 후 상태
        status = requests.get(f"{base_url}/api/test/memory/status/").json()
        results["memory_after_gc"] = status.get("current_mb", 0)
        
        print(f"  Memory after GC: {results['memory_after_gc']:.1f}MB")
        print(f"  GC reclaimed: {results['gc_reclaimed']:.1f}MB in {results['gc_time_ms']:.2f}ms")
        
        # 정리
        requests.post(f"{base_url}/api/test/memory/release-all/")
        
    except Exception as e:
        results["error"] = str(e)
    
    return results


def run_full_stage36_real_test(base_url: str = "http://localhost:8000") -> Dict[str, Any]:
    """전체 Stage 36 Real 테스트 실행"""
    print(f"\n{'='*60}")
    print(f"{STAGE_NAME} Stage 36 Real Memory Pressure Test")
    print(f"{'='*60}")
    print(f"Target: {base_url}")
    
    results = {
        "stage": "36-real",
        "name": "Real HTTP Memory Pressure Test",
        "start_time": datetime.now().isoformat(),
        "tests": {}
    }
    
    # 초기화
    print("\nInitializing...")
    try:
        requests.post(f"{base_url}/api/test/memory/release-all/")
    except:
        print("  Warning: Could not reset memory pool")
    
    # 테스트 1: 점진적 증가
    results["tests"]["gradual_increase"] = run_gradual_increase_test(base_url, target_percent=85)
    requests.post(f"{base_url}/api/test/memory/release-all/")
    time.sleep(2)
    
    # 테스트 2: 대용량 페이로드 비교
    results["tests"]["large_payload"] = run_large_payload_comparison_test(base_url)
    requests.post(f"{base_url}/api/test/memory/release-all/")
    time.sleep(2)
    
    # 테스트 3: GC 회복
    results["tests"]["gc_recovery"] = run_gc_recovery_test(base_url)
    
    # 최종 정리
    requests.post(f"{base_url}/api/test/memory/release-all/")
    
    results["end_time"] = datetime.now().isoformat()
    
    # 결과 요약
    print(f"\n{'='*60}")
    print(f"{STAGE_NAME} Test Complete")
    print(f"{'='*60}")
    
    grad = results["tests"]["gradual_increase"]
    print("\n1. Gradual Increase:")
    print(f"   Warning at: {grad.get('warning_at', 'N/A')}%")
    print(f"   Throttle at: {grad.get('throttle_at', 'N/A')}%")
    
    payload = results["tests"]["large_payload"]
    print("\n2. Large Payload:")
    print(f"   Streaming success: {payload['streaming']['success']}")
    print(f"   Non-streaming fail: {payload['nonstreaming']['fail']}")
    
    gc = results["tests"]["gc_recovery"]
    print("\n3. GC Recovery:")
    print(f"   Reclaimed: {gc.get('gc_reclaimed', 0):.1f}MB")
    print(f"   Time: {gc.get('gc_time_ms', 0):.2f}ms")
    
    return results


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Stage 36 Real HTTP Memory Test")
    parser.add_argument("--host", default="http://localhost:8000", help="Target host")
    args = parser.parse_args()
    
    results = run_full_stage36_real_test(args.host)
    
    # Save results
    output_file = f"stage36_real_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_file}")
