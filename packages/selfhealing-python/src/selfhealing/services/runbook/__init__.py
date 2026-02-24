"""
Automated Runbook Executor — 자동화된 런북 실행 오케스트레이션 패키지.

기존 복구 컴포넌트(RecoveryCoordinator, AutoRollbackGuard, ActionExecutor 등)를
선언적 런북 정의에 따라 통합 호출하는 파사드/오케스트레이터 계층입니다.

Modules:
    pattern_matcher    — 현재 증상 → 과거 패턴 매칭
    runbook_registry   — Step-by-step 조치 등록/조회
    executor           — 단계별 실행, 중간 검증, 보상
    approval_gate      — 위험도별 자동/수동 승인
    playback_recorder  — 실행 과정 재생 가능 기록
    service            — 통합 서비스 (파이프라인 오케스트레이션)

Reference:
    docs/self_healing/middleware_system/272_RUNBOOK_ARCHITECTURE_OVERVIEW.md
"""
