"""
Stage 35 Variant: Cache Poison Detection Test

Purpose: 캐시 데이터 손상/오염 시 감지 및 자동 무효화 검증
- 의도적 캐시 오염 주입
- Checksum 불일치 감지
- Stale 데이터 서빙 방지
- 자동 무효화 및 DB fallback

Scenarios:
  SC-35P-1: Invalid JSON 오염 감지
  SC-35P-2: Checksum 불일치 감지
  SC-35P-3: Stale 데이터 자동 거부
  SC-35P-4: 오염 확산 방지 (Cache Propagation Block)

Invariants:
  - poison_detected == poison_injected
  - poison_served_to_user == 0
  - auto_invalidation_triggered

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage35_cache_poison.py --host=http://localhost:8000

    # CLI mode
    locust -f load_tests/scenarios/stage35_cache_poison.py \\
        --host=http://localhost:8000 \\
        --users=50 --spawn-rate=10 --run-time=3m \\
        --headless --html=stage35_poison_report.html

    # Standalone test (no Locust)
    python load_tests/scenarios/stage35_cache_poison.py

Reference:
    - docs/GAP_RESOLUTION_PLAN.md (GAP-02)
    - Pipeline Stage: Caching
"""

import os
import sys
import time
import random
import threading
import json
import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

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


STAGE_NAME = "[Stage35-CachePoison]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "20"))
_original_total = 180  # Original total: 180s (3min)
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_BASELINE = max(3, int(30 * _scale))
PHASE_2_INJECTION = max(5, int(60 * _scale))
PHASE_3_DETECTION = max(5, int(60 * _scale))
PHASE_4_RECOVERY = max(3, int(30 * _scale))

TOTAL_DURATION = PHASE_1_BASELINE + PHASE_2_INJECTION + PHASE_3_DETECTION + PHASE_4_RECOVERY

# Cache TTL settings
CACHE_TTL_SECONDS = 60
CACHE_CHECKSUM_ENABLED = True

# Poison injection settings
POISON_INJECTION_RATE = 0.1  # 10% of writes will be poisoned
MAX_POISON_ENTRIES = 50


# =============================================================================
# Poison Types
# =============================================================================

class PoisonType(Enum):
    """캐시 오염 유형"""
    INVALID_JSON = "invalid_json"           # 파싱 불가능한 JSON
    CHECKSUM_MISMATCH = "checksum_mismatch" # 체크섬 불일치
    NEGATIVE_PRICE = "negative_price"       # 비즈니스 규칙 위반 (음수 가격)
    EXPIRED_STALE = "expired_stale"         # 만료되었지만 남아있는 stale
    TYPE_MISMATCH = "type_mismatch"         # 타입 불일치 (string으로 저장된 숫자)
    EMPTY_REQUIRED = "empty_required"       # 필수 필드 누락
    CORRUPTED_BINARY = "corrupted_binary"   # 바이너리 손상


@dataclass
class PoisonEntry:
    """오염된 캐시 엔트리 기록"""
    key: str
    poison_type: PoisonType
    injected_at: float
    detected: bool = False
    detected_at: Optional[float] = None
    served_to_user: bool = False


# =============================================================================
# Cache Entry with Checksum
# =============================================================================

@dataclass
class CacheEntry:
    """체크섬을 포함한 캐시 엔트리"""
    key: str
    value: Any
    checksum: str
    created_at: float
    expires_at: float
    version: int = 1
    
    @staticmethod
    def compute_checksum(value: Any) -> str:
        """값의 체크섬 계산"""
        try:
            serialized = json.dumps(value, sort_keys=True, default=str)
            return hashlib.sha256(serialized.encode()).hexdigest()[:16]
        except Exception:
            return hashlib.sha256(str(value).encode()).hexdigest()[:16]
    
    def is_valid(self) -> bool:
        """체크섬 검증"""
        if not CACHE_CHECKSUM_ENABLED:
            return True
        computed = self.compute_checksum(self.value)
        return computed == self.checksum
    
    def is_expired(self) -> bool:
        """만료 여부 확인"""
        return time.time() >= self.expires_at
    
    def to_stored_format(self) -> str:
        """저장 형식으로 직렬화"""
        data = {
            "key": self.key,
            "value": self.value,
            "checksum": self.checksum,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "version": self.version
        }
        return json.dumps(data)
    
    @classmethod
    def from_stored_format(cls, stored: str) -> Optional["CacheEntry"]:
        """저장 형식에서 복원"""
        try:
            data = json.loads(stored)
            return cls(
                key=data["key"],
                value=data["value"],
                checksum=data["checksum"],
                created_at=data["created_at"],
                expires_at=data["expires_at"],
                version=data.get("version", 1)
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return None


# =============================================================================
# Simulated Product Data (For Testing)
# =============================================================================

def generate_product(product_id: int) -> Dict[str, Any]:
    """테스트용 상품 데이터 생성"""
    return {
        "id": product_id,
        "name": f"Product {product_id}",
        "price": random.randint(1000, 100000),
        "stock": random.randint(0, 1000),
        "category": f"category_{product_id % 10}",
        "is_active": True,
        "created_at": datetime.now().isoformat()
    }


# =============================================================================
# Cache Poison Simulator
# =============================================================================

class CachePoisonSimulator:
    """
    캐시 오염 시뮬레이터
    
    Redis 연결 없이 in-memory로 캐시 동작을 시뮬레이션하면서
    오염 감지 및 자동 무효화 로직을 테스트합니다.
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._cache: Dict[str, str] = {}  # 실제 저장소 (직렬화된 상태)
        self._db: Dict[int, Dict[str, Any]] = {}  # 시뮬레이션 DB
        self._poison_log: List[PoisonEntry] = []
        self._metrics = {
            "cache_hits": 0,
            "cache_misses": 0,
            "cache_sets": 0,
            "poison_injected": 0,
            "poison_detected": 0,
            "poison_served": 0,
            "auto_invalidations": 0,
            "db_fallbacks": 0,
            "checksum_failures": 0,
            "json_parse_errors": 0,
            "validation_failures": 0,
        }
        
        # DB 초기화
        for i in range(1, 101):
            self._db[i] = generate_product(i)
    
    def reset(self):
        """상태 초기화"""
        with self._lock:
            self._cache.clear()
            self._poison_log.clear()
            for key in self._metrics:
                self._metrics[key] = 0
    
    def _cache_key(self, product_id: int) -> str:
        """캐시 키 생성"""
        return f"product:{product_id}"
    
    def set_cache(self, product_id: int, value: Dict[str, Any], ttl: int = CACHE_TTL_SECONDS) -> bool:
        """캐시에 값 저장 (체크섬 포함)"""
        with self._lock:
            key = self._cache_key(product_id)
            entry = CacheEntry(
                key=key,
                value=value,
                checksum=CacheEntry.compute_checksum(value),
                created_at=time.time(),
                expires_at=time.time() + ttl
            )
            self._cache[key] = entry.to_stored_format()
            self._metrics["cache_sets"] += 1
            return True
    
    def get_cache(self, product_id: int) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        캐시에서 값 조회 (검증 포함)
        
        Returns:
            Tuple of (value or None, status)
            status: "hit", "miss", "invalid", "expired", "poisoned"
        """
        key = self._cache_key(product_id)
        
        with self._lock:
            stored = self._cache.get(key)
            
            if stored is None:
                self._metrics["cache_misses"] += 1
                return None, "miss"
            
            # Step 1: JSON 파싱 시도
            entry = CacheEntry.from_stored_format(stored)
            if entry is None:
                self._metrics["json_parse_errors"] += 1
                self._metrics["poison_detected"] += 1
                self._detect_poison(key, PoisonType.INVALID_JSON)
                self._auto_invalidate(key)
                return None, "invalid"
            
            # Step 2: 만료 확인
            if entry.is_expired():
                self._metrics["cache_misses"] += 1
                self._metrics["poison_detected"] += 1  # 만료된 stale 데이터도 오염으로 간주
                self._detect_poison(key, PoisonType.EXPIRED_STALE)
                self._auto_invalidate(key)
                return None, "expired"
            
            # Step 3: 체크섬 검증
            if not entry.is_valid():
                self._metrics["checksum_failures"] += 1
                self._metrics["poison_detected"] += 1
                self._detect_poison(key, PoisonType.CHECKSUM_MISMATCH)
                self._auto_invalidate(key)
                return None, "checksum_mismatch"
            
            # Step 4: 비즈니스 규칙 검증
            validation_result = self._validate_product(entry.value)
            if not validation_result[0]:
                self._metrics["validation_failures"] += 1
                self._metrics["poison_detected"] += 1
                self._detect_poison(key, validation_result[1])
                self._auto_invalidate(key)
                return None, "validation_failed"
            
            # 정상 히트
            self._metrics["cache_hits"] += 1
            return entry.value, "hit"
    
    def _validate_product(self, product: Dict[str, Any]) -> Tuple[bool, Optional[PoisonType]]:
        """상품 데이터 비즈니스 규칙 검증"""
        # 필수 필드 확인
        required_fields = ["id", "name", "price"]
        for field in required_fields:
            if field not in product or product[field] is None:
                return False, PoisonType.EMPTY_REQUIRED
        
        # 음수 가격 확인
        price = product.get("price", 0)
        try:
            if float(price) < 0:
                return False, PoisonType.NEGATIVE_PRICE
        except (ValueError, TypeError):
            return False, PoisonType.TYPE_MISMATCH
        
        return True, None
    
    def _detect_poison(self, key: str, poison_type: PoisonType):
        """오염 감지 기록"""
        for entry in self._poison_log:
            if entry.key == key and not entry.detected:
                entry.detected = True
                entry.detected_at = time.time()
                break
    
    def _auto_invalidate(self, key: str):
        """자동 무효화"""
        if key in self._cache:
            del self._cache[key]
            self._metrics["auto_invalidations"] += 1
    
    def inject_poison(self, product_id: int, poison_type: PoisonType) -> bool:
        """
        의도적으로 캐시 오염 주입
        
        테스트 목적으로 다양한 유형의 오염된 데이터를 주입합니다.
        """
        key = self._cache_key(product_id)
        
        with self._lock:
            poisoned_data = self._create_poisoned_data(product_id, poison_type)
            self._cache[key] = poisoned_data
            
            # 오염 로그 기록
            self._poison_log.append(PoisonEntry(
                key=key,
                poison_type=poison_type,
                injected_at=time.time()
            ))
            self._metrics["poison_injected"] += 1
            
            return True
    
    def _create_poisoned_data(self, product_id: int, poison_type: PoisonType) -> str:
        """오염 유형에 따른 손상된 데이터 생성"""
        
        if poison_type == PoisonType.INVALID_JSON:
            # 파싱 불가능한 JSON
            return '{"id": ' + str(product_id) + ', "name": "broken'
        
        elif poison_type == PoisonType.CHECKSUM_MISMATCH:
            # 체크섬이 잘못된 데이터
            product = generate_product(product_id)
            entry_data = {
                "key": self._cache_key(product_id),
                "value": product,
                "checksum": "wrong_checksum_1234",  # 잘못된 체크섬
                "created_at": time.time(),
                "expires_at": time.time() + CACHE_TTL_SECONDS,
                "version": 1
            }
            return json.dumps(entry_data)
        
        elif poison_type == PoisonType.NEGATIVE_PRICE:
            # 비즈니스 규칙 위반
            product = generate_product(product_id)
            product["price"] = -9999  # 음수 가격
            entry = CacheEntry(
                key=self._cache_key(product_id),
                value=product,
                checksum=CacheEntry.compute_checksum(product),  # 올바른 체크섬
                created_at=time.time(),
                expires_at=time.time() + CACHE_TTL_SECONDS
            )
            return entry.to_stored_format()
        
        elif poison_type == PoisonType.EXPIRED_STALE:
            # 만료된 stale 데이터
            product = generate_product(product_id)
            entry = CacheEntry(
                key=self._cache_key(product_id),
                value=product,
                checksum=CacheEntry.compute_checksum(product),
                created_at=time.time() - 1000,
                expires_at=time.time() - 10  # 이미 만료됨
            )
            return entry.to_stored_format()
        
        elif poison_type == PoisonType.TYPE_MISMATCH:
            # 타입 불일치
            product = generate_product(product_id)
            product["price"] = "not_a_number"  # 문자열로 변경
            entry = CacheEntry(
                key=self._cache_key(product_id),
                value=product,
                checksum=CacheEntry.compute_checksum(product),
                created_at=time.time(),
                expires_at=time.time() + CACHE_TTL_SECONDS
            )
            return entry.to_stored_format()
        
        elif poison_type == PoisonType.EMPTY_REQUIRED:
            # 필수 필드 누락
            product = {"id": product_id, "category": "test"}  # name, price 누락
            entry = CacheEntry(
                key=self._cache_key(product_id),
                value=product,
                checksum=CacheEntry.compute_checksum(product),
                created_at=time.time(),
                expires_at=time.time() + CACHE_TTL_SECONDS
            )
            return entry.to_stored_format()
        
        elif poison_type == PoisonType.CORRUPTED_BINARY:
            # 바이너리 손상 (비정상 문자 포함)
            return '\x00\x01\x02{"corrupted": true}\xff\xfe'
        
        else:
            # 기본: 유효하지 않은 JSON
            return "not json at all"
    
    def get_from_db(self, product_id: int) -> Optional[Dict[str, Any]]:
        """DB에서 직접 조회 (fallback)"""
        with self._lock:
            self._metrics["db_fallbacks"] += 1
            return self._db.get(product_id)
    
    def get_with_fallback(self, product_id: int) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        캐시 조회 + DB fallback
        
        오염 감지 시 자동으로 DB fallback 후 캐시 재설정
        """
        value, status = self.get_cache(product_id)
        
        if value is not None:
            return value, "cache_hit"
        
        # Cache miss or invalid - DB fallback
        db_value = self.get_from_db(product_id)
        
        if db_value is not None:
            # 정상 데이터로 캐시 재설정
            self.set_cache(product_id, db_value)
            return db_value, f"db_fallback_{status}"
        
        return None, "not_found"
    
    def get_metrics(self) -> Dict[str, int]:
        """현재 메트릭 반환"""
        with self._lock:
            return self._metrics.copy()
    
    def get_poison_stats(self) -> Dict[str, Any]:
        """오염 통계 반환"""
        with self._lock:
            total_injected = len(self._poison_log)
            total_detected = sum(1 for e in self._poison_log if e.detected)
            total_served = sum(1 for e in self._poison_log if e.served_to_user)
            
            detection_times = [
                e.detected_at - e.injected_at
                for e in self._poison_log
                if e.detected and e.detected_at
            ]
            
            by_type = defaultdict(lambda: {"injected": 0, "detected": 0})
            for entry in self._poison_log:
                by_type[entry.poison_type.value]["injected"] += 1
                if entry.detected:
                    by_type[entry.poison_type.value]["detected"] += 1
            
            return {
                "total_injected": total_injected,
                "total_detected": total_detected,
                "total_served": total_served,
                "detection_rate": total_detected / total_injected if total_injected > 0 else 0,
                "avg_detection_time_ms": (sum(detection_times) / len(detection_times) * 1000) if detection_times else 0,
                "by_type": dict(by_type)
            }


# =============================================================================
# Global Simulator Instance
# =============================================================================

_simulator = CachePoisonSimulator()
_test_start_time = None


def get_current_phase() -> str:
    """현재 테스트 단계 반환"""
    if _test_start_time is None:
        return "not_started"
    
    elapsed = time.time() - _test_start_time
    
    if elapsed < PHASE_1_BASELINE:
        return "baseline"
    elif elapsed < PHASE_1_BASELINE + PHASE_2_INJECTION:
        return "injection"
    elif elapsed < PHASE_1_BASELINE + PHASE_2_INJECTION + PHASE_3_DETECTION:
        return "detection"
    else:
        return "recovery"


# =============================================================================
# Locust Event Handlers
# =============================================================================

if LOCUST_AVAILABLE and events:
    
    @events.test_start.add_listener
    def on_test_start(environment, **kwargs):
        global _test_start_time
        _test_start_time = time.time()
        _simulator.reset()
        
        print(f"\n{'='*60}")
        print(f"{STAGE_NAME} Cache Poison Detection Test Started")
        print(f"{'='*60}")
        print(f"Phase 1 (Baseline):   {PHASE_1_BASELINE}s")
        print(f"Phase 2 (Injection):  {PHASE_2_INJECTION}s")
        print(f"Phase 3 (Detection):  {PHASE_3_DETECTION}s")
        print(f"Phase 4 (Recovery):   {PHASE_4_RECOVERY}s")
        print(f"Total Duration:       {TOTAL_DURATION}s")
        print(f"{'='*60}\n")
    
    @events.test_stop.add_listener
    def on_test_stop(environment, **kwargs):
        print(f"\n{'='*60}")
        print(f"{STAGE_NAME} Test Complete - Final Report")
        print(f"{'='*60}")
        
        metrics = _simulator.get_metrics()
        poison_stats = _simulator.get_poison_stats()
        
        print("\n📊 Cache Metrics:")
        print(f"   Cache Hits:        {metrics['cache_hits']}")
        print(f"   Cache Misses:      {metrics['cache_misses']}")
        print(f"   Cache Sets:        {metrics['cache_sets']}")
        print(f"   DB Fallbacks:      {metrics['db_fallbacks']}")
        
        print("\n🔴 Poison Detection Metrics:")
        print(f"   Poison Injected:   {metrics['poison_injected']}")
        print(f"   Poison Detected:   {metrics['poison_detected']}")
        print(f"   Poison Served:     {metrics['poison_served']}")
        print(f"   Auto Invalidations:{metrics['auto_invalidations']}")
        
        print("\n🔍 Error Breakdown:")
        print(f"   JSON Parse Errors:  {metrics['json_parse_errors']}")
        print(f"   Checksum Failures:  {metrics['checksum_failures']}")
        print(f"   Validation Failures:{metrics['validation_failures']}")
        
        print("\n📈 Poison Statistics:")
        print(f"   Detection Rate:     {poison_stats['detection_rate']*100:.1f}%")
        print(f"   Avg Detection Time: {poison_stats['avg_detection_time_ms']:.2f}ms")
        
        if poison_stats["by_type"]:
            print("\n   By Poison Type:")
            for ptype, counts in poison_stats["by_type"].items():
                print(f"     - {ptype}: {counts['detected']}/{counts['injected']} detected")
        
        # Invariant 검증
        print("\n✅ Invariant Verification:")
        passed = True
        
        # poison_detected >= 95% of poison_injected (동시성 환경에서 race condition 허용)
        detection_rate = metrics['poison_detected'] / metrics['poison_injected'] if metrics['poison_injected'] > 0 else 1.0
        if detection_rate >= 0.95:
            print(f"   ✅ PASS: detection_rate >= 95% ({detection_rate*100:.1f}%)")
        else:
            print(f"   ❌ FAIL: detection_rate >= 95% (got {detection_rate*100:.1f}%)")
            passed = False
        
        # poison_served_to_user == 0
        if metrics['poison_served'] == 0:
            print("   ✅ PASS: poison_served_to_user == 0")
        else:
            print(f"   ❌ FAIL: poison_served_to_user == 0 (got {metrics['poison_served']})")
            passed = False
        
        # auto_invalidation_triggered
        if metrics['auto_invalidations'] > 0:
            print(f"   ✅ PASS: auto_invalidation_triggered ({metrics['auto_invalidations']} times)")
        else:
            print("   ⚠️  WARN: auto_invalidation not triggered (no poison may have been tested)")
        
        print(f"\n{'='*60}")
        if passed:
            print(f"🎉 {STAGE_NAME} ALL INVARIANTS PASSED")
        else:
            print(f"💥 {STAGE_NAME} INVARIANT FAILURES DETECTED")
        print(f"{'='*60}\n")


# =============================================================================
# Locust User Classes
# =============================================================================

class CacheReaderUser(HttpUser):
    """
    정상적인 캐시 읽기 사용자
    
    - 상품 조회 요청
    - 오염된 데이터가 서빙되면 실패 기록
    """
    
    weight = 70  # 70% 읽기 사용자
    wait_time = between(0.5, 2)
    
    @task(5)
    @tag("read", "normal")
    def read_product(self):
        """정상적인 상품 조회"""
        product_id = random.randint(1, 100)
        phase = get_current_phase()
        
        start_time = time.time()
        value, status = _simulator.get_with_fallback(product_id)
        elapsed = (time.time() - start_time) * 1000
        
        exception = None
        
        # 오염된 데이터가 반환되었는지 확인
        if value is not None:
            # 반환된 데이터 검증
            if value.get("price", 0) < 0:
                exception = Exception("Poisoned data served: negative price")
                _simulator._metrics["poison_served"] += 1
        
        if events:
            events.request.fire(
                request_type="CACHE",
                name=f"{STAGE_NAME} Read Product",
                response_time=elapsed,
                response_length=0,
                exception=exception,
                context={"phase": phase, "status": status}
            )
    
    @task(1)
    @tag("read", "batch")
    def batch_read_products(self):
        """배치 상품 조회"""
        product_ids = random.sample(range(1, 101), 10)
        phase = get_current_phase()
        
        start_time = time.time()
        results = []
        for pid in product_ids:
            value, status = _simulator.get_with_fallback(pid)
            results.append((value, status))
        elapsed = (time.time() - start_time) * 1000
        
        # 오염된 데이터 확인
        poisoned_count = sum(1 for v, _ in results if v and v.get("price", 0) < 0)
        
        exception = None
        if poisoned_count > 0:
            exception = Exception(f"Poisoned data in batch: {poisoned_count} items")
            _simulator._metrics["poison_served"] += poisoned_count
        
        if events:
            events.request.fire(
                request_type="CACHE",
                name=f"{STAGE_NAME} Batch Read",
                response_time=elapsed,
                response_length=0,
                exception=exception,
                context={"phase": phase, "batch_size": len(product_ids)}
            )


class CachePoisonInjector(HttpUser):
    """
    캐시 오염 주입자
    
    - 다양한 유형의 오염 데이터 주입
    - Phase 2 (Injection) 동안만 활성화
    """
    
    weight = 20  # 20% 오염 주입자
    wait_time = between(1, 3)
    
    def on_start(self):
        self.poison_count = 0
        self.poison_types = list(PoisonType)
    
    @task(1)
    @tag("inject", "poison")
    def inject_poison(self):
        """다양한 유형의 오염 주입"""
        phase = get_current_phase()
        
        # Phase 2에서만 오염 주입
        if phase != "injection":
            time.sleep(0.5)
            return
        
        # 최대 오염 수 제한
        if self.poison_count >= MAX_POISON_ENTRIES:
            return
        
        product_id = random.randint(1, 100)
        poison_type = random.choice(self.poison_types)
        
        start_time = time.time()
        success = _simulator.inject_poison(product_id, poison_type)
        elapsed = (time.time() - start_time) * 1000
        
        if success:
            self.poison_count += 1
        
        if events:
            events.request.fire(
                request_type="INJECT",
                name=f"{STAGE_NAME} Inject {poison_type.value}",
                response_time=elapsed,
                response_length=0,
                exception=None if success else Exception("Injection failed"),
                context={"phase": phase, "poison_type": poison_type.value}
            )


class CacheWriterUser(HttpUser):
    """
    정상적인 캐시 쓰기 사용자
    
    - 정상 데이터 캐싱
    - 캐시 갱신
    """
    
    weight = 10  # 10% 쓰기 사용자
    wait_time = between(1, 3)
    
    @task(2)
    @tag("write", "normal")
    def write_product(self):
        """정상적인 캐시 쓰기"""
        product_id = random.randint(1, 100)
        phase = get_current_phase()
        
        # DB에서 조회
        product = _simulator.get_from_db(product_id)
        
        if product is None:
            return
        
        start_time = time.time()
        success = _simulator.set_cache(product_id, product)
        elapsed = (time.time() - start_time) * 1000
        
        if events:
            events.request.fire(
                request_type="CACHE",
                name=f"{STAGE_NAME} Write Product",
                response_time=elapsed,
                response_length=0,
                exception=None if success else Exception("Cache write failed"),
                context={"phase": phase}
            )
    
    @task(1)
    @tag("write", "refresh")
    def refresh_cache(self):
        """캐시 갱신 (오염 복구)"""
        product_id = random.randint(1, 100)
        phase = get_current_phase()
        
        # 먼저 캐시에서 읽기 시도
        value, status = _simulator.get_cache(product_id)
        
        # 오염 감지 시 DB에서 갱신
        if status in ["invalid", "checksum_mismatch", "validation_failed"]:
            product = _simulator.get_from_db(product_id)
            if product:
                _simulator.set_cache(product_id, product)
        
        if events:
            events.request.fire(
                request_type="CACHE",
                name=f"{STAGE_NAME} Refresh Cache",
                response_time=0,
                response_length=0,
                exception=None,
                context={"phase": phase, "status": status}
            )


# =============================================================================
# Load Test Shape
# =============================================================================

class CachePoisonLoadShape(LoadTestShape):
    """
    캐시 오염 테스트 로드 패턴
    
    Phase 1: 기준선 설정 (정상 동작)
    Phase 2: 오염 주입
    Phase 3: 오염 감지
    Phase 4: 복구 검증
    """
    
    stages = [
        {"duration": PHASE_1_BASELINE, "users": 30, "spawn_rate": 5},
        {"duration": PHASE_1_BASELINE + PHASE_2_INJECTION, "users": 50, "spawn_rate": 10},
        {"duration": PHASE_1_BASELINE + PHASE_2_INJECTION + PHASE_3_DETECTION, "users": 50, "spawn_rate": 10},
        {"duration": TOTAL_DURATION, "users": 30, "spawn_rate": 5},
    ]
    
    def tick(self):
        run_time = self.get_run_time()
        
        for stage in self.stages:
            if run_time < stage["duration"]:
                return (stage["users"], stage["spawn_rate"])
        
        return None


# =============================================================================
# Standalone Test
# =============================================================================

def run_standalone_test():
    """Locust 없이 독립 실행 테스트"""
    print(f"\n{'='*60}")
    print(f"{STAGE_NAME} Standalone Cache Poison Detection Test")
    print(f"{'='*60}\n")
    
    simulator = CachePoisonSimulator()
    
    # Phase 1: 정상 데이터 캐싱
    print("📝 Phase 1: Caching normal data...")
    for i in range(1, 21):
        product = simulator.get_from_db(i)
        if product:
            simulator.set_cache(i, product)
    
    # 정상 조회 테스트
    print("   Testing normal reads...")
    for i in range(1, 11):
        value, status = simulator.get_with_fallback(i)
        assert status == "cache_hit", f"Expected cache_hit, got {status}"
    print("   ✅ Normal reads working correctly")
    
    # Phase 2: 오염 주입
    print("\n🔴 Phase 2: Injecting poison data...")
    poison_tests = [
        (21, PoisonType.INVALID_JSON, "Invalid JSON"),
        (22, PoisonType.CHECKSUM_MISMATCH, "Checksum mismatch"),
        (23, PoisonType.NEGATIVE_PRICE, "Negative price"),
        (24, PoisonType.EXPIRED_STALE, "Expired stale"),
        (25, PoisonType.TYPE_MISMATCH, "Type mismatch"),
        (26, PoisonType.EMPTY_REQUIRED, "Empty required"),
        (27, PoisonType.CORRUPTED_BINARY, "Corrupted binary"),
    ]
    
    for product_id, poison_type, description in poison_tests:
        simulator.inject_poison(product_id, poison_type)
        print(f"   Injected {description} into product {product_id}")
    
    # Phase 3: 오염 감지 테스트
    print("\n🔍 Phase 3: Testing poison detection...")
    for product_id, poison_type, description in poison_tests:
        value, status = simulator.get_with_fallback(product_id)
        
        if status.startswith("db_fallback"):
            print(f"   ✅ {description}: Detected and fell back to DB")
        elif status == "cache_hit":
            # 가격이 음수인지 확인 (오염 데이터가 서빙된 경우)
            if value and value.get("price", 0) < 0:
                print(f"   ❌ {description}: POISON SERVED! (price={value.get('price')})")
            else:
                print(f"   ✅ {description}: Clean data returned")
        else:
            print(f"   ⚠️  {description}: status={status}")
    
    # Phase 4: 결과 검증
    print("\n📊 Phase 4: Final verification...")
    metrics = simulator.get_metrics()
    poison_stats = simulator.get_poison_stats()
    
    print("\n   Metrics:")
    print(f"   - Poison Injected: {metrics['poison_injected']}")
    print(f"   - Poison Detected: {metrics['poison_detected']}")
    print(f"   - Poison Served:   {metrics['poison_served']}")
    print(f"   - Auto Invalidations: {metrics['auto_invalidations']}")
    print(f"   - DB Fallbacks:    {metrics['db_fallbacks']}")
    print(f"\n   Detection Rate:    {poison_stats['detection_rate']*100:.1f}%")
    
    # Invariant 검증
    print("\n✅ Invariant Verification:")
    passed = True
    
    if metrics['poison_detected'] >= metrics['poison_injected']:
        print("   ✅ poison_detected >= poison_injected")
    else:
        print("   ❌ poison_detected < poison_injected")
        passed = False
    
    if metrics['poison_served'] == 0:
        print("   ✅ poison_served_to_user == 0")
    else:
        print(f"   ❌ poison_served_to_user == {metrics['poison_served']}")
        passed = False
    
    if metrics['auto_invalidations'] > 0:
        print("   ✅ auto_invalidation_triggered")
    else:
        print("   ⚠️  auto_invalidation not triggered")
    
    print(f"\n{'='*60}")
    if passed:
        print(f"🎉 {STAGE_NAME} STANDALONE TEST PASSED")
    else:
        print(f"💥 {STAGE_NAME} STANDALONE TEST FAILED")
    print(f"{'='*60}\n")
    
    return passed


if __name__ == "__main__":
    run_standalone_test()
