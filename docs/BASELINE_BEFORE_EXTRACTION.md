=== BASELINE TEST RESULTS ===
Date: 2025년 12월 10일 수 오후  1:57:36
Branch: feature/self-healing-extraction

## Test Summary
- Total Passed: 2948
- Total Failed: 2
- Errors: 1
- Coverage: 78.42%

## Known Failures (Pre-existing)
1. test_dlq_storage_and_replay.py::TestReplayService::test_replay_max_attempts_exceeded
2. test_retry_persistence.py::TestRetryCountPersistence::test_max_retry_count_enforced_after_restart

## Known Errors (Pre-existing)
1. test_time_based_behaviors.py - Import/Setup error
