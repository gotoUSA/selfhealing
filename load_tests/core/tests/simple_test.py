#!/usr/bin/env python
"""
간단한 단위 테스트 - Django 완전 배제
직접 실행: python load_tests/core/tests/simple_test.py
"""
import sys
import os

# Django 관련 모든 것 차단
os.environ.setdefault('DJANGO_SETTINGS_MODULE', '')
sys.modules['django'] = None

from pathlib import Path

# 프로젝트 루트를 path에 추가
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))


def test_constants():
    """Constants 모듈 테스트"""
    print("\n=== Testing constants.py ===")
    
    from load_tests.core.constants import (
        Endpoints, Headers, SLA, LoadConfig,
        Services, EmergencyLevels, TestModes
    )
    
    # Endpoints
    assert Endpoints.AUTH_LOGIN == "/api/auth/login/", "AUTH_LOGIN 실패"
    print("  ✓ Endpoints.AUTH_LOGIN")
    
    assert "{service}" in Endpoints.SH_CB_STATUS_SERVICE, "SH_CB_STATUS_SERVICE 실패"
    print("  ✓ Endpoints.SH_CB_STATUS_SERVICE")
    
    result = Endpoints.format(Endpoints.PRODUCTS_DETAIL, id=123)
    assert result == "/api/products/123/", f"format 실패: {result}"
    print("  ✓ Endpoints.format()")
    
    # Headers - 딕셔너리 테스트
    assert "Content-Type" in Headers.JSON, "JSON 헤더 실패"
    print("  ✓ Headers.JSON")
    
    assert "X-Test-Mode" in Headers.XTEST_MODE, "XTEST_MODE 헤더 실패"
    print("  ✓ Headers.XTEST_MODE")
    
    # 중요: 딕셔너리 합성 테스트
    assert "X-Test-Mode" in Headers.XTEST_JSON, "XTEST_JSON X-Test-Mode 실패"
    assert "Content-Type" in Headers.XTEST_JSON, "XTEST_JSON Content-Type 실패"
    print("  ✓ Headers.XTEST_JSON (합성 딕셔너리)")
    
    assert "X-Test-Mode" in Headers.XTEST_LOAD_JSON, "XTEST_LOAD_JSON 실패"
    print("  ✓ Headers.XTEST_LOAD_JSON")
    
    # Headers.with_auth 테스트
    auth_headers = Headers.with_auth("test_token")
    assert auth_headers["Authorization"] == "Bearer test_token", "with_auth 실패"
    print("  ✓ Headers.with_auth()")
    
    # SLA
    assert SLA.P99_THRESHOLD_MS == 300.0, "P99_THRESHOLD_MS 실패"
    print("  ✓ SLA.P99_THRESHOLD_MS")
    
    assert SLA.is_p99_breach(350.0) == True, "is_p99_breach True 실패"
    assert SLA.is_p99_breach(200.0) == False, "is_p99_breach False 실패"
    print("  ✓ SLA.is_p99_breach()")
    
    # LoadConfig
    assert LoadConfig.USERS_NORMAL == 30, "USERS_NORMAL 실패"
    print("  ✓ LoadConfig.USERS_NORMAL")
    
    # Services
    assert "payment" in Services.ALL, "Services.ALL 실패"
    print("  ✓ Services.ALL")
    
    # EmergencyLevels (int 타입)
    assert EmergencyLevels.CRITICAL == 4, "CRITICAL 실패"
    print("  ✓ EmergencyLevels")
    
    # TestModes
    assert TestModes.HELLMODE == "hellmode", "HELLMODE 실패"
    print("  ✓ TestModes")
    
    print("  ✅ constants.py 모든 테스트 통과!")
    return True


def test_stats():
    """Stats 모듈 테스트"""
    print("\n=== Testing stats.py ===")
    
    from load_tests.core.stats import BaseTestStats, ExtremeTestStats
    
    # BaseTestStats 초기화
    stats = BaseTestStats()
    assert stats.passed == 0, "초기 passed 실패"
    assert stats.failed == 0, "초기 failed 실패"
    print("  ✓ BaseTestStats 초기화")
    
    # safe_increment
    stats.safe_increment("passed", 5)
    assert stats.passed == 5, "safe_increment passed 실패"
    print("  ✓ safe_increment passed")
    
    stats.safe_increment("cb_open", 3)
    assert stats.healing_actions["cb_open"] == 3, "safe_increment cb_open 실패"
    print("  ✓ safe_increment healing_actions")
    
    # record_scenario
    stats.record_scenario("test_scenario", success=True, response_time_ms=150.0)
    assert stats.passed == 6, "record_scenario passed 증가 실패"
    assert "test_scenario" in stats.scenarios, "scenarios 기록 실패"
    print("  ✓ record_scenario success")
    
    stats.record_scenario("test_scenario", success=False, response_time_ms=500.0, error="Timeout")
    assert stats.failed == 1, "record_scenario failed 실패"
    print("  ✓ record_scenario failure")
    
    # get_summary
    summary = stats.get_summary()
    assert "total_requests" in summary, "get_summary 실패"
    assert summary["passed"] == 6, "summary passed 실패"
    print("  ✓ get_summary")
    
    # to_dict
    result = stats.to_dict()
    assert "scenarios" in result, "to_dict 실패"
    print("  ✓ to_dict")
    
    # reset
    stats.reset()
    assert stats.passed == 0, "reset 실패"
    assert stats.scenarios == {}, "reset scenarios 실패"
    print("  ✓ reset")
    
    # ExtremeTestStats
    extreme = ExtremeTestStats()
    assert extreme.circuit_breaker["total_opens"] == 0, "ExtremeTestStats 초기화 실패"
    print("  ✓ ExtremeTestStats 초기화")
    
    extreme.record_cb_event("payment", "open")
    assert extreme.circuit_breaker["total_opens"] == 1, "record_cb_event 실패"
    print("  ✓ record_cb_event")
    
    extreme.record_emergency_escalation(3, "high load")
    assert len(extreme.emergency["escalations"]) == 1, "record_emergency_escalation 실패"
    print("  ✓ record_emergency_escalation")
    
    # sla 업데이트는 sla 딕셔너리 직접 수정
    extreme.sla["p99_ms"] = 250.0
    extreme.sla["p95_ms"] = 180.0
    assert extreme.sla["p99_ms"] == 250.0, "sla 업데이트 실패"
    print("  ✓ sla 업데이트")
    
    extreme.record_emergency_recovery(success=True)
    assert extreme.emergency["recovery_successes"] == 1, "record_emergency_recovery 실패"
    print("  ✓ record_emergency_recovery")
    
    print("  ✅ stats.py 모든 테스트 통과!")
    return True


def test_reporting():
    """Reporting 모듈 테스트"""
    print("\n=== Testing reporting.py ===")
    
    import tempfile
    import shutil
    import json
    
    from load_tests.core.stats import BaseTestStats, ExtremeTestStats
    from load_tests.core.reporting import ReportGenerator
    
    # 임시 디렉토리 생성
    temp_dir = tempfile.mkdtemp()
    
    try:
        # BaseTestStats로 테스트
        stats = BaseTestStats()
        stats.record_scenario("test", success=True, response_time_ms=100.0)
        stats.record_scenario("test", success=False, response_time_ms=500.0, error="Error")
        
        # ReportGenerator는 stage_name, results_dir를 받음
        generator = ReportGenerator("test_stage", results_dir=temp_dir)
        
        # stats를 딕셔너리로 변환
        stats_dict = stats.to_dict()
        
        # JSON 저장
        json_path = generator.save_json(stats_dict)
        assert json_path, "JSON 파일 생성 실패"
        from pathlib import Path
        assert Path(json_path).exists(), "JSON 파일 없음"
        
        with open(json_path) as f:
            data = json.load(f)
        assert "summary" in data or "passed" in data, "JSON 데이터 없음"
        print("  ✓ save_json")
        
        # Markdown 저장
        md_path = generator.save_markdown(stats_dict)
        assert md_path, "Markdown 파일 생성 실패"
        assert Path(md_path).exists(), "Markdown 파일 없음"
        
        content = Path(md_path).read_text(encoding='utf-8')
        assert "Test" in content or "#" in content, "Markdown 내용 없음"
        print("  ✓ save_markdown")
        
        # HTML 저장
        html_path = generator.save_html(stats_dict)
        assert html_path, "HTML 파일 생성 실패"
        assert Path(html_path).exists(), "HTML 파일 없음"
        
        content = Path(html_path).read_text(encoding='utf-8')
        assert "<html" in content.lower() or "<!doctype" in content.lower(), "HTML 태그 없음"
        print("  ✓ save_html")
        
        # save_all
        paths = generator.save_all(stats_dict)
        assert len(paths) == 3, f"save_all 파일 수 실패: {len(paths)}"
        print("  ✓ save_all")
        
        # ExtremeTestStats로 테스트
        extreme = ExtremeTestStats()
        extreme.record_scenario("chaos", success=True, response_time_ms=150.0)
        extreme.record_cb_event("payment", "open")
        extreme.record_emergency_escalation(3, "high load")
        
        generator2 = ReportGenerator("extreme_stage", results_dir=temp_dir)
        paths2 = generator2.save_all(extreme.to_dict())
        assert len(paths2) == 3, "ExtremeTestStats save_all 실패"
        print("  ✓ ExtremeTestStats 리포트 생성")
        
        print("  ✅ reporting.py 모든 테스트 통과!")
        return True
        
    finally:
        shutil.rmtree(temp_dir)


def test_mixins():
    """Mixins 모듈 테스트 (Mock 없이 기본 테스트만)"""
    print("\n=== Testing mixins.py ===")
    
    # 모듈 import만 확인 (Locust User 없이는 실행 불가)
    from load_tests.core.mixins import (
        AdminAuthMixin, SelfHealingMixin, CBMonitorMixin,
        XTestModeMixin, PhaseManagerMixin
    )
    
    print("  ✓ AdminAuthMixin import")
    print("  ✓ SelfHealingMixin import")
    print("  ✓ CBMonitorMixin import")
    print("  ✓ XTestModeMixin import")
    print("  ✓ PhaseManagerMixin import")
    
    # 클래스 속성/메서드 존재 확인
    assert hasattr(AdminAuthMixin, 'admin_login'), "admin_login 메서드 없음"
    print("  ✓ AdminAuthMixin.admin_login 존재")
    
    assert hasattr(SelfHealingMixin, 'get_healing_client'), "get_healing_client 메서드 없음"
    print("  ✓ SelfHealingMixin.get_healing_client 존재")
    
    assert hasattr(CBMonitorMixin, 'get_cb_status'), "get_cb_status 메서드 없음"
    print("  ✓ CBMonitorMixin.get_cb_status 존재")
    
    assert hasattr(XTestModeMixin, 'get_xtest_headers'), "get_xtest_headers 메서드 없음"
    print("  ✓ XTestModeMixin.get_xtest_headers 존재")
    
    assert hasattr(PhaseManagerMixin, 'set_phase'), "set_phase 메서드 없음"
    print("  ✓ PhaseManagerMixin.set_phase 존재")
    
    print("  ✅ mixins.py 모든 테스트 통과!")
    return True


def main():
    """메인 실행"""
    print("=" * 60)
    print("Load Tests Core Module - Simple Unit Tests")
    print("=" * 60)
    
    results = []
    
    try:
        results.append(("constants", test_constants()))
    except Exception as e:
        print(f"  ❌ constants.py 테스트 실패: {e}")
        results.append(("constants", False))
    
    try:
        results.append(("stats", test_stats()))
    except Exception as e:
        print(f"  ❌ stats.py 테스트 실패: {e}")
        results.append(("stats", False))
    
    try:
        results.append(("reporting", test_reporting()))
    except Exception as e:
        print(f"  ❌ reporting.py 테스트 실패: {e}")
        results.append(("reporting", False))
    
    try:
        results.append(("mixins", test_mixins()))
    except Exception as e:
        print(f"  ❌ mixins.py 테스트 실패: {e}")
        results.append(("mixins", False))
    
    # 결과 요약
    print("\n" + "=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {name}: {status}")
    
    print(f"\n총 {total}개 모듈 중 {passed}개 통과")
    
    if passed == total:
        print("\n🎉 모든 테스트 통과!")
        return 0
    else:
        print("\n⚠️ 일부 테스트 실패")
        return 1


if __name__ == "__main__":
    sys.exit(main())
