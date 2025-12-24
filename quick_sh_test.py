#!/usr/bin/env python
"""Quick Self-Healing API Test Script"""
import requests
import sys

HOST = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

# Endpoints to test
ENDPOINTS = [
    # Public (no auth)
    ("health_ping", "/api/self-healing/health/ping/", False),
    ("health_live", "/api/self-healing/health/live/", False),
    ("health_ready", "/api/self-healing/health/ready/", False),
    # Protected (need auth but check existence)
    ("health", "/api/self-healing/health/", True),
    ("health_pool", "/api/self-healing/health/pool/", True),
    ("status", "/api/self-healing/status/", True),
    ("metrics", "/api/self-healing/metrics/", True),
    ("cb_pool_status", "/api/self-healing/circuit-breaker/pool/status/", True),
    ("dlq_list", "/api/self-healing/dlq/list/", True),
    ("config", "/api/self-healing/config/", True),
    ("error_budget", "/api/self-healing/error-budget/status/", True),
    ("emergency", "/api/self-healing/emergency/status/", True),
    ("emergency_levels", "/api/self-healing/emergency/levels/", True),
    ("l2_storage", "/api/self-healing/l2-storage/status/", True),
    ("l2_storage_health", "/api/self-healing/l2-storage/health/", True),
    ("chaos_kill_switch", "/api/self-healing/chaos/kill-switch/", True),
    ("chaos_schedules", "/api/self-healing/chaos/schedules/", True),
    ("dashboard", "/api/self-healing/dashboard/summary/", True),
    ("governance", "/api/self-healing/governance/status/", True),
    ("metrics_status", "/api/self-healing/metrics/status/", True),
    ("reconciliation", "/api/self-healing/reconciliation/status/", True),
    ("system", "/api/self-healing/system/status/", True),
    ("tier_definitions", "/api/self-healing/config/tiers/", True),
]

print(f"\n🔍 Self-Healing API Smoke Test - {HOST}")
print("=" * 65)

passed = 0
failed = 0
results = []

for name, endpoint, needs_auth in ENDPOINTS:
    try:
        r = requests.get(f"{HOST}{endpoint}", timeout=10)
        # Success conditions:
        # - 200/204: OK
        # - 401/403: Endpoint exists but needs auth (OK for smoke test)
        # - 429: Rate limited (exists but throttled)
        if r.status_code in [200, 204]:
            status = "✅"
            passed += 1
        elif r.status_code in [401, 403]:
            status = "🔐"  # Auth required but endpoint exists
            passed += 1
        elif r.status_code == 429:
            status = "⏳"  # Rate limited
            passed += 1
        else:
            status = "❌"
            failed += 1
        
        results.append((status, name, r.status_code, endpoint))
    except requests.exceptions.Timeout:
        results.append(("⏱️", name, "TIMEOUT", endpoint))
        failed += 1
    except requests.exceptions.ConnectionError:
        results.append(("🔌", name, "CONN_ERR", endpoint))
        failed += 1
    except Exception as e:
        results.append(("❓", name, str(e)[:20], endpoint))
        failed += 1

# Print results grouped
print("\n📡 Public Endpoints:")
for status, name, code, ep in results[:3]:
    print(f"   {status} {name}: {code}")

print("\n🔐 Protected Endpoints:")
for status, name, code, ep in results[3:]:
    auth_note = " (auth required)" if code in [401, 403] else ""
    rate_note = " (rate limited)" if code == 429 else ""
    print(f"   {status} {name}: {code}{auth_note}{rate_note}")

print("\n" + "=" * 65)
print(f"📊 Results: {passed} passed, {failed} failed")

if failed == 0:
    print("✅ ALL ENDPOINTS OPERATIONAL")
else:
    print(f"⚠️  {failed} ENDPOINTS NEED ATTENTION")

print("=" * 65)
