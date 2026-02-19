"""
Saga Lua Scripts.

Redis Lua 스크립트를 정의한다.
- SAGA_TRANSITION_SCRIPT: Saga 상태의 원자적 CAS(Compare-And-Swap) 전환
- SAGA_INSTANCE_CAS_SCRIPT: SagaInstance 저장 시 OCC(Optimistic Concurrency Control) 적용
"""

# =============================================================================
# SAGA_TRANSITION_SCRIPT — Saga 상태 원자적 전환
# =============================================================================

SAGA_TRANSITION_SCRIPT = """
local key = KEYS[1]
local expected_status = ARGV[1]
local new_status = ARGV[2]
local updated_at = ARGV[3]

local current = redis.call("HGET", key, "status")
if current ~= expected_status then
    return {0, "status_mismatch", current or "nil"}
end

redis.call("HMSET", key,
    "status", new_status,
    "updated_at", updated_at
)
return {1, "ok", new_status}
"""
"""Saga 상태 전환 Lua 스크립트.

expected_status와 현재 status가 일치할 때만 new_status로 전환한다.
불일치 시 {0, "status_mismatch", current}를 반환하여 호출자가 감지.

사용 사례:
- RUNNING → COMPENSATING (Step 실패 시)
- COMPENSATING → COMPENSATED (모든 compensate 성공)
- COMPENSATING → COMPENSATION_FAILED (compensate 실패)
"""


# =============================================================================
# SAGA_INSTANCE_CAS_SCRIPT — SagaInstance 저장용 OCC
# =============================================================================

SAGA_INSTANCE_CAS_SCRIPT = """
local key = KEYS[1]
local new_data = ARGV[1]
local expected_version = tonumber(ARGV[2])

local current = redis.call("GET", key)
if current then
    local decoded = cjson.decode(current)
    local current_version = tonumber(decoded["version"] or 0)
    if current_version ~= expected_version then
        return 0
    end
end

redis.call("SET", key, new_data)
return 1
"""
"""SagaInstance 저장용 CAS Lua 스크립트.

expected_version과 현재 저장된 version을 비교한다.
- 일치하면 새 데이터로 덮어쓰고 1을 반환한다.
- 불일치하면 0을 반환하여 SessionVersionConflictError를 유발한다.
- 키가 존재하지 않으면 신규 저장으로 간주하여 바로 SET한다.
"""
