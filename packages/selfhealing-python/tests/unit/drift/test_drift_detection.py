"""
Unit tests for Drift Detection Metrics.

Tests for drift detection implementations:
- PoolCircuitBreaker, PrecomputedCache
- EmergencyMode, RateLimiter
- Config lru_cache
- WAL Sync, ShadowLogger, TTLCache
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


# =============================================================================
# WAL Sync Drift Tests
# =============================================================================


class TestWALSyncDrift:
    """Test WAL Sync drift metrics."""
    
    def test_wal_metrics_import(self):
        """WAL 관련 메트릭 함수들이 import 가능해야 함."""
        from selfhealing.metrics.drift_metrics import (
            record_wal_entry_written,
            record_wal_entries_recovered,
            record_wal_corruption,
            record_wal_rotation,
            update_wal_sync_lag,
            update_wal_last_sequence,
        )
        
        assert callable(record_wal_entry_written)
        assert callable(record_wal_entries_recovered)
        assert callable(record_wal_corruption)
        assert callable(record_wal_rotation)
        assert callable(update_wal_sync_lag)
        assert callable(update_wal_last_sequence)
    
    def test_wal_helper_functions_dont_raise(self):
        """WAL helper 함수들이 예외 없이 호출 가능해야 함."""
        from selfhealing.metrics.drift_metrics import (
            record_wal_entry_written,
            record_wal_entries_recovered,
            record_wal_corruption,
            record_wal_rotation,
            update_wal_sync_lag,
            update_wal_last_sequence,
        )
        
        # 예외 없이 호출
        record_wal_entry_written()
        record_wal_entries_recovered(5)
        record_wal_corruption()
        record_wal_rotation()
        update_wal_sync_lag(10)
        update_wal_last_sequence(100)
    
    def test_wal_has_drift_metrics_flag(self):
        """WAL 모듈이 HAS_DRIFT_METRICS 플래그를 가져야 함."""
        from selfhealing.audit import wal
        
        assert hasattr(wal, 'HAS_DRIFT_METRICS')
    
    def test_wal_get_sync_lag_method(self):
        """WAL get_sync_lag 메서드 동작 확인."""
        import tempfile
        from selfhealing.audit.wal import WriteAheadLog, WALConfig
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = WALConfig(wal_dir=tmpdir, sync_on_write=False)
            wal = WriteAheadLog(config=config)
            
            try:
                # 몇 개의 엔트리 기록
                wal.write({"test": "entry1"})
                wal.write({"test": "entry2"})
                wal.write({"test": "entry3"})
                
                # sync lag 확인
                lag = wal.get_sync_lag(last_synced_seq=1)
                assert lag == 2  # 3 - 1 = 2
                
                lag_all = wal.get_sync_lag(last_synced_seq=0)
                assert lag_all == 3  # 3 - 0 = 3
            finally:
                wal.close()


# =============================================================================
# ShadowLogger Drift Tests
# =============================================================================


class TestShadowLoggerDrift:
    """Test ShadowLogger drift metrics."""
    
    def test_shadow_log_metrics_import(self):
        """ShadowLogger 관련 메트릭 함수들이 import 가능해야 함."""
        from selfhealing.metrics.drift_metrics import (
            record_shadow_log_sync_failure,
            update_shadow_log_unsynced_count,
            record_shadow_log_recovered,
            update_shadow_log_affected_services,
            update_shadow_log_oldest_unsynced_age,
        )
        
        assert callable(record_shadow_log_sync_failure)
        assert callable(update_shadow_log_unsynced_count)
        assert callable(record_shadow_log_recovered)
        assert callable(update_shadow_log_affected_services)
        assert callable(update_shadow_log_oldest_unsynced_age)
    
    def test_shadow_log_helper_functions_dont_raise(self):
        """ShadowLogger helper 함수들이 예외 없이 호출 가능해야 함."""
        from selfhealing.metrics.drift_metrics import (
            record_shadow_log_sync_failure,
            update_shadow_log_unsynced_count,
            record_shadow_log_recovered,
            update_shadow_log_affected_services,
            update_shadow_log_oldest_unsynced_age,
        )
        
        record_shadow_log_sync_failure("redis", "sync")
        update_shadow_log_unsynced_count(5)
        record_shadow_log_recovered("test_service", 3)
        update_shadow_log_affected_services(2)
        update_shadow_log_oldest_unsynced_age(60.0)
    
    def test_shadow_logger_has_drift_metrics_flag(self):
        """ShadowLogger 모듈이 HAS_DRIFT_METRICS 플래그를 가져야 함."""
        from selfhealing.adapters.memory import shadow_logger
        
        assert hasattr(shadow_logger, 'HAS_DRIFT_METRICS')
    
    def test_shadow_logger_record_sync_failure(self):
        """ShadowLogger record_sync_failure 동작 확인."""
        from selfhealing.adapters.memory.shadow_logger import ShadowLogger
        
        # 싱글톤 인스턴스 가져오기
        logger = ShadowLogger()
        logger.clear()  # 이전 기록 정리
        
        # 실패 기록
        logger.record_sync_failure(
            service_name="test_service",
            intended_state="active",
            error=Exception("Test error"),
            adapter_type="redis",
            operation="sync",
        )
        
        # 기록 확인
        stats = logger.get_stats()
        assert stats["total_records"] == 1
        assert stats["unsynced_count"] == 1
        assert "test_service" in stats["affected_services"]
        
        # 정리
        logger.clear()
    
    def test_shadow_logger_mark_as_synced(self):
        """ShadowLogger mark_as_synced 동작 확인."""
        from selfhealing.adapters.memory.shadow_logger import ShadowLogger
        
        logger = ShadowLogger()
        logger.clear()
        
        # 실패 기록
        logger.record_sync_failure(
            service_name="service_a",
            intended_state="active",
            error=Exception("Test"),
            adapter_type="redis",
        )
        
        # 동기화 완료 마킹
        count = logger.mark_as_synced("service_a")
        assert count == 1
        
        # 미동기화 레코드 수 확인
        stats = logger.get_stats()
        assert stats["unsynced_count"] == 0
        
        # 정리
        logger.clear()


# =============================================================================
# TTLCache Drift Tests
# =============================================================================


class TestTTLCacheDrift:
    """Test TTLCache drift metrics."""
    
    def test_ttl_cache_metrics_import(self):
        """TTLCache 관련 메트릭 함수들이 import 가능해야 함."""
        from selfhealing.metrics.drift_metrics import (
            record_cache_ttl_expired,
            record_cache_ttl_evicted,
            update_cache_entries_count,
            record_cache_get,
            record_cache_set,
        )
        
        assert callable(record_cache_ttl_expired)
        assert callable(record_cache_ttl_evicted)
        assert callable(update_cache_entries_count)
        assert callable(record_cache_get)
        assert callable(record_cache_set)
    
    def test_ttl_cache_helper_functions_dont_raise(self):
        """TTLCache helper 함수들이 예외 없이 호출 가능해야 함."""
        from selfhealing.metrics.drift_metrics import (
            record_cache_ttl_expired,
            record_cache_ttl_evicted,
            update_cache_entries_count,
            record_cache_get,
            record_cache_set,
        )
        
        record_cache_ttl_expired("test_cache")
        record_cache_ttl_evicted("test_cache")
        update_cache_entries_count("test_cache", 100)
        record_cache_get("test_cache", "hit")
        record_cache_get("test_cache", "miss")
        record_cache_get("test_cache", "expired")
        record_cache_set("test_cache")
    
    def test_inmemory_cache_has_drift_metrics_flag(self):
        """InMemoryCacheAdapter 모듈이 HAS_DRIFT_METRICS 플래그를 가져야 함."""
        from selfhealing.adapters.cache import memory_adapter
        
        assert hasattr(memory_adapter, 'HAS_DRIFT_METRICS')
    
    def test_inmemory_cache_get_set_with_metrics(self):
        """InMemoryCacheAdapter get/set이 메트릭과 함께 동작해야 함."""
        from datetime import timedelta
        from selfhealing.adapters.cache.memory_adapter import InMemoryCacheAdapter
        
        cache = InMemoryCacheAdapter(key_prefix="test:", cache_name="test_cache")
        
        # set
        result = cache.set("key1", "value1", ttl=timedelta(minutes=5))
        assert result is True
        
        # get (hit)
        value = cache.get("key1")
        assert value == "value1"
        
        # get (miss)
        value = cache.get("nonexistent")
        assert value is None
        
        # 정리
        cache.clear_all()
    
    def test_inmemory_cache_ttl_expiration(self):
        """InMemoryCacheAdapter TTL 만료 시 메트릭 기록."""
        import time
        from datetime import timedelta
        from selfhealing.adapters.cache.memory_adapter import InMemoryCacheAdapter
        
        cache = InMemoryCacheAdapter(key_prefix="test:", cache_name="ttl_test")
        
        # 매우 짧은 TTL로 설정
        cache.set("short_ttl", "value", ttl=timedelta(milliseconds=50))
        
        # TTL 만료 대기
        time.sleep(0.1)
        
        # 만료된 키 조회 시 None 반환
        value = cache.get("short_ttl")
        assert value is None
        
        # 정리
        cache.clear_all()
    
    def test_inmemory_cache_cache_name_parameter(self):
        """InMemoryCacheAdapter cache_name 파라미터 동작."""
        from selfhealing.adapters.cache.memory_adapter import InMemoryCacheAdapter
        
        cache = InMemoryCacheAdapter(key_prefix="custom:", cache_name="custom_cache")
        
        assert cache._cache_name == "custom_cache"
        
        # 정리
        cache.clear_all()
