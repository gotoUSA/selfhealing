"""
MTTR Calculator 테스트.

테스트 대상:
- TestMTTRCalculator: MTTR 계산 기능
"""



class TestMTTRCalculator:
    """MTTR Calculator 테스트."""

    def test_empty_events(self):
        """빈 이벤트 목록."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator

        calculator = MTTRCalculator()
        report = calculator.calculate_mttr([])

        assert report.total_incidents == 0
        assert report.avg_mttr_seconds == 0
        assert report.by_service == {}

    def test_single_recovery_event(self):
        """단일 복구 이벤트."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator

        calculator = MTTRCalculator()

        events = [
            {
                "timestamp": "2026-01-08T10:00:00Z",
                "service_name": "payment",
                "old_state": "closed",
                "new_state": "open",
                "cause": "timeout",
            },
            {
                "timestamp": "2026-01-08T10:05:00Z",
                "service_name": "payment",
                "old_state": "open",
                "new_state": "closed",
            },
        ]

        report = calculator.calculate_mttr(events)

        assert report.total_incidents == 1
        assert report.avg_mttr_seconds == 300  # 5분 = 300초
        assert report.by_service["payment"] == 300

    def test_multiple_services(self):
        """여러 서비스의 복구 이벤트."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator

        calculator = MTTRCalculator()

        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:01:00Z", "service_name": "inventory", "new_state": "open"},
            {"timestamp": "2026-01-08T10:03:00Z", "service_name": "payment", "new_state": "closed"},
            {"timestamp": "2026-01-08T10:06:00Z", "service_name": "inventory", "new_state": "closed"},
        ]

        report = calculator.calculate_mttr(events)

        assert report.total_incidents == 2
        assert "payment" in report.by_service
        assert "inventory" in report.by_service
        assert report.by_service["payment"] == 180  # 3분
        assert report.by_service["inventory"] == 300  # 5분

    def test_percentile_calculation(self):
        """P50/P90/P99 백분위수 계산."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator

        calculator = MTTRCalculator()

        # 10개의 복구 이벤트 생성 (60초 간격으로 MTTR 증가)
        events = []

        for i in range(10):
            start_hour = 10 + i
            # OPEN
            events.append({
                "timestamp": f"2026-01-08T{start_hour:02d}:00:00Z",
                "service_name": f"service_{i}",
                "new_state": "open",
            })
            # CLOSED (i+1분 후)
            events.append({
                "timestamp": f"2026-01-08T{start_hour:02d}:{(i+1):02d}:00Z",
                "service_name": f"service_{i}",
                "new_state": "closed",
            })

        report = calculator.calculate_mttr(events)

        assert report.total_incidents == 10
        assert report.min_mttr_seconds == 60  # 1분
        assert report.max_mttr_seconds == 600  # 10분

        # P50은 중간값
        assert report.p50_mttr_seconds > 0
        assert report.p90_mttr_seconds > report.p50_mttr_seconds

    def test_unmatched_open_ignored(self):
        """매칭되지 않은 OPEN 이벤트 무시."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator

        calculator = MTTRCalculator()

        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            # CLOSED 없이 종료
        ]

        report = calculator.calculate_mttr(events)

        # 매칭되지 않은 OPEN은 복구 이벤트로 카운트되지 않음
        assert report.total_incidents == 0

    def test_closed_without_open_ignored(self):
        """OPEN 없이 CLOSED만 있는 경우 무시."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator

        calculator = MTTRCalculator()

        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "closed"},
        ]

        report = calculator.calculate_mttr(events)

        assert report.total_incidents == 0

    def test_half_open_state_ignored(self):
        """HALF_OPEN 상태는 복구로 간주하지 않음."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator

        calculator = MTTRCalculator()

        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:02:00Z", "service_name": "payment", "new_state": "half_open"},
            {"timestamp": "2026-01-08T10:05:00Z", "service_name": "payment", "new_state": "closed"},
        ]

        report = calculator.calculate_mttr(events)

        # OPEN -> CLOSED 기준으로 5분
        assert report.total_incidents == 1
        assert report.avg_mttr_seconds == 300

    def test_multiple_incidents_same_service(self):
        """같은 서비스의 여러 장애."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator

        calculator = MTTRCalculator()

        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:02:00Z", "service_name": "payment", "new_state": "closed"},
            {"timestamp": "2026-01-08T11:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T11:08:00Z", "service_name": "payment", "new_state": "closed"},
        ]

        report = calculator.calculate_mttr(events)

        assert report.total_incidents == 2
        # 첫 번째: 2분 = 120초, 두 번째: 8분 = 480초
        assert report.by_service["payment"] == (120 + 480) / 2  # 평균 300초

    def test_service_mttr_filter(self):
        """특정 서비스만 필터링하여 MTTR 계산."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator

        calculator = MTTRCalculator()

        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:02:00Z", "service_name": "payment", "new_state": "closed"},
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "inventory", "new_state": "open"},
            {"timestamp": "2026-01-08T10:10:00Z", "service_name": "inventory", "new_state": "closed"},
        ]

        report = calculator.calculate_service_mttr(events, "payment")

        assert report.total_incidents == 1
        assert report.avg_mttr_seconds == 120  # 2분

    def test_period_mttr(self):
        """기간별 MTTR 계산."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator

        calculator = MTTRCalculator()

        events = [
            # 첫 번째 시간대
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:05:00Z", "service_name": "payment", "new_state": "closed"},
            # 두 번째 시간대 (1시간 후)
            {"timestamp": "2026-01-08T11:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T11:10:00Z", "service_name": "payment", "new_state": "closed"},
        ]

        reports = calculator.calculate_mttr_by_period(events, period_hours=1)

        assert len(reports) == 2
        assert reports[0].avg_mttr_seconds == 300  # 5분
        assert reports[1].avg_mttr_seconds == 600  # 10분

    def test_report_to_dict(self):
        """리포트 딕셔너리 변환."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator

        calculator = MTTRCalculator()

        events = [
            {"timestamp": "2026-01-08T10:00:00Z", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:05:00Z", "service_name": "payment", "new_state": "closed"},
        ]

        report = calculator.calculate_mttr(events)
        report_dict = report.to_dict()

        assert "period_start" in report_dict
        assert "period_end" in report_dict
        assert "total_incidents" in report_dict
        assert "avg_mttr_seconds" in report_dict
        assert "avg_mttr_minutes" in report_dict
        assert report_dict["avg_mttr_minutes"] == 5.0

    def test_recovery_event_details(self):
        """복구 이벤트 상세 정보."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator

        calculator = MTTRCalculator()

        events = [
            {
                "timestamp": "2026-01-08T10:00:00Z",
                "service_name": "payment",
                "new_state": "open",
                "cause": "connection_timeout",
                "failure_count": 5,
                "trace_id": "abc123",
            },
            {"timestamp": "2026-01-08T10:05:00Z", "service_name": "payment", "new_state": "closed"},
        ]

        report = calculator.calculate_mttr(events)

        assert len(report.recovery_events) == 1
        event = report.recovery_events[0]
        assert event.service_name == "payment"
        assert event.cause == "connection_timeout"
        assert event.failure_count == 5
        assert event.trace_id == "abc123"
        assert event.duration_seconds == 300

    def test_invalid_timestamp_handled(self):
        """잘못된 타임스탬프 처리."""
        from selfhealing.services.audit.mttr_calculator import MTTRCalculator

        calculator = MTTRCalculator()

        events = [
            {"timestamp": "invalid", "service_name": "payment", "new_state": "open"},
            {"timestamp": "2026-01-08T10:05:00Z", "service_name": "payment", "new_state": "closed"},
        ]

        # 예외 없이 처리
        report = calculator.calculate_mttr(events)

        # 유효한 이벤트만 처리됨
        assert report.total_incidents == 0

    def test_singleton_instance(self):
        """싱글톤 인스턴스."""
        from selfhealing.services.audit.mttr_calculator import get_mttr_calculator

        calc1 = get_mttr_calculator()
        calc2 = get_mttr_calculator()

        assert calc1 is calc2
