"""
Unit tests for Drift Detection Metrics.

Tests for Phase 1, 2, 3 drift detection implementations:
- Phase 1: PoolCircuitBreaker, PrecomputedCache
- Phase 2: EmergencyMode, RateLimiter
- Phase 3: Config lru_cache
"""

import pytest
from unittest.mock import MagicMock, patch


class TestDriftMetricsModule:
    """Test drift_metrics module."""
    
    def test_import_without_prometheus(self):
        """prometheus_client 없이도 import 성공해야 함."""
        # 이미 import 되어 있으므로, helper 함수들이 동작하는지 확인
        from selfhealing.metrics.drift_metrics import (
            record_pool_cb_stale,
            record_pool_cb_cache_age,
            update_pool_cb_hit_rate,
            record_cache_drift,
            update_cache_consistency,
            record_emergency_cache_stale,
            record_ratelimit_redis_unavailable,
            record_config_env_changed,
        )
        
        # 모든 helper 함수가 callable인지 확인
        assert callable(record_pool_cb_stale)
        assert callable(record_pool_cb_cache_age)
        assert callable(update_pool_cb_hit_rate)
        assert callable(record_cache_drift)
        assert callable(update_cache_consistency)
        assert callable(record_emergency_cache_stale)
        assert callable(record_ratelimit_redis_unavailable)
        assert callable(record_config_env_changed)
    
    def test_helper_functions_dont_raise(self):
        """Helper 함수들이 예외를 발생시키지 않아야 함."""
        from selfhealing.metrics.drift_metrics import (
            record_pool_cb_stale,
            record_pool_cb_cache_age,
            update_pool_cb_hit_rate,
            record_pool_cb_background_restart,
            record_cache_drift,
            update_cache_consistency,
            update_cache_hit_rate,
            record_cache_refresh,
            record_emergency_cache_stale,
            record_emergency_cache_drift,
            update_emergency_cache_age,
            record_emergency_cache_load,
            record_ratelimit_redis_unavailable,
            record_ratelimit_drift,
            set_ratelimit_fallback_mode,
            record_ratelimit_reconciliation,
            record_config_env_changed,
            record_config_cache_invalidated,
            record_config_cache_hit,
            record_config_cache_miss,
        )
        
        # 모든 함수가 예외 없이 호출 가능해야 함
        record_pool_cb_stale("warning")
        record_pool_cb_cache_age(100.0)
        update_pool_cb_hit_rate(0.95)
        record_pool_cb_background_restart()
        record_cache_drift("test_key")
        update_cache_consistency("test_key", 0.99)
        update_cache_hit_rate("test_key", "l1", 0.8)
        record_cache_refresh("test_key", True)
        record_emergency_cache_stale()
        record_emergency_cache_drift()
        update_emergency_cache_age(30.0)
        record_emergency_cache_load("startup")
        record_ratelimit_redis_unavailable()
        record_ratelimit_drift("api_key")
        set_ratelimit_fallback_mode(True)
        record_ratelimit_reconciliation(True)
        record_config_env_changed("test_config")
        record_config_cache_invalidated("test_config")
        record_config_cache_hit("test_config")
        record_config_cache_miss("test_config")


class TestPoolCircuitBreakerDrift:
    """Test PoolCircuitBreaker stale cache metrics."""
    
    def test_stale_cache_increments_counter(self):
        """Stale 캐시 접근 시 Prometheus 메트릭 증가."""
        from selfhealing.metrics.drift_metrics import (
            pool_cb_cache_stale_total,
            record_pool_cb_stale,
            PROMETHEUS_AVAILABLE,
        )
        
        if PROMETHEUS_AVAILABLE and pool_cb_cache_stale_total is not None:
            # 현재 값 저장
            initial_warning = pool_cb_cache_stale_total.labels(severity="warning")._value.get()
            initial_critical = pool_cb_cache_stale_total.labels(severity="critical")._value.get()
            
            # 메트릭 증가
            record_pool_cb_stale("warning")
            record_pool_cb_stale("critical")
            
            # 증가 확인
            assert pool_cb_cache_stale_total.labels(severity="warning")._value.get() == initial_warning + 1
            assert pool_cb_cache_stale_total.labels(severity="critical")._value.get() == initial_critical + 1
    
    def test_cache_age_histogram(self):
        """캐시 age가 히스토그램에 기록됨."""
        from selfhealing.metrics.drift_metrics import (
            pool_cb_cache_age_ms,
            record_pool_cb_cache_age,
            PROMETHEUS_AVAILABLE,
        )
        
        if PROMETHEUS_AVAILABLE and pool_cb_cache_age_ms is not None:
            # 다양한 age 값 기록
            record_pool_cb_cache_age(50.0)
            record_pool_cb_cache_age(150.0)
            record_pool_cb_cache_age(500.0)
            
            # 히스토그램에 데이터가 기록되었는지 확인
            assert pool_cb_cache_age_ms._sum.get() > 0


class TestPrecomputedCacheDrift:
    """Test PrecomputedCache drift detection."""
    
    def test_drift_stats_tracking(self):
        """Drift 통계가 정확하게 추적됨."""
        from selfhealing.services.precomputed_cache import (
            _get_drift_stats,
            get_drift_stats_all,
        )
        
        # 새 캐시 키에 대한 통계 초기화
        stats = _get_drift_stats("test_drift_key")
        
        assert stats["l1_hits"] == 0
        assert stats["l2_hits"] == 0
        assert stats["l3_fallbacks"] == 0
        assert stats["drift_count"] == 0
        assert stats["total_accesses"] == 0
        
        # 수동으로 통계 업데이트
        stats["l1_hits"] += 1
        stats["total_accesses"] += 1
        
        # 전체 통계 조회
        all_stats = get_drift_stats_all()
        assert "test_drift_key" in all_stats
        assert all_stats["test_drift_key"]["l1_hits"] == 1
    
    def test_check_l1_l2_drift_no_data(self):
        """L1 또는 L2에 데이터가 없으면 drift 체크 불가."""
        from selfhealing.services.precomputed_cache import check_l1_l2_drift
        
        # 존재하지 않는 키로 drift 체크
        result = check_l1_l2_drift("nonexistent_key_12345")
        assert result is None
    
    def test_cache_hit_rate_metrics(self):
        """캐시 히트율 메트릭이 올바르게 계산됨."""
        from selfhealing.services.precomputed_cache import _update_hit_rate_metrics
        
        stats = {
            "l1_hits": 60,
            "l2_hits": 30,
            "l3_fallbacks": 10,
            "total_accesses": 100,
            "drift_count": 0,
        }
        
        # 히트율 업데이트 (예외 없이 실행되어야 함)
        _update_hit_rate_metrics("test_hit_rate_key", stats)


class TestEmergencyModeDrift:
    """Test EmergencyMode cache drift detection."""
    
    def test_cache_age_tracking(self):
        """캐시 age가 메트릭에 기록됨."""
        from selfhealing.metrics.drift_metrics import (
            update_emergency_cache_age,
            emergency_cache_age_seconds,
            PROMETHEUS_AVAILABLE,
        )
        
        update_emergency_cache_age(45.5)
        
        if PROMETHEUS_AVAILABLE and emergency_cache_age_seconds is not None:
            assert emergency_cache_age_seconds._value.get() == 45.5
    
    def test_cache_load_reasons(self):
        """다양한 로드 이유가 메트릭에 기록됨."""
        from selfhealing.metrics.drift_metrics import (
            record_emergency_cache_load,
            emergency_cache_load_total,
            PROMETHEUS_AVAILABLE,
        )
        
        if PROMETHEUS_AVAILABLE and emergency_cache_load_total is not None:
            initial_startup = emergency_cache_load_total.labels(reason="startup")._value.get()
            initial_expired = emergency_cache_load_total.labels(reason="expired")._value.get()
            
            record_emergency_cache_load("startup")
            record_emergency_cache_load("expired")
            
            assert emergency_cache_load_total.labels(reason="startup")._value.get() == initial_startup + 1
            assert emergency_cache_load_total.labels(reason="expired")._value.get() == initial_expired + 1


class TestRateLimiterDrift:
    """Test RateLimiter Redis drift detection."""
    
    def test_fallback_mode_gauge(self):
        """Fallback 모드 게이지가 올바르게 설정됨."""
        from selfhealing.metrics.drift_metrics import (
            set_ratelimit_fallback_mode,
            ratelimit_fallback_active,
            PROMETHEUS_AVAILABLE,
        )
        
        if PROMETHEUS_AVAILABLE and ratelimit_fallback_active is not None:
            set_ratelimit_fallback_mode(True)
            assert ratelimit_fallback_active._value.get() == 1
            
            set_ratelimit_fallback_mode(False)
            assert ratelimit_fallback_active._value.get() == 0
    
    def test_reconciliation_tracking(self):
        """Reconciliation 결과가 메트릭에 기록됨."""
        from selfhealing.metrics.drift_metrics import (
            record_ratelimit_reconciliation,
            ratelimit_reconciliation_total,
            PROMETHEUS_AVAILABLE,
        )
        
        if PROMETHEUS_AVAILABLE and ratelimit_reconciliation_total is not None:
            initial_success = ratelimit_reconciliation_total.labels(result="success")._value.get()
            initial_failed = ratelimit_reconciliation_total.labels(result="failed")._value.get()
            
            record_ratelimit_reconciliation(True)
            record_ratelimit_reconciliation(False)
            
            assert ratelimit_reconciliation_total.labels(result="success")._value.get() == initial_success + 1
            assert ratelimit_reconciliation_total.labels(result="failed")._value.get() == initial_failed + 1


class TestConfigDriftMonitor:
    """Test ConfigDriftMonitor functionality."""
    
    def test_singleton_pattern(self):
        """ConfigDriftMonitor가 싱글톤으로 동작함."""
        from selfhealing.config import get_config_drift_monitor
        
        monitor1 = get_config_drift_monitor()
        monitor2 = get_config_drift_monitor()
        
        assert monitor1 is monitor2
    
    def test_env_hash_computation(self):
        """환경변수 해시가 올바르게 계산됨."""
        from selfhealing.config import get_config_drift_monitor
        import os
        
        monitor = get_config_drift_monitor()
        
        # 테스트용 환경변수 설정
        test_prefix = "SELFHEALING_TEST_DRIFT_"
        os.environ[f"{test_prefix}VAR1"] = "value1"
        
        hash1 = monitor._compute_env_hash(test_prefix)
        assert isinstance(hash1, str)
        assert len(hash1) == 32  # MD5 해시 길이
        
        # 같은 환경변수면 같은 해시
        hash2 = monitor._compute_env_hash(test_prefix)
        assert hash1 == hash2
        
        # 환경변수 변경 시 다른 해시
        os.environ[f"{test_prefix}VAR1"] = "value2"
        hash3 = monitor._compute_env_hash(test_prefix)
        assert hash1 != hash3
        
        # 정리
        del os.environ[f"{test_prefix}VAR1"]
    
    def test_check_and_invalidate_detects_change(self):
        """환경변수 변경을 감지함."""
        from selfhealing.config import get_config_drift_monitor
        import os
        
        monitor = get_config_drift_monitor()
        test_prefix = "SELFHEALING_TEST_CHANGE_"
        config_type = "test_change_detection"
        
        # 초기 상태 설정
        os.environ[f"{test_prefix}VALUE"] = "initial"
        result1 = monitor.check_and_invalidate(config_type, test_prefix)
        # 첫 호출은 이전 해시가 없으므로 False
        assert result1 is False
        
        # 환경변수 변경
        os.environ[f"{test_prefix}VALUE"] = "changed"
        result2 = monitor.check_and_invalidate(config_type, test_prefix)
        # 변경이 감지되면 True
        assert result2 is True
        
        # 같은 값으로 다시 호출하면 False
        result3 = monitor.check_and_invalidate(config_type, test_prefix)
        assert result3 is False
        
        # 정리
        del os.environ[f"{test_prefix}VALUE"]
    
    def test_cache_function_registration(self):
        """캐시 함수 등록 동작."""
        from selfhealing.config import get_config_drift_monitor
        
        monitor = get_config_drift_monitor()
        
        # 테스트용 함수 등록
        test_func = MagicMock()
        test_func.cache_clear = MagicMock()
        
        monitor.register_cache_function("test_registered", test_func)
        
        # 함수가 등록되었는지 확인
        assert "test_registered" in monitor._cache_functions
        assert monitor._cache_functions["test_registered"] is test_func
    
    def test_get_stats(self):
        """통계 조회 동작."""
        from selfhealing.config import get_config_drift_monitor
        import os
        
        monitor = get_config_drift_monitor()
        test_prefix = "SELFHEALING_TEST_STATS_"
        
        os.environ[f"{test_prefix}VAR"] = "test"
        monitor.check_and_invalidate("test_stats", test_prefix)
        
        stats = monitor.get_stats()
        assert isinstance(stats, dict)
        assert "test_stats" in stats
        
        # 정리
        del os.environ[f"{test_prefix}VAR"]


class TestSafeFunctions:
    """Test _safe 버전 함수들."""
    
    def test_get_notification_limits_safe(self):
        """get_notification_limits_safe 함수 동작."""
        from selfhealing.config import get_notification_limits_safe
        
        limits = get_notification_limits_safe()
        
        assert limits is not None
        assert hasattr(limits, 'slack_block_text_limit')
        assert limits.slack_block_text_limit > 0
    
    def test_get_forensic_settings_safe(self):
        """get_forensic_settings_safe 함수 동작."""
        from selfhealing.config import get_forensic_settings_safe
        
        settings = get_forensic_settings_safe()
        
        assert settings is not None
        assert hasattr(settings, 'max_stack_frames')
        assert settings.max_stack_frames > 0
    
    def test_get_metric_collection_settings_safe(self):
        """get_metric_collection_settings_safe 함수 동작."""
        from selfhealing.config import get_metric_collection_settings_safe
        
        settings = get_metric_collection_settings_safe()
        
        assert settings is not None
        assert hasattr(settings, 'drift_detection_enabled')
    
    def test_get_l2_storage_config_safe(self):
        """get_l2_storage_config_safe 함수 동작."""
        from selfhealing.config import get_l2_storage_config_safe
        
        config = get_l2_storage_config_safe()
        
        assert config is not None
        assert hasattr(config, 'redis_timeout_ms')
        assert config.redis_timeout_ms > 0
