"""
Tests for Canary Shadow Evaluation Gate — _config_hash() and _check_shadow_evaluation().

Target: services/canary/service.py (commit 300)
Covers: _config_hash determinism/idempotency, _check_shadow_evaluation full decision tree.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.canary.service import _config_hash

# ---------------------------------------------------------------------------
# Test helpers — lightweight stand-ins for CanaryRollout / ShadowEvaluation
# ---------------------------------------------------------------------------


@dataclass
class _FakeReport:
    passed: bool = True
    confidence_score: float = 0.9
    summary: str = "All checks passed"
    warnings: list[str] = field(default_factory=list)


@dataclass
class _FakeEvaluation:
    evaluation_id: str = "eval-abc123"
    status: Any = None  # set per test
    completed_at: datetime | None = None
    candidate_config: dict[str, Any] | None = None
    report: _FakeReport | None = None


@dataclass
class _FakeRollout:
    id: str = "rollout-001"
    state: Any = None
    current_stage: Any = None
    current_stage_index: int = 0
    affected_clusters: list[str] = field(default_factory=list)
    candidate_config: dict[str, Any] | None = None
    previous_values: dict[str, Any] = field(default_factory=dict)
    new_values: dict[str, Any] = field(default_factory=dict)
    config_type: str = "circuit_breaker"
    created_by: str = "test"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = "test"


# ---------------------------------------------------------------------------
# _config_hash() tests
# ---------------------------------------------------------------------------


class TestConfigHashContract:
    """_config_hash 계약 검증."""

    def test_hash_length_is_sixteen_hex_chars(self):
        """해시 길이가 항상 16자(SHA-256 앞 16자)이다."""
        result = _config_hash({"key": "value"})
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_uses_sha256_algorithm(self):
        """SHA-256 기반으로 해시를 생성한다."""
        config = {"a": 1, "b": [2, 3]}
        expected = hashlib.sha256(
            json.dumps({"a": 1, "b": [2, 3]}).encode()
        ).hexdigest()[:16]
        assert _config_hash(config) == expected


class TestConfigHashBehavior:
    """_config_hash 동작 검증."""

    def test_deterministic_for_same_input(self):
        """동일 입력에 대해 동일 해시를 반환한다 (멱등성)."""
        config = {"timeout": 30, "retries": 3}
        assert _config_hash(config) == _config_hash(config)

    def test_dict_key_order_does_not_affect_hash(self):
        """딕셔너리 키 순서가 달라도 동일 해시 (정렬)."""
        config_a = {"z": 1, "a": 2}
        config_b = {"a": 2, "z": 1}
        assert _config_hash(config_a) == _config_hash(config_b)

    def test_nested_dict_key_order_does_not_affect_hash(self):
        """중첩 딕셔너리의 키 순서도 해시에 영향 없음."""
        config_a = {"outer": {"z": 1, "a": 2}}
        config_b = {"outer": {"a": 2, "z": 1}}
        assert _config_hash(config_a) == _config_hash(config_b)

    def test_different_values_produce_different_hashes(self):
        """다른 값은 다른 해시를 생성한다."""
        config_a = {"timeout": 30}
        config_b = {"timeout": 60}
        assert _config_hash(config_a) != _config_hash(config_b)

    def test_empty_dict_produces_valid_hash(self):
        """빈 딕셔너리도 유효한 해시를 생성한다."""
        result = _config_hash({})
        assert len(result) == 16

    def test_non_serializable_values_converted_to_string(self):
        """비직렬화 객체는 str()로 변환 후 해싱된다."""
        config = {"custom": object()}
        result = _config_hash(config)
        assert len(result) == 16

    def test_list_values_are_preserved_in_hash(self):
        """리스트 값은 순서를 유지하므로 해시에 영향."""
        config_a = {"items": [1, 2, 3]}
        config_b = {"items": [3, 2, 1]}
        assert _config_hash(config_a) != _config_hash(config_b)

    def test_input_dict_is_not_mutated(self):
        """입력 딕셔너리가 변경되지 않는다 (데이터 불변성)."""
        config = {"b": 2, "a": 1, "nested": {"z": 9, "y": 8}}
        original = json.dumps(config)
        _config_hash(config)
        assert json.dumps(config) == original


# ---------------------------------------------------------------------------
# _check_shadow_evaluation() tests
# ---------------------------------------------------------------------------


def _make_completed_evaluation(
    passed: bool = True,
    confidence: float = 0.9,
    candidate_config: dict | None = None,
    completed_at: datetime | None = None,
) -> _FakeEvaluation:
    """Helper to create a COMPLETED evaluation with a report."""
    from selfhealing.services.config_shadow.models import EvaluationStatus

    return _FakeEvaluation(
        status=EvaluationStatus.COMPLETED,
        completed_at=completed_at or datetime.now(timezone.utc),
        candidate_config=candidate_config,
        report=_FakeReport(
            passed=passed,
            confidence_score=confidence,
        ),
    )


@pytest.fixture()
def canary_service():
    """Minimal CanaryRolloutService with all deps mocked."""
    from selfhealing.services.canary.service import CanaryRolloutService

    return CanaryRolloutService.__new__(CanaryRolloutService)


@pytest.fixture()
def rollout():
    return _FakeRollout()


class TestCheckShadowEvaluationBehavior:
    """_check_shadow_evaluation 동작 검증."""

    @pytest.fixture(autouse=True)
    def reset_settings(self):
        from selfhealing.settings.config_shadow import reset_config_shadow_settings

        reset_config_shadow_settings()
        yield
        reset_config_shadow_settings()

    # --- Gate disabled / import failure → None ---

    def test_gate_disabled_returns_none(self, canary_service, rollout):
        """gate_enabled=False 시 None을 반환한다 (체크 생략)."""
        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = False
            mock_settings.return_value = settings_obj

            result = canary_service._check_shadow_evaluation(
                rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
            )
        assert result is None

    def test_settings_import_error_returns_none(self, canary_service, rollout):
        """settings 모듈 import 실패 시 None을 반환한다."""
        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            side_effect=ImportError("no module"),
        ):
            result = canary_service._check_shadow_evaluation(
                rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
            )
        assert result is None

    def test_shadow_service_import_error_returns_none(self, canary_service, rollout):
        """shadow evaluator service import 실패 시 None을 반환한다."""
        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                side_effect=ImportError("no module"),
            ):
                result = canary_service._check_shadow_evaluation(
                    rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                )
        assert result is None

    def test_shadow_service_general_exception_returns_none(
        self, canary_service, rollout
    ):
        """shadow evaluator service에서 일반 예외 발생 시 None을 반환한다."""
        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.side_effect = RuntimeError("db error")
                mock_svc_fn.return_value = mock_svc

                result = canary_service._check_shadow_evaluation(
                    rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                )
        assert result is None

    # --- No evaluation found ---

    def test_no_evaluation_and_require_false_returns_none(
        self, canary_service, rollout
    ):
        """평가 없고 require_evaluation=False → None."""
        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = None
                mock_svc_fn.return_value = mock_svc

                result = canary_service._check_shadow_evaluation(
                    rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                )
        assert result is None

    def test_no_evaluation_and_require_true_returns_false(
        self, canary_service, rollout
    ):
        """평가 없고 require_evaluation=True → False (차단)."""
        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = True
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = None
                mock_svc_fn.return_value = mock_svc

                result = canary_service._check_shadow_evaluation(
                    rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                )
        assert result is False

    # --- Evaluation in progress (PENDING / RUNNING) ---

    @pytest.mark.parametrize("status_name", ["PENDING", "RUNNING"])
    def test_evaluation_in_progress_returns_false(
        self, canary_service, rollout, status_name
    ):
        """PENDING/RUNNING 상태 평가 → False (차단)."""
        from selfhealing.services.config_shadow.models import EvaluationStatus

        status = EvaluationStatus[status_name]
        evaluation = _FakeEvaluation(status=status)

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                result = canary_service._check_shadow_evaluation(
                    rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                )
        assert result is False

    # --- Stale evaluation (TTL expired) ---

    def test_stale_evaluation_returns_false(self, canary_service, rollout):
        """TTL을 초과한 평가 → False (차단)."""
        evaluation = _make_completed_evaluation(
            completed_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            settings_obj.evaluation_ttl_hours = 1.0
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                result = canary_service._check_shadow_evaluation(
                    rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                )
        assert result is False

    def test_fresh_evaluation_within_ttl_passes(self, canary_service, rollout):
        """TTL 이내 평가 + passed → True."""
        evaluation = _make_completed_evaluation(
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            settings_obj.evaluation_ttl_hours = 1.0
            settings_obj.min_confidence = 0.3
            settings_obj.block_on_low_confidence = False
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                with patch("selfhealing.services.canary.service.log_canary_action"):
                    result = canary_service._check_shadow_evaluation(
                        rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                    )
        assert result is True

    # --- Config mismatch ---

    def test_config_hash_mismatch_returns_false(self, canary_service, rollout):
        """평가 시 config와 현재 config의 해시가 다르면 False."""
        rollout.candidate_config = {"timeout": 30}
        evaluation = _make_completed_evaluation(
            candidate_config={"timeout": 60},
            completed_at=datetime.now(timezone.utc),
        )

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            settings_obj.evaluation_ttl_hours = 1.0
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                result = canary_service._check_shadow_evaluation(
                    rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                )
        assert result is False

    def test_config_hash_match_allows_pass(self, canary_service, rollout):
        """평가 시 config와 현재 config의 해시가 동일하면 통과 가능."""
        config = {"timeout": 30, "retries": 3}
        rollout.candidate_config = config
        evaluation = _make_completed_evaluation(
            candidate_config=config.copy(),
            completed_at=datetime.now(timezone.utc),
        )

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            settings_obj.evaluation_ttl_hours = 1.0
            settings_obj.min_confidence = 0.3
            settings_obj.block_on_low_confidence = False
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                with patch("selfhealing.services.canary.service.log_canary_action"):
                    result = canary_service._check_shadow_evaluation(
                        rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                    )
        assert result is True

    # --- Low confidence ---

    def test_low_confidence_with_block_enabled_returns_false(
        self, canary_service, rollout
    ):
        """passed=True, 저신뢰도 + block_on_low_confidence=True → False."""
        evaluation = _make_completed_evaluation(
            confidence=0.1,
            completed_at=datetime.now(timezone.utc),
        )

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            settings_obj.evaluation_ttl_hours = 24.0
            settings_obj.min_confidence = 0.3
            settings_obj.block_on_low_confidence = True
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                with patch("selfhealing.services.canary.service.log_canary_action"):
                    result = canary_service._check_shadow_evaluation(
                        rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                    )
        assert result is False

    def test_low_confidence_with_block_disabled_returns_true(
        self, canary_service, rollout
    ):
        """passed=True, 저신뢰도 + block_on_low_confidence=False → True (경고만)."""
        evaluation = _make_completed_evaluation(
            confidence=0.1,
            completed_at=datetime.now(timezone.utc),
        )

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            settings_obj.evaluation_ttl_hours = 24.0
            settings_obj.min_confidence = 0.3
            settings_obj.block_on_low_confidence = False
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                with patch("selfhealing.services.canary.service.log_canary_action"):
                    result = canary_service._check_shadow_evaluation(
                        rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                    )
        assert result is True

    def test_low_confidence_emits_audit_action(self, canary_service, rollout):
        """저신뢰도 시 shadow_evaluation_low_confidence 감사 로그를 기록한다."""
        evaluation = _make_completed_evaluation(
            confidence=0.1,
            completed_at=datetime.now(timezone.utc),
        )

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            settings_obj.evaluation_ttl_hours = 24.0
            settings_obj.min_confidence = 0.3
            settings_obj.block_on_low_confidence = False
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                with patch(
                    "selfhealing.services.canary.service.log_canary_action"
                ) as mock_audit:
                    canary_service._check_shadow_evaluation(
                        rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                    )

        mock_audit.assert_called_once_with(
            action="shadow_evaluation_low_confidence",
            rollout=rollout,
            safety_check_result={
                "evaluation_id": evaluation.evaluation_id,
                "confidence_score": 0.1,
                "min_confidence": 0.3,
            },
        )

    # --- Bypass logic ---

    def test_bypass_with_sufficient_reason_returns_true(self, canary_service, rollout):
        """평가 실패 + bypass + 충분한 사유 → True."""
        evaluation = _make_completed_evaluation(
            passed=False,
            confidence=0.0,
            completed_at=datetime.now(timezone.utc),
        )

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            settings_obj.evaluation_ttl_hours = 24.0
            settings_obj.min_confidence = 0.3
            settings_obj.block_on_low_confidence = False
            settings_obj.bypass_min_reason_length = 10
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                with patch("selfhealing.services.canary.service.log_canary_action"):
                    result = canary_service._check_shadow_evaluation(
                        rollout=rollout,
                        bypass_shadow=True,
                        bypass_shadow_reason="Emergency: production hotfix required now",
                    )
        assert result is True

    def test_bypass_with_short_reason_returns_false(self, canary_service, rollout):
        """bypass 시 사유가 최소 길이 미만이면 False."""
        evaluation = _make_completed_evaluation(
            passed=False,
            confidence=0.0,
            completed_at=datetime.now(timezone.utc),
        )

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            settings_obj.evaluation_ttl_hours = 24.0
            settings_obj.min_confidence = 0.3
            settings_obj.block_on_low_confidence = False
            settings_obj.bypass_min_reason_length = 10
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                result = canary_service._check_shadow_evaluation(
                    rollout=rollout,
                    bypass_shadow=True,
                    bypass_shadow_reason="short",
                )
        assert result is False

    def test_bypass_with_empty_reason_returns_false(self, canary_service, rollout):
        """bypass 시 사유가 빈 문자열이면 False."""
        evaluation = _make_completed_evaluation(
            passed=False,
            confidence=0.0,
            completed_at=datetime.now(timezone.utc),
        )

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            settings_obj.evaluation_ttl_hours = 24.0
            settings_obj.bypass_min_reason_length = 10
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                result = canary_service._check_shadow_evaluation(
                    rollout=rollout,
                    bypass_shadow=True,
                    bypass_shadow_reason="",
                )
        assert result is False

    def test_bypass_emits_audit_action(self, canary_service, rollout):
        """bypass 성공 시 shadow_evaluation_bypass 감사 로그를 기록한다."""
        evaluation = _make_completed_evaluation(
            passed=False,
            confidence=0.0,
            completed_at=datetime.now(timezone.utc),
        )

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            settings_obj.evaluation_ttl_hours = 24.0
            settings_obj.bypass_min_reason_length = 10
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                with patch(
                    "selfhealing.services.canary.service.log_canary_action"
                ) as mock_audit:
                    canary_service._check_shadow_evaluation(
                        rollout=rollout,
                        bypass_shadow=True,
                        bypass_shadow_reason="Emergency hotfix needed",
                    )

        mock_audit.assert_called_once_with(
            action="shadow_evaluation_bypass",
            rollout=rollout,
            safety_check_result={
                "evaluation_id": evaluation.evaluation_id,
                "bypass_reason": "Emergency hotfix needed",
                "evaluation_summary": evaluation.report.summary,
                "confidence_score": evaluation.report.confidence_score,
            },
        )

    # --- Evaluation failed, no bypass ---

    def test_failed_evaluation_without_bypass_returns_false(
        self, canary_service, rollout
    ):
        """평가 실패 + bypass=False → False (차단)."""
        evaluation = _make_completed_evaluation(
            passed=False,
            confidence=0.0,
            completed_at=datetime.now(timezone.utc),
        )

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            settings_obj.evaluation_ttl_hours = 24.0
            settings_obj.min_confidence = 0.3
            settings_obj.block_on_low_confidence = False
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                result = canary_service._check_shadow_evaluation(
                    rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                )
        assert result is False

    # --- Passed evaluation → True ---

    def test_passed_evaluation_with_high_confidence_returns_true(
        self, canary_service, rollout
    ):
        """passed=True, 고신뢰도 → True."""
        evaluation = _make_completed_evaluation(
            passed=True,
            confidence=0.95,
            completed_at=datetime.now(timezone.utc),
        )

        with patch(
            "selfhealing.settings.config_shadow.get_config_shadow_settings",
            autospec=True,
        ) as mock_settings_fn:
            settings_obj = MagicMock()
            settings_obj.gate_enabled = True
            settings_obj.require_evaluation = False
            settings_obj.evaluation_ttl_hours = 24.0
            settings_obj.min_confidence = 0.3
            settings_obj.block_on_low_confidence = False
            mock_settings_fn.return_value = settings_obj

            with patch(
                "selfhealing.services.config_shadow.get_shadow_evaluator_service",
                autospec=True,
            ) as mock_svc_fn:
                mock_svc = MagicMock()
                mock_svc.get_latest_for_rollout.return_value = evaluation
                mock_svc_fn.return_value = mock_svc

                with patch("selfhealing.services.canary.service.log_canary_action"):
                    result = canary_service._check_shadow_evaluation(
                        rollout=rollout, bypass_shadow=False, bypass_shadow_reason=""
                    )
        assert result is True
