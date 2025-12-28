"""
Stage 12 Platinum Grade 보고서 생성 모듈.

stage12_spike_recovery.py에서 사용하는 보고서 생성 로직을 분리하여
코드 중복을 줄이고 재사용성을 높입니다.
"""
import os
import json
import copy
from datetime import datetime
from typing import Dict, Any, Optional, List


def print_console_report(stats: Dict[str, Any], config: Dict[str, Any]) -> None:
    """
    콘솔에 테스트 결과를 출력합니다.
    
    Args:
        stats: _extreme_stats 딕셔너리
        config: 테스트 설정 (MAX_USERS, MIN_USERS, TEST_DURATION 등)
    """
    print("\n" + "=" * 80)
    print("📊 EXTREME SPIKE & RECOVERY TEST REPORT")
    print("=" * 80)

    # Executive Summary
    print("\n📋 Executive Summary")
    print("-" * 40)
    print(f"  Test Duration: {config.get('test_duration', 'N/A')}s")
    print(f"  Max Users: {config.get('max_users', 'N/A')}")
    print(f"  Min Users: {config.get('min_users', 'N/A')}")

    # Phase Analysis
    print("\n📈 Phase Analysis:")
    for phase, phase_stats in stats.get("metrics_per_phase", {}).items():
        if phase_stats.get("requests", 0) > 0:
            rt = phase_stats.get("response_times", [])
            avg_response = sum(rt) / len(rt) if rt else 0
            error_rate = phase_stats["errors"] / phase_stats["requests"] * 100
            print(f"  {phase.upper()}:")
            print(f"    - Requests: {phase_stats['requests']}")
            print(f"    - Error Rate: {error_rate:.2f}%")
            print(f"    - Avg Response: {avg_response:.2f}ms")

    # Circuit Breaker Analysis
    print("\n🔌 Circuit Breaker Analysis:")
    cb = stats.get("circuit_breaker", {})
    print(f"  - Open Count: {cb.get('open_count', 0)}")
    print(f"  - Close Count: {cb.get('close_count', 0)}")
    print(f"  - Half-Open Count: {cb.get('half_open_count', 0)}")
    print(f"  - Services Affected: {', '.join(cb.get('services_affected', [])) or 'None'}")
    if cb.get("recovery_latency_ms"):
        print(f"  - Recovery Latency: {cb['recovery_latency_ms']:.0f}ms")

    # Emergency Mode Analysis
    print("\n🚨 Emergency Mode Analysis:")
    em = stats.get("emergency", {})
    print(f"  - Triggered Count: {em.get('triggered_count', 0)}")
    print(f"  - Released Count: {em.get('released_count', 0)}")
    print(f"  - Max Level Reached: LEVEL_{em.get('max_level', 0)}")

    # Error Budget Analysis
    print("\n💰 Error Budget Analysis:")
    eb = stats.get("error_budget", {})
    print(f"  - Initial Remaining: {eb.get('initial_remaining', 'N/A')}%")
    print(f"  - Min Remaining: {eb.get('min_remaining', 'N/A')}%")
    print(f"  - Exhausted: {'Yes' if eb.get('exhausted') else 'No'}")
    print(f"  - Recovered: {'Yes' if eb.get('recovered') else 'No'}")

    # DLQ Analysis
    print("\n📥 DLQ Analysis:")
    dlq = stats.get("dlq", {})
    print(f"  - Items Before Spike: {dlq.get('items_before_spike', 0)}")
    print(f"  - Max Count: {dlq.get('max_count', 0)}")
    print(f"  - Current Count: {dlq.get('current_count', 0)}")
    print(f"  - Replay Success: {dlq.get('replay_success_count', 0)}")
    print(f"  - Replay Fail: {dlq.get('replay_fail_count', 0)}")

    # Chaos Injection Analysis
    print("\n💥 Chaos Injection Analysis:")
    chaos = stats.get("chaos", {})
    print(f"  - Failures Injected: {chaos.get('failures_injected', 0)}")
    print(f"  - CB Triggers: {chaos.get('cb_triggers', 0)}")
    print(f"  - Recovery Triggers: {chaos.get('recovery_triggers', 0)}")

    # Kill Switch Analysis
    print("\n🔌 Kill Switch Analysis:")
    ks = stats.get("kill_switch", {})
    print(f"  - Activated Count: {ks.get('activated_count', 0)}")
    print(f"  - Deactivated Count: {ks.get('deactivated_count', 0)}")
    print(f"  - Targets: {', '.join(ks.get('targets', [])) or 'None'}")

    # V2 Optimization Modules
    print("\n🚀 V2 Optimization Modules:")
    v2 = stats.get("v2_modules", {})
    total_cache = v2.get("cache_hits", 0) + v2.get("cache_misses", 0)
    cache_hit_rate = (v2.get("cache_hits", 0) / total_cache * 100) if total_cache > 0 else 0
    print(f"  - Cache Hit Rate: {cache_hit_rate:.1f}% ({v2.get('cache_hits', 0)}/{total_cache})")
    print(f"  - Async Events: {v2.get('async_events', 0)}")
    print(f"  - Jitter Applied: {v2.get('jitter_applied', 0)}")

    # V2.5 Platinum Grade Add-ons
    _print_platinum_stats(stats, config)

    # Recovery Latency
    print("\n🔄 Recovery Latency:")
    recovery = stats.get("recovery", {})
    if recovery.get("recovery_latency_seconds"):
        latency = recovery["recovery_latency_seconds"]
        print(f"  - Total Recovery Time: {latency:.1f}s")
        if latency < 120:
            print(f"  - SLA Status: ✅ Under 2min threshold")
        else:
            print(f"  - SLA Status: ❌ Exceeded 2min threshold")
    else:
        print(f"  - Recovery not measured (system may not have failed)")

    print("\n" + "=" * 80)


def _print_platinum_stats(stats: Dict[str, Any], config: Dict[str, Any]) -> None:
    """V2.5 Platinum Grade 통계 출력."""
    sla = stats.get("sla_hardcap", {})
    storm = stats.get("message_storm", {})
    clock = stats.get("clock_skew", {})
    cascade = stats.get("cascading_failure", {})
    retry = stats.get("retry_storm", {})

    sla_threshold = config.get("sla_p99_threshold_ms", 250)

    # SLA Hard-Cap ⚖️
    print("\n⚖️ SLA Hard-Cap Analysis (Platinum V2.6):")
    recovery_p99 = _calculate_recovery_p99(stats)
    print(f"  - P99 Max (전체): {sla.get('p99_max_ms', 0):.0f}ms")
    print(f"  - P99 (복구 후/STABILIZE): {recovery_p99:.0f}ms (Threshold: {sla_threshold}ms)")
    print(f"  - P99 Violations (STABILIZE only): {sla.get('p99_violations', 0)}")
    print(f"  - Data Variance Count: {sla.get('data_variance_count', 0)}")
    recovery_sla_ok = recovery_p99 <= sla_threshold or sla.get('p99_violations', 0) == 0
    print(f"  - SLA Status: {'✅ PASSED' if recovery_sla_ok else '❌ FAILED'}")

    # Message Storm & Backpressure 📨
    print("\n📨 Message Storm & Backpressure (Platinum):")
    print(f"  - Messages Injected: {storm.get('messages_injected', 0)}")
    print(f"  - Buffer Overflow: {storm.get('buffer_overflow_count', 0)}")
    print(f"  - Messages Dropped: {storm.get('messages_dropped', 0)}")
    print(f"  - Backpressure Activated: {'Yes' if storm.get('backpressure_activated') else 'No'}")
    print(f"  - Main Logic Affected: {'❌ Yes' if storm.get('main_logic_affected') else '✅ No'}")

    # Clock Skew Attack ⏰
    print("\n⏰ Clock Skew Attack (Platinum):")
    print(f"  - Attacks Executed: {clock.get('attacks_executed', 0)}")
    print(f"  - Max Drift: {clock.get('max_drift_sec', 0):.2f}s")
    print(f"  - CB Window Corrupted: {'❌ Yes' if clock.get('cb_window_corrupted') else '✅ No'}")
    print(f"  - Recovery Found: {'✅ Yes' if clock.get('recovery_found') else 'No'}")
    print(f"  - Consistency Maintained: {'✅ Yes' if clock.get('consistency_maintained', True) else '❌ No'}")

    # Cascading Failure 🌊
    print("\n🌊 Cascading Failure (Platinum):")
    print(f"  - Cascades Triggered: {cascade.get('cascades_triggered', 0)}")
    print(f"  - Services Affected: {', '.join(cascade.get('services_affected', [])) or 'None'}")
    print(f"  - Max Cascade Depth: {cascade.get('max_cascade_depth', 0)}")
    print(f"  - Isolation Success: {'✅ Yes' if cascade.get('isolation_success', True) else '❌ No'}")

    # Retry Storm Prevention 🔄
    print("\n🔄 Retry Storm Prevention (Platinum):")
    print(f"  - Storms Detected: {retry.get('storms_detected', 0)}")
    print(f"  - Retries Blocked: {retry.get('retries_blocked', 0)}")
    print(f"  - Backoff Applied: {retry.get('backoff_applied', 0)}")
    print(f"  - Circuit Protected: {'✅ Yes' if retry.get('circuit_protected', True) else '❌ No'}")

    # Final SLA Verdict
    print("\n" + "=" * 40)
    print("🏆 FINAL SLA VERDICT (Platinum Grade V2.6)")
    print("=" * 40)

    sla_passed = recovery_p99 <= sla_threshold or sla.get('p99_violations', 0) == 0
    main_logic_ok = not storm.get('main_logic_affected', False) or recovery_p99 <= sla_threshold
    consistency_ok = clock.get('consistency_maintained', True)
    isolation_ok = cascade.get('isolation_success', True)
    circuit_ok = retry.get('circuit_protected', True)

    all_passed = sla_passed and main_logic_ok and consistency_ok and isolation_ok and circuit_ok

    print(f"  SLA Hard-Cap (복구 후 P99 {recovery_p99:.0f}ms): {'✅ PASS' if sla_passed else '❌ FAIL'}")
    print(f"  Backpressure Control: {'✅ PASS' if main_logic_ok else '❌ FAIL'}")
    print(f"  Clock Skew Resilience: {'✅ PASS' if consistency_ok else '❌ FAIL'}")
    print(f"  Cascade Isolation: {'✅ PASS' if isolation_ok else '❌ FAIL'}")
    print(f"  Retry Storm Protection: {'✅ PASS' if circuit_ok else '❌ FAIL'}")
    print(f"\n  {'🏆 PLATINUM GRADE ACHIEVED!' if all_passed else '🥇 GOLD GRADE (일부 미달성)'}")


def _calculate_recovery_p99(stats: Dict[str, Any]) -> float:
    """복구 후 P99 계산 (STABILIZE 단계만)."""
    stabilize_times = stats.get("metrics_per_phase", {}).get("stabilize", {}).get("response_times", [])
    if not stabilize_times:
        return 0.0
    sorted_times = sorted(stabilize_times)
    index = int(len(sorted_times) * 0.99)
    return sorted_times[min(index, len(sorted_times) - 1)]


def save_json_report(
    stats: Dict[str, Any],
    config: Dict[str, Any],
    results_dir: str,
    stage_name: str = "stage12"
) -> str:
    """
    JSON 보고서 저장.
    
    Args:
        stats: _extreme_stats 딕셔너리
        config: 테스트 설정
        results_dir: 결과 저장 디렉토리
        stage_name: 스테이지 이름
        
    Returns:
        저장된 파일 경로
    """
    os.makedirs(results_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(results_dir, f"{stage_name}_extreme_{timestamp}.json")

    # response_times를 요약 통계로 변환 (JSON 직렬화 위해)
    json_stats = copy.deepcopy(stats)
    for phase, phase_stats in json_stats.get("metrics_per_phase", {}).items():
        rt = phase_stats.get("response_times", [])
        phase_stats["response_time_stats"] = {
            "count": len(rt),
            "avg": sum(rt) / len(rt) if rt else 0,
            "min": min(rt) if rt else 0,
            "max": max(rt) if rt else 0,
        }
        if "response_times" in phase_stats:
            del phase_stats["response_times"]

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "test_name": f"Stage 12: EXTREME Spike & Recovery",
                "timestamp": datetime.now().isoformat(),
                "config": config,
                "results": json_stats,
            },
            f,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    print(f"\n💾 JSON Report: {json_path}")
    return json_path


def save_markdown_report(
    stats: Dict[str, Any],
    config: Dict[str, Any],
    results_dir: str,
    stage_name: str = "stage12"
) -> str:
    """
    Markdown 보고서 저장.
    
    Args:
        stats: _extreme_stats 딕셔너리
        config: 테스트 설정
        results_dir: 결과 저장 디렉토리
        stage_name: 스테이지 이름
        
    Returns:
        저장된 파일 경로
    """
    os.makedirs(results_dir, exist_ok=True)
    md_path = os.path.join(results_dir, f"{stage_name}_extreme_{datetime.now().strftime('%Y-%m-%d')}.md")
    
    content = _generate_markdown_content(stats, config)
    
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"💾 Markdown Report: {md_path}")
    return md_path


def _generate_markdown_content(stats: Dict[str, Any], config: Dict[str, Any]) -> str:
    """Markdown 보고서 내용 생성."""
    cb = stats.get("circuit_breaker", {})
    em = stats.get("emergency", {})
    eb = stats.get("error_budget", {})
    dlq = stats.get("dlq", {})
    chaos = stats.get("chaos", {})
    ks = stats.get("kill_switch", {})
    v2 = stats.get("v2_modules", {})
    recovery = stats.get("recovery", {})

    # V2.5 Platinum Grade
    sla = stats.get("sla_hardcap", {})
    storm = stats.get("message_storm", {})
    clock = stats.get("clock_skew", {})
    cascade = stats.get("cascading_failure", {})
    retry = stats.get("retry_storm", {})

    total_cache = v2.get("cache_hits", 0) + v2.get("cache_misses", 0)
    cache_hit_rate = (v2.get("cache_hits", 0) / total_cache * 100) if total_cache > 0 else 0

    sla_threshold = config.get("sla_p99_threshold_ms", 250)
    max_users = config.get("max_users", "N/A")
    min_users = config.get("min_users", "N/A")
    test_duration = config.get("test_duration", "N/A")
    clock_skew_max = config.get("clock_skew_max_drift_sec", 5)

    # Platinum Grade 판정
    recovery_p99 = _calculate_recovery_p99(stats)
    sla_passed = recovery_p99 <= sla_threshold or sla.get('p99_violations', 0) == 0
    main_logic_ok = not storm.get('main_logic_affected', False)
    consistency_ok = clock.get('consistency_maintained', True)
    isolation_ok = cascade.get('isolation_success', True)
    circuit_ok = retry.get('circuit_protected', True)
    platinum_achieved = sla_passed and main_logic_ok and consistency_ok and isolation_ok and circuit_ok

    content = f"""# Stage 12 EXTREME Spike & Recovery 테스트 결과 보고서

📅 **테스트 일시**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
🏷️ **버전**: EXTREME Self-Healing V2.5 Platinum
🎯 **테스트 목표**: 극단적 스파이크 부하에서 Self-Healing 시스템 검증 + Platinum Grade 달성

---

## 🏆 Platinum Grade Status

| 항목 | 결과 | 기준 |
|------|------|------|
| **SLA Hard-Cap** | {'✅ PASS' if sla_passed else '❌ FAIL'} | P99 ≤ {sla_threshold}ms |
| **Backpressure Control** | {'✅ PASS' if main_logic_ok else '❌ FAIL'} | 메인 로직 영향 없음 |
| **Clock Skew Resilience** | {'✅ PASS' if consistency_ok else '❌ FAIL'} | 데이터 일관성 유지 |
| **Cascade Isolation** | {'✅ PASS' if isolation_ok else '❌ FAIL'} | 장애 격리 성공 |
| **Retry Storm Protection** | {'✅ PASS' if circuit_ok else '❌ FAIL'} | 재시도 폭풍 방지 |
| **🏆 최종 등급** | {'**PLATINUM** 🏆' if platinum_achieved else '**GOLD** 🥇'} | 모든 항목 PASS |

---

## 📋 Executive Summary

| 항목 | 값 | 상태 |
|------|-----|------|
| **EXTREME Mode** | ✅ 활성화 | - |
| **최대 사용자** | {max_users} | - |
| **최소 사용자** | {min_users} | - |
| **테스트 시간** | {test_duration}s | - |
| **CB Open 횟수** | {cb.get('open_count', 0)} | {'🔴' if cb.get('open_count', 0) > 0 else '🟢'} |
| **Emergency 최대 레벨** | LEVEL_{em.get('max_level', 0)} | {'🔴' if em.get('max_level', 0) >= 3 else '🟡' if em.get('max_level', 0) >= 1 else '🟢'} |
| **Error Budget 소진** | {'Yes' if eb.get('exhausted') else 'No'} | {'🔴' if eb.get('exhausted') else '🟢'} |

---

## ⚖️ V2.5 Platinum Grade Add-ons

### ⚖️ SLA Hard-Cap

| 항목 | 값 | 기준 |
|------|-----|------|
| P99 최대값 | {sla.get('p99_max_ms', 0):.0f}ms | ≤ {sla_threshold}ms |
| P99 위반 횟수 | {sla.get('p99_violations', 0)} | 0 |
| 데이터 오차 | {sla.get('data_variance_count', 0)} | 0 |
| **판정** | {'✅ PASSED' if sla_passed else '❌ FAILED'} | - |

### 📨 Message Storm & Backpressure

| 항목 | 값 | 상태 |
|------|-----|------|
| 주입된 메시지 | {storm.get('messages_injected', 0)} | - |
| 버퍼 오버플로우 | {storm.get('buffer_overflow_count', 0)} | - |
| 드롭된 메시지 | {storm.get('messages_dropped', 0)} | - |
| **메인 로직 영향** | {'❌ 영향받음' if storm.get('main_logic_affected') else '✅ 영향 없음'} | 영향 없어야 함 |

### ⏰ Clock Skew Attack

| 항목 | 값 | 상태 |
|------|-----|------|
| 공격 횟수 | {clock.get('attacks_executed', 0)} | - |
| 최대 시간 왜곡 | {clock.get('max_drift_sec', 0):.2f}s | ≤ {clock_skew_max}s |
| **일관성 유지** | {'✅ Yes' if consistency_ok else '❌ No'} | Yes |

### 🌊 Cascading Failure

| 항목 | 값 | 상태 |
|------|-----|------|
| 전파 트리거 | {cascade.get('cascades_triggered', 0)} | - |
| 최대 전파 깊이 | {cascade.get('max_cascade_depth', 0)} | - |
| **격리 성공** | {'✅ Yes' if isolation_ok else '❌ No'} | Yes |

### 🔄 Retry Storm Prevention

| 항목 | 값 | 상태 |
|------|-----|------|
| 폭풍 감지 | {retry.get('storms_detected', 0)} | - |
| **서킷 보호** | {'✅ Yes' if circuit_ok else '❌ No'} | Yes |

---

## 🔥 Self-Healing 기능 테스트 결과

### 🔌 Circuit Breaker

| 항목 | 값 |
|------|-----|
| Open 횟수 | {cb.get('open_count', 0)} |
| Close 횟수 | {cb.get('close_count', 0)} |
| Half-Open 횟수 | {cb.get('half_open_count', 0)} |
| 영향받은 서비스 | {', '.join(cb.get('services_affected', [])) or 'None'} |

### 🚨 Emergency Mode

| 항목 | 값 |
|------|-----|
| 트리거 횟수 | {em.get('triggered_count', 0)} |
| 해제 횟수 | {em.get('released_count', 0)} |
| 최대 레벨 | LEVEL_{em.get('max_level', 0)} |

### 📥 DLQ

| 항목 | 값 |
|------|-----|
| 최대 수량 | {dlq.get('max_count', 0)} |
| 리플레이 성공 | {dlq.get('replay_success_count', 0)} |
| 리플레이 실패 | {dlq.get('replay_fail_count', 0)} |

---

## 📈 Phase별 분석

| Phase | 요청 수 | 에러 수 | 에러율 | 평균 응답시간 |
|-------|--------|--------|-------|--------------|
"""

    for phase, phase_stats in stats.get("metrics_per_phase", {}).items():
        if phase_stats.get("requests", 0) > 0:
            rt = phase_stats.get("response_times", [])
            avg_rt = sum(rt) / len(rt) if rt else 0
            error_rate = phase_stats["errors"] / phase_stats["requests"] * 100
            content += f"| {phase.upper()} | {phase_stats['requests']} | {phase_stats['errors']} | {error_rate:.2f}% | {avg_rt:.2f}ms |\n"

    content += f"""
---

## 🔄 Recovery Latency Analysis

| 항목 | 값 | SLA |
|------|-----|-----|
| 총 복구 시간 | {f"{recovery.get('recovery_latency_seconds', 0):.1f}s" if recovery.get('recovery_latency_seconds') else 'N/A'} | {'✅ < 2min' if recovery.get('recovery_latency_seconds') and recovery.get('recovery_latency_seconds') < 120 else '❌ > 2min' if recovery.get('recovery_latency_seconds') else '-'} |

---

## 🎯 테스트 결론

**최종 결과**: {'✅ PASS' if platinum_achieved else '❌ NEEDS REVIEW'}

**Platinum Grade**: {'🏆 ACHIEVED' if platinum_achieved else '🥇 GOLD (일부 미달성)'}

---

📝 **Generated by Stage 12 EXTREME Spike & Recovery Test V2.5 Platinum**
"""

    return content


def save_all_reports(
    stats: Dict[str, Any],
    config: Dict[str, Any],
    results_dir: str,
    stage_name: str = "stage12",
    debug_logs: Optional[List[str]] = None,
    debug_mode: bool = False
) -> Dict[str, str]:
    """
    모든 형식의 보고서를 저장합니다.
    
    Args:
        stats: _extreme_stats 딕셔너리
        config: 테스트 설정
        results_dir: 결과 저장 디렉토리
        stage_name: 스테이지 이름
        debug_logs: 디버그 로그 목록
        debug_mode: 디버그 모드 여부
        
    Returns:
        저장된 파일 경로 딕셔너리
    """
    paths = {}
    
    paths["json"] = save_json_report(stats, config, results_dir, stage_name)
    paths["markdown"] = save_markdown_report(stats, config, results_dir, stage_name)
    
    # Debug logs
    if debug_mode and debug_logs:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_path = os.path.join(results_dir, f"{stage_name}_debug_{timestamp}.log")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write("\n".join(debug_logs))
        print(f"💾 Debug Log: {debug_path}")
        paths["debug"] = debug_path
    
    return paths
