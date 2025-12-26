"""
SelfHealingClient 사용 예시 및 테스트.

이 파일은 SelfHealingClient의 기본 사용법을 보여줍니다.
실제 테스트 스테이지에서 이렇게 사용할 수 있습니다.
"""

import os
import sys

# 상위 경로 추가 (load_tests에서 실행 시)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.selfhealing import SelfHealingClient, configure


def example_basic_usage():
    """기본 사용 예시."""
    print("=" * 60)
    print("기본 사용 예시")
    print("=" * 60)

    # 클라이언트 생성
    client = SelfHealingClient()

    # 헬스 체크
    health = client.health.ping()
    print(f"Ping 결과: {health}")

    # 전체 헬스 상태
    full_health = client.health.full_health()
    print(f"Full Health: {full_health}")

    return client


def example_jwt_auth():
    """JWT 인증 예시."""
    print("\n" + "=" * 60)
    print("JWT 인증 예시")
    print("=" * 60)

    client = SelfHealingClient(auth_mode="jwt")

    # 로그인
    success = client.login("admin", "admin_password")
    print(f"로그인 성공: {success}")
    print(f"인증됨: {client.is_authenticated}")

    if success:
        # 인증이 필요한 API 호출
        cb_status = client.circuit_breaker.get_all_status()
        print(f"Circuit Breaker 상태: {cb_status}")

    return client


def example_xtest_mode():
    """XTest 모드 예시 (Chaos Engineering)."""
    print("\n" + "=" * 60)
    print("XTest 모드 예시")
    print("=" * 60)

    # XTest 모드 클라이언트 생성
    client = SelfHealingClient(auth_mode="xtest")

    # 장애 주입
    result = client.circuit_breaker.xtest_inject_failure(
        service_name="payment-service",
        failure_type="timeout",
        failure_rate=0.5,
    )
    print(f"장애 주입 결과: {result}")

    # 리셋
    reset_result = client.circuit_breaker.xtest_reset("payment-service")
    print(f"리셋 결과: {reset_result}")

    return client


def example_circuit_breaker():
    """Circuit Breaker 제어 예시."""
    print("\n" + "=" * 60)
    print("Circuit Breaker 예시")
    print("=" * 60)

    client = SelfHealingClient()
    client.login("admin", "admin_password")

    service = "payment-service"

    # 상태 확인
    status = client.circuit_breaker.get_status(service)
    print(f"{service} 상태: {status}")

    # 상태 확인 헬퍼
    print(f"Open: {client.circuit_breaker.is_open(service)}")
    print(f"Closed: {client.circuit_breaker.is_closed(service)}")

    # 제어 (관리자 권한 필요)
    # client.circuit_breaker.block(service, reason="테스트")
    # client.circuit_breaker.allow(service, reason="테스트 완료")

    return client


def example_dlq_management():
    """DLQ 관리 예시."""
    print("\n" + "=" * 60)
    print("DLQ 관리 예시")
    print("=" * 60)

    client = SelfHealingClient()
    client.login("admin", "admin_password")

    # 통계 조회
    stats = client.dlq.stats()
    print(f"DLQ 통계: {stats}")

    # 헬퍼 메서드
    pending = client.dlq.get_pending_count()
    print(f"대기 중: {pending}")

    has_pending = client.dlq.has_pending()
    print(f"대기 항목 있음: {has_pending}")

    # 목록 조회
    entries = client.dlq.list(limit=10)
    print(f"DLQ 항목: {entries}")

    return client


def example_error_budget():
    """Error Budget 예시."""
    print("\n" + "=" * 60)
    print("Error Budget 예시")
    print("=" * 60)

    client = SelfHealingClient()
    client.login("admin", "admin_password")

    # 상태 조회
    status = client.error_budget.get_status()
    print(f"Error Budget 상태: {status}")

    # 남은 예산
    remaining = client.error_budget.get_remaining_percent()
    print(f"남은 예산: {remaining}%")

    # 배포 판단
    verdict = client.error_budget.get_deployment_verdict()
    print(f"배포 가능 여부: {verdict}")

    return client


def example_emergency_mode():
    """Emergency Mode 예시."""
    print("\n" + "=" * 60)
    print("Emergency Mode 예시")
    print("=" * 60)

    client = SelfHealingClient()
    client.login("admin", "admin_password")

    # 상태 조회
    status = client.emergency.get_status()
    print(f"Emergency 상태: {status}")

    # 헬퍼 메서드
    is_active = client.emergency.is_active()
    print(f"활성화됨: {is_active}")

    level = client.emergency.get_current_level()
    print(f"현재 레벨: {level}")

    # 활성화/비활성화 (주의해서 사용)
    # client.emergency.trigger(level="LEVEL_1", reason="부하 테스트")
    # client.emergency.release(reason="테스트 완료")

    return client


def example_observability():
    """Observability 예시."""
    print("\n" + "=" * 60)
    print("Observability 예시")
    print("=" * 60)

    client = SelfHealingClient()
    client.login("admin", "admin_password")

    # 현재 스냅샷
    snapshot = client.observability.get_current_snapshot()
    print(f"현재 스냅샷: {snapshot}")

    # 타임라인
    timeline = client.observability.get_timeline(limit=10)
    print(f"최근 타임라인: {timeline}")

    # 힐링 이벤트 통계
    stats = client.observability.get_healing_statistics(period="1h")
    print(f"힐링 통계: {stats}")

    return client


def example_chaos_engineering():
    """Chaos Engineering 예시."""
    print("\n" + "=" * 60)
    print("Chaos Engineering 예시")
    print("=" * 60)

    client = SelfHealingClient(auth_mode="xtest")

    # 안전 체크
    safety = client.chaos.get_safety_status()
    print(f"안전 상태: {safety}")

    # 킬스위치 상태
    kill_switch = client.chaos.get_kill_switch_status()
    print(f"킬스위치: {kill_switch}")

    # XTest 실험 (주의해서 사용)
    # client.chaos.xtest_inject_latency("payment-service", latency_ms=500)
    # client.chaos.xtest_inject_failure("payment-service", failure_rate=0.3)
    # client.chaos.xtest_reset_all()

    return client


def example_with_context_manager():
    """컨텍스트 매니저 사용 예시."""
    print("\n" + "=" * 60)
    print("컨텍스트 매니저 예시")
    print("=" * 60)

    with SelfHealingClient() as client:
        health = client.health.ping()
        print(f"Ping: {health}")

        # 사용 후 자동으로 세션 정리됨

    print("세션이 정리되었습니다.")


def example_environment_config():
    """환경변수 설정 예시."""
    print("\n" + "=" * 60)
    print("환경변수 설정 예시")
    print("=" * 60)

    # 환경변수로 설정
    os.environ["SELFHEALING_HOST"] = "http://healing-server:8000"
    os.environ["SELFHEALING_AUTH_MODE"] = "jwt"
    os.environ["SELFHEALING_TIMEOUT"] = "60"

    # 설정 새로고침
    from utils.selfhealing import reset_config, get_config

    reset_config()

    config = get_config()
    print(f"Host: {config.host}")
    print(f"Auth Mode: {config.auth_mode}")
    print(f"Timeout: {config.timeout}")

    # 또는 프로그래밍 방식으로 설정
    configure(
        host="http://localhost:8000",
        auth_mode="xtest",
        timeout=30,
    )

    config = get_config()
    print(f"Updated Host: {config.host}")


def run_all_examples():
    """모든 예시 실행."""
    print("\n" + "=" * 60)
    print("SelfHealingClient 사용 예시")
    print("=" * 60 + "\n")

    try:
        example_basic_usage()
    except Exception as e:
        print(f"기본 사용 예시 오류: {e}")

    try:
        example_circuit_breaker()
    except Exception as e:
        print(f"Circuit Breaker 예시 오류: {e}")

    try:
        example_dlq_management()
    except Exception as e:
        print(f"DLQ 예시 오류: {e}")

    try:
        example_error_budget()
    except Exception as e:
        print(f"Error Budget 예시 오류: {e}")

    try:
        example_with_context_manager()
    except Exception as e:
        print(f"컨텍스트 매니저 예시 오류: {e}")

    print("\n모든 예시 실행 완료!")


if __name__ == "__main__":
    run_all_examples()
