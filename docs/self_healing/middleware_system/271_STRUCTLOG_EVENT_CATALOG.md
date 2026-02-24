# Structlog 이벤트 카탈로그

> 이 문서는 selfhealing 패키지 structlog 마이그레이션(Phase 4) 완료 후
> 코드베이스 전체를 자동 스캔하여 생성된 이벤트 카탈로그입니다.
> 재생성: `python scripts/generate_event_catalog.py`

## 요약

| 항목 | 수치 |
|------|------|
| 고유 이벤트 수 | **687** |
| 컴포넌트 수 | **289** |
| 스캔 경로 | `packages/selfhealing-python/src/selfhealing/` |

## 이벤트 목록 (컴포넌트별 알파벳 정렬)

> 이벤트 이름 규칙: `<component>.<action_verb>_<detail>`
> 로그 레벨: debug / info / warning / error / exception / critical

### `actor_context` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `actor_context.celery_task_started_without` | warning |

### `adaptive_replay` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `adaptive_replay.empty_batch_skipping_adjustment` | debug |

### `adaptive_threshold` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `adaptive_threshold.emergencymodemanager_available_using_normal` | warning |

### `adaptive_throttle` (33개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `adaptive_throttle.audit_module_available` | debug |
| `adaptive_throttle.break_glass_overriding_full` | warning |
| `adaptive_throttle.circuitbreakerservice_available` | debug |
| `adaptive_throttle.dlq_replay_integration_init` | debug |
| `adaptive_throttle.dlq_service_available_rejection` | debug |
| `adaptive_throttle.emergencymode_available_sync` | debug / info |
| `adaptive_throttle.errorbudgetservice_available` | debug |
| `adaptive_throttle.eventbus_available_auto_replay` | debug |
| `adaptive_throttle.eventbus_available_error_budget` | debug |
| `adaptive_throttle.eventbus_available_kill_switch` | debug |
| `adaptive_throttle.eventbus_available_load_shedding` | debug |
| `adaptive_throttle.eventbus_available_skipping_event` | debug |
| `adaptive_throttle.eventbus_available_subscription` | debug |
| `adaptive_throttle.forecaster_errorbudgetservice_available` | debug |
| `adaptive_throttle.full_stop_deactivated_starting` | warning |
| `adaptive_throttle.governance_blocked` | warning |
| `adaptive_throttle.governance_checks_available_kill` | debug |
| `adaptive_throttle.governance_settings_available` | debug |
| `adaptive_throttle.gradient_frozen_skipping_limit` | debug |
| `adaptive_throttle.kill_switch_activated_gradient` | warning |
| `adaptive_throttle.kill_switch_deactivated_recovery` | info |
| `adaptive_throttle.kill_switch_drift_corrected` | info |
| `adaptive_throttle.kill_switch_drift_detected` | warning |
| `adaptive_throttle.load_shedding_deactivated_restored` | info |
| `adaptive_throttle.postmortem_module_available` | debug |
| `adaptive_throttle.recovery_dampening_completed` | info |
| `adaptive_throttle.skipping_dlq_store_hedged` | debug |
| `adaptive_throttle.skipping_dlq_store_tier` | debug |
| `adaptive_throttle.subscribed_dlq_auto_replay` | info |
| `adaptive_throttle.subscribed_error_budget_events` | info |
| `adaptive_throttle.subscribed_kill_switch_events` | info |
| `adaptive_throttle.subscribed_load_shedding_events` | info |
| `adaptive_throttle.subscribed_rate_limit_events` | info |

### `adjustment_recorder` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `adjustment_recorder.saved_file` | debug |

### `admission_control_middleware` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `admission_control_middleware.initialized_disabled` | info |
| `admission_control_middleware.initialized_enabled` | info |

### `air_gap` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `air_gap.adapter_reset` | debug |
| `air_gap.air_gap_disabled_using` | info |
| `air_gap.nullairgapadapter_initialized_air_gap` | debug |
| `air_gap.redis_package_installed` | exception |

### `analyze_cross_stage_insights` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `analyze_cross_stage_insights.starting_cross_stage_analysis` | info |

### `anti_flapping_guard` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `anti_flapping_guard.flapping_lockout_cleared_manually` | info |

### `anti_flapping_window` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `anti_flapping_window.file_backend_detected_using` | info |
| `anti_flapping_window.redis_mode_enabled_distributed` | info |

### `apply_strategy_settings` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `apply_strategy_settings.reset` | debug |

### `async_audit_lifecycle` (9개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `async_audit_lifecycle.already_started` | debug |
| `async_audit_lifecycle.async_audit_system_started` | info |
| `async_audit_lifecycle.asynchealinglogger_initialized` | info |
| `async_audit_lifecycle.auditadapter_available` | debug |
| `async_audit_lifecycle.auditsyncworker_started` | info |
| `async_audit_lifecycle.shutdown_handlers_already_registered` | debug |
| `async_audit_lifecycle.shutdown_handlers_registered` | info |
| `async_audit_lifecycle.skipping_shutdown_handlers_test` | debug |
| `async_audit_lifecycle.starting_async_audit_system` | info |

### `async_healing_logger` (8개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `async_healing_logger.background_worker_started` | debug |
| `async_healing_logger.background_worker_stopped` | debug |
| `async_healing_logger.dlq_available_events_lost` | warning |
| `async_healing_logger.events_lost_no_dlq` | warning |
| `async_healing_logger.queue_full_dropping_newest` | warning |
| `async_healing_logger.queue_full_dropping_oldest` | warning |
| `async_healing_logger.queue_full_event_dropped` | warning |
| `async_healing_logger.unifiednotificationmanager_available` | warning |

### `async_hedging_strategy` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `async_hedging_strategy.bulkhead_full_fallback_single` | warning |
| `async_hedging_strategy.bulkhead_registry_available` | debug |
| `async_hedging_strategy.eventbus_available` | debug |
| `async_hedging_strategy.subscribed` | debug |

### `async_logger_adapter` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `async_logger_adapter.queue_full_event_dropped` | warning |
| `async_logger_adapter.worker_started` | info |
| `async_logger_adapter.worker_stopped` | info |

### `async_persist` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `async_persist.entry_returned_none` | warning |
| `async_persist.stats_adapter_unavailable` | debug |

### `atomic_consumer` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `atomic_consumer.redis_client_available` | warning |

### `atomic_merge_swap` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `atomic_merge_swap.failed_acquire_global_lock` | warning |
| `atomic_merge_swap.global_lock_acquired` | info |
| `atomic_merge_swap.global_lock_released` | info |
| `atomic_merge_swap.lock_already_released_stolen` | warning |

### `audit_adapter` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `audit_adapter.reset_adapter` | debug |
| `audit_adapter.using_adapter_providerregistry` | debug |
| `audit_adapter.using_nullauditlogadapter_fallback` | warning |

### `audit_batch_lua_scripts` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `audit_batch_lua_scripts.lua_scripts_registered` | info |
| `audit_batch_lua_scripts.script_cache_miss_re` | warning |

### `audit_checkpoint` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `audit_checkpoint.cache_hit` | debug |

### `audit_export` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `audit_export.no_entries` | warning |
| `audit_export.no_input_files` | warning |

### `audit_flush` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `audit_flush.file_adapter_unavailable` | warning |

### `audit_helpers` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `audit_helpers.diskpersistentbuffer_available` | debug |
| `audit_helpers.entry_saved_memory_buffer` | warning |
| `audit_helpers.entry_written_stderr_last` | error |
| `audit_helpers.saved_diskpersistentbuffer` | debug |

### `audit_metrics` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `audit_metrics.entered_degraded_mode` | warning |
| `audit_metrics.exited_degraded_mode` | info |

### `audit_middleware` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `audit_middleware.checkpoint_strategy_loaded` | info |
| `audit_middleware.initialized_continuousauditrecorder` | info |

### `audit_reconciler` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `audit_reconciler.thread_stop_gracefully` | warning |

### `audit_storage_failure` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `audit_storage_failure.resilience_manager_available` | debug |
| `audit_storage_failure.rollback_already_completed` | info |

### `audit_sync_worker` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `audit_sync_worker.thread_stop_gracefully` | warning |

### `audit_watchdog` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `audit_watchdog.already_running` | warning |
| `audit_watchdog.stopped` | info |

### `auto_rollback_guard` (6개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `auto_rollback_guard.emergency_recovery_starting_tiered` | critical |
| `auto_rollback_guard.initialized` | info |
| `auto_rollback_guard.rollback_skipped_cooldown` | warning |
| `auto_rollback_guard.started_monitoring` | info |
| `auto_rollback_guard.stopped` | info |
| `auto_rollback_guard.system_recovered` | info |

### `auto_rollback_settings` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `auto_rollback_settings.reset` | debug |

### `auto_tuning` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `auto_tuning.import_failed_sla_auto` | warning |
| `auto_tuning.internalmetricsadapter_import_failed_falling` | warning |
| `auto_tuning.throttleconfigapplier_import_failed_falling` | warning |

### `auto_tuning_service` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `auto_tuning_service.initialized` | info |
| `auto_tuning_service.started` | info |
| `auto_tuning_service.stopped` | info |

### `background_integrity_verifier` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `background_integrity_verifier.another_verify_task_running` | info |
| `background_integrity_verifier.redis_unavailable_skip` | warning |

### `backpressure_metrics` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `backpressure_metrics.installed` | warning |

### `beat_schedule` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `beat_schedule.added_legacy_schedules` | debug |
| `beat_schedule.all_self_healing_tasks` | info |

### `blast_radius_service` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `blast_radius_service.initialized` | info |

### `budget_depletion_forecaster` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `budget_depletion_forecaster.ewmaforecaster_available_falling_back` | debug |

### `bulkhead_metrics` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `bulkhead_metrics.available_metrics_disabled` | warning |
| `bulkhead_metrics.prometheus_metrics_initialized` | debug |

### `bulkhead_metrics_updater` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `bulkhead_metrics_updater.stopped` | info |

### `bulkhead_registry` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `bulkhead_registry.subscribed_events` | info |

### `canary_audit` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `canary_audit.audit_system_available` | debug |

### `canary_recovery` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `canary_recovery.all_states_reset` | info |

### `canary_rollout` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `canary_rollout.governancechecks_available_skipping` | debug |
| `canary_rollout.required_min_chars` | error |

### `cascading_failure` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `cascading_failure.kill_switch_activated_stopping` | warning |
| `cascading_failure.no_specified` | error |

### `causation_propagation` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `causation_propagation.already_connected` | debug |
| `causation_propagation.celery_causation_propagation_enabled` | info |

### `cb_state_snapshot` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `cb_state_snapshot.invalid_magic_number` | warning |
| `cb_state_snapshot.max_cb_count_reached` | warning |
| `cb_state_snapshot.stopped` | info |

### `cb_tracing` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `cb_tracing.otel_enabled_use_links` | debug |

### `celery_signal` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `celery_signal.causation_headers_present` | debug |
| `celery_signal.headers_unavailable` | debug |

### `celery_task` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `celery_task.audit_logger_available` | debug |
| `celery_task.emergencymodemanager_available` | debug |

### `cell_health_aggregator` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `cell_health_aggregator.became_leader_ewma_fallback` | info |
| `cell_health_aggregator.lost_leadership` | info |

### `cell_policy` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `cell_policy.region_isolation_gate_unavailable` | debug |

### `cell_registry` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `cell_registry.bulkhead_registry_unavailable` | warning |
| `cell_registry.bulkheads_registered` | debug / info |
| `cell_registry.subscribed_cell_state_change` | info |

### `certificate_expiry` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `certificate_expiry.rollback_already_completed` | info |

### `cgroup_resource_monitor` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `cgroup_resource_monitor.cannot_detect_cgroup_limits` | warning |

### `chaos_blast_radius` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `chaos_blast_radius.매우_위험합니다_region_레벨` | warning |

### `chaos_cleanup` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `chaos_cleanup.starting_expired_chaos_experiment` | info |

### `chaos_execution_service` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `chaos_execution_service.error_checking_pending_approvals` | exception |
| `chaos_execution_service.error_cleaning_up_approvals` | exception |
| `chaos_execution_service.error_generating_daily_report` | exception |
| `chaos_execution_service.no_experiments_due_execution` | debug |

### `chaos_experiment` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `chaos_experiment.canaryrecoverymanager_available` | debug |

### `chaos_guard` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `chaos_guard.django_cache_available` | debug |
| `chaos_guard.smart_all_target_clusters` | warning |

### `chaos_monitor` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `chaos_monitor.recovery_monitoring_checked` | info |

### `chaos_scheduler` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `chaos_scheduler.croniter_installed_install_pip` | warning |
| `chaos_scheduler.daily_report_generation_failed` | exception |
| `chaos_scheduler.idempotencyservice_available` | debug |

### `check_recovery_transitions` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `check_recovery_transitions.checking_circuit_breaker_states` | info |

### `check_sla_drift` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `check_sla_drift.starting_sla_drift_detection` | info |

### `circuit_breaker` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `circuit_breaker.expired_overrides_checked` | debug |

### `circuit_breaker_policy` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `circuit_breaker_policy.layered_repo_available_falling` | debug |

### `circuit_check` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `circuit_check.transition_check_started` | debug |

### `cleanup_tasks` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `cleanup_tasks.celery_available_beat_schedule` | debug |
| `cleanup_tasks.celery_available_skipping_task` | debug |

### `clock_skew` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `clock_skew.rollback_already_completed` | info |

### `cloud_watch_backend` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `cloud_watch_backend.enable_called_configured_interface` | warning |
| `cloud_watch_backend.initialized_interface_only_enable` | info |

### `co_occurrence_tracker` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `co_occurrence_tracker.no_saved_state_found` | debug |

### `collect_self_healing_metrics` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `collect_self_healing_metrics.starting_metrics_collection` | info |

### `config_history` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `config_history.cache_backend_support_redis` | warning |
| `config_history.default_cache_configured` | warning |
| `config_history.redis_unavailable_returning_empty` | warning |
| `config_history.redis_unavailable_skip_save` | warning |

### `config_task` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `config_task.retry_after_emergency_mode` | info |

### `connection_health` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `connection_health.simulated_partition_returned` | debug |
| `connection_health.simulation_overrides_cleared` | info |

### `connection_partition` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `connection_partition.partition_simulation_cleared` | info |

### `context_utils` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `context_utils.cannot_store_tokens_task` | debug |

### `continuous_audit` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `continuous_audit.checkpoint_strategy_initialized` | info |
| `continuous_audit.wal_enabled` | info |

### `correlation_engine` (5개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `correlation_engine.disabled_settings` | info |
| `correlation_engine.disabled_via_dynamic_config` | info |
| `correlation_engine.initialized_successfully` | info |
| `correlation_engine.lease_expired_aborting_analysis` | warning |
| `correlation_engine.shut_down` | info |

### `crisis_multiplier` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `crisis_multiplier.cache_invalidated` | debug |

### `cross_cluster_audit_linker` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `cross_cluster_audit_linker.global_redis_available` | warning |
| `cross_cluster_audit_linker.local_redis_available` | warning |
| `cross_cluster_audit_linker.no_hash_chain_state` | warning |

### `daily_report` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `daily_report.celery_available_skipping_task` | debug |

### `daily_report_service` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `daily_report_service.no_entries_report_skipping` | info |

### `dashboard` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `dashboard.cache_invalidated` | info |
| `dashboard.recoverydashboardservice_available` | debug |

### `datadog_backend` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `datadog_backend.enable_called_datadog_api` | warning |
| `datadog_backend.initialized_interface_only_enable` | info |

### `decision_engine_settings` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `decision_engine_settings.loaded` | debug |
| `decision_engine_settings.reset` | debug |

### `deployment_correlator` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `deployment_correlator.feature_disabled` | debug |
| `deployment_correlator.kubernetes_available_falling_back` | warning |
| `deployment_correlator.using_kubernetes_adapter` | info |
| `deployment_correlator.using_mock_adapter` | info |

### `disk_buffer` (8개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `disk_buffer.buffer_closed_skipping_write` | warning |
| `disk_buffer.closed` | info |
| `disk_buffer.disk_full_fail_open` | warning |
| `disk_buffer.disk_full_switching_fail` | critical |
| `disk_buffer.disk_space_recovered_resuming` | info |
| `disk_buffer.shutdown_handlers_registered` | debug |
| `disk_buffer.sigint_received_initiating_shutdown` | info |
| `disk_buffer.sigterm_received_initiating_shutdown` | info |

### `disk_buffer_metrics` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `disk_buffer_metrics.available` | debug |

### `distributed_rate_limit_channel` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `distributed_rate_limit_channel.already_running` | warning |

### `django_adapter` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `django_adapter.circuit_breaker_model_configured` | debug |
| `django_adapter.dlq_model_configured` | debug |

### `django_statistics_adapter` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `django_statistics_adapter.model_configured` | warning |

### `dlq_cleanup` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `dlq_cleanup.stats_adapter_unavailable` | info |

### `dlq_consumer` (5개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `dlq_consumer.dlq_서비스_없음_테스트` | debug |
| `dlq_consumer.리더십_상실_배치_처리` | warning |
| `dlq_consumer.리더십_확인_실패_소비` | warning |
| `dlq_consumer.소비_루프_시작` | info |
| `dlq_consumer.소비_루프_종료` | info |

### `dlq_service` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `dlq_service.diskpersistentbuffer_available` | debug |
| `dlq_service.dlq_disabled_skipping_storage` | debug |

### `dns_failure` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `dns_failure.rollback_already_completed` | info |

### `drain_on_startup` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `drain_on_startup.no_pending_entries_drain` | info |

### `drift_detection` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `drift_detection.django_adapter_available_skipping` | debug |
| `drift_detection.sla_check_no_violations` | info |
| `drift_detection.sla_check_started` | info |

### `durable_event_logger` (6개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `durable_event_logger.no_events_recover_wal` | info |
| `durable_event_logger.queue_full_during_recovery` | warning |
| `durable_event_logger.queue_full_event_wal` | warning |
| `durable_event_logger.wal_configured_cannot_recover` | warning |
| `durable_event_logger.wal_configured_durable_mode` | info |
| `durable_event_logger.wal_configured_event_durable` | warning |

### `emergency_mode` (5개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `emergency_mode.auto_expired_deactivating` | info |
| `emergency_mode.cache_invalidated_external_event` | debug |
| `emergency_mode.gradual_recovery_complete_normal` | info |
| `emergency_mode.no_previous_state_rollback` | warning |
| `emergency_mode.state_reset_defaults` | info |

### `emergency_state_refresher` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `emergency_state_refresher.disabled_starting` | info |

### `env_audit` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `env_audit.no_tracked_environment_variables` | debug |
| `env_audit.primary_logging_failed_activating` | warning |

### `error_budget` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `error_budget.fail_safe_adapter_configured` | warning |

### `error_budget_api` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `error_budget_api.simulation_stats_reset` | info |

### `error_budget_gate` (8개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `error_budget_gate.alert_cooldowns_reset_admin` | info |
| `error_budget_gate.fail_close_triggered_retrieve` | error |
| `error_budget_gate.fail_open` | debug |
| `error_budget_gate.fail_open_triggered_retrieve` | warning |
| `error_budget_gate.fault_detector_degraded_fast` | debug |
| `error_budget_gate.fault_detector_reset_admin` | info |
| `error_budget_gate.rate_limiter_reset_admin` | info |
| `error_budget_gate.rate_limiting_disabled_allowing` | info |

### `escalation` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `escalation.escalation_disabled` | debug |
| `escalation.pagerduty_configured` | debug |
| `escalation.slack_configured` | debug |

### `escalation_invalidation` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `escalation_invalidation.crisismultiplierprovider_registered_push_based` | info |
| `escalation_invalidation.event_bus_available_falling` | warning |

### `event_bus` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `event_bus.all_subscriptions_cleared` | info |
| `event_bus.default_handlers_registered` | info |
| `event_bus.not_available` | debug |
| `event_bus.reset_defaults` | info |

### `event_handler` (6개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `event_handler.celery_tasks_available_skipping` | debug |
| `event_handler.emergency_blocking_non_essential` | warning |
| `event_handler.metrics_available` | warning |
| `event_handler.postmortem_module_available_skipping` | debug |
| `event_handler.safegauge_available_using_raw` | warning |
| `event_handler.throttle_module_available` | debug |

### `event_stream_proxy` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `event_stream_proxy.eventbus_available` | warning |

### `exception_handler` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `exception_handler.available_metrics_disabled` | debug |
| `exception_handler.prometheus_metrics_initialized` | debug |

### `exception_weights` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `exception_weights.loaded_custom_weights_settings` | info |

### `factory` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `factory.reset_all_service_singletons` | debug |

### `fallback_escalation` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `fallback_escalation.file_cleared` | info |

### `fallback_strategy` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `fallback_strategy.cache_fallback_used` | info |
| `fallback_strategy.db_fallback_used` | info |
| `fallback_strategy.default_value_used` | info |

### `fin_ops` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `fin_ops.no_chaos_budget_configured` | debug |

### `finops_service` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `finops_service.initialized` | info |

### `flush_notifications` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `flush_notifications.aggregation_disabled` | debug |

### `flush_redis_audit_to_db` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `flush_redis_audit_to_db.redis_buffer_unavailable` | warning |

### `forensic_audit_bridge` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `forensic_audit_bridge.anomaly_capture_rate_limited` | debug |
| `forensic_audit_bridge.exception_capture_rate_limited` | debug |
| `forensic_audit_bridge.snapshot_capture_rate_limited` | debug |

### `freeze_decision` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `freeze_decision.escalation_disabled_skipping` | debug |

### `freeze_mode` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `freeze_mode.cannot_deactivate_during_lockdown` | warning |
| `freeze_mode.emergencymodemanager_available` | debug |

### `gate_alert` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `gate_alert.alert_cooldowns_reset` | info |

### `gate_fault_detector` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `gate_fault_detector.reset_healthy_state` | info |
| `gate_fault_detector.state_degraded_recovering_attempting` | info |
| `gate_fault_detector.state_recovering_degraded_recovery` | warning |
| `gate_fault_detector.state_recovering_healthy_recovered` | info |

### `global_config_propagator` (6개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `global_config_propagator.ignoring_own_message` | debug |
| `global_config_propagator.listener_stopped` | info |
| `global_config_propagator.local_scope_skipping_propagation` | debug |
| `global_config_propagator.quarantine_mode_active_skipping` | warning |
| `global_config_propagator.redis_available_cannot_start` | warning |
| `global_config_propagator.redis_available_skipping_propagation` | warning |

### `governance` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `governance.celery_installed_skipping_task` | debug |

### `governance_checks` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `governance_checks.blocked_kill_switch` | warning |
| `governance_checks.cache_invalidated` | debug |

### `governance_service` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `governance_service.emergency_mode_active_skipping` | debug |
| `governance_service.emergency_mode_expired_auto` | warning |
| `governance_service.final_warning_hours_until` | warning |

### `graceful_degradation` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `graceful_degradation.initialized_all_phase_components` | info |

### `graceful_shutdown` (5개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `graceful_shutdown.asynchealinglogger_stopped` | info |
| `graceful_shutdown.audit_system_shutdown_complete` | info |
| `graceful_shutdown.auditsyncworker_stopped` | info |
| `graceful_shutdown.starting_audit_system_shutdown` | info |
| `graceful_shutdown.wal_closed` | info |

### `grafana_webhook` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `grafana_webhook.no_alerts` | info |

### `grpc_server` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `grpc_server.server_already_running` | warning |
| `grpc_server.services_registered_stub` | debug |
| `grpc_server.stopped` | info |

### `hash_chain` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `hash_chain.chain_state_reset` | warning |

### `hash_chain_wal` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `hash_chain_wal.idempotencyservice_available` | debug |
| `hash_chain_wal.no_redis_client_replay` | warning |

### `healing_events` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `healing_events.event_saved_memory_fallback` | debug |

### `health_probe_manager` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `health_probe_manager.started` | info |
| `health_probe_manager.stopped` | info |

### `health_score` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `health_score.prometheus_metrics_initialized` | debug |

### `hedging_config_update_hook` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `hedging_config_update_hook.eventbus_available` | debug |
| `hedging_config_update_hook.subscribed` | debug |

### `hedging_strategy` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `hedging_strategy.bulkhead_full_fallback_single` | warning |
| `hedging_strategy.bulkhead_registry_available` | debug |
| `hedging_strategy.eventbus_available` | debug |
| `hedging_strategy.subscribed` | debug |

### `hedging_validator` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `hedging_validator.skipped_due_high_load` | debug |

### `hpa_metrics_exporter` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `hpa_metrics_exporter.hpa_disabled` | info |
| `hpa_metrics_exporter.metrics_disabled` | info |

### `idempotent_budget_reset_handler` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `idempotent_budget_reset_handler.crisismultiplierprovider_available` | warning |

### `idempotent_canary_resume_handler` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `idempotent_canary_resume_handler.canaryservice_available` | warning |

### `idempotent_governance_normal_handler` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `idempotent_governance_normal_handler.emergencymodetracker_available` | warning |

### `idempotent_health_check_handler` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `idempotent_health_check_handler.stability_check` | debug |

### `in_memory_rate_limit_storage` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `in_memory_rate_limit_storage.cleared_all_state` | debug |

### `incident_group_manager` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `incident_group_manager.file_backend_using_memory` | info |
| `incident_group_manager.redis_mode_enabled` | info |

### `incident_timeline` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `incident_timeline.empty_dag_returning_minimal` | warning |

### `integrated_recorder` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `integrated_recorder.asyncloggeradapter_attached` | info |

### `integrity_sealer` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `integrity_sealer.chain_state_reset` | warning |

### `ipc_state_cache` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `ipc_state_cache.eventbus_available` | debug |

### `k8s_ingress_traffic_router` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `k8s_ingress_traffic_router.kubernetes_package_installed` | warning |

### `kafka_audit_adapter` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `kafka_audit_adapter.adapter_closed_ignoring_log` | warning |

### `kafka_consumer` (7개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `kafka_consumer.consumer_루프_시작` | info |
| `kafka_consumer.consumer_루프_종료` | info |
| `kafka_consumer.백그라운드_스레드_시작` | info |
| `kafka_consumer.스레드가_시간_종료되지_않음` | warning |
| `kafka_consumer.이미_실행` | warning |
| `kafka_consumer.정지됨` | info |
| `kafka_consumer.종료됨` | info |

### `kafka_event_bus` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `kafka_event_bus.시작됨` | info |
| `kafka_event_bus.이미_실행` | warning |
| `kafka_event_bus.정지됨` | info |
| `kafka_event_bus.종료됨` | info |

### `kafka_metrics` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `kafka_metrics.prometheus_client_미설치` | debug |

### `kafka_producer` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `kafka_producer.confluent_kafka_패키지가_설치되지` | exception |
| `kafka_producer.producer가_초기화되지_않았습니다` | error |

### `kafka_redis_checkpoint` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `kafka_redis_checkpoint.checksum_module_available` | debug |

### `kill_switch` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `kill_switch.system_control_unavailable` | debug |

### `kubernetes_adapter` (5개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `kubernetes_adapter.client_available` | warning |
| `kubernetes_adapter.config_changes_collected_runtimeconfig` | debug |
| `kubernetes_adapter.kubernetes_package_installed_install` | warning |
| `kubernetes_adapter.loaded_cluster_config` | info |
| `kubernetes_adapter.loaded_kubeconfig` | info |

### `kubernetes_recovery_adapter` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `kubernetes_recovery_adapter.kubernetes_package_installed` | warning |
| `kubernetes_recovery_adapter.loaded_cluster_config` | info |
| `kubernetes_recovery_adapter.loaded_kubeconfig` | info |

### `layered_provider` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `layered_provider.request_overrides_cleared` | debug |

### `layered_repo` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `layered_repo.health_status_reset_manually` | info |
| `layered_repo.manual_drift_reconciliation_triggered` | info |

### `leader_elector` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `leader_elector.graceful_shutdown_핸들러가_등록되었습니다` | info |
| `leader_elector.gracefulshutdowncoordinator_통합_준비_완료` | info |
| `leader_elector.gracefulshutdowncoordinator를_찾을_없습니다` | debug |
| `leader_elector.shutdown_시작_리더십_반납` | info |

### `load_shedding_manager` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `load_shedding_manager.eventbus_available_shedding_event` | debug |
| `load_shedding_manager.reset_complete` | debug |

### `local_file_backend` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `local_file_backend.distributed_hash_chain_requested` | warning |
| `local_file_backend.pendingsequencemanager_enabled` | info |
| `local_file_backend.using_distributed_hash_chain` | info |

### `lua_atomic_hash_chain` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `lua_atomic_hash_chain.scripts_loaded_successfully` | debug |

### `memcached_cache` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `memcached_cache.flushed_all_keys` | warning |

### `metric_adapter` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `metric_adapter.django_adapter_created_without` | info |
| `metric_adapter.redis_package_installed_falling` | warning |
| `metric_adapter.using_nullmetricsourceadapter_no_op` | info |

### `metrics` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `metrics.collection_disabled` | debug |

### `micro_batch_consumer` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `micro_batch_consumer.batch_flush_failed` | exception |

### `mmap_buffer` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `mmap_buffer.buffer_full_wrapping_around` | warning |
| `mmap_buffer.closed` | info |

### `mock_deployment_adapter` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `mock_deployment_adapter.adapter_available` | warning |
| `mock_deployment_adapter.initialized_mock_data` | debug |

### `multiregion_router` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `multiregion_router.write_skipped_preferred_region` | debug |

### `notification_aggregator` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `notification_aggregator.file_backend_using_memory` | info |
| `notification_aggregator.redis_mode_enabled` | info |

### `null_statistics_repository` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `null_statistics_repository.called_no_op` | debug |

### `otel` (19개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `otel.baggage_propagation_enabled` | info |
| `otel.celery_instrumentation_disabled` | debug |
| `otel.celery_instrumentation_enabled` | info |
| `otel.celery_instrumentation_installed` | debug |
| `otel.django_instrumentation_disabled` | debug |
| `otel.django_instrumentation_disabled_config` | debug |
| `otel.django_instrumentation_installed` | debug |
| `otel.logger_provider_shutdown` | debug |
| `otel.logging_instrumentation_disabled` | debug |
| `otel.logging_instrumentation_enabled` | info |
| `otel.logging_instrumentation_installed` | debug |
| `otel.logging_sdk_installed` | debug |
| `otel.propagation_packages_installed` | debug |
| `otel.requests_instrumentation_disabled` | debug |
| `otel.requests_instrumentation_enabled` | info |
| `otel.requests_instrumentation_installed` | debug |
| `otel.sdk_already_installed` | debug |
| `otel.sdk_disabled` | debug |
| `otel.tracer_provider_shutdown` | debug |

### `otel_sampler` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `otel_sampler.sla_violation_forced` | debug |
| `otel_sampler.throttle_response_forced` | debug |

### `panic_threshold` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `panic_threshold.circuitbreakerservice_available` | warning |
| `panic_threshold.emergencymanager_available_escalation` | warning |
| `panic_threshold.emergencymodemanager_available` | warning |

### `partial_failure` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `partial_failure.loadsheddingmanager_available` | debug |

### `partition_reconciliation` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `partition_reconciliation.heartbeat_loop_already_running` | warning |
| `partition_reconciliation.heartbeat_loop_stopped` | info |
| `partition_reconciliation.partition_recovered_triggering_reconciliation` | info |
| `partition_reconciliation.tieredredisprovider_available` | warning |

### `pending_watchdog` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `pending_watchdog.started` | info |

### `pool_circuit_breaker` (8개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `pool_circuit_breaker.background_refresh_thread_started` | debug |
| `pool_circuit_breaker.background_refresh_thread_stopped` | debug |
| `pool_circuit_breaker.background_thread_died_restarting` | error |
| `pool_circuit_breaker.empty_trying_direct_access` | debug |
| `pool_circuit_breaker.no_available` | debug |
| `pool_circuit_breaker.no_connection_yet_pool` | debug |
| `pool_circuit_breaker.recovered_circuit_closed` | info |
| `pool_circuit_breaker.reset_closed` | info |

### `pool_exhaustion` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `pool_exhaustion.simulation_override_cleared` | info |

### `pool_monitor` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `pool_monitor.simulation_override_cleared` | info |

### `postmortem` (5개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `postmortem.incident_saved_db_cache` | debug |
| `postmortem.incident_saved_memory_cache` | debug |
| `postmortem.postmortemrecord_model_available` | debug |
| `postmortem.redisdistributedlock_available` | debug |
| `postmortem.required_locked_save` | warning |

### `postmortem_notifier` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `postmortem_notifier.notification_disabled` | debug |
| `postmortem_notifier.slack_webhook_configured` | debug |
| `postmortem_notifier.unifiednotificationmanager_available` | debug |

### `pre_stop` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `pre_stop.shutdown_force_recovery_still` | warning |
| `pre_stop.shutdown_safe` | info |

### `precomputed_cache` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `precomputed_cache.starting_background_worker` | info |
| `precomputed_cache.stopped_background_worker` | info |

### `proactive_action_trigger` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `proactive_action_trigger.forecaster_manual_only_mode` | warning |

### `protection_orchestrator` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `protection_orchestrator.emergency_level_activated` | info / warning / critical |
| `protection_orchestrator.some_rollbacks_failed_manual` | critical |

### `quarantine` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `quarantine.system_started_quarantine_mode` | critical |

### `quarantine_mode` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `quarantine_mode.disabled_administrator` | info |
| `quarantine_mode.manually_enabled_administrator` | warning |

### `rate_limit` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `rate_limit.redis_unavailable` | warning |

### `rate_limit_coordinator` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `rate_limit_coordinator.eventbus_available` | debug |
| `rate_limit_coordinator.metrics_module_available` | debug |

### `rate_limit_escalation_handler` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `rate_limit_escalation_handler.eventbus_available` | debug |

### `rate_limit_storage` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `rate_limit_storage.auto_detected_database` | info |
| `rate_limit_storage.auto_detected_redis` | info |
| `rate_limit_storage.falling_back_memory_storage` | warning |

### `rate_limiter` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `rate_limiter.reset_all_timestamps_cleared` | info |

### `recovery` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `recovery.alert_adapter_support_recovery` | warning |

### `recovery_adapter` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `recovery_adapter.no_adapter_available_using` | warning |
| `recovery_adapter.unavailable_falling_back_docker` | info |

### `recovery_aware_shutdown_hook` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `recovery_aware_shutdown_hook.critical_force_shutdown_during` | critical |
| `recovery_aware_shutdown_hook.drain_completed_successfully` | info |
| `recovery_aware_shutdown_hook.no_active_recovery_session` | info |
| `recovery_aware_shutdown_hook.recovery_session_progress_extending` | warning |

### `recovery_coordinator_settings` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `recovery_coordinator_settings.event` | warning |

### `recovery_session_archive` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `recovery_session_archive.django_model_available_using` | warning |

### `recovery_strategy` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `recovery_strategy.all_states_reset` | info |

### `redis` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `redis.no_redis_client_available` | debug |

### `redis_audit_buffer` (5개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `redis_audit_buffer.distributed_lock_unavailable` | warning |
| `redis_audit_buffer.fileauditlogadapter_available` | debug |
| `redis_audit_buffer.redis_package_installed` | info |
| `redis_audit_buffer.shutdown_hooks_registered` | debug |
| `redis_audit_buffer.used_file_fallback` | info |

### `redis_checkpoint` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `redis_checkpoint.checksum_module_available_skipping` | debug |
| `redis_checkpoint.distributedrecoverylock_available` | warning |
| `redis_checkpoint.unifiednotificationmanager_available` | warning |

### `redis_event_bus` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `redis_event_bus.connected_redis` | info |
| `redis_event_bus.listener_stopped` | info |
| `redis_event_bus.no_redis_url_configured` | info |
| `redis_event_bus.redis_package_installed_using` | warning |

### `redis_hash_chain` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `redis_hash_chain.chain_state_reset_redis` | warning |

### `redis_health` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `redis_health.recovered_resuming_normal_operation` | info |
| `redis_health.redis_configured_using_local` | info |

### `redis_throttle_limit_manager` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `redis_throttle_limit_manager.scripts_loaded` | debug |

### `region_health` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `region_health.config_set_permitted_using` | warning |
| `region_health.keyspace_notifications_enabled_notify` | info |
| `region_health.redis_package_installed_keyspace` | warning |

### `regional_isolation_gate` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `regional_isolation_gate.redis_available` | warning |

### `remote_audit_backend` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `remote_audit_backend.enable_called_remote_server` | warning |
| `remote_audit_backend.initialized_interface_only_enable` | info |

### `replay_flood` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `replay_flood.replay_queue_service_available` | debug |
| `replay_flood.rollback_already_completed` | info |

### `replicator` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `replicator.multi_region_disabled` | info |
| `replicator.queue_full_dropping_event` | warning |
| `replicator.redis_package_installed` | exception |
| `replicator.stopped` | info |

### `resilience` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `resilience.all_bypass_hooks_unregistered` | info |

### `resilient_recorder` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `resilient_recorder.background_flush_worker_started` | info |
| `resilient_recorder.background_flush_worker_stopped` | info |

### `resilient_storage` (5개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `resilient_storage.redis_connected` | info |
| `resilient_storage.redis_mode_recovered` | info |
| `resilient_storage.redis_unavailable_wal_recovery` | warning |
| `resilient_storage.switched_degraded_mode_using` | critical |
| `resilient_storage.wal_initialized` | debug |

### `resource_guard` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `resource_guard.initialized_singleton_instance` | debug |
| `resource_guard.reset_singleton` | debug |
| `resource_guard.resource_check_disabled_allowing` | debug |

### `resource_guard_settings` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `resource_guard_settings.reset` | debug |

### `resource_monitor_settings` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `resource_monitor_settings.reset` | debug |

### `retention_cleaner` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `retention_cleaner.available` | debug |

### `retention_scheduler` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `retention_scheduler.started` | info |
| `retention_scheduler.stopped` | info |

### `retry_audit_fallback_buffer` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `retry_audit_fallback_buffer.redis_buffer_unavailable` | warning |

### `retry_handler` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `retry_handler.full_stop_triggered_moving` | warning |

### `rollback_service` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `rollback_service.initialized` | info |

### `runtime_config` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `runtime_config.reset_config_defaults` | info |
| `runtime_config.slo_definition_missing_name` | warning |
| `runtime_config.updated_chaos` | info |

### `runtime_feedback` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `runtime_feedback.already_running` | warning |
| `runtime_feedback.degradation_detected_initiating_rollback` | warning |
| `runtime_feedback.running_cannot_resume` | warning |

### `runtime_feedback_settings` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `runtime_feedback_settings.reset` | debug |

### `s3_worm_backend` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `s3_worm_backend.cannot_place_legal_hold` | warning |
| `s3_worm_backend.enable_called_configured_interface` | warning |
| `s3_worm_backend.initialized_interface_only_enable` | info |

### `safe_default` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `safe_default.chaos_production_forcing_true` | warning |
| `safe_default.multiply_strategy_risky_ensure` | warning |
| `safe_default.throughput_check_effectively_disabled` | warning |

### `safe_open_fallback` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `safe_open_fallback.redis_connection_lost_using` | warning |
| `safe_open_fallback.redis_connection_restored` | info |

### `safety_bounds` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `safety_bounds.reset_defaults` | info |

### `safety_bounds_settings` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `safety_bounds_settings.reset` | debug |

### `safety_guard` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `safety_guard.freezemodemanager_available_skipping_check` | debug |
| `safety_guard.global_block_removed` | info |

### `sampling_verifier` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `sampling_verifier.sample_verification_failed_performing` | info |

### `secure_redis` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `secure_redis.redis_package_installed` | exception |

### `security` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `security.forensic_masking_unavailable_no` | debug |
| `security.set_production_environment_auth` | error |

### `security_notification` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `security_notification.notifications_disabled` | debug |

### `self_healing` (26개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `self_healing.all_secrets_validated_successfully` | info |
| `self_healing.celery_installed_skipping_task` | debug |
| `self_healing.celery_tasks_autodiscovered_selfhealing` | info |
| `self_healing.distributed_hash_chain_enabled` | debug |
| `self_healing.gauge_hydration_already_scheduled` | debug |
| `self_healing.gauge_hydration_disabled_settings` | debug |
| `self_healing.hash_chain_sync_fresh` | info |
| `self_healing.installed_skipping_jwt_hook` | debug |
| `self_healing.integrity_module_available_hash` | debug |
| `self_healing.jwt_blacklist_hook_registered` | info |
| `self_healing.meta_watchdog_already_started` | debug |
| `self_healing.meta_watchdog_disabled_django` | debug |
| `self_healing.meta_watchdog_disabled_environment` | debug |
| `self_healing.meta_watchdog_module_available` | debug |
| `self_healing.meta_watchdog_started_monitoring` | info |
| `self_healing.module_available` | debug |
| `self_healing.module_available_quarantine` | warning |
| `self_healing.pre_computed_cache_disabled` | debug |
| `self_healing.pre_computed_cache_worker` | debug / info |
| `self_healing.reconciler_module_available` | debug |
| `self_healing.redis_client_available_hash` | debug |
| `self_healing.session_signal_handlers_connected` | debug |
| `self_healing.signal_hooks_already_connected` | warning |
| `self_healing.signal_hooks_disconnected` | info |
| `self_healing.startup_config_validation_all` | debug |
| `self_healing.system_metrics_cache_disabled` | debug |

### `service_factory` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `service_factory.redis_adapter_available_using` | info |
| `service_factory.using_layered_storage` | info |

### `service_provider_registry` (6개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `service_provider_registry.configured_production` | info |
| `service_provider_registry.configured_testing` | info |
| `service_provider_registry.entering_isolated_test_context` | debug |
| `service_provider_registry.exited_isolated_test_context` | debug |
| `service_provider_registry.reset_all_instances` | debug |
| `service_provider_registry.reset_all_registrations` | debug |

### `shadow_budget` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `shadow_budget.using_fallback_estimation_based` | info |

### `shadow_logger` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `shadow_logger.audit_recording_skipped_available` | debug |

### `shutdown` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `shutdown.drain_timeout_reached` | warning |
| `shutdown.graceful_initiated` | info |
| `shutdown.in_flight_drained` | info |

### `sidecar_auth` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `sidecar_auth.authentication_disabled` | debug |
| `sidecar_auth.authentication_enabled_no_token` | warning |
| `sidecar_auth.bearer_token_authentication_enabled` | info |

### `sidecar_ipc_probe` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `sidecar_ipc_probe.initialized` | debug |

### `sidecar_metrics` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `sidecar_metrics.available_metrics_disabled` | debug |
| `sidecar_metrics.initialized` | debug |

### `signed_manifest` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `signed_manifest.rfc_timestamp_disabled` | warning |
| `signed_manifest.verification_passed` | info |

### `simulated_disk_io` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `simulated_disk_io.rollback_already_completed` | info |

### `simulated_tls` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `simulated_tls.rollback_already_completed` | info |

### `sla_check` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `sla_check.breach_check_started` | debug |
| `sla_check.no_breaches_found` | debug |

### `sla_notification` (5개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `sla_notification.celery_available_using_sync` | debug |
| `sla_notification.clusteridentity_available` | debug |
| `sla_notification.eventbus_available` | debug |
| `sla_notification.subscribed_throttle_sla_events` | info |
| `sla_notification.unifiednotification_available` | debug |

### `slack_notification` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `slack_notification.configured` | warning |

### `snapshot_builder` (5개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `snapshot_builder.incidentlogbuffer_available` | debug |
| `snapshot_builder.no_skipping_prometheus_query` | debug |
| `snapshot_builder.prometheus_query_disabled` | debug |
| `snapshot_builder.prometheuscollector_available` | debug |
| `snapshot_builder.redis_client_available` | debug / warning |

### `state_backend` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `state_backend.memory_backend_initialized_testing` | info |
| `state_backend.redis_package_installed_run` | exception |

### `state_cache_settings` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `state_cache_settings.reset` | debug |

### `sync_info` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `sync_info.stabilization_complete_now_synced` | info |

### `syslog_fallback` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `syslog_fallback.syslog_initialized` | debug |
| `syslog_fallback.windows_detected_using_stderr` | debug |

### `system_control` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `system_control.dry_run_action` | info |
| `system_control.no_existing_state_using` | info |
| `system_control.state_saved` | debug |
| `system_control.system_state_reset_defaults` | info |

### `test_mode` (5개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `test_mode.global_flag_warning` | warning |
| `test_mode.resource_guard_unavailable` | debug |
| `test_mode.session_manager_unavailable` | debug |
| `test_mode.synthetic_context_unavailable` | debug |
| `test_mode.throttle_status` | info |

### `test_mode_context` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `test_mode_context.manual_exit_synthetic_mode` | debug |

### `throttle_audit` (4개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `throttle_audit.audit_module_available` | debug |
| `throttle_audit.background_worker_started` | info |
| `throttle_audit.clusteridentity_available` | debug |
| `throttle_audit.queue_full_dropping_event` | warning |

### `throttle_aware_backoff` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `throttle_aware_backoff.eventbus_subscription_enabled` | debug |

### `throttle_cb_bridge` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `throttle_cb_bridge.bridge_reset` | info |

### `throttle_dlq` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `throttle_dlq.dlq_service_available` | warning |
| `throttle_dlq.dlq_service_unavailable_skipping` | debug |

### `throttle_registry` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `throttle_registry.cb_callbacks_already_registered` | debug |
| `throttle_registry.registry_reset` | info |

### `tier_registry` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `tier_registry.no_previous_config_rollback` | warning |

### `tiered_redis_provider` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `tiered_redis_provider.global_redis_reusing_local` | debug |

### `tiering_cb` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `tiering_cb.closed_recovered` | info |
| `tiering_cb.transitioning` | info |

### `tiering_middleware` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `tiering_middleware.initialized_disabled` | info |
| `tiering_middleware.initialized_enabled` | info |

### `time_sync` (5개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `time_sync.chronyc_command_failed` | warning |
| `time_sync.chronyc_command_timeout` | warning |
| `time_sync.chronyc_found_trying_ntpq` | debug |
| `time_sync.clock_accuracy_degraded` | warning |
| `time_sync.found` | debug |

### `traffic_aware_replay` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `traffic_aware_replay.replayservice_available` | exception |
| `traffic_aware_replay.runtimeconfigmanager_available` | debug |
| `traffic_aware_replay.track_disabled` | debug |

### `traffic_gate` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `traffic_gate.cascadeloadshedding_available_creating_gate` | warning |

### `traffic_health` (3개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `traffic_health.circuitbreakerservice_available_skipping_cb` | debug |
| `traffic_health.errorbudgetgate_available_skipping` | debug |
| `traffic_health.governancechecks_available_skipping` | debug |

### `uds_client` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `uds_client.closed` | debug |

### `uds_server` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `uds_server.server_already_running` | warning |
| `uds_server.stopped` | info |

### `verify_reconciliation_accuracy` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `verify_reconciliation_accuracy.starting_accuracy_verification` | info |

### `version_conflict` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `version_conflict.audit_system_available` | debug |

### `wal` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `wal.disk_full_fail_open` | warning |

### `watchdog` (17개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `watchdog.cb_service_unavailable` | debug |
| `watchdog.disabled` | info |
| `watchdog.dlq_recovery_started` | info |
| `watchdog.providerregistry_available` | warning |
| `watchdog.recovery_pipeline_recovery` | info |
| `watchdog.recoveryadapter_available` | warning |
| `watchdog.redis_recovery_stage1` | info |
| `watchdog.redis_recovery_stage1_succeeded` | info |
| `watchdog.redis_stage_failed` | error |
| `watchdog.redis_stage_failed_reconnect` | warning |
| `watchdog.redis_stage_success` | info |
| `watchdog.self_cb_half_open` | info |
| `watchdog.self_cb_opened` | warning |
| `watchdog.self_cb_skipped` | debug |
| `watchdog.started` | info |
| `watchdog.stopped` | info |
| `watchdog.stuck_cb_force_half_open` | info |

### `weighted_audit` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `weighted_audit.hash_chain_manager_available` | debug |

### `wildcard_observer` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `wildcard_observer.consumer_loop_exited` | info |
| `wildcard_observer.unregistered_consumer_stopped` | info |

### `x_test_cleanup` (5개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `x_test_cleanup.circuit_breaker_service_available` | debug |
| `x_test_cleanup.dlq_service_available` | debug |
| `x_test_cleanup.no_expired_sessions_found` | debug |
| `x_test_cleanup.redis_adapter_available` | warning |
| `x_test_cleanup.scenario_module_available` | debug |

### `x_test_cleanup_tasks` (2개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `x_test_cleanup_tasks.celery_available_beat_schedule` | debug |
| `x_test_cleanup_tasks.celery_available_skipping_task` | debug |

### `x_test_session` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `x_test_session.redis_adapter_available` | warning |

### `zombie_hunter` (1개 이벤트)

| 이벤트 이름 | 로그 레벨 |
|-------------|-----------|
| `zombie_hunter.starting_zombie_experiment_hunt` | info |
