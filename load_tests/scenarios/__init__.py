# load_tests/scenarios/__init__.py
"""
Load Test Scenarios Module

Stage-based Payment Load & Chaos Test Scenarios

Stage 순서는 실제 장애 시나리오 재현을 기반으로 함:
1. PG 지연 (Latency) → 2. 사용자 Cancel 연타 → 3. Rollback 실패

Available Stages:
    - Stage 0: Smoke (환경 검증)
    - Stage 1: Happy Load (정상 성능 측정)
    - Stage 2: Idempotent (중복 결제 방지)
    - Stage 3: Latency (PG 지연/timeout)
    - Stage 4: Cancel Storm (결제 직후 취소)
    - Stage 5: Rollback (재고/포인트 복구)
    - Stage 6: Chaos Random (3~15% 랜덤 실패)
    - Stage 7: Race Conflict (동일 order 동시 결제)
    - Stage 8: Webhook (Webhook 중복/역전)
    - Stage 9: Soak (메모리 누수/커넥션 풀)
    - Stage 10: Self-Healing Control API
    - Stage 11: Ramp-up Threshold Discovery
    - Stage 12: Spike & Recovery
    - Stage 13: Repeated Spike (Backoff Tuning)
    - Stage 14: DLQ Replay Verification
    - Stage 15: Circuit Breaker Auto Transitions
"""

from pathlib import Path

# 모든 Stage 파일 경로
STAGES_DIR = Path(__file__).parent

STAGE_FILES = {
    "stage0_smoke": STAGES_DIR / "stage0_smoke.py",
    "stage1_happy": STAGES_DIR / "stage1_happy_load.py",
    "stage2_idempotent": STAGES_DIR / "stage2_idempotent.py",
    "stage3_latency": STAGES_DIR / "stage3_latency.py",
    "stage4_cancel": STAGES_DIR / "stage4_cancel_storm.py",
    "stage5_rollback": STAGES_DIR / "stage5_rollback.py",
    "stage6_chaos": STAGES_DIR / "stage6_chaos_random.py",
    "stage7_race": STAGES_DIR / "stage7_race_conflict.py",
    "stage8_webhook": STAGES_DIR / "stage8_webhook.py",
    "stage9_soak": STAGES_DIR / "stage9_soak.py",
    "stage10_self_healing": STAGES_DIR / "stage10_self_healing.py",
    "stage11_ramp_threshold": STAGES_DIR / "stage11_ramp_threshold.py",
    "stage12_spike_recovery": STAGES_DIR / "stage12_spike_recovery.py",
    "stage13_repeated_spike": STAGES_DIR / "stage13_repeated_spike.py",
    "stage14_dlq_replay": STAGES_DIR / "stage14_dlq_replay.py",
    "stage15_cb_transitions": STAGES_DIR / "stage15_cb_transitions.py",
}

__all__ = [
    "STAGES_DIR",
    "STAGE_FILES",
]
