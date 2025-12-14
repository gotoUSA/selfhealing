# Load Test Results - Stage 11~15

## Test Execution Date
2025-12-09

## Test Environment
- Docker Compose
- Test Duration: 60 seconds per stage
- Max Users: 30
- Environment Variables: `LOCUST_TEST_DURATION=60`, `LOCUST_MAX_USERS=30`

---

## Overall Summary

| Stage | Name | Status | Key Findings |
|-------|------|--------|--------------|
| 11 | Ramp Threshold Discovery | ✅ PASS | CB Open at 17 users, Error Spike 17.44% at 29 users |
| 12 | Spike & Recovery | ✅ PASS | CB opened/closed, Recovery Time 12.8s |
| 13 | Repeated Spike Cycles | ✅ PASS | 3 cycles, 57 CB transitions |
| 14 | DLQ Replay Verification | ⏭️ SKIP | DLQ replay API not implemented |
| 15 | CB State Transitions | ⏭️ SKIP | inject_failure doesn't change CB state |

---

## Stage 11: Ramp Threshold Discovery
**Status: ✅ PASS**

### Purpose
Gradually increase load to discover Circuit Breaker threshold and system breaking point.

### Results
- **Total Requests**: 156
- **Error Rate**: 9.62%
- **Milestones Detected**:
  - `first_cb_open`: 17 users (inventory service)
  - `first_error_spike`: 29 users (17.44% error rate)
  - `breaking_point`: 29 users

### Command
```bash
docker compose exec -T -e LOCUST_TEST_DURATION=60 -e LOCUST_MAX_USERS=30 web \
  python -m locust -f load_tests/scenarios/stage11_ramp_threshold.py \
  --host=http://web:8000 --headless --only-summary
```

---

## Stage 12: Spike & Recovery
**Status: ✅ PASS**

### Purpose
Test system recovery after sudden load spike.

### Results
- **Total Requests**: 148
- **Error Rate**: 4.73%
- **Phases**:
  - SPIKE: 14 requests, 7.14% errors
  - SUSTAIN: 36 requests, 13.89% errors
- **CB Events**:
  - CB OPENED: inventory service @ 18.3s
  - CB CLOSED: payment service @ 31.1s
- **Recovery Time**: 12.8 seconds

### Command
```bash
docker compose exec -T -e LOCUST_TEST_DURATION=60 -e LOCUST_MAX_USERS=30 web \
  python -m locust -f load_tests/scenarios/stage12_spike_recovery.py \
  --host=http://web:8000 --headless --only-summary
```

---

## Stage 13: Repeated Spike Cycles
**Status: ✅ PASS**

### Purpose
Test system stability under repeated spike-recovery cycles.

### Results
- **Total Requests**: 543
- **Error Rate**: 9.21%
- **Cycles Completed**: 3
- **CB Transitions**: 57 total

### Per-Cycle Analysis
| Cycle | Phase | Requests | Error Rate | Avg Response |
|-------|-------|----------|------------|--------------|
| 1 | sustain | 1 | 0.0% | 4401ms |
| 1 | recovery | 30 | 6.7% | 4972ms |
| 1 | cool | 36 | 16.7% | 4649ms |
| 2 | spike | 10 | 20.0% | 4404ms |
| 2 | sustain | 36 | 8.3% | 4994ms |
| 2 | recovery | 40 | 15.0% | 4592ms |
| 2 | cool | 41 | 9.8% | 4211ms |
| 3 | spike | 12 | 33.3% | 4033ms |
| 3 | sustain | 46 | 15.2% | 4403ms |
| 3 | recovery | 40 | 15.0% | 4072ms |
| 3 | cool | 41 | 24.4% | 3859ms |

### Command
```bash
docker compose exec -T -e LOCUST_TEST_DURATION=60 -e LOCUST_MAX_USERS=30 web \
  python -m locust -f load_tests/scenarios/stage13_repeated_spike.py \
  --host=http://web:8000 --headless --only-summary
```

---

## Stage 14: DLQ Replay Verification
**Status: ⏭️ SKIP (API Limitation)**

### Purpose
Verify DLQ (Dead Letter Queue) replay functionality and data integrity.

### Issue
The `replay_dlq` action is not implemented in the Control API. DLQ replay is only available through the Django Admin interface (`DLQAdmin.replay_selected`), not via REST API.

### Workaround
- Use Django Admin for manual DLQ replay operations
- Or implement `/api/self-healing/dlq/replay/` endpoint

### Admin Login Fix Applied
- Added `admin_login_helper` with `login_as_admin()` method
- Control API calls now use admin authentication (403 errors resolved)

---

## Stage 15: CB State Transitions
**Status: ⏭️ SKIP (API Limitation)**

### Purpose
Verify complete CB state transition cycle: CLOSED → OPEN → HALF_OPEN → CLOSED

### Issue
The `inject_failure` action stores failure configuration in memory but does not actually change the Circuit Breaker state. The actual CB state change requires real service failures, not simulated ones.

### Admin Login Fix Applied
- `inject_failure` and `reset` API calls now succeed (0% error)
- CB state check uses admin authentication

### Future Improvement
- Implement actual failure injection that triggers CB state change
- Or add a `force_open` action to the Control API

---

## Code Changes Made

### 1. Fixed SelfHealingLog Import Error
**File**: `shopping/services/self_healing/control_api_service.py`
- Removed invalid `SelfHealingLog` import

### 2. Added Helper Methods
**File**: `load_tests/utils/login_helper.py`
- Added `get_auth_header()` method

**File**: `load_tests/utils/product_helper.py`
- Added `get_random_product()` method

### 3. Fixed Services List Handling
**Files**: `stage11_ramp_threshold.py`, `stage12_spike_recovery.py`, `stage13_repeated_spike.py`
- Changed `services` from dict to list format

### 4. Added Environment Variable Support
**Files**: All Stage 11-15 scenario files
- Added `LOCUST_TEST_DURATION` (default: 15s → 60s for tests)
- Added `LOCUST_MAX_USERS` (default: varies by stage)
- Shape classes return `None` when duration exceeded (auto-terminate)

### 5. Added Admin Login Support
**File**: `load_tests/config.py`
- Added `ADMIN_USERNAME`, `ADMIN_PASSWORD` config

**File**: `load_tests/utils/login_helper.py`
- Added `login_as_admin()` method

**Files**: `stage14_dlq_replay.py`, `stage15_cb_transitions.py`
- Added `admin_login_helper` for Control API access

---

## Pass Criteria

| Stage | Criteria | Met? |
|-------|----------|------|
| 11 | CB Open detected | ✅ |
| 11 | Error Spike detected | ✅ |
| 12 | Spike phase executed | ✅ |
| 12 | Recovery detected | ✅ |
| 13 | Multiple cycles completed | ✅ |
| 13 | CB reset between cycles | ✅ |
| 14 | DLQ replay success | ⏭️ N/A |
| 15 | CLOSED→OPEN→HALF_OPEN→CLOSED | ⏭️ N/A |

---

## Recommendations

1. **Implement DLQ Replay API**
   - Create `/api/self-healing/dlq/replay/` endpoint
   - Allow batch replay with filters

2. **Enhance inject_failure Action**
   - Actually trigger CB state change
   - Or add `force_open_cb` action

3. **Extend Test Duration**
   - 60s is minimum for meaningful results
   - Consider 120s-180s for production validation

4. **Add Test Data Reset Command**
   - Automate cart/order table truncation
   - Automate product stock reset
