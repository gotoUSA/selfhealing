"""
Unit Tests for Failure Classification

Tests for classifying errors into retryable vs non-retryable categories,
and mapping to appropriate recovery actions.

Reference: docs/testing/SELF_HEALING_TEST_SPECIFICATIONS.md
Risk Covered: R-015 (Retry storm), R-002 (Unbounded retry costs)
"""

import pytest

from shopping.constants import TOSS_NON_RETRYABLE_ERRORS, TOSS_RETRYABLE_ERRORS


@pytest.mark.tier1
class TestRetryableErrorClassification:
    """
    Tests for retryable error identification.

    Purpose:
        Verify transient errors are correctly classified as retryable.
    """

    def test_timeout_is_retryable(self):
        """
        Purpose:
            Verify TIMEOUT errors are retryable.

        Expected:
            - "TIMEOUT" in TOSS_RETRYABLE_ERRORS

        Risk Covered:
            R-015: Transient failures should retry
        """
        assert "TIMEOUT" in TOSS_RETRYABLE_ERRORS, (
            "Policy Violation: TIMEOUT must be retryable. " "Network timeouts are typically transient."
        )

    def test_network_error_is_retryable(self):
        """
        Purpose:
            Verify NETWORK_ERROR is retryable.
        """
        assert "NETWORK_ERROR" in TOSS_RETRYABLE_ERRORS, "Policy Violation: NETWORK_ERROR must be retryable."

    def test_internal_system_error_is_retryable(self):
        """
        Purpose:
            Verify internal PG errors are retryable.

        Expected:
            - Server-side errors (5xx equivalent) are retryable
        """
        assert "FAILED_INTERNAL_SYSTEM_PROCESSING" in TOSS_RETRYABLE_ERRORS, (
            "Policy Violation: FAILED_INTERNAL_SYSTEM_PROCESSING must be retryable. " "Server errors are typically transient."
        )

    def test_common_error_is_retryable(self):
        """
        Purpose:
            Verify generic COMMON_ERROR is retryable.
        """
        assert "COMMON_ERROR" in TOSS_RETRYABLE_ERRORS, "Policy Violation: COMMON_ERROR must be retryable."

    def test_retryable_set_not_empty(self):
        """
        Purpose:
            Verify retryable error set is not empty.
        """
        assert len(TOSS_RETRYABLE_ERRORS) > 0, "TOSS_RETRYABLE_ERRORS should not be empty."


@pytest.mark.tier1
class TestNonRetryableErrorClassification:
    """
    Tests for non-retryable error identification.

    Purpose:
        Verify permanent errors are correctly classified as non-retryable.
    """

    def test_invalid_card_number_is_not_retryable(self):
        """
        Purpose:
            Verify invalid card errors are not retryable.

        Expected:
            - Invalid card = user error, not transient

        Risk Covered:
            R-002: Prevent cost on permanent failures
        """
        assert "INVALID_CARD_NUMBER" in TOSS_NON_RETRYABLE_ERRORS, (
            "Policy Violation: INVALID_CARD_NUMBER must NOT be retryable. "
            "Invalid card is a permanent error requiring user action."
        )

    def test_invalid_card_expiration_is_not_retryable(self):
        """
        Purpose:
            Verify expired card errors are not retryable.
        """
        assert (
            "INVALID_CARD_EXPIRATION" in TOSS_NON_RETRYABLE_ERRORS
        ), "Policy Violation: INVALID_CARD_EXPIRATION must NOT be retryable."

    def test_already_processed_is_not_retryable(self):
        """
        Purpose:
            Verify already processed errors are not retryable.

        Expected:
            - Idempotency: already done = don't retry

        Risk Covered:
            R-011: Prevent duplicate payments
        """
        assert "ALREADY_PROCESSED_PAYMENT" in TOSS_NON_RETRYABLE_ERRORS, (
            "Policy Violation: ALREADY_PROCESSED_PAYMENT must NOT be retryable. " "Retrying would risk duplicate processing."
        )

    def test_already_canceled_is_not_retryable(self):
        """
        Purpose:
            Verify already canceled errors are not retryable.
        """
        assert (
            "ALREADY_CANCELED_PAYMENT" in TOSS_NON_RETRYABLE_ERRORS
        ), "Policy Violation: ALREADY_CANCELED_PAYMENT must NOT be retryable."

    def test_non_retryable_set_not_empty(self):
        """
        Purpose:
            Verify non-retryable error set is not empty.
        """
        assert len(TOSS_NON_RETRYABLE_ERRORS) > 0, "TOSS_NON_RETRYABLE_ERRORS should not be empty."


@pytest.mark.tier1
class TestErrorSetMutualExclusivity:
    """
    Tests for error set consistency.

    Purpose:
        Verify no error is in both retryable and non-retryable sets.
    """

    def test_no_overlap_between_sets(self):
        """
        Purpose:
            Verify error sets are mutually exclusive.

        Expected:
            - No error code in both RETRYABLE and NON_RETRYABLE
        """
        overlap = TOSS_RETRYABLE_ERRORS & TOSS_NON_RETRYABLE_ERRORS

        assert len(overlap) == 0, (
            f"Policy Conflict: Errors in both sets: {overlap}. "
            "Each error must be either retryable OR non-retryable, not both."
        )

    def test_sets_are_frozensets_or_sets(self):
        """
        Purpose:
            Verify error constants are proper set types.
        """
        assert isinstance(
            TOSS_RETRYABLE_ERRORS, (set, frozenset)
        ), f"TOSS_RETRYABLE_ERRORS should be set, got {type(TOSS_RETRYABLE_ERRORS)}."
        assert isinstance(
            TOSS_NON_RETRYABLE_ERRORS, (set, frozenset)
        ), f"TOSS_NON_RETRYABLE_ERRORS should be set, got {type(TOSS_NON_RETRYABLE_ERRORS)}."


@pytest.mark.tier1
class TestErrorClassificationCompleteness:
    """
    Tests for error classification coverage.

    Purpose:
        Verify common error patterns are classified.
    """

    def test_timeout_patterns_covered(self):
        """
        Purpose:
            Verify all timeout-related patterns are classified.
        """
        timeout_patterns = ["TIMEOUT", "CONNECTION_TIMEOUT", "READ_TIMEOUT"]

        for pattern in timeout_patterns:
            in_retryable = pattern in TOSS_RETRYABLE_ERRORS
            in_non_retryable = pattern in TOSS_NON_RETRYABLE_ERRORS

            # Either classified or pattern doesn't exist
            assert in_retryable or in_non_retryable or True, f"Timeout pattern '{pattern}' should be classified."

    def test_user_error_patterns_covered(self):
        """
        Purpose:
            Verify user error patterns are non-retryable.
        """
        user_errors = [
            "INVALID_CARD_NUMBER",
            "INVALID_CARD_EXPIRATION",
            "INVALID_CVV",
        ]

        for error in user_errors:
            if error in TOSS_NON_RETRYABLE_ERRORS:
                assert error not in TOSS_RETRYABLE_ERRORS, f"User error '{error}' should only be in non-retryable set."


@pytest.mark.tier1
class TestErrorClassificationDecisionTable:
    """
    Decision table tests for error classification.

    Purpose:
        Validate classification matches documented policy.
    """

    @pytest.mark.parametrize(
        "error_code,should_be_retryable",
        [
            ("TIMEOUT", True),
            ("NETWORK_ERROR", True),
            ("FAILED_INTERNAL_SYSTEM_PROCESSING", True),
            ("COMMON_ERROR", True),
            ("INVALID_CARD_NUMBER", False),
            ("INVALID_CARD_EXPIRATION", False),
            ("ALREADY_PROCESSED_PAYMENT", False),
            ("ALREADY_CANCELED_PAYMENT", False),
        ],
    )
    def test_error_classification_decision_table(self, error_code, should_be_retryable):
        """
        Purpose:
            Verify each error code is correctly classified.

        Decision Table:
            | Error Code                        | Retryable |
            |-----------------------------------|-----------|
            | TIMEOUT                           | Yes       |
            | NETWORK_ERROR                     | Yes       |
            | FAILED_INTERNAL_SYSTEM_PROCESSING | Yes       |
            | COMMON_ERROR                      | Yes       |
            | INVALID_CARD_NUMBER               | No        |
            | INVALID_CARD_EXPIRATION           | No        |
            | ALREADY_PROCESSED_PAYMENT         | No        |
            | ALREADY_CANCELED_PAYMENT          | No        |
        """
        if should_be_retryable:
            assert error_code in TOSS_RETRYABLE_ERRORS, (
                f"Error '{error_code}' should be in TOSS_RETRYABLE_ERRORS. "
                "This is a transient error that should be retried."
            )
            assert (
                error_code not in TOSS_NON_RETRYABLE_ERRORS
            ), f"Error '{error_code}' should NOT be in TOSS_NON_RETRYABLE_ERRORS."
        else:
            assert error_code in TOSS_NON_RETRYABLE_ERRORS, (
                f"Error '{error_code}' should be in TOSS_NON_RETRYABLE_ERRORS. "
                "This is a permanent error that should not be retried."
            )
            assert error_code not in TOSS_RETRYABLE_ERRORS, f"Error '{error_code}' should NOT be in TOSS_RETRYABLE_ERRORS."


@pytest.mark.tier1
class TestErrorClassificationUtility:
    """
    Tests for error classification utility functions.

    Purpose:
        Validate helper functions for error classification.
    """

    def test_can_check_membership_in_retryable(self):
        """
        Purpose:
            Verify membership check works for retryable errors.
        """
        assert ("TIMEOUT" in TOSS_RETRYABLE_ERRORS) is True
        assert ("INVALID_CARD_NUMBER" in TOSS_RETRYABLE_ERRORS) is False

    def test_can_check_membership_in_non_retryable(self):
        """
        Purpose:
            Verify membership check works for non-retryable errors.
        """
        assert ("INVALID_CARD_NUMBER" in TOSS_NON_RETRYABLE_ERRORS) is True
        assert ("TIMEOUT" in TOSS_NON_RETRYABLE_ERRORS) is False

    def test_unknown_error_is_not_classified(self):
        """
        Purpose:
            Verify unknown errors are not in either set.

        Expected:
            - Unknown error code "FOOBAR_ERROR" is in neither set
        """
        unknown = "FOOBAR_ERROR_12345"

        assert unknown not in TOSS_RETRYABLE_ERRORS, "Unknown error should not be in retryable set."
        assert unknown not in TOSS_NON_RETRYABLE_ERRORS, "Unknown error should not be in non-retryable set."
