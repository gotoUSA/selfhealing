# 259. Watchdog Redis 복구 — RecoveryAdapter 연결

> **Version**: 1.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `meta/watchdog.py` — `_recover_redis()` 개선

---

## 0. 요약

`SelfHealerWatchdog._recover_redis()`가 현재 **연결 풀 리셋만** 수행하는 문제를 해결한다.
동일 모듈의 `_recover_dlq()`가 이미 `RecoveryAdapter`를 사용하는 패턴을 따라,
Redis 복구에도 **2단계 복구 전략**(연결 리셋 → 인프라 재시작)을 적용한다.

---

## 1. 현재 상태 분석

### 1.1 `_recover_redis()` — 현재 코드

**파일**: `packages/selfhealing-python/src/selfhealing/meta/watchdog.py` L475–L503

```python
def _recover_redis(self, result: ProbeResult) -> bool:
    try:
        logger.info("[SelfHealerWatchdog] Redis connection reset")
        try:
            from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter
            adapter = RedisCacheAdapter()
            adapter._redis.ping()
            return True
        except Exception:
            pass
        return False
    except Exception as e:
        logger.error(f"[SelfHealerWatchdog] Redis recovery error: {e}")
        return False
```

**문제점**:
- `RedisCacheAdapter()` 재생성으로 연결 풀만 리셋
- 연결 리셋 실패 시 즉시 `False` 반환 — 인프라 레벨 복구 시도 없음
- `_recover_dlq()`와 달리 `RecoveryAdapter`를 전혀 사용하지 않음

### 1.2 `_recover_dlq()` — 참조 패턴

**파일**: `packages/selfhealing-python/src/selfhealing/meta/watchdog.py` L442–L473

```python
def _recover_dlq(self, result: ProbeResult) -> bool:
    try:
        logger.info("[SelfHealerWatchdog] Attempting DLQ recovery")
        try:
            from selfhealing.meta.recovery_adapter import get_recovery_adapter
            adapter = get_recovery_adapter()
            result = adapter.restart_worker("celery-dlq-worker")
            return result.success
        except ImportError:
            pass
        return False
    except Exception as e:
        logger.error(f"[SelfHealerWatchdog] DLQ recovery error: {e}")
        return False
```

**핵심**: `get_recovery_adapter()` → `adapter.restart_worker()` 호출 패턴이 이미 존재.

### 1.3 `RecoveryInfrastructureAdapter` — 사용 가능한 메서드

**파일**: `packages/selfhealing-python/src/selfhealing/meta/recovery_adapter.py`

| 메서드 | 시그니처 | K8s 구현 |
|--------|---------|---------|
| `restart_worker` | `(worker_name: str) → RecoveryResult` | Deployment annotation patch → rolling restart |
| `scale_deployment` | `(name: str, replicas: int) → RecoveryResult` | `patch_namespaced_deployment_scale` |
| `delete_pod` | `(pod_name: str, namespace: str) → RecoveryResult` | `delete_namespaced_pod` → ReplicaSet 재생성 |

### 1.4 K8s Redis 배포 정보

**파일**: `k8s/redis-config.yaml` L91–L121

| 항목 | 값 |
|------|-----|
| `kind` | `Deployment` |
| `metadata.name` | `redis` |
| `metadata.namespace` | `selfhealing` |
| `image` | `redis:7-alpine` |
| `containerPort` | `6379` |

### 1.5 `get_recovery_adapter()` — 어댑터 선택 체인

**파일**: `packages/selfhealing-python/src/selfhealing/meta/recovery_adapter.py` L530–L554

```python
def get_recovery_adapter() -> RecoveryInfrastructureAdapter:
    adapter_type = os.environ.get("SELFHEALING_RECOVERY_ADAPTER", "kubernetes").lower()
    if adapter_type == "noop":
        return NoOpRecoveryAdapter()
    elif adapter_type == "docker":
        return DockerComposeRecoveryAdapter()
    else:
        adapter = KubernetesRecoveryAdapter()
        if adapter.is_available():
            return adapter
        docker_adapter = DockerComposeRecoveryAdapter()
        if docker_adapter.is_available():
            return docker_adapter
        return NoOpRecoveryAdapter()
```

선택 체인: `K8s → Docker → NoOp` (환경변수 `SELFHEALING_RECOVERY_ADAPTER`로 오버라이드)

---

## 2. 구현 설계

### 2.1 2단계 복구 전략

```
Stage 1: 연결 풀 리셋 (현재와 동일)
  ↓ 실패 시
Stage 2: RecoveryAdapter를 통한 인프라 재시작
  - K8s: restart_worker("redis") → Deployment rolling restart
  - Docker: docker-compose restart redis
  - NoOp: 로깅만 (테스트 환경)
```

### 2.2 구현 코드

```python
def _recover_redis(self, result: ProbeResult) -> bool:
    """
    Redis 연결 복구 — 2단계 전략.

    Stage 1: 연결 풀 리셋 (소프트 복구)
    Stage 2: RecoveryAdapter를 통한 인프라 재시작 (하드 복구)

    Args:
        result: 프로브 결과

    Returns:
        복구 성공 여부
    """
    # === Stage 1: 연결 풀 리셋 ===
    try:
        logger.info("[SelfHealerWatchdog] Redis recovery Stage 1: connection reset")

        from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter

        adapter = RedisCacheAdapter()
        adapter._redis.ping()
        logger.info("[SelfHealerWatchdog] Redis Stage 1 success: connection restored")
        return True
    except Exception as e:
        logger.warning(
            f"[SelfHealerWatchdog] Redis Stage 1 failed: {e}, "
            "proceeding to Stage 2 (infrastructure restart)"
        )

    # === Stage 2: RecoveryAdapter 인프라 재시작 ===
    try:
        from selfhealing.meta.recovery_adapter import get_recovery_adapter

        recovery_adapter = get_recovery_adapter()
        recovery_result = recovery_adapter.restart_worker("redis")
        if recovery_result.success:
            logger.info(
                "[SelfHealerWatchdog] Redis Stage 2 success: "
                f"{recovery_result.message}"
            )
        else:
            logger.error(
                "[SelfHealerWatchdog] Redis Stage 2 failed: "
                f"{recovery_result.message}"
            )
        return recovery_result.success
    except ImportError:
        logger.warning(
            "[SelfHealerWatchdog] RecoveryAdapter not available"
        )
        return False
    except Exception as e:
        logger.error(f"[SelfHealerWatchdog] Redis Stage 2 error: {e}")
        return False
```

### 2.3 변경 범위

| 파일 | 변경 | 라인 |
|------|------|------|
| `meta/watchdog.py` | `_recover_redis()` 메서드 교체 | L475–L503 |

**새로 추가되는 import**: 없음 (`recovery_adapter`는 지연 import — `_recover_dlq()`와 동일 패턴)

**새로 추가되는 파일**: 없음

---

## 3. K8s 환경 동작 상세

### 3.1 `KubernetesRecoveryAdapter.restart_worker("redis")` 실행 흐름

**파일**: `packages/selfhealing-python/src/selfhealing/meta/recovery_adapter.py` L249–L290

```python
def restart_worker(self, worker_name: str) -> RecoveryResult:
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "selfhealing.watchdog/restartedAt":
                            datetime.now(timezone.utc).isoformat()
                    }
                }
            }
        }
    }
    self._apps_v1.patch_namespaced_deployment(
        name=worker_name,       # "redis"
        namespace=self._namespace,  # "selfhealing"
        body=patch,
    )
```

**결과**: `kubectl rollout restart deployment/redis -n selfhealing`과 동일한 효과

### 3.2 RBAC 요구사항

**파일**: `k8s/selfhealing-rbac.yaml` (기존)

```yaml
rules:
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "patch"]       # restart_worker에 필요
  - apiGroups: ["apps"]
    resources: ["deployments/scale"]
    verbs: ["get", "patch"]               # scale_deployment에 필요
  - apiGroups: ["apps"]
    resources: ["statefulsets"]
    verbs: ["get", "list", "patch"]       # StatefulSet 전환 시 필요
```

**추가 RBAC 불필요** — 기존 `selfhealing-watchdog` ServiceAccount에 `deployments` patch 권한 이미 존재.

---

## 4. Docker 환경 동작 상세

**파일**: `packages/selfhealing-python/src/selfhealing/meta/recovery_adapter.py` L353–L479

`DockerComposeRecoveryAdapter.restart_worker("redis")`는 내부적으로:

```python
subprocess.run(
    ["docker-compose", "restart", worker_name],  # "redis"
    capture_output=True, text=True, timeout=30
)
```

---

## 5. 테스트 전략

### 5.1 단위 테스트

```python
class TestRecoverRedis:
    """_recover_redis() 2단계 복구 테스트."""

    def test_stage1_success_skips_stage2(self, watchdog, mock_redis_adapter):
        """Stage 1 성공 시 Stage 2를 호출하지 않는다."""
        mock_redis_adapter._redis.ping.return_value = True
        result = watchdog._recover_redis(mock_probe_result)
        assert result is True
        # RecoveryAdapter는 호출되지 않아야 함

    def test_stage1_fail_triggers_stage2(self, watchdog, mock_redis_adapter, mock_recovery_adapter):
        """Stage 1 실패 시 Stage 2 RecoveryAdapter를 호출한다."""
        mock_redis_adapter._redis.ping.side_effect = ConnectionError
        mock_recovery_adapter.restart_worker.return_value = RecoveryResult(
            action=RecoveryAction.RESTART_WORKER,
            success=True,
            target="redis",
            message="Rolling restart triggered",
            timestamp=datetime.now(timezone.utc),
        )
        result = watchdog._recover_redis(mock_probe_result)
        assert result is True
        mock_recovery_adapter.restart_worker.assert_called_once_with("redis")

    def test_both_stages_fail(self, watchdog, mock_redis_adapter, mock_recovery_adapter):
        """Stage 1, 2 모두 실패 시 False 반환."""
        mock_redis_adapter._redis.ping.side_effect = ConnectionError
        mock_recovery_adapter.restart_worker.return_value = RecoveryResult(
            action=RecoveryAction.RESTART_WORKER,
            success=False,
            target="redis",
            message="K8s client not available",
            timestamp=datetime.now(timezone.utc),
        )
        result = watchdog._recover_redis(mock_probe_result)
        assert result is False
```

### 5.2 Audit 연동 확인

기존 `_attempt_recovery()` (L252–L310)가 `_recover_redis()` 전후로 `RecoveryAuditRecorder`를 통해 감사 로그를 기록하므로, Stage 2 추가 시에도 Audit는 **자동으로 기록됨** — 별도 Audit 코드 변경 불필요.

---

## 6. 영향 분석

| 항목 | 영향 |
|------|------|
| 기존 Stage 1 동작 | 변경 없음 (동일 로직 유지) |
| 기존 import | 변경 없음 (지연 import 패턴) |
| Audit 시스템 | 변경 불필요 (`_attempt_recovery`에서 처리) |
| RBAC | 추가 불필요 (기존 권한 충분) |
| 설정 | 추가 불필요 (RecoveryAdapter가 환경 자동 감지) |
| 다른 _recover_* 메서드 | 영향 없음 |

---

## 7. 관련 문서

| 문서 | 관계 |
|------|------|
| `meta/watchdog.py` | 수정 대상 |
| `meta/recovery_adapter.py` | 사용 (변경 없음) |
| `k8s/redis-config.yaml` | 참조 (Deployment name = "redis") |
| `k8s/selfhealing-rbac.yaml` | 참조 (RBAC 확인) |
