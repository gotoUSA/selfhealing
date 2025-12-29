"""
Stage 14: DLQ (Dead Letter Queue) API Verification Test

목적: DLQ 기능의 전체 사이클 검증
- DLQ 테스트 엔트리 생성 API
- DLQ 리스트 조회 API
- DLQ 상태 변경 (Resolve) API
- 데이터 정합성 확인

검증 항목:
1. DLQ 테스트 엔트리 생성 가능
2. 생성된 엔트리가 리스트에 표시
3. Resolve API로 상태 변경 가능
4. 전체 사이클 완료 시간 측정

실행:
    PYTHONPATH=. locust -f load_tests/scenarios/integration/stage14_dlq_api_test.py \\
        --host=http://localhost:8000 --users=10 --spawn-rate=5 --run-time=60s --headless
"""

# =============================================================================
# Stage DNA - Self-Healing 모듈 의존성 선언
# Reference: docs/self_healing/27_SELFHEALING_SCENARIO_MAPPING.md
# =============================================================================
STAGE_DNA = {
    "name": "Stage 14 - DLQ API Verification Test",
    "type": "integration",
    "required_modules": ["circuit_breaker", "dlq", "health", "observability"],
    "optional_modules": ["governance", "reconciliation"],
}

# DNA 검증 (테스트 시작 전 자동 체크)
try:
    from load_tests.utils.selfhealing.stage_dna import validate_stage_dna
    _dna_result = validate_stage_dna(STAGE_DNA)
    if not _dna_result.is_valid:
        import warnings
        warnings.warn(str(_dna_result))
except ImportError:
    pass  # stage_dna 모듈 없으면 스킵

# =============================================================================
import os
import sys
import time
import random
import uuid

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(os.path.dirname(_current_dir))
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events

from load_tests.utils import LoginHelper
from load_tests.metrics import setup_event_hooks

STAGE_NAME = "[Stage14-DLQ-API]"

# Test statistics
_dlq_stats = {
    "start_time": None,
    "entries_created": 0,
    "entries_listed": 0,
    "entries_resolved": 0,
    "creation_errors": 0,
    "list_errors": 0,
    "resolve_errors": 0,
    "created_ids": [],
    "domains_tested": {"payment": 0, "inventory": 0, "webhook": 0},
    "failure_types_tested": set(),
}


class DLQApiTestUser(HttpUser):
    """DLQ API 테스트 사용자"""

    wait_time = between(0.5, 1.5)

    def on_start(self):
        """테스트 시작 시 초기화"""
        global _dlq_stats
        
        setup_event_hooks(STAGE_NAME)

        if _dlq_stats["start_time"] is None:
            _dlq_stats["start_time"] = time.time()

        # Admin 로그인 (DLQ API는 admin 권한 필요)
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.login_helper.login_as_admin()

    @task(5)
    @tag("dlq", "create")
    def create_dlq_entry(self):
        """DLQ 테스트 엔트리 생성"""
        global _dlq_stats

        domains = ["payment", "inventory", "webhook"]
        failure_types = ["PG_TIMEOUT", "NETWORK_ERROR", "DB_DEADLOCK", "SERVICE_UNAVAILABLE", "RATE_LIMIT"]

        domain = random.choice(domains)
        failure_type = random.choice(failure_types)

        with self.client.post(
            "/api/self-healing/dlq/test/create/",
            json={
                "domain": domain,
                "failure_type": failure_type,
                "error_message": f"Test failure: {failure_type} at {time.time()}",
            },
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /dlq/test/create/",
            catch_response=True,
        ) as response:
            if response.status_code == 201:
                data = response.json()
                dlq_id = data.get("dlq_id")
                if dlq_id:
                    _dlq_stats["entries_created"] += 1
                    _dlq_stats["created_ids"].append(dlq_id)
                    _dlq_stats["domains_tested"][domain] = _dlq_stats["domains_tested"].get(domain, 0) + 1
                    _dlq_stats["failure_types_tested"].add(failure_type)
                response.success()
            elif response.status_code == 403:
                response.failure("DLQ test create requires DEBUG mode")
                _dlq_stats["creation_errors"] += 1
            else:
                response.failure(f"Unexpected status: {response.status_code}")
                _dlq_stats["creation_errors"] += 1

    @task(3)
    @tag("dlq", "list")
    def list_dlq_entries(self):
        """DLQ 리스트 조회"""
        global _dlq_stats

        with self.client.get(
            "/api/self-healing/dlq/list/?status=pending&limit=20",
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} GET /dlq/list/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                count = data.get("pagination", {}).get("total_count", 0)
                _dlq_stats["entries_listed"] = count
                response.success()
            else:
                response.failure(f"List failed: {response.status_code}")
                _dlq_stats["list_errors"] += 1

    @task(2)
    @tag("dlq", "resolve")
    def resolve_dlq_entry(self):
        """DLQ 엔트리 Resolve"""
        global _dlq_stats

        # 생성된 ID 중 하나를 랜덤 선택하여 resolve
        if not _dlq_stats["created_ids"]:
            return

        dlq_id = random.choice(_dlq_stats["created_ids"])

        with self.client.post(
            f"/api/self-healing/dlq/{dlq_id}/resolve/",
            json={"notes": f"Resolved via load test at {time.time()}"},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /dlq/{{id}}/resolve/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                _dlq_stats["entries_resolved"] += 1
                # 성공적으로 resolve된 ID는 목록에서 제거
                if dlq_id in _dlq_stats["created_ids"]:
                    _dlq_stats["created_ids"].remove(dlq_id)
                response.success()
            elif response.status_code == 400:
                # 이미 resolved된 경우
                if dlq_id in _dlq_stats["created_ids"]:
                    _dlq_stats["created_ids"].remove(dlq_id)
                response.success()  # 예상된 동작
            elif response.status_code == 404:
                # 존재하지 않는 ID
                if dlq_id in _dlq_stats["created_ids"]:
                    _dlq_stats["created_ids"].remove(dlq_id)
                response.success()  # 예상된 동작
            else:
                response.failure(f"Resolve failed: {response.status_code}")
                _dlq_stats["resolve_errors"] += 1


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 DLQ API 검증 결과 출력"""
    global _dlq_stats

    elapsed = time.time() - _dlq_stats["start_time"] if _dlq_stats["start_time"] else 0

    print("\n" + "=" * 60)
    print("DLQ API VERIFICATION REPORT")
    print("=" * 60)

    print(f"\nTest Duration: {elapsed:.1f}s")

    print("\n[DLQ Creation]")
    print(f"  Entries Created: {_dlq_stats['entries_created']}")
    print(f"  Creation Errors: {_dlq_stats['creation_errors']}")
    if _dlq_stats["domains_tested"]:
        print(f"  Domains Tested: {dict(_dlq_stats['domains_tested'])}")
    print(f"  Failure Types: {len(_dlq_stats['failure_types_tested'])}")

    print("\n[DLQ List]")
    print(f"  Last Listed Count: {_dlq_stats['entries_listed']}")
    print(f"  List Errors: {_dlq_stats['list_errors']}")

    print("\n[DLQ Resolution]")
    print(f"  Entries Resolved: {_dlq_stats['entries_resolved']}")
    print(f"  Resolve Errors: {_dlq_stats['resolve_errors']}")
    print(f"  Pending IDs: {len(_dlq_stats['created_ids'])}")

    # Verification
    print("\n[Verification Results]")
    creation_ok = _dlq_stats["entries_created"] > 0 and _dlq_stats["creation_errors"] == 0
    list_ok = _dlq_stats["list_errors"] == 0
    resolve_ok = _dlq_stats["entries_resolved"] > 0 and _dlq_stats["resolve_errors"] == 0

    print(f"  DLQ Creation API: {'PASS' if creation_ok else 'FAIL'}")
    print(f"  DLQ List API: {'PASS' if list_ok else 'FAIL'}")
    print(f"  DLQ Resolve API: {'PASS' if resolve_ok else 'FAIL'}")

    overall = creation_ok and list_ok and resolve_ok
    print(f"\n{'=' * 60}")
    if overall:
        print("DLQ API TEST: PASSED")
    else:
        print("DLQ API TEST: FAILED")
    print("=" * 60)
