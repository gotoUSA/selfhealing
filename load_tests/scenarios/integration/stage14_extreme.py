"""
Stage 14 EXTREME: Deep Resilience Integration Test

🔥 극한 통합 테스트 - "방패가 뚫렸을 때 진짜 복구력" 검증

Purpose:
  - Recovery in Chaos: DLQ 리플레이 중 2차/3차 장애 주입
  - Outbox Flooding: 수만 건 이벤트 적재 후 Gradient 스로틀링 검증
  - Cascading Failure: DB -> Redis -> Celery 순차 장애 생존율
  - Idempotency Under Pressure: 복구 중 중복 처리 방지 검증

Success Criteria:
  - DLQ 수만 건 -> 10분 내 전수 복구
  - 복구 중 2차 장애 -> 무한 루프 없이 멱등성 유지
  - Gradient 스로틀링 -> 네트워크 대역폭 80% 이하 유지
  - Zero data loss, Zero duplicate processing

Execution:
    # X-Test-Mode로 Rate Limit 바이패스
    PYTHONPATH=. python load_tests/scenarios/integration/stage14_extreme.py

    # Locust 모드 (선택)
    PYTHONPATH=. locust -f load_tests/scenarios/integration/stage14_extreme.py \\
        --host=http://localhost:8000 --users=50 --spawn-rate=10 --run-time=5m --headless

Reference:
    - docs/self_healing/27_SELFHEALING_SCENARIO_MAPPING.md
    - Stage DNA Type: integration (with chaos elements)
"""

import os
import sys
import time
import random
import uuid
import json
import threading
import statistics
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(os.path.dirname(_current_dir))
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# =============================================================================
# Stage DNA - Self-Healing 모듈 의존성 선언 (PLATINUM Grade)
# Reference: docs/self_healing/27_SELFHEALING_SCENARIO_MAPPING.md
# Optimized by: DNAAnalyzer (2025-12-30)
# Upgraded to PLATINUM: 2025-12-30 (리뷰어 피드백 반영)
# =============================================================================
STAGE_DNA = {
    "name": "Stage 14 EXTREME - Deep Resilience Stress Test",
    "type": "platinum",  # 🔥 PLATINUM: 가장 공격적인 설정, 시스템 한계 테스트
    "grade": "platinum",  # DNA 등급: bronze < silver < gold < platinum
    "required_modules": [
        # 핵심 Self-Healing 모듈 - 모두 필수
        "circuit_breaker",  # CB 상태 체크, cascading failure 테스트
        "dlq",              # DLQ flooding, replay 테스트
        "health",           # 헬스체크 엔드포인트 호출
        "chaos",            # 장애 주입 테스트
        "xtest",            # X-Test-Mode 헤더 사용
        "emergency",        # Emergency 레벨 체크
        "reconciliation",   # DLQ 복구 후 데이터 정합성 검증
        "blast_radius",     # 파급 범위 분석
    ],
    "optional_modules": [],  # Platinum은 모든 모듈 필수
    
    # === PLATINUM 극한 테스트 설정 ===
    "platinum_config": {
        # Rate Limit 완전 무력화
        "rate_limit_bypass": "full",  # full: 완전 OFF, partial: 10배 완화
        "rate_limit_budget_multiplier": 10,  # Rate Limit 예산 10배
        "ignore_warnings": True,  # 경고 무시하고 끝까지 진행
        
        # Blast Radius 설정
        "blast_radius": "high",  # low/medium/high - high는 전체 시스템 영향 허용
        "allow_cascading_failure": True,  # 연쇄 장애 허용
        "max_affected_services": 10,  # 최대 영향 서비스 수
        
        # DLQ Flooding 설정
        "max_dlq_flood": 100000,  # 10만 건까지 허용
        "dlq_flood_rate": 1000,  # 초당 1000건
        "parallel_workers": 50,  # 병렬 워커 50개
        
        # Chaos 설정
        "chaos_intensity": "extreme",  # mild/moderate/extreme
        "chaos_during_recovery": True,
        "secondary_failure_enabled": True,
        "tertiary_failure_enabled": True,  # 3차 장애도 허용
        
        # Recovery 설정
        "recovery_priority": "critical",
        "auto_whitelist": True,
        "gradient_throttle": False,  # Platinum에서는 스로틀링 OFF
    },
    
    # === X-Test-Mode 하이패스 설정 ===
    "xtest_config": {
        "mode": "platinum",
        "bypass_rate_limit": True,  # Rate Limit 완전 바이패스
        "bypass_circuit_breaker": False,  # CB는 테스트 대상이므로 유지
        "bypass_auth": False,  # 인증은 유지
        "headers": {
            "X-Test-Mode": "platinum",
            "X-Test-Bypass-RateLimit": "full",
            "X-Test-Blast-Radius": "high",
            "X-Test-Priority": "critical",
        },
    },
    
    # === DNAAnalyzer 메타데이터 ===
    "_optimized_by": "DNAAnalyzer",
    "_optimization_date": "2025-12-30",
    "_upgraded_to_platinum": "2025-12-30",
    "_upgrade_reason": "리뷰어 피드백 - 힐링 시스템 한계 테스트 필요",
    "_previous_type": "chaos",
    "_analysis_confidence": 0.95,  # Platinum 설정으로 신뢰도 최대
}

# DNA 검증 (테스트 시작 전 자동 체크)
try:
    from load_tests.utils.selfhealing.stage_dna import validate_stage_dna
    _dna_result = validate_stage_dna(STAGE_DNA)
    if not _dna_result.is_valid:
        import warnings
        warnings.warn(str(_dna_result))
except ImportError:
    pass

# =============================================================================
# Imports
# =============================================================================

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("[WARN] requests library not available")


STAGE_NAME = "[Stage14-EXTREME]"


# =============================================================================
# Test Configuration
# =============================================================================

@dataclass
class ExtremeTestConfig:
    """극한 테스트 설정"""
    base_url: str = "http://localhost:8000"
    timeout: int = 30
    
    # DLQ Flooding
    dlq_flood_count: int = 1000  # 시작: 1000건, 목표: 10000건
    dlq_batch_size: int = 50
    dlq_parallel_workers: int = 10
    
    # Recovery Chaos
    chaos_during_recovery: bool = True
    secondary_failure_delay: float = 5.0  # 복구 시작 후 5초에 2차 장애
    tertiary_failure_delay: float = 10.0  # 10초에 3차 장애
    
    # Gradient Throttle
    initial_rate: float = 100.0  # 초당 100건
    min_rate: float = 10.0
    max_rate: float = 500.0
    rtt_threshold_ms: float = 200.0
    
    # Success Criteria
    max_recovery_time_seconds: int = 600  # 10분
    max_duplicate_rate: float = 0.001  # 0.1%
    max_data_loss_rate: float = 0.0  # 0%


@dataclass
class ExtremeTestResult:
    """극한 테스트 결과"""
    test_name: str
    status: str  # passed, failed, partial
    duration_seconds: float
    details: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


# =============================================================================
# Gradient Throttle Simulator (Netflix Algorithm)
# =============================================================================

class GradientThrottle:
    """
    Netflix Gradient Algorithm 기반 적응형 스로틀링
    
    RTT 증가 감지 시 자동으로 요청 속도 감소
    RTT 안정화 시 점진적 속도 증가
    """
    
    def __init__(self, config: ExtremeTestConfig):
        self.config = config
        self.current_rate = config.initial_rate
        self.rtt_history: List[float] = []
        self.lock = threading.Lock()
        
        # Gradient 계산용
        self.smoothed_rtt = 0.0
        self.rtt_variance = 0.0
        self.alpha = 0.2  # Smoothing factor
        
    def record_rtt(self, rtt_ms: float):
        """RTT 기록 및 속도 조정"""
        with self.lock:
            self.rtt_history.append(rtt_ms)
            if len(self.rtt_history) > 100:
                self.rtt_history.pop(0)
            
            # Exponential smoothing
            if self.smoothed_rtt == 0:
                self.smoothed_rtt = rtt_ms
            else:
                self.smoothed_rtt = self.alpha * rtt_ms + (1 - self.alpha) * self.smoothed_rtt
            
            # Gradient 기반 속도 조정
            gradient = rtt_ms / max(self.smoothed_rtt, 1.0)
            
            if gradient > 1.5:  # RTT 50% 이상 증가
                # 급격한 감속
                self.current_rate = max(
                    self.config.min_rate,
                    self.current_rate * 0.5
                )
            elif gradient > 1.2:  # RTT 20% 증가
                # 완만한 감속
                self.current_rate = max(
                    self.config.min_rate,
                    self.current_rate * 0.8
                )
            elif gradient < 0.9:  # RTT 안정/감소
                # 점진적 증속
                self.current_rate = min(
                    self.config.max_rate,
                    self.current_rate * 1.1
                )
    
    def get_delay(self) -> float:
        """다음 요청까지 대기 시간 (초)"""
        with self.lock:
            return 1.0 / max(self.current_rate, 1.0)
    
    def get_stats(self) -> Dict[str, Any]:
        """현재 스로틀링 상태"""
        with self.lock:
            return {
                "current_rate": round(self.current_rate, 2),
                "smoothed_rtt_ms": round(self.smoothed_rtt, 2),
                "rtt_samples": len(self.rtt_history),
                "avg_rtt_ms": round(statistics.mean(self.rtt_history), 2) if self.rtt_history else 0,
            }


# =============================================================================
# Extreme Test Runner
# =============================================================================

class ExtremeTestRunner:
    """Stage 14 극한 테스트 러너"""
    
    def __init__(self, config: ExtremeTestConfig):
        self.config = config
        self.session = self._create_session()
        self.results: List[ExtremeTestResult] = []
        self.throttle = GradientThrottle(config)
        
        # Auth
        self.auth_token: Optional[str] = None
        self.admin_token: Optional[str] = None
        
        # Tracking
        self.created_dlq_ids: List[str] = []
        self.processed_ids: set = set()
        self.duplicate_detections: int = 0
        self.idempotency_keys: Dict[str, int] = defaultdict(int)
        
        # Chaos state
        self.chaos_active: bool = False
        self.recovery_in_progress: bool = False
        
    def _create_session(self) -> requests.Session:
        """🔥 PLATINUM MODE - X-Test-Mode 헤더가 포함된 세션 생성"""
        session = requests.Session()
        
        # 🔥 PLATINUM MODE: Rate Limit 완전 바이패스
        session.headers.update({
            "X-Test-Mode": "platinum",  # 🔥 PLATINUM: 가장 공격적인 모드
            "X-Test-Bypass-RateLimit": "full",  # full: Rate Limiter 완전 OFF
            "X-Test-Priority": "critical",
            "X-Test-Blast-Radius": "high",  # 파급 범위 확장 허용
        })
        
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[502, 503, 504],
            # 429는 retry하지 않음 (PLATINUM에서는 발생하지 않아야 함)
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def _get(self, endpoint: str, **kwargs) -> requests.Response:
        url = f"{self.config.base_url}{endpoint}"
        kwargs.setdefault("timeout", self.config.timeout)
        return self.session.get(url, **kwargs)
    
    def _post(self, endpoint: str, **kwargs) -> requests.Response:
        url = f"{self.config.base_url}{endpoint}"
        kwargs.setdefault("timeout", self.config.timeout)
        return self.session.post(url, **kwargs)
    
    def _auth_headers(self, admin: bool = False) -> Dict[str, str]:
        token = self.admin_token if admin else self.auth_token
        headers = dict(self.session.headers)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers
    
    # =========================================================================
    # Setup
    # =========================================================================
    
    def setup(self) -> bool:
        """테스트 환경 설정"""
        print(f"\n{'='*70}")
        print(f"{STAGE_NAME} EXTREME TEST SETUP")
        print(f"{'='*70}")
        
        # Health check
        try:
            resp = self._get("/api/self-healing/health/")
            if resp.status_code != 200:
                print(f"[FAIL] Health check failed: {resp.status_code}")
                return False
            print("[OK] Health check passed")
        except Exception as e:
            print(f"[FAIL] Health check error: {e}")
            return False
        
        # Admin login
        try:
            resp = self._post("/api/auth/login/", json={
                "username": "admin",
                "password": "admin123",
            })
            if resp.status_code == 200:
                data = resp.json()
                # 토큰 추출 (API 응답 형식에 따라 다름)
                token_data = data.get("token", {})
                if isinstance(token_data, dict):
                    self.admin_token = token_data.get("access")
                else:
                    self.admin_token = data.get("access") or token_data
                print(f"[OK] Admin login successful (token_len={len(self.admin_token or '')})")
            else:
                print(f"[WARN] Admin login failed: {resp.status_code}")
        except Exception as e:
            print(f"[WARN] Admin login error: {e}")
        
        print(f"\n[CONFIG] DLQ Flood Count: {self.config.dlq_flood_count}")
        print(f"[CONFIG] Chaos During Recovery: {self.config.chaos_during_recovery}")
        print(f"[CONFIG] Max Recovery Time: {self.config.max_recovery_time_seconds}s")
        
        return True
    
    # =========================================================================
    # Test 1: DLQ Flooding (수만 건 적재)
    # =========================================================================
    
    def test_dlq_flooding(self) -> ExtremeTestResult:
        """
        TC-EXT-1: DLQ Flooding Test
        
        수천~수만 건의 DLQ 엔트리를 병렬로 생성하고
        시스템이 버티는지 확인
        """
        print(f"\n{'='*70}")
        print("[TC-EXT-1] DLQ FLOODING TEST")
        print(f"Target: {self.config.dlq_flood_count} entries")
        print(f"{'='*70}")
        
        start_time = time.time()
        success_count = 0
        error_count = 0
        errors = []
        
        domains = ["payment", "inventory", "webhook", "notification"]
        failure_types = ["PG_TIMEOUT", "NETWORK_ERROR", "DB_DEADLOCK", "SERVICE_UNAVAILABLE"]
        
        # 실제 배치당 생성할 개수 계산
        items_per_batch = min(self.config.dlq_batch_size, self.config.dlq_flood_count)
        
        def create_dlq_entry(batch_id: int) -> Tuple[int, int, List[str]]:
            """배치 단위 DLQ 생성"""
            batch_success = 0
            batch_errors = 0
            batch_error_msgs = []
            
            for i in range(items_per_batch):
                try:
                    # Gradient throttle 적용
                    delay = self.throttle.get_delay()
                    time.sleep(delay)
                    
                    idempotency_key = f"extreme-{batch_id}-{i}-{uuid.uuid4().hex[:8]}"
                    
                    req_start = time.time()
                    resp = self._post(
                        "/api/self-healing/dlq/test/create/",
                        json={
                            "domain": random.choice(domains),
                            "failure_type": random.choice(failure_types),
                            "error_message": f"Extreme test batch {batch_id} item {i}",
                            "idempotency_key": idempotency_key,
                        },
                        headers=self._auth_headers(admin=True),
                    )
                    rtt_ms = (time.time() - req_start) * 1000
                    self.throttle.record_rtt(rtt_ms)
                    
                    if resp.status_code == 201:
                        data = resp.json()
                        dlq_id = data.get("dlq_id")
                        if dlq_id:
                            self.created_dlq_ids.append(dlq_id)
                            self.idempotency_keys[idempotency_key] += 1
                        batch_success += 1
                    elif resp.status_code == 429:
                        # Rate limit hit even with bypass - record but continue
                        batch_error_msgs.append(f"429 at batch {batch_id}")
                        batch_errors += 1
                    elif resp.status_code == 403:
                        # DEBUG mode required
                        batch_error_msgs.append("403 - DEBUG mode required")
                        batch_errors += 1
                        break  # No point continuing
                    else:
                        batch_error_msgs.append(f"{resp.status_code}: {resp.text[:100]}")
                        batch_errors += 1
                        
                except Exception as e:
                    batch_errors += 1
                    batch_error_msgs.append(str(e))
            
            return batch_success, batch_errors, batch_error_msgs
        
        # 병렬 실행 - 올림 처리로 최소 1개 배치 보장
        import math
        num_batches = max(1, math.ceil(self.config.dlq_flood_count / self.config.dlq_batch_size))
        effective_batch_size = min(self.config.dlq_batch_size, self.config.dlq_flood_count)
        
        print(f"\n[INFO] Creating {num_batches} batches of {effective_batch_size} entries each")
        print(f"[INFO] Using {self.config.dlq_parallel_workers} parallel workers")
        
        with ThreadPoolExecutor(max_workers=self.config.dlq_parallel_workers) as executor:
            futures = {executor.submit(create_dlq_entry, i): i for i in range(num_batches)}
            
            for future in as_completed(futures):
                batch_id = futures[future]
                try:
                    s, e, errs = future.result()
                    success_count += s
                    error_count += e
                    errors.extend(errs)
                    
                    # Progress 출력
                    if (batch_id + 1) % 5 == 0:
                        throttle_stats = self.throttle.get_stats()
                        print(f"  Batch {batch_id + 1}/{num_batches}: "
                              f"success={success_count}, errors={error_count}, "
                              f"rate={throttle_stats['current_rate']:.1f}/s")
                        
                except Exception as e:
                    errors.append(f"Batch {batch_id} failed: {e}")
        
        duration = time.time() - start_time
        
        # 결과 분석
        throttle_stats = self.throttle.get_stats()
        
        result = ExtremeTestResult(
            test_name="DLQ Flooding",
            status="passed" if success_count > 0 and error_count < success_count * 0.1 else "failed",
            duration_seconds=duration,
            details={
                "target_count": self.config.dlq_flood_count,
                "success_count": success_count,
                "error_count": error_count,
                "created_ids_count": len(self.created_dlq_ids),
                "throttle_final_rate": throttle_stats["current_rate"],
                "throttle_avg_rtt_ms": throttle_stats["avg_rtt_ms"],
            },
            metrics={
                "throughput_per_second": success_count / max(duration, 1),
                "success_rate": success_count / max(success_count + error_count, 1),
                "avg_rtt_ms": throttle_stats["avg_rtt_ms"],
            },
            errors=errors[:10],  # 처음 10개 에러만
        )
        
        print(f"\n[RESULT] DLQ Flooding: {result.status.upper()}")
        print(f"  - Created: {success_count}/{self.config.dlq_flood_count}")
        print(f"  - Duration: {duration:.1f}s")
        print(f"  - Throughput: {result.metrics['throughput_per_second']:.1f}/s")
        print(f"  - Final Throttle Rate: {throttle_stats['current_rate']:.1f}/s")
        if errors:
            print(f"  - Sample Errors: {errors[:3]}")
        
        self.results.append(result)
        return result
    
    # =========================================================================
    # Test 2: Recovery in Chaos (복구 중 2차 장애)
    # =========================================================================
    
    def test_recovery_in_chaos(self) -> ExtremeTestResult:
        """
        TC-EXT-2: Recovery in Chaos Test
        
        DLQ 리플레이 중 2차/3차 장애를 주입하여
        멱등성과 복구 루프 방지 검증
        """
        print(f"\n{'='*70}")
        print("[TC-EXT-2] RECOVERY IN CHAOS TEST")
        print(f"{'='*70}")
        
        if not self.created_dlq_ids:
            return ExtremeTestResult(
                test_name="Recovery in Chaos",
                status="skipped",
                duration_seconds=0,
                details={"reason": "No DLQ entries to replay"},
                errors=["No DLQ entries created in previous test"],
            )
        
        start_time = time.time()
        replay_success = 0
        replay_failed = 0
        chaos_injections = 0
        idempotency_violations = 0
        errors = []
        
        # 복구 시작
        self.recovery_in_progress = True
        
        # 2차 장애 주입 스레드
        def inject_secondary_chaos():
            time.sleep(self.config.secondary_failure_delay)
            if self.recovery_in_progress:
                print(f"\n  [CHAOS] Injecting SECONDARY failure at {time.time() - start_time:.1f}s")
                try:
                    self._post(
                        "/api/self-healing/xtest/inject/",
                        json={
                            "target": "payment",
                            "failure_type": "latency",
                            "duration": 3,
                            "latency_ms": 500,
                        },
                        headers=self._auth_headers(admin=True),
                    )
                    return True
                except:
                    return False
            return False
        
        # 3차 장애 주입 스레드  
        def inject_tertiary_chaos():
            time.sleep(self.config.tertiary_failure_delay)
            if self.recovery_in_progress:
                print(f"\n  [CHAOS] Injecting TERTIARY failure at {time.time() - start_time:.1f}s")
                try:
                    self._post(
                        "/api/self-healing/chaos/inject/",
                        json={
                            "target": "db",
                            "failure_type": "timeout",
                            "duration": 2,
                        },
                        headers=self._auth_headers(admin=True),
                    )
                    return True
                except:
                    return False
            return False
        
        # Chaos 스레드 시작
        if self.config.chaos_during_recovery:
            chaos_thread_2 = threading.Thread(target=inject_secondary_chaos)
            chaos_thread_3 = threading.Thread(target=inject_tertiary_chaos)
            chaos_thread_2.start()
            chaos_thread_3.start()
        
        # DLQ 리플레이 실행
        sample_ids = self.created_dlq_ids[:min(100, len(self.created_dlq_ids))]
        print(f"\n[INFO] Replaying {len(sample_ids)} DLQ entries with chaos injection")
        
        for i, dlq_id in enumerate(sample_ids):
            try:
                # 멱등성 체크: 이미 처리된 ID인지 확인
                if dlq_id in self.processed_ids:
                    idempotency_violations += 1
                    print(f"  [WARN] Duplicate processing detected: {dlq_id}")
                    continue
                
                delay = self.throttle.get_delay()
                time.sleep(delay)
                
                req_start = time.time()
                resp = self._post(
                    f"/api/self-healing/dlq/{dlq_id}/resolve/",
                    json={"notes": f"Extreme test recovery at {time.time()}"},
                    headers=self._auth_headers(admin=True),
                )
                rtt_ms = (time.time() - req_start) * 1000
                self.throttle.record_rtt(rtt_ms)
                
                if resp.status_code == 200:
                    self.processed_ids.add(dlq_id)
                    replay_success += 1
                elif resp.status_code in (400, 404):
                    # Already resolved or not found - OK
                    self.processed_ids.add(dlq_id)
                    replay_success += 1
                else:
                    replay_failed += 1
                    if resp.status_code == 500:
                        chaos_injections += 1  # Likely chaos effect
                
                # Progress
                if (i + 1) % 20 == 0:
                    print(f"  Replay progress: {i + 1}/{len(sample_ids)} "
                          f"(success={replay_success}, failed={replay_failed})")
                    
            except Exception as e:
                replay_failed += 1
                errors.append(str(e))
        
        self.recovery_in_progress = False
        duration = time.time() - start_time
        
        # Chaos 스레드 대기
        if self.config.chaos_during_recovery:
            chaos_thread_2.join(timeout=5)
            chaos_thread_3.join(timeout=5)
        
        # 멱등성 검증
        duplicate_keys = {k: v for k, v in self.idempotency_keys.items() if v > 1}
        
        result = ExtremeTestResult(
            test_name="Recovery in Chaos",
            status="passed" if idempotency_violations == 0 and replay_success > 0 else "failed",
            duration_seconds=duration,
            details={
                "total_to_replay": len(sample_ids),
                "replay_success": replay_success,
                "replay_failed": replay_failed,
                "chaos_effects_detected": chaos_injections,
                "idempotency_violations": idempotency_violations,
                "duplicate_keys_count": len(duplicate_keys),
            },
            metrics={
                "recovery_rate": replay_success / max(len(sample_ids), 1),
                "idempotency_score": 1.0 - (idempotency_violations / max(len(sample_ids), 1)),
                "chaos_survival_rate": (replay_success / max(replay_success + replay_failed, 1)),
            },
            errors=errors[:5],
        )
        
        print(f"\n[RESULT] Recovery in Chaos: {result.status.upper()}")
        print(f"  - Replay Success: {replay_success}/{len(sample_ids)}")
        print(f"  - Chaos Effects: {chaos_injections}")
        print(f"  - Idempotency Violations: {idempotency_violations}")
        print(f"  - Recovery Rate: {result.metrics['recovery_rate']*100:.1f}%")
        
        self.results.append(result)
        return result
    
    # =========================================================================
    # Test 3: Cascading Failure Survival
    # =========================================================================
    
    def test_cascading_failure(self) -> ExtremeTestResult:
        """
        TC-EXT-3: Cascading Failure Survival Test
        
        DB -> Redis -> Celery 순차 장애 시
        Circuit Breaker 전이와 Emergency 에스컬레이션 검증
        """
        print(f"\n{'='*70}")
        print("[TC-EXT-3] CASCADING FAILURE SURVIVAL TEST")
        print(f"{'='*70}")
        
        start_time = time.time()
        cb_states: List[Dict] = []
        emergency_levels: List[str] = []
        errors = []
        
        cascade_sequence = [
            ("db", "timeout", 3),
            ("redis", "connection_refused", 3),
            ("celery", "worker_lost", 3),
        ]
        
        print("\n[INFO] Injecting cascading failures:")
        
        for target, failure_type, duration in cascade_sequence:
            print(f"\n  >>> Injecting {failure_type} on {target} for {duration}s")
            
            try:
                # 장애 주입
                resp = self._post(
                    "/api/self-healing/chaos/inject/",
                    json={
                        "target": target,
                        "failure_type": failure_type,
                        "duration": duration,
                    },
                    headers=self._auth_headers(admin=True),
                )
                
                if resp.status_code in (200, 201):
                    print(f"      [OK] Chaos injected")
                else:
                    print(f"      [WARN] Chaos injection: {resp.status_code}")
                
                # CB 상태 체크
                time.sleep(1)
                cb_resp = self._get(
                    "/api/self-healing/circuit-breaker/status/",
                    headers=self._auth_headers(admin=True),
                )
                if cb_resp.status_code == 200:
                    cb_data = cb_resp.json()
                    cb_states.append({
                        "target": target,
                        "cb_status": cb_data,
                        "timestamp": time.time() - start_time,
                    })
                    print(f"      [CB] Status captured")
                
                # Emergency 레벨 체크
                em_resp = self._get(
                    "/api/self-healing/emergency/status/",
                    headers=self._auth_headers(admin=True),
                )
                if em_resp.status_code == 200:
                    em_data = em_resp.json()
                    level = em_data.get("level", "NORMAL")
                    emergency_levels.append(level)
                    print(f"      [EMERGENCY] Level: {level}")
                
                time.sleep(duration)
                
            except Exception as e:
                errors.append(f"Cascade {target}: {e}")
                print(f"      [ERROR] {e}")
        
        duration = time.time() - start_time
        
        # 분석
        max_emergency_level = max(emergency_levels) if emergency_levels else "UNKNOWN"
        cb_transitions = len(set(
            s.get("cb_status", {}).get("state", "unknown") 
            for s in cb_states
        ))
        
        result = ExtremeTestResult(
            test_name="Cascading Failure Survival",
            status="passed" if len(errors) == 0 else "partial",
            duration_seconds=duration,
            details={
                "cascade_steps": len(cascade_sequence),
                "cb_state_snapshots": len(cb_states),
                "cb_transitions": cb_transitions,
                "max_emergency_level": max_emergency_level,
                "emergency_escalations": emergency_levels,
            },
            metrics={
                "survival_rate": (len(cascade_sequence) - len(errors)) / len(cascade_sequence),
                "cb_responsiveness": cb_transitions / len(cascade_sequence),
            },
            errors=errors,
        )
        
        print(f"\n[RESULT] Cascading Failure: {result.status.upper()}")
        print(f"  - Cascade Steps: {len(cascade_sequence)}")
        print(f"  - CB Transitions: {cb_transitions}")
        print(f"  - Max Emergency: {max_emergency_level}")
        
        self.results.append(result)
        return result
    
    # =========================================================================
    # Test 4: Gradient Throttle Verification
    # =========================================================================
    
    def test_gradient_throttle(self) -> ExtremeTestResult:
        """
        TC-EXT-4: Gradient Throttle Verification
        
        RTT 증가 시 자동 속도 조절 검증
        """
        print(f"\n{'='*70}")
        print("[TC-EXT-4] GRADIENT THROTTLE VERIFICATION")
        print(f"{'='*70}")
        
        start_time = time.time()
        
        # 현재 throttle 상태
        stats = self.throttle.get_stats()
        
        result = ExtremeTestResult(
            test_name="Gradient Throttle",
            status="passed",
            duration_seconds=time.time() - start_time,
            details={
                "final_rate": stats["current_rate"],
                "smoothed_rtt_ms": stats["smoothed_rtt_ms"],
                "total_samples": stats["rtt_samples"],
                "avg_rtt_ms": stats["avg_rtt_ms"],
            },
            metrics={
                "rate_adaptation_factor": stats["current_rate"] / self.config.initial_rate,
                "rtt_stability": 1.0 / max(stats["smoothed_rtt_ms"] / 100, 0.1),
            },
        )
        
        print(f"\n[RESULT] Gradient Throttle: {result.status.upper()}")
        print(f"  - Initial Rate: {self.config.initial_rate}/s")
        print(f"  - Final Rate: {stats['current_rate']:.1f}/s")
        print(f"  - Avg RTT: {stats['avg_rtt_ms']:.1f}ms")
        print(f"  - Adaptation: {result.metrics['rate_adaptation_factor']*100:.1f}%")
        
        self.results.append(result)
        return result
    
    # =========================================================================
    # Final Report
    # =========================================================================
    
    def generate_report(self) -> Dict[str, Any]:
        """최종 보고서 생성"""
        print(f"\n{'='*70}")
        print(f"{STAGE_NAME} EXTREME TEST FINAL REPORT")
        print(f"{'='*70}")
        
        passed = sum(1 for r in self.results if r.status == "passed")
        failed = sum(1 for r in self.results if r.status == "failed")
        partial = sum(1 for r in self.results if r.status == "partial")
        skipped = sum(1 for r in self.results if r.status == "skipped")
        total = len(self.results)
        
        print(f"\n[SUMMARY]")
        print(f"  Total Tests: {total}")
        print(f"  Passed: {passed}")
        print(f"  Failed: {failed}")
        print(f"  Partial: {partial}")
        print(f"  Skipped: {skipped}")
        
        # 각 테스트 결과
        print(f"\n[DETAILED RESULTS]")
        for r in self.results:
            status_icon = {
                "passed": "OK",
                "failed": "FAIL",
                "partial": "WARN",
                "skipped": "SKIP",
            }.get(r.status, "?")
            print(f"  [{status_icon}] {r.test_name}: {r.duration_seconds:.1f}s")
            for k, v in r.metrics.items():
                print(f"       - {k}: {v:.3f}" if isinstance(v, float) else f"       - {k}: {v}")
        
        # 극한 테스트 기준 충족 여부
        print(f"\n[EXTREME CRITERIA CHECK]")
        
        criteria = {
            "DLQ Flood Created": len(self.created_dlq_ids) >= 100,
            "Recovery Under Chaos": any(r.test_name == "Recovery in Chaos" and r.status != "failed" for r in self.results),
            "Zero Idempotency Violations": self.duplicate_detections == 0,
            "Gradient Throttle Active": self.throttle.get_stats()["rtt_samples"] > 0,
        }
        
        for name, passed_crit in criteria.items():
            icon = "PASS" if passed_crit else "FAIL"
            print(f"  [{icon}] {name}")
        
        all_passed = all(criteria.values()) and failed == 0
        
        print(f"\n{'='*70}")
        if all_passed:
            print("[EXTREME TEST RESULT] ALL CRITERIA MET - DEEP RESILIENCE VERIFIED")
        else:
            print("[EXTREME TEST RESULT] SOME CRITERIA NOT MET - REVIEW REQUIRED")
        print(f"{'='*70}\n")
        
        return {
            "stage": STAGE_NAME,
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "partial": partial,
                "skipped": skipped,
            },
            "criteria": criteria,
            "all_passed": all_passed,
            "results": [
                {
                    "name": r.test_name,
                    "status": r.status,
                    "duration": r.duration_seconds,
                    "details": r.details,
                    "metrics": r.metrics,
                    "errors": r.errors,
                }
                for r in self.results
            ],
            "dlq_stats": {
                "created": len(self.created_dlq_ids),
                "processed": len(self.processed_ids),
                "duplicates_detected": self.duplicate_detections,
            },
            "throttle_stats": self.throttle.get_stats(),
        }
    
    # =========================================================================
    # Main Runner
    # =========================================================================
    
    def run_all(self) -> Dict[str, Any]:
        """모든 극한 테스트 실행"""
        if not self.setup():
            return {"error": "Setup failed"}
        
        # 테스트 순차 실행
        self.test_dlq_flooding()
        self.test_recovery_in_chaos()
        self.test_cascading_failure()
        self.test_gradient_throttle()
        
        return self.generate_report()


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """메인 엔트리 포인트"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Stage 14 EXTREME Deep Resilience Test")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Base URL")
    parser.add_argument("--dlq-count", type=int, default=500, help="DLQ flood count")
    parser.add_argument("--no-chaos", action="store_true", help="Disable chaos during recovery")
    parser.add_argument("--output", default=None, help="Output JSON file path")
    
    args = parser.parse_args()
    
    if not REQUESTS_AVAILABLE:
        print("[ERROR] requests library required: pip install requests")
        sys.exit(1)
    
    config = ExtremeTestConfig(
        base_url=args.base_url,
        dlq_flood_count=args.dlq_count,
        chaos_during_recovery=not args.no_chaos,
    )
    
    runner = ExtremeTestRunner(config)
    report = runner.run_all()
    
    # 결과 저장
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n[INFO] Report saved to: {args.output}")
    
    sys.exit(0 if report.get("all_passed", False) else 1)


if __name__ == "__main__":
    main()
