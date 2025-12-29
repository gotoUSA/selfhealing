"""
Self-Learning DNA Service - 자가 학습 서비스
"""

import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any
from threading import Lock
from collections import defaultdict

from .models import (
    LearningPattern,
    LearningSession,
    Suggestion,
    PerformanceMetric,
    PatternType,
    SuggestionPriority,
)

logger = logging.getLogger(__name__)


class LearningService:
    """
    Self-Learning DNA 서비스
    
    패턴을 학습하고 최적화 제안을 생성합니다.
    """
    
    _instance: Optional["LearningService"] = None
    _lock = Lock()
    
    def __new__(cls) -> "LearningService":
        """싱글톤 패턴"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._patterns: Dict[str, LearningPattern] = {}
        self._sessions: Dict[str, LearningSession] = {}
        self._suggestions: List[Suggestion] = []
        self._metrics: List[PerformanceMetric] = []
        self._enabled = True
        self._suggestion_threshold = 0.8  # 제안 생성 임계값
        self._initialized = True
        
        logger.info("LearningService initialized")
    
    def start_session(self, stage_name: str) -> LearningSession:
        """
        학습 세션 시작
        
        Args:
            stage_name: Stage 이름
        
        Returns:
            LearningSession: 생성된 세션
        """
        session_id = str(uuid.uuid4())[:8]
        session = LearningSession(
            session_id=session_id,
            stage_name=stage_name,
        )
        self._sessions[session_id] = session
        logger.info(f"Learning session started: {session_id} for {stage_name}")
        return session
    
    def end_session(self, session_id: str) -> Optional[LearningSession]:
        """
        학습 세션 종료
        
        Args:
            session_id: 세션 ID
        
        Returns:
            LearningSession: 종료된 세션
        """
        session = self._sessions.get(session_id)
        if session:
            session.ended_at = datetime.now()
            session.status = "completed"
            logger.info(
                f"Learning session completed: {session_id}, "
                f"patterns: {session.patterns_learned}, "
                f"suggestions: {session.suggestions_generated}"
            )
        return session
    
    def learn_pattern(
        self,
        pattern_type: PatternType,
        name: str,
        description: str,
        features: Dict[str, Any],
        confidence: float = 0.8,
        session_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> LearningPattern:
        """
        패턴 학습
        
        Args:
            pattern_type: 패턴 유형
            name: 패턴 이름
            description: 패턴 설명
            features: 패턴 특성
            confidence: 신뢰도
            session_id: 세션 ID (있으면 세션에 기록)
            metadata: 추가 메타데이터
        
        Returns:
            LearningPattern: 학습된 패턴
        """
        # 기존 패턴 찾기 (이름 기반)
        existing = None
        for p in self._patterns.values():
            if p.name == name and p.pattern_type == pattern_type:
                existing = p
                break
        
        if existing:
            # 기존 패턴 업데이트
            existing.occurrence_count += 1
            existing.last_seen = datetime.now()
            existing.confidence = (existing.confidence + confidence) / 2  # 평균
            pattern = existing
        else:
            # 새 패턴 생성
            pattern_id = str(uuid.uuid4())[:8]
            pattern = LearningPattern(
                pattern_id=pattern_id,
                pattern_type=pattern_type,
                name=name,
                description=description,
                confidence=confidence,
                features=features,
                metadata=metadata or {},
            )
            self._patterns[pattern_id] = pattern
        
        # 세션 업데이트
        if session_id and session_id in self._sessions:
            self._sessions[session_id].patterns_learned += 1
        
        logger.debug(f"Pattern learned: {name} ({pattern_type.value})")
        
        # 제안 생성 체크
        self._check_and_generate_suggestions(pattern)
        
        return pattern
    
    def _check_and_generate_suggestions(self, pattern: LearningPattern) -> None:
        """패턴 기반 제안 생성 체크"""
        if pattern.confidence < self._suggestion_threshold:
            return
        
        # 반복 패턴에서 제안 생성
        if pattern.occurrence_count >= 3:
            if pattern.pattern_type == PatternType.FAILURE:
                self._generate_failure_suggestion(pattern)
            elif pattern.pattern_type == PatternType.PERFORMANCE:
                self._generate_performance_suggestion(pattern)
    
    def _generate_failure_suggestion(self, pattern: LearningPattern) -> Suggestion:
        """장애 패턴 기반 제안 생성"""
        suggestion = Suggestion(
            suggestion_id=str(uuid.uuid4())[:8],
            title=f"Prevent {pattern.name}",
            description=f"패턴 '{pattern.name}'이(가) {pattern.occurrence_count}회 발생했습니다. "
                       f"선제적 조치를 권장합니다.",
            priority=SuggestionPriority.HIGH,
            stage_name=pattern.metadata.get("stage_name", "unknown"),
            pattern_id=pattern.pattern_id,
            confidence=pattern.confidence,
            expected_improvement=15.0,  # 예상 15% 개선
            action="enable_circuit_breaker",
            parameters={"threshold": 0.5},
        )
        self._suggestions.append(suggestion)
        return suggestion
    
    def _generate_performance_suggestion(self, pattern: LearningPattern) -> Suggestion:
        """성능 패턴 기반 제안 생성"""
        suggestion = Suggestion(
            suggestion_id=str(uuid.uuid4())[:8],
            title=f"Optimize {pattern.name}",
            description=f"성능 패턴 '{pattern.name}'을(를) 기반으로 최적화를 제안합니다.",
            priority=SuggestionPriority.MEDIUM,
            stage_name=pattern.metadata.get("stage_name", "unknown"),
            pattern_id=pattern.pattern_id,
            confidence=pattern.confidence,
            expected_improvement=10.0,
            action="tune_parameters",
            parameters=pattern.features,
        )
        self._suggestions.append(suggestion)
        return suggestion
    
    def record_metric(
        self,
        metric_name: str,
        value: float,
        stage_name: str = "",
        unit: str = "",
        tags: Optional[Dict[str, str]] = None,
    ) -> PerformanceMetric:
        """
        성능 메트릭 기록
        
        Args:
            metric_name: 메트릭 이름
            value: 값
            stage_name: Stage 이름
            unit: 단위
            tags: 태그
        
        Returns:
            PerformanceMetric: 기록된 메트릭
        """
        metric = PerformanceMetric(
            metric_name=metric_name,
            value=value,
            unit=unit,
            stage_name=stage_name,
            tags=tags or {},
        )
        self._metrics.append(metric)
        
        # 이상 탐지
        self._detect_anomaly(metric)
        
        return metric
    
    def _detect_anomaly(self, metric: PerformanceMetric) -> None:
        """이상 탐지"""
        # 같은 메트릭의 최근 값들 가져오기
        recent_values = [
            m.value for m in self._metrics[-100:]
            if m.metric_name == metric.metric_name
        ]
        
        if len(recent_values) < 10:
            return
        
        avg = sum(recent_values) / len(recent_values)
        if metric.value > avg * 2:  # 평균의 2배 초과
            self.learn_pattern(
                pattern_type=PatternType.ANOMALY,
                name=f"High {metric.metric_name}",
                description=f"메트릭 {metric.metric_name}이(가) 평균의 2배를 초과했습니다.",
                features={"value": metric.value, "avg": avg},
                confidence=0.9,
                metadata={"stage_name": metric.stage_name},
            )
    
    def get_suggestions(
        self,
        stage_name: Optional[str] = None,
        priority: Optional[SuggestionPriority] = None,
        unapplied_only: bool = False,
    ) -> List[Suggestion]:
        """
        제안 조회
        
        Args:
            stage_name: Stage 이름 필터
            priority: 우선순위 필터
            unapplied_only: 미적용 제안만
        
        Returns:
            List[Suggestion]: 제안 목록
        """
        suggestions = self._suggestions
        
        if stage_name:
            suggestions = [s for s in suggestions if s.stage_name == stage_name]
        if priority:
            suggestions = [s for s in suggestions if s.priority == priority]
        if unapplied_only:
            suggestions = [s for s in suggestions if not s.applied]
        
        return suggestions
    
    def apply_suggestion(self, suggestion_id: str) -> bool:
        """
        제안 적용
        
        Args:
            suggestion_id: 제안 ID
        
        Returns:
            bool: 성공 여부
        """
        for suggestion in self._suggestions:
            if suggestion.suggestion_id == suggestion_id:
                suggestion.applied = True
                suggestion.applied_at = datetime.now()
                logger.info(f"Suggestion applied: {suggestion_id}")
                return True
        return False
    
    def get_patterns(
        self,
        pattern_type: Optional[PatternType] = None,
        min_confidence: float = 0.0,
    ) -> List[LearningPattern]:
        """
        패턴 조회
        
        Args:
            pattern_type: 패턴 유형 필터
            min_confidence: 최소 신뢰도
        
        Returns:
            List[LearningPattern]: 패턴 목록
        """
        patterns = list(self._patterns.values())
        
        if pattern_type:
            patterns = [p for p in patterns if p.pattern_type == pattern_type]
        patterns = [p for p in patterns if p.confidence >= min_confidence]
        
        return patterns
    
    def get_cross_stage_insights(self) -> Dict[str, Any]:
        """
        Cross-Stage 인사이트 생성
        
        여러 Stage에서 발견된 공통 패턴을 분석합니다.
        
        Returns:
            Dict: 인사이트 결과
        """
        # Stage별 패턴 그룹화
        patterns_by_stage: Dict[str, List[LearningPattern]] = defaultdict(list)
        for pattern in self._patterns.values():
            stage = pattern.metadata.get("stage_name", "unknown")
            patterns_by_stage[stage].append(pattern)
        
        # 공통 패턴 찾기
        pattern_names = defaultdict(list)
        for stage, patterns in patterns_by_stage.items():
            for p in patterns:
                pattern_names[p.name].append(stage)
        
        common_patterns = {
            name: stages
            for name, stages in pattern_names.items()
            if len(stages) > 1
        }
        
        return {
            "total_stages": len(patterns_by_stage),
            "total_patterns": len(self._patterns),
            "common_patterns": common_patterns,
            "suggestions_pending": len([s for s in self._suggestions if not s.applied]),
        }
    
    def set_suggestion_threshold(self, threshold: float) -> None:
        """제안 생성 임계값 설정"""
        self._suggestion_threshold = max(0.0, min(1.0, threshold))
    
    def enable(self) -> None:
        """서비스 활성화"""
        self._enabled = True
        
    def disable(self) -> None:
        """서비스 비활성화"""
        self._enabled = False
    
    def clear(self) -> None:
        """모든 데이터 초기화 (테스트용)"""
        self._patterns.clear()
        self._sessions.clear()
        self._suggestions.clear()
        self._metrics.clear()
