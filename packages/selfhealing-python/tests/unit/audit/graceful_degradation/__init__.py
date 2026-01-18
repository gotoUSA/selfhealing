# Hash Chain Graceful Degradation Test Package
"""
test_hash_chain_graceful_degradation.py에서 분리된 테스트 모음.

원본 파일: 1050줄 → 패키지로 분리 (52_REFACTORING_TEST_CODE.md Phase 1)

Covers fault-tolerant features:
- HashChainFallbackChain: Multi-tier fallback (Redis → Replica → Local → Memory)
- DegradedEntryMarker: Marking entries recorded during failures
- HashChainWALRecovery: WAL-based crash recovery
- HashChainDegradationManager: Unified degradation level management
- HashChainCircuitBreaker: Circuit breaker for hash chain operations
- HashChainGracefulDegradationManager: Unified access
"""
