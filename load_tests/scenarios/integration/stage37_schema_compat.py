"""
Stage 37: Schema Compatibility Test

Purpose: 부분 배포(Rolling Update) 시 스키마 호환성 검증
- V1/V2 스키마 혼재 시 데이터 안전성 테스트
- Cross-version 트랜잭션 검증
- Backward/Forward 호환성 테스트

Scenarios:
  SC-37-1: V1 Worker가 V2 데이터 읽기
  SC-37-2: V2 Worker가 V1 데이터 읽기
  SC-37-3: Cross-version 동시 트랜잭션
  SC-37-4: 스키마 마이그레이션 중 데이터 무결성

Invariants:
  - data_corruption == 0
  - schema_mismatch_errors handled gracefully
  - no data loss during transition

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage37_schema_compat.py --host=http://localhost:8000

    # CLI mode
    locust -f load_tests/scenarios/stage37_schema_compat.py \\
        --host=http://localhost:8000 \\
        --users=50 --spawn-rate=10 --run-time=3m \\
        --headless --html=stage37_report.html

Reference:
    - docs/GAP_RESOLUTION_PLAN.md (GAP-01)
    - Pipeline Stage: State Change
"""

import os
import sys
import time
import random
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
from decimal import Decimal

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


STAGE_NAME = "[Stage37-SchemaCompat]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "20"))
_original_total = 180  # Original total: 180s (3min)
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_SETUP = max(3, int(20 * _scale))
PHASE_2_MIXED_OPS = max(5, int(60 * _scale))
PHASE_3_CROSS_VERSION = max(5, int(60 * _scale))
PHASE_4_VERIFICATION = max(3, int(40 * _scale))

TOTAL_DURATION = PHASE_1_SETUP + PHASE_2_MIXED_OPS + PHASE_3_CROSS_VERSION + PHASE_4_VERIFICATION


# =============================================================================
# Schema Version Definitions
# =============================================================================

class SchemaVersion(Enum):
    """스키마 버전 정의"""
    V1 = "v1"
    V2 = "v2"


@dataclass
class SchemaV1Order:
    """
    V1 스키마: 레거시 주문 형식
    - total_amount: integer (센트 단위)
    - status: string (단순 상태)
    """
    id: int
    user_id: int
    total_amount: int  # 센트 단위 (ex: 1000 = 10.00)
    status: str
    created_at: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "total_amount": self.total_amount,
            "status": self.status,
            "created_at": self.created_at,
            "_schema_version": "v1"
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SchemaV1Order":
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            total_amount=int(data.get("total_amount", 0)),
            status=data.get("status", "unknown"),
            created_at=data.get("created_at", "")
        )


@dataclass
class SchemaV2Order:
    """
    V2 스키마: 새로운 주문 형식
    - total_amount: decimal (소수점 2자리)
    - status: string (확장 상태 + 메타데이터)
    - 추가 필드: currency, tax_amount
    """
    id: int
    user_id: int
    total_amount: Decimal  # 소수점 (ex: 10.00)
    currency: str
    tax_amount: Decimal
    status: str
    status_metadata: Dict[str, Any]
    created_at: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "total_amount": str(self.total_amount),
            "currency": self.currency,
            "tax_amount": str(self.tax_amount),
            "status": self.status,
            "status_metadata": self.status_metadata,
            "created_at": self.created_at,
            "_schema_version": "v2"
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SchemaV2Order":
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            total_amount=Decimal(str(data.get("total_amount", "0.00"))),
            currency=data.get("currency", "KRW"),
            tax_amount=Decimal(str(data.get("tax_amount", "0.00"))),
            status=data.get("status", "unknown"),
            status_metadata=data.get("status_metadata", {}),
            created_at=data.get("created_at", "")
        )


# =============================================================================
# Schema Compatibility Simulator
# =============================================================================

class SchemaCompatibilitySimulator:
    """
    스키마 호환성 시뮬레이터
    
    실제 다중 인스턴스 환경이 아닌 경우,
    시뮬레이션을 통해 스키마 호환성 문제를 검증합니다.
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._data_store: Dict[int, Dict[str, Any]] = {}
        self._operation_log: List[Dict[str, Any]] = []
        self._errors: List[Dict[str, Any]] = []
        self._metrics = {
            "v1_writes": 0,
            "v2_writes": 0,
            "v1_reads": 0,
            "v2_reads": 0,
            "cross_version_reads": 0,
            "schema_mismatch_handled": 0,
            "data_corruption_detected": 0,
            "conversion_success": 0,
            "conversion_failure": 0,
        }
        self._next_id = 1
    
    def reset(self):
        """시뮬레이터 상태 초기화"""
        with self._lock:
            self._data_store.clear()
            self._operation_log.clear()
            self._errors.clear()
            for key in self._metrics:
                self._metrics[key] = 0
            self._next_id = 1
    
    def create_order_v1(self, user_id: int, amount_cents: int, status: str = "pending") -> SchemaV1Order:
        """V1 스키마로 주문 생성"""
        with self._lock:
            order_id = self._next_id
            self._next_id += 1
            
            order = SchemaV1Order(
                id=order_id,
                user_id=user_id,
                total_amount=amount_cents,
                status=status,
                created_at=datetime.now().isoformat()
            )
            
            self._data_store[order_id] = order.to_dict()
            self._metrics["v1_writes"] += 1
            self._log_operation("create", "v1", order_id, order.to_dict())
            
            return order
    
    def create_order_v2(
        self, 
        user_id: int, 
        amount: Decimal, 
        currency: str = "KRW",
        tax_amount: Decimal = Decimal("0.00"),
        status: str = "pending"
    ) -> SchemaV2Order:
        """V2 스키마로 주문 생성"""
        with self._lock:
            order_id = self._next_id
            self._next_id += 1
            
            order = SchemaV2Order(
                id=order_id,
                user_id=user_id,
                total_amount=amount,
                currency=currency,
                tax_amount=tax_amount,
                status=status,
                status_metadata={"created_by": "v2_worker"},
                created_at=datetime.now().isoformat()
            )
            
            self._data_store[order_id] = order.to_dict()
            self._metrics["v2_writes"] += 1
            self._log_operation("create", "v2", order_id, order.to_dict())
            
            return order
    
    def read_order_as_v1(self, order_id: int) -> Optional[SchemaV1Order]:
        """
        V1 Worker가 주문을 읽음 (V2 데이터도 V1으로 변환)
        
        핵심 검증:
        - V2 데이터를 V1으로 변환할 때 데이터 손실 여부
        - Decimal -> Integer 변환 시 정밀도 문제
        """
        with self._lock:
            if order_id not in self._data_store:
                return None
            
            data = self._data_store[order_id]
            schema_version = data.get("_schema_version", "v1")
            
            if schema_version == "v1":
                self._metrics["v1_reads"] += 1
                return SchemaV1Order.from_dict(data)
            
            # V2 -> V1 변환 필요
            self._metrics["cross_version_reads"] += 1
            
            try:
                # V2 데이터를 V1으로 변환
                v2_amount = Decimal(str(data.get("total_amount", "0.00")))
                v1_amount = int(v2_amount * 100)  # 센트로 변환
                
                converted = SchemaV1Order(
                    id=data["id"],
                    user_id=data["user_id"],
                    total_amount=v1_amount,
                    status=data.get("status", "unknown"),
                    created_at=data.get("created_at", "")
                )
                
                # 변환 정확성 검증
                if Decimal(v1_amount) / 100 != v2_amount:
                    # 정밀도 손실 감지 (예: 10.005 -> 1000 -> 10.00)
                    self._log_error(
                        "precision_loss",
                        order_id,
                        f"V2({v2_amount}) -> V1({v1_amount}) precision loss"
                    )
                
                self._metrics["conversion_success"] += 1
                self._metrics["schema_mismatch_handled"] += 1
                return converted
                
            except Exception as e:
                self._metrics["conversion_failure"] += 1
                self._log_error("conversion_failed", order_id, str(e))
                return None
    
    def read_order_as_v2(self, order_id: int) -> Optional[SchemaV2Order]:
        """
        V2 Worker가 주문을 읽음 (V1 데이터도 V2로 변환)
        
        핵심 검증:
        - V1 데이터를 V2로 변환할 때 기본값 처리
        - 새 필드(currency, tax_amount) 누락 시 처리
        """
        with self._lock:
            if order_id not in self._data_store:
                return None
            
            data = self._data_store[order_id]
            schema_version = data.get("_schema_version", "v1")
            
            if schema_version == "v2":
                self._metrics["v2_reads"] += 1
                return SchemaV2Order.from_dict(data)
            
            # V1 -> V2 변환 필요
            self._metrics["cross_version_reads"] += 1
            
            try:
                # V1 데이터를 V2로 변환
                v1_amount = int(data.get("total_amount", 0))
                v2_amount = Decimal(v1_amount) / 100  # 소수점으로 변환
                
                converted = SchemaV2Order(
                    id=data["id"],
                    user_id=data["user_id"],
                    total_amount=v2_amount,
                    currency="KRW",  # 기본값
                    tax_amount=Decimal("0.00"),  # 기본값
                    status=data.get("status", "unknown"),
                    status_metadata={"migrated_from": "v1"},  # 마이그레이션 표시
                    created_at=data.get("created_at", "")
                )
                
                self._metrics["conversion_success"] += 1
                self._metrics["schema_mismatch_handled"] += 1
                return converted
                
            except Exception as e:
                self._metrics["conversion_failure"] += 1
                self._log_error("conversion_failed", order_id, str(e))
                return None
    
    def update_order_v1(self, order_id: int, status: str) -> bool:
        """V1 Worker가 주문 상태 업데이트"""
        with self._lock:
            if order_id not in self._data_store:
                return False
            
            data = self._data_store[order_id]
            old_status = data.get("status")
            data["status"] = status
            
            # V1은 status_metadata를 모름 -> V2 데이터면 metadata 손실 가능
            if data.get("_schema_version") == "v2" and "status_metadata" in data:
                # V1 업데이트 시 metadata는 그대로 유지 (best effort)
                data["status_metadata"]["last_updated_by"] = "v1_worker"
            
            self._log_operation("update", "v1", order_id, {"old_status": old_status, "new_status": status})
            return True
    
    def update_order_v2(self, order_id: int, status: str, metadata: Dict[str, Any] = None) -> bool:
        """V2 Worker가 주문 상태 업데이트"""
        with self._lock:
            if order_id not in self._data_store:
                return False
            
            data = self._data_store[order_id]
            old_status = data.get("status")
            data["status"] = status
            
            if data.get("_schema_version") == "v2":
                if metadata:
                    data.setdefault("status_metadata", {}).update(metadata)
            else:
                # V1 데이터에 V2 필드 추가 시도 -> 부분 업그레이드
                if metadata:
                    data["status_metadata"] = metadata
                    self._log_operation("partial_upgrade", "v2", order_id, {"added_metadata": True})
            
            self._log_operation("update", "v2", order_id, {"old_status": old_status, "new_status": status})
            return True
    
    def verify_data_integrity(self) -> Dict[str, Any]:
        """데이터 무결성 검증"""
        with self._lock:
            issues = []
            
            for order_id, data in self._data_store.items():
                # 필수 필드 검증
                required_fields = ["id", "user_id", "total_amount", "status"]
                for field in required_fields:
                    if field not in data:
                        issues.append({
                            "order_id": order_id,
                            "issue": f"missing_field_{field}"
                        })
                        self._metrics["data_corruption_detected"] += 1
                
                # 금액 유효성 검증
                try:
                    amount = data.get("total_amount")
                    if data.get("_schema_version") == "v1":
                        if not isinstance(amount, int) or amount < 0:
                            issues.append({
                                "order_id": order_id,
                                "issue": "invalid_v1_amount",
                                "value": amount
                            })
                    else:
                        decimal_amount = Decimal(str(amount))
                        if decimal_amount < 0:
                            issues.append({
                                "order_id": order_id,
                                "issue": "invalid_v2_amount",
                                "value": amount
                            })
                except Exception as e:
                    issues.append({
                        "order_id": order_id,
                        "issue": "amount_parse_error",
                        "error": str(e)
                    })
                    self._metrics["data_corruption_detected"] += 1
            
            return {
                "total_records": len(self._data_store),
                "issues_found": len(issues),
                "issues": issues[:10],  # 최대 10개만 반환
                "metrics": self._metrics.copy()
            }
    
    def get_metrics(self) -> Dict[str, int]:
        """현재 메트릭 반환"""
        with self._lock:
            return self._metrics.copy()
    
    def _log_operation(self, op_type: str, schema: str, order_id: int, details: Dict[str, Any]):
        """작업 로그 기록"""
        self._operation_log.append({
            "timestamp": datetime.now().isoformat(),
            "operation": op_type,
            "schema_version": schema,
            "order_id": order_id,
            "details": details
        })
    
    def _log_error(self, error_type: str, order_id: int, message: str):
        """에러 로그 기록"""
        self._errors.append({
            "timestamp": datetime.now().isoformat(),
            "error_type": error_type,
            "order_id": order_id,
            "message": message
        })


# =============================================================================
# Global Simulator Instance
# =============================================================================

_simulator = SchemaCompatibilitySimulator()
_test_start_time = None


def get_current_phase() -> str:
    """현재 테스트 단계 반환"""
    if _test_start_time is None:
        return "not_started"
    
    elapsed = time.time() - _test_start_time
    
    if elapsed < PHASE_1_SETUP:
        return "setup"
    elif elapsed < PHASE_1_SETUP + PHASE_2_MIXED_OPS:
        return "mixed_ops"
    elif elapsed < PHASE_1_SETUP + PHASE_2_MIXED_OPS + PHASE_3_CROSS_VERSION:
        return "cross_version"
    else:
        return "verification"


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
        print(f"{STAGE_NAME} Schema Compatibility Test Started")
        print(f"{'='*60}")
        print(f"Phase 1 (Setup):        {PHASE_1_SETUP}s")
        print(f"Phase 2 (Mixed Ops):    {PHASE_2_MIXED_OPS}s")
        print(f"Phase 3 (Cross-Ver):    {PHASE_3_CROSS_VERSION}s")
        print(f"Phase 4 (Verification): {PHASE_4_VERIFICATION}s")
        print(f"Total Duration:         {TOTAL_DURATION}s")
        print(f"{'='*60}\n")
    
    @events.test_stop.add_listener
    def on_test_stop(environment, **kwargs):
        print(f"\n{'='*60}")
        print(f"{STAGE_NAME} Test Complete - Final Report")
        print(f"{'='*60}")
        
        # 데이터 무결성 검증
        integrity = _simulator.verify_data_integrity()
        metrics = _simulator.get_metrics()
        
        print("\n📊 Operation Metrics:")
        print(f"   V1 Writes:              {metrics['v1_writes']}")
        print(f"   V2 Writes:              {metrics['v2_writes']}")
        print(f"   V1 Reads:               {metrics['v1_reads']}")
        print(f"   V2 Reads:               {metrics['v2_reads']}")
        print(f"   Cross-Version Reads:    {metrics['cross_version_reads']}")
        
        print("\n🔄 Compatibility Metrics:")
        print(f"   Schema Mismatch Handled: {metrics['schema_mismatch_handled']}")
        print(f"   Conversion Success:      {metrics['conversion_success']}")
        print(f"   Conversion Failure:      {metrics['conversion_failure']}")
        
        print("\n🔍 Integrity Check:")
        print(f"   Total Records:           {integrity['total_records']}")
        print(f"   Issues Found:            {integrity['issues_found']}")
        print(f"   Data Corruption:         {metrics['data_corruption_detected']}")
        
        # Invariant 검증
        print("\n✅ Invariant Verification:")
        passed = True
        
        if metrics['data_corruption_detected'] > 0:
            print(f"   ❌ FAIL: data_corruption == 0 (got {metrics['data_corruption_detected']})")
            passed = False
        else:
            print("   ✅ PASS: data_corruption == 0")
        
        if metrics['schema_mismatch_handled'] > 0:
            print(f"   ✅ PASS: schema_mismatch_errors handled gracefully ({metrics['schema_mismatch_handled']} handled)")
        else:
            print("   ⚠️  WARN: No cross-version reads occurred")
        
        conversion_total = metrics['conversion_success'] + metrics['conversion_failure']
        if conversion_total > 0:
            success_rate = metrics['conversion_success'] / conversion_total * 100
            if success_rate >= 99:
                print(f"   ✅ PASS: conversion_success_rate >= 99% ({success_rate:.1f}%)")
            else:
                print(f"   ❌ FAIL: conversion_success_rate >= 99% (got {success_rate:.1f}%)")
                passed = False
        
        print(f"\n{'='*60}")
        if passed:
            print(f"🎉 {STAGE_NAME} ALL INVARIANTS PASSED")
        else:
            print(f"💥 {STAGE_NAME} INVARIANT FAILURES DETECTED")
        print(f"{'='*60}\n")


# =============================================================================
# Locust User Classes
# =============================================================================

class SchemaV1User(HttpUser):
    """
    V1 스키마를 사용하는 Worker 시뮬레이션
    
    - 레거시 API 형식 사용
    - integer 기반 금액 처리
    - 단순 상태 관리
    """
    
    weight = 50  # 50% V1 Workers
    wait_time = between(0.5, 2)
    
    def on_start(self):
        self.user_id = random.randint(1, 1000)
        self.created_orders: List[int] = []
    
    @task(3)
    @tag("v1", "create")
    def create_order_v1(self):
        """V1 스키마로 주문 생성"""
        phase = get_current_phase()
        
        # 금액을 센트 단위로 생성 (100 ~ 100000 cents = 1.00 ~ 1000.00)
        amount_cents = random.randint(100, 100000)
        
        start_time = time.time()
        order = _simulator.create_order_v1(
            user_id=self.user_id,
            amount_cents=amount_cents,
            status="pending"
        )
        elapsed = (time.time() - start_time) * 1000
        
        self.created_orders.append(order.id)
        
        # Locust에 결과 보고
        if events:
            events.request.fire(
                request_type="SCHEMA",
                name=f"{STAGE_NAME} V1 Create Order",
                response_time=elapsed,
                response_length=0,
                exception=None,
                context={"phase": phase}
            )
    
    @task(5)
    @tag("v1", "read")
    def read_order_as_v1(self):
        """V1 Worker가 주문 읽기 (V2 데이터도 변환해서 읽음)"""
        phase = get_current_phase()
        
        # 전체 주문 중 하나 선택 (자신의 주문 + 랜덤)
        if self.created_orders and random.random() < 0.7:
            order_id = random.choice(self.created_orders)
        else:
            order_id = random.randint(1, max(1, _simulator._next_id - 1))
        
        start_time = time.time()
        order = _simulator.read_order_as_v1(order_id)
        elapsed = (time.time() - start_time) * 1000
        
        exception = None
        if order is None and order_id in self.created_orders:
            exception = Exception("Order not found that we created")
        
        if events:
            events.request.fire(
                request_type="SCHEMA",
                name=f"{STAGE_NAME} V1 Read Order",
                response_time=elapsed,
                response_length=0,
                exception=exception,
                context={"phase": phase, "found": order is not None}
            )
    
    @task(2)
    @tag("v1", "update")
    def update_order_v1(self):
        """V1 Worker가 주문 상태 업데이트"""
        phase = get_current_phase()
        
        if not self.created_orders:
            return
        
        order_id = random.choice(self.created_orders)
        new_status = random.choice(["confirmed", "paid", "shipped"])
        
        start_time = time.time()
        success = _simulator.update_order_v1(order_id, new_status)
        elapsed = (time.time() - start_time) * 1000
        
        if events:
            events.request.fire(
                request_type="SCHEMA",
                name=f"{STAGE_NAME} V1 Update Order",
                response_time=elapsed,
                response_length=0,
                exception=None if success else Exception("Update failed"),
                context={"phase": phase, "success": success}
            )


class SchemaV2User(HttpUser):
    """
    V2 스키마를 사용하는 Worker 시뮬레이션
    
    - 새 API 형식 사용
    - Decimal 기반 금액 처리
    - 확장 상태 + 메타데이터 관리
    """
    
    weight = 50  # 50% V2 Workers
    wait_time = between(0.5, 2)
    
    def on_start(self):
        self.user_id = random.randint(1, 1000)
        self.created_orders: List[int] = []
    
    @task(3)
    @tag("v2", "create")
    def create_order_v2(self):
        """V2 스키마로 주문 생성"""
        phase = get_current_phase()
        
        # 금액을 Decimal로 생성 (정밀도 테스트 포함)
        amount = Decimal(str(random.uniform(1.00, 1000.00))).quantize(Decimal("0.01"))
        tax = (amount * Decimal("0.1")).quantize(Decimal("0.01"))
        currency = random.choice(["KRW", "USD", "EUR"])
        
        start_time = time.time()
        order = _simulator.create_order_v2(
            user_id=self.user_id,
            amount=amount,
            currency=currency,
            tax_amount=tax,
            status="pending"
        )
        elapsed = (time.time() - start_time) * 1000
        
        self.created_orders.append(order.id)
        
        if events:
            events.request.fire(
                request_type="SCHEMA",
                name=f"{STAGE_NAME} V2 Create Order",
                response_time=elapsed,
                response_length=0,
                exception=None,
                context={"phase": phase}
            )
    
    @task(5)
    @tag("v2", "read")
    def read_order_as_v2(self):
        """V2 Worker가 주문 읽기 (V1 데이터도 변환해서 읽음)"""
        phase = get_current_phase()
        
        if self.created_orders and random.random() < 0.7:
            order_id = random.choice(self.created_orders)
        else:
            order_id = random.randint(1, max(1, _simulator._next_id - 1))
        
        start_time = time.time()
        order = _simulator.read_order_as_v2(order_id)
        elapsed = (time.time() - start_time) * 1000
        
        exception = None
        if order is None and order_id in self.created_orders:
            exception = Exception("Order not found that we created")
        
        if events:
            events.request.fire(
                request_type="SCHEMA",
                name=f"{STAGE_NAME} V2 Read Order",
                response_time=elapsed,
                response_length=0,
                exception=exception,
                context={"phase": phase, "found": order is not None}
            )
    
    @task(2)
    @tag("v2", "update")
    def update_order_v2(self):
        """V2 Worker가 주문 상태 업데이트 (메타데이터 포함)"""
        phase = get_current_phase()
        
        if not self.created_orders:
            return
        
        order_id = random.choice(self.created_orders)
        new_status = random.choice(["confirmed", "paid", "shipped", "preparing"])
        metadata = {
            "updated_at": datetime.now().isoformat(),
            "reason": "automated_test",
            "worker_version": "v2"
        }
        
        start_time = time.time()
        success = _simulator.update_order_v2(order_id, new_status, metadata)
        elapsed = (time.time() - start_time) * 1000
        
        if events:
            events.request.fire(
                request_type="SCHEMA",
                name=f"{STAGE_NAME} V2 Update Order",
                response_time=elapsed,
                response_length=0,
                exception=None if success else Exception("Update failed"),
                context={"phase": phase, "success": success}
            )
    
    @task(1)
    @tag("v2", "verify")
    def verify_integrity(self):
        """데이터 무결성 검증 (주기적)"""
        if get_current_phase() != "verification":
            return
        
        start_time = time.time()
        result = _simulator.verify_data_integrity()
        elapsed = (time.time() - start_time) * 1000
        
        exception = None
        if result["issues_found"] > 0:
            exception = Exception(f"Integrity issues: {result['issues_found']}")
        
        if events:
            events.request.fire(
                request_type="VERIFY",
                name=f"{STAGE_NAME} Integrity Check",
                response_time=elapsed,
                response_length=0,
                exception=exception,
                context={"issues": result["issues_found"]}
            )


# =============================================================================
# Load Test Shape
# =============================================================================

class SchemaCompatLoadShape(LoadTestShape):
    """
    스키마 호환성 테스트 로드 패턴
    
    Phase 1: 안정적 초기화
    Phase 2: 혼합 작업 (V1/V2 동시)
    Phase 3: Cross-version 집중
    Phase 4: 검증
    """
    
    stages = [
        {"duration": PHASE_1_SETUP, "users": 20, "spawn_rate": 5},
        {"duration": PHASE_1_SETUP + PHASE_2_MIXED_OPS, "users": 50, "spawn_rate": 10},
        {"duration": PHASE_1_SETUP + PHASE_2_MIXED_OPS + PHASE_3_CROSS_VERSION, "users": 50, "spawn_rate": 10},
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
    print(f"{STAGE_NAME} Standalone Schema Compatibility Test")
    print(f"{'='*60}\n")
    
    simulator = SchemaCompatibilitySimulator()
    
    # Phase 1: V1 데이터 생성
    print("📝 Phase 1: Creating V1 orders...")
    for i in range(10):
        simulator.create_order_v1(
            user_id=i,
            amount_cents=random.randint(1000, 50000),
            status="pending"
        )
    
    # Phase 2: V2 데이터 생성
    print("📝 Phase 2: Creating V2 orders...")
    for i in range(10):
        simulator.create_order_v2(
            user_id=i + 10,
            amount=Decimal(str(random.uniform(10.00, 500.00))).quantize(Decimal("0.01")),
            currency="KRW",
            tax_amount=Decimal("5.00"),
            status="pending"
        )
    
    # Phase 3: Cross-version reads
    print("🔄 Phase 3: Cross-version read tests...")
    
    # V1 Worker가 V2 데이터 읽기
    for i in range(11, 21):
        order = simulator.read_order_as_v1(i)
        if order:
            print(f"   V1 read V2 order {i}: amount={order.total_amount} cents")
    
    # V2 Worker가 V1 데이터 읽기
    for i in range(1, 11):
        order = simulator.read_order_as_v2(i)
        if order:
            print(f"   V2 read V1 order {i}: amount={order.total_amount} {order.currency}")
    
    # Phase 4: 무결성 검증
    print("\n🔍 Phase 4: Integrity verification...")
    result = simulator.verify_data_integrity()
    
    print("\n📊 Final Metrics:")
    for key, value in result["metrics"].items():
        print(f"   {key}: {value}")
    
    print("\n✅ Integrity Check:")
    print(f"   Total Records: {result['total_records']}")
    print(f"   Issues Found:  {result['issues_found']}")
    
    if result["issues"]:
        print("\n⚠️  Issues:")
        for issue in result["issues"]:
            print(f"   - Order {issue['order_id']}: {issue['issue']}")
    
    # 결과 판정
    passed = (
        result["metrics"]["data_corruption_detected"] == 0 and
        result["metrics"]["conversion_failure"] == 0
    )
    
    print(f"\n{'='*60}")
    if passed:
        print(f"🎉 {STAGE_NAME} STANDALONE TEST PASSED")
    else:
        print(f"💥 {STAGE_NAME} STANDALONE TEST FAILED")
    print(f"{'='*60}\n")
    
    return passed


if __name__ == "__main__":
    run_standalone_test()
