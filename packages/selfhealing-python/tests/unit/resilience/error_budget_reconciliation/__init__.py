# Error Budget Reconciliation Test Package
"""
test_error_budget_reconciliation.py에서 분리된 테스트 모음.

원본 파일: 1017줄 → 패키지로 분리 (52_REFACTORING_TEST_CODE.md Phase 1)

핵심 원칙: "시스템은 계산하고, 반영은 사람이 결정한다."

Covers:
- FailSafePeriod: Fail-Safe 기간 데이터 모델
- ShadowBudget: Shadow Budget 데이터 모델
- FailSafePeriodTracker: Fail-Safe 기간 추적
- ShadowBudgetCalculator: Shadow Budget 계산
- ReconciliationConfig: Reconciliation 설정
- ErrorBudgetReconciliationService: 통합 서비스
"""
