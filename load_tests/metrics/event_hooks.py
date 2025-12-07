"""
Locust 이벤트 훅

Locust 이벤트에 커스텀 메트릭 수집 연결
"""

from typing import Optional, Any

from locust import events

from .custom_metrics import get_metrics_collector


# 이벤트 훅이 이미 설정되었는지 추적
_hooks_initialized = False


def setup_event_hooks(stage_name: str = ""):
    """
    Locust 이벤트 훅 설정 (최초 1회만 실행)

    Args:
        stage_name: Stage 이름 (메트릭 구분용)

    Usage:
        from load_tests.metrics import setup_event_hooks

        class MyUser(HttpUser):
            def on_start(self):
                setup_event_hooks("Stage1")
    """
    global _hooks_initialized
    if _hooks_initialized:
        return
    _hooks_initialized = True

    collector = get_metrics_collector()

    @events.request.add_listener
    def on_request(
        request_type: str,
        name: str,
        response_time: float,
        response_length: int,
        response: Any,
        context: dict,
        exception: Optional[Exception],
        **kwargs,
    ):
        """요청 완료 이벤트 핸들러"""

        # Stage 추출 (context 또는 name에서)
        stage = context.get("stage", stage_name) if context else stage_name

        # 상태 코드 추출
        status_code = None
        if response is not None:
            status_code = getattr(response, "status_code", None)

        # 에러 메시지 - exception이 있을 때만 에러로 처리
        # catch_response=True 사용 시, Locust가 response.success() 호출하면 exception이 None
        error = str(exception) if exception else None

        # 메트릭 기록
        # exception이 없으면 Locust가 성공으로 간주 (catch_response에서 success() 호출 포함)
        # 이 경우 status_code가 400/404여도 성공으로 처리해야 함
        if exception:
            # 실패: error 메시지와 함께 기록
            collector.record_request(
                name=name,
                method=request_type,
                response_time=response_time,
                status_code=None,  # 에러 시에는 status_code로 판단하지 않음
                error=error,
                stage=stage,
            )
        else:
            # 성공: status_code 200으로 강제 (catch_response에서 success() 호출 시)
            collector.record_request(
                name=name,
                method=request_type,
                response_time=response_time,
                status_code=200,  # 성공으로 처리
                error=None,
                stage=stage,
            )

    @events.test_stop.add_listener
    def on_test_stop(environment, **kwargs):
        """테스트 종료 이벤트 핸들러"""
        collector.print_summary()


def setup_simple_hooks():
    """
    간단한 이벤트 훅 (Stage 없음)
    """
    setup_event_hooks("")


# 편의 함수
def log_request(
    name: str,
    method: str,
    response_time: float,
    status_code: Optional[int] = None,
    error: Optional[str] = None,
    stage: str = "",
):
    """
    수동 메트릭 기록

    Locust 이벤트 훅 외에 직접 기록할 때 사용
    """
    collector = get_metrics_collector()
    collector.record_request(
        name=name,
        method=method,
        response_time=response_time,
        status_code=status_code,
        error=error,
        stage=stage,
    )
