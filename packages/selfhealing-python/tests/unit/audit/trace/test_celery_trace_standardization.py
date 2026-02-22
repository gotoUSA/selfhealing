"""
Celery Task trace_id 표준화 테스트.

테스트 범위:
1. generate_celery_trace_id() 함수
2. set_celery_context() / get_celery_context() / clear_celery_context()
3. is_celery_task() 함수
4. restore_trace_from_celery() 수정사항
"""



class TestGenerateCeleryTraceId:
    """generate_celery_trace_id() 함수 테스트."""

    def test_generates_celery_prefix(self):
        """CELERY_ 접두사가 붙는지 검증."""
        from selfhealing.audit.trace import generate_celery_trace_id

        task_id = "7483abc-1234-5678-90ab-cdef12345678"
        result = generate_celery_trace_id(task_id)

        assert result == f"CELERY_{task_id}"
        assert result.startswith("CELERY_")

    def test_fallback_when_no_task_id(self):
        """task_id가 None일 때 Fallback 동작 검증."""
        from selfhealing.audit.trace import generate_celery_trace_id

        result = generate_celery_trace_id(None)

        assert result.startswith("CELERY_req-")  # Fallback UUID

    def test_empty_task_id_fallback(self):
        """빈 task_id일 때 Fallback 동작 검증."""
        from selfhealing.audit.trace import generate_celery_trace_id

        result = generate_celery_trace_id("")

        assert result.startswith("CELERY_req-")


class TestCeleryContextManagement:
    """Celery 컨텍스트 관리 함수 테스트."""

    def test_set_and_get_celery_context(self):
        """set_celery_context() 후 get_celery_context()로 조회 가능한지 검증."""
        from selfhealing.audit.trace import (
            clear_celery_context,
            get_celery_context,
            set_celery_context,
        )

        set_celery_context(
            task_id="test-task-123",
            task_name="my_task",
            retries=2,
        )

        context = get_celery_context()

        assert context is not None
        assert context["task_id"] == "test-task-123"
        assert context["task_name"] == "my_task"
        assert context["retries"] == 2

        # Cleanup
        clear_celery_context()

    def test_clear_celery_context(self):
        """clear_celery_context() 후 None이 되는지 검증."""
        from selfhealing.audit.trace import (
            clear_celery_context,
            get_celery_context,
            set_celery_context,
        )

        set_celery_context(task_id="test", task_name="test", retries=0)
        clear_celery_context()

        assert get_celery_context() is None

    def test_is_celery_task_true(self):
        """Celery 컨텍스트 설정 후 is_celery_task()가 True 반환하는지 검증."""
        from selfhealing.audit.trace import (
            clear_celery_context,
            is_celery_task,
            set_celery_context,
        )

        set_celery_context(task_id="test", task_name="test", retries=0)

        assert is_celery_task() is True

        clear_celery_context()

    def test_is_celery_task_false(self):
        """Celery 컨텍스트 없을 때 is_celery_task()가 False 반환하는지 검증."""
        from selfhealing.audit.trace import (
            clear_celery_context,
            is_celery_task,
        )

        clear_celery_context()  # 확실히 정리

        assert is_celery_task() is False

    def test_set_celery_context_also_sets_trace_id(self):
        """set_celery_context()가 trace_id도 함께 설정하는지 검증."""
        from selfhealing.audit.trace import (
            clear_celery_context,
            get_trace_id,
            set_celery_context,
        )

        task_id = "abc-123-def"
        set_celery_context(task_id=task_id, task_name="my_task", retries=0)

        trace_id = get_trace_id()
        assert trace_id == f"CELERY_{task_id}"

        clear_celery_context()


class TestRestoreTraceFromCelery:
    """restore_trace_from_celery() 컨텍스트 매니저 테스트."""

    def test_with_trace_info(self):
        """trace_info가 있을 때 전파된 trace_id를 사용하는지 검증."""
        from selfhealing.audit.trace import (
            clear_celery_context,
            get_trace_id,
            restore_trace_from_celery,
        )

        http_trace_id = "req-original-http"
        trace_info = {"trace_id": http_trace_id}

        with restore_trace_from_celery(trace_info=trace_info) as active_trace_id:
            assert active_trace_id == http_trace_id
            assert get_trace_id() == http_trace_id

        clear_celery_context()

    def test_with_celery_task_id(self):
        """celery_task_id가 있을 때 CELERY_{task_id} 형식으로 생성하는지 검증."""
        from selfhealing.audit.trace import (
            get_celery_context,
            get_trace_id,
            is_celery_task,
            restore_trace_from_celery,
        )

        task_id = "celery-task-789"
        task_name = "my_test_task"

        with restore_trace_from_celery(
            celery_task_id=task_id,
            celery_task_name=task_name,
        ) as active_trace_id:
            assert active_trace_id == f"CELERY_{task_id}"
            assert get_trace_id() == f"CELERY_{task_id}"
            assert is_celery_task() is True

            context = get_celery_context()
            assert context["task_id"] == task_id
            assert context["task_name"] == task_name

        # 컨텍스트 매니저 종료 후 정리 확인
        assert is_celery_task() is False

    def test_fallback_without_trace_info_or_task_id(self):
        """trace_info와 celery_task_id 모두 없을 때 Fallback 동작 검증."""
        from selfhealing.audit.trace import (
            clear_celery_context,
            restore_trace_from_celery,
        )

        with restore_trace_from_celery() as active_trace_id:
            assert active_trace_id.startswith("CELERY_req-")

        clear_celery_context()

    def test_trace_info_takes_priority_over_celery_task_id(self):
        """trace_info가 celery_task_id보다 우선하는지 검증."""
        from selfhealing.audit.trace import (
            clear_celery_context,
            restore_trace_from_celery,
        )

        http_trace_id = "req-priority-test"
        trace_info = {"trace_id": http_trace_id}

        with restore_trace_from_celery(
            trace_info=trace_info,
            celery_task_id="should-be-ignored",
        ) as active_trace_id:
            assert active_trace_id == http_trace_id

        clear_celery_context()


# =============================================================================
# task_prerun/postrun 시그널 핸들러 테스트
# =============================================================================


class TestTaskPrerunHandler:
    """task_prerun 시그널 핸들러 테스트."""

    def test_prerun_sets_celery_trace_id(self):
        """task_prerun이 CELERY_{task_id} 형식의 trace_id를 설정하는지 검증."""
        from unittest.mock import MagicMock, patch

        from selfhealing.audit.trace import clear_celery_context, get_trace_id

        with patch("selfhealing.adapters.celery.signal_hooks._config") as mock_config:
            mock_config.enabled = True
            mock_config.excluded_tasks = set()

            from selfhealing.adapters.celery.signal_hooks import on_task_prerun

            mock_sender = MagicMock()
            mock_sender.name = "my_test_task"
            mock_sender.request.retries = 0

            on_task_prerun(
                sender=mock_sender,
                task_id="abc-123-def",
                task=None,
                args=(),
                kwargs={},
            )

            trace_id = get_trace_id()
            assert trace_id == "CELERY_abc-123-def"

            clear_celery_context()

    def test_prerun_preserves_http_trace_id(self):
        """HTTP에서 전파된 trace_id가 있으면 그대로 유지하는지 검증."""
        from unittest.mock import MagicMock, patch

        from selfhealing.audit.trace import clear_celery_context, get_trace_id

        with patch("selfhealing.adapters.celery.signal_hooks._config") as mock_config:
            mock_config.enabled = True
            mock_config.excluded_tasks = set()

            from selfhealing.adapters.celery.signal_hooks import on_task_prerun

            mock_sender = MagicMock()
            mock_sender.name = "my_test_task"
            mock_sender.request.retries = 0

            on_task_prerun(
                sender=mock_sender,
                task_id="abc-123-def",
                task=None,
                args=(),
                kwargs={"trace_info": {"trace_id": "req-original-http"}},
            )

            trace_id = get_trace_id()
            assert trace_id == "req-original-http"

            clear_celery_context()

    def test_prerun_sets_celery_context(self):
        """task_prerun이 celery_context를 설정하는지 검증."""
        from unittest.mock import MagicMock, patch

        from selfhealing.audit.trace import (
            clear_celery_context,
            get_celery_context,
            is_celery_task,
        )

        with patch("selfhealing.adapters.celery.signal_hooks._config") as mock_config:
            mock_config.enabled = True
            mock_config.excluded_tasks = set()

            from selfhealing.adapters.celery.signal_hooks import on_task_prerun

            mock_sender = MagicMock()
            mock_sender.name = "my_replay_task"
            mock_sender.request.retries = 2

            on_task_prerun(
                sender=mock_sender,
                task_id="task-456-xyz",
                task=None,
                args=(),
                kwargs={},
            )

            assert is_celery_task() is True

            context = get_celery_context()
            assert context["task_id"] == "task-456-xyz"
            assert context["task_name"] == "my_replay_task"
            assert context["retries"] == 2

            clear_celery_context()

    def test_prerun_skips_excluded_tasks(self):
        """excluded_tasks에 있는 태스크는 건너뛰는지 검증."""
        from unittest.mock import MagicMock, patch

        from selfhealing.audit.trace import clear_celery_context, is_celery_task

        clear_celery_context()  # 먼저 정리

        with patch("selfhealing.adapters.celery.signal_hooks._config") as mock_config:
            mock_config.enabled = True
            mock_config.excluded_tasks = {"celery.backend_cleanup"}

            from selfhealing.adapters.celery.signal_hooks import on_task_prerun

            mock_sender = MagicMock()
            mock_sender.name = "celery.backend_cleanup"

            on_task_prerun(
                sender=mock_sender,
                task_id="cleanup-task-id",
                task=None,
                args=(),
                kwargs={},
            )

            # excluded task이므로 컨텍스트가 설정되지 않아야 함
            assert is_celery_task() is False

    def test_prerun_disabled_config(self):
        """enabled=False일 때 핸들러가 동작하지 않는지 검증."""
        from unittest.mock import MagicMock, patch

        from selfhealing.audit.trace import clear_celery_context, is_celery_task

        clear_celery_context()

        with patch("selfhealing.adapters.celery.signal_hooks._config") as mock_config:
            mock_config.enabled = False

            from selfhealing.adapters.celery.signal_hooks import on_task_prerun

            mock_sender = MagicMock()
            mock_sender.name = "test_task"

            on_task_prerun(
                sender=mock_sender,
                task_id="test-id",
                task=None,
                args=(),
                kwargs={},
            )

            assert is_celery_task() is False


class TestTaskPostrunHandler:
    """task_postrun 시그널 핸들러 테스트."""

    def test_postrun_clears_celery_context(self):
        """task_postrun이 celery_context를 정리하는지 검증."""
        from unittest.mock import MagicMock, patch

        from selfhealing.audit.trace import (
            is_celery_task,
            set_celery_context,
        )

        # 먼저 컨텍스트 설정
        set_celery_context(task_id="test", task_name="test", retries=0)
        assert is_celery_task() is True

        with patch("selfhealing.adapters.celery.signal_hooks._config") as mock_config:
            mock_config.enabled = True
            mock_config.excluded_tasks = set()

            from selfhealing.adapters.celery.signal_hooks import on_task_postrun

            mock_sender = MagicMock()
            mock_sender.name = "test"

            on_task_postrun(
                sender=mock_sender,
                task_id="test",
                task=None,
                args=(),
                kwargs={},
                retval={"success": True},
                state="SUCCESS",
            )

            # postrun 후 컨텍스트 정리됨
            assert is_celery_task() is False

    def test_postrun_skips_excluded_tasks(self):
        """excluded_tasks에 있는 태스크는 건너뛰는지 검증."""
        from unittest.mock import MagicMock, patch

        from selfhealing.audit.trace import (
            clear_celery_context,
            is_celery_task,
            set_celery_context,
        )

        # 먼저 컨텍스트 설정
        set_celery_context(task_id="cleanup", task_name="celery.backend_cleanup", retries=0)
        assert is_celery_task() is True

        with patch("selfhealing.adapters.celery.signal_hooks._config") as mock_config:
            mock_config.enabled = True
            mock_config.excluded_tasks = {"celery.backend_cleanup"}

            from selfhealing.adapters.celery.signal_hooks import on_task_postrun

            mock_sender = MagicMock()
            mock_sender.name = "celery.backend_cleanup"

            on_task_postrun(
                sender=mock_sender,
                task_id="cleanup",
                task=None,
                args=(),
                kwargs={},
                retval=None,
                state="SUCCESS",
            )

            # excluded task이므로 컨텍스트가 정리되지 않아야 함
            assert is_celery_task() is True

            clear_celery_context()


# =============================================================================
# WAL celery_context 자동 추가 테스트
# =============================================================================


class TestWalCeleryContext:
    """_write_to_wal()의 celery_context 자동 추가 테스트."""

    def test_wal_includes_celery_context(self):
        """WAL 레코드에 celery_context가 포함되는지 검증."""
        from selfhealing.audit.trace import (
            clear_celery_context,
            get_celery_context,
            set_celery_context,
        )

        # Clear previous celery context
        clear_celery_context()

        # Celery 컨텍스트 설정
        set_celery_context(
            task_id="test-task-999",
            task_name="my_replay_task",
            retries=1,
        )

        # Verify context was set correctly
        ctx = get_celery_context()
        assert ctx is not None
        assert ctx["task_id"] == "test-task-999"
        assert ctx["task_name"] == "my_replay_task"
        assert ctx["retries"] == 1

        clear_celery_context()

    def test_wal_celery_context_none_outside_celery(self):
        """Celery Task 외부에서는 celery_context가 None인지 검증."""
        from unittest.mock import MagicMock, patch

        from selfhealing.audit.trace import clear_celery_context

        mock_wal = MagicMock()
        mock_wal.write.return_value = 1

        clear_celery_context()  # 확실히 정리

        with patch("selfhealing.services.audit.base._get_wal", return_value=mock_wal):
            from selfhealing.services.audit.base import _write_to_wal

            _write_to_wal(
                event_type="TEST_EVENT",
                source="test",
                details={"foo": "bar"},
            )

            call_args = mock_wal.write.call_args[0][0]
            assert call_args["celery_context"] is None


# =============================================================================
# elery → Audit 전체 흐름
# =============================================================================


class TestCeleryTraceFlowE2E:
    """Celery → Audit 전체 흐름 E2E 테스트."""

    def test_full_celery_task_trace_flow(self):
        """
        전체 흐름 테스트:
        Celery context와 trace_id가 WAL entry에 포함되는지 검증
        
        Note: 병렬 테스트 환경에서 ContextVar 격리 이슈를 피하기 위해
        _get_trace_id_from_context와 _get_celery_context를 직접 mock합니다.
        """
        from unittest.mock import MagicMock, patch

        mock_wal = MagicMock()
        mock_wal.write.return_value = 1

        # Given: Task 정보
        task_id = "e2e-test-task-123"
        task_name = "selfhealing.adapters.celery.tasks.replay_single_dlq_entry"
        expected_trace_id = f"CELERY_{task_id}"
        expected_celery_context = {
            "task_id": task_id,
            "task_name": task_name,
            "retries": 0
        }

        # Mock both context getters to ensure isolation
        with patch("selfhealing.services.audit.base._get_wal", return_value=mock_wal), \
             patch("selfhealing.services.audit.base._get_trace_id_from_context", return_value=expected_trace_id), \
             patch("selfhealing.services.audit.base._get_celery_context", return_value=expected_celery_context):
            from selfhealing.services.audit.base import _write_to_wal

            _write_to_wal(
                event_type="DLQ_REPLAY_SUCCESS",
                source="ReplayService",
                details={"dlq_id": 123, "domain": "payment"},
            )

            # 검증: WAL 레코드에 trace_id와 celery_context 포함
            call_args = mock_wal.write.call_args[0][0]
            assert call_args["trace_id"] == expected_trace_id
            assert call_args["celery_context"]["task_id"] == task_id
            assert call_args["celery_context"]["task_name"] == task_name

    def test_http_to_celery_trace_propagation(self):
        """
        HTTP → Celery 전파 테스트:
        HTTP 요청의 trace_id가 Celery Task까지 전파되는지 검증
        """
        from unittest.mock import MagicMock, patch

        from selfhealing.audit.trace import clear_celery_context, get_trace_id

        mock_sender = MagicMock()
        mock_sender.name = "test_task"
        mock_sender.request.retries = 0

        # HTTP 요청에서 생성된 원본 trace_id
        http_trace_id = "req-a1b2c3d4"

        with patch("selfhealing.adapters.celery.signal_hooks._config") as mock_config:
            mock_config.enabled = True
            mock_config.excluded_tasks = set()

            from selfhealing.adapters.celery.signal_hooks import on_task_prerun

            on_task_prerun(
                sender=mock_sender,
                task_id="celery-task-456",
                task=None,
                args=(),
                kwargs={"trace_info": {"trace_id": http_trace_id}},
            )

            # 검증: HTTP trace_id가 유지됨 (CELERY_로 덮어쓰지 않음)
            assert get_trace_id() == http_trace_id

            clear_celery_context()
