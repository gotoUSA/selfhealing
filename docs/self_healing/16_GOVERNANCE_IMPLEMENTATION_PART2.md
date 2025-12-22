# Governance Implementation Plan - Part 2

> Config Versioning, API 미노출 설정, Fail-Safe 강화 구현 계획

---

## 개요

Part 1에 이어지는 거버넌스 강화 구현 계획입니다.

| 순위 | 항목 | 중요도 | 예상 작업 |
|------|------|--------|----------|
| 4 | Config Versioning & Rollback | 중 | 1일 |
| 5 | API 미노출 설정 추가 | 중 | 1일 |
| 6 | Fail-Safe Default 강화 | 중 | 0.5일 |

---

## 4. Config Versioning & Rollback

### 4.1 현재 상태

- 설정 변경 시 이전 버전 저장 안 됨
- 롤백 불가

### 4.2 목표 상태

Redis에 설정 히스토리 저장 + Rollback API 제공.

### 4.3 구현 계획

#### Phase 1: Config History Service

**파일**: `packages/selfhealing-python/src/selfhealing/services/config_history.py`

```python
"""
Configuration History & Rollback Service.

Redis에 설정 변경 이력을 저장하고 롤백 기능 제공.
"""
import json
import time
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# Redis 키 패턴
CONFIG_HISTORY_KEY = "selfhealing:config:history:{config_type}"
CONFIG_CURRENT_KEY = "selfhealing:config:current:{config_type}"
MAX_HISTORY_ENTRIES = 50  # 최대 보관 버전 수


@dataclass
class ConfigVersion:
    """설정 버전 정보."""
    version: int
    timestamp: float
    config_type: str
    values: Dict[str, Any]
    changed_by: str
    reason: str
    hash: str


class ConfigHistoryService:
    """
    설정 변경 이력 관리 서비스.
    
    Features:
    - 변경 시 자동 버전 저장
    - 최근 N개 버전 유지
    - 특정 버전으로 롤백
    - Redis 장애 시 Graceful Degradation
    """
    
    def __init__(self):
        self.redis_client = self._get_redis_client()
    
    def _get_redis_client(self):
        """Redis 클라이언트 획득."""
        try:
            from django.core.cache import caches
            return caches['default'].client.get_client()
        except Exception as e:
            logger.warning(f"[ConfigHistory] Redis unavailable: {e}")
            return None
    
    def save_version(
        self,
        config_type: str,
        values: Dict[str, Any],
        changed_by: str,
        reason: str = ""
    ) -> Optional[ConfigVersion]:
        """
        새 설정 버전 저장.
        
        Args:
            config_type: 설정 유형 (circuit_breaker, dlq, retry 등)
            values: 설정 값
            changed_by: 변경자
            reason: 변경 사유
            
        Returns:
            저장된 ConfigVersion 또는 None (Redis 장애 시)
        """
        if not self.redis_client:
            logger.warning("[ConfigHistory] Redis unavailable - skip save")
            return None
        
        try:
            history_key = CONFIG_HISTORY_KEY.format(config_type=config_type)
            current_key = CONFIG_CURRENT_KEY.format(config_type=config_type)
            
            # 새 버전 번호
            version_num = self.redis_client.incr(f"{history_key}:version")
            
            # 해시 생성
            config_hash = self._compute_hash(values)
            
            version = ConfigVersion(
                version=version_num,
                timestamp=time.time(),
                config_type=config_type,
                values=values,
                changed_by=changed_by,
                reason=reason,
                hash=config_hash,
            )
            
            # 히스토리에 추가 (LPUSH + LTRIM)
            pipe = self.redis_client.pipeline()
            pipe.lpush(history_key, json.dumps(asdict(version)))
            pipe.ltrim(history_key, 0, MAX_HISTORY_ENTRIES - 1)
            pipe.set(current_key, json.dumps(asdict(version)))
            pipe.execute()
            
            logger.info(
                f"[ConfigHistory] Saved: type={config_type}, "
                f"version={version_num}, by={changed_by}"
            )
            
            return version
            
        except Exception as e:
            logger.error(f"[ConfigHistory] Save failed: {e}")
            return None
    
    def get_history(
        self,
        config_type: str,
        limit: int = 10
    ) -> List[ConfigVersion]:
        """
        설정 변경 이력 조회.
        
        Args:
            config_type: 설정 유형
            limit: 조회할 버전 수
            
        Returns:
            ConfigVersion 목록 (최신순)
        """
        if not self.redis_client:
            return []
        
        try:
            history_key = CONFIG_HISTORY_KEY.format(config_type=config_type)
            entries = self.redis_client.lrange(history_key, 0, limit - 1)
            
            versions = []
            for entry in entries:
                data = json.loads(entry)
                versions.append(ConfigVersion(**data))
            
            return versions
            
        except Exception as e:
            logger.error(f"[ConfigHistory] Get history failed: {e}")
            return []
    
    def get_version(
        self,
        config_type: str,
        version: int
    ) -> Optional[ConfigVersion]:
        """특정 버전 조회."""
        history = self.get_history(config_type, limit=MAX_HISTORY_ENTRIES)
        
        for v in history:
            if v.version == version:
                return v
        
        return None
    
    def rollback(
        self,
        config_type: str,
        target_version: int,
        rolled_back_by: str
    ) -> Optional[ConfigVersion]:
        """
        특정 버전으로 롤백.
        
        Args:
            config_type: 설정 유형
            target_version: 롤백할 버전 번호
            rolled_back_by: 롤백 수행자
            
        Returns:
            롤백된 버전 정보
        """
        target = self.get_version(config_type, target_version)
        
        if not target:
            logger.error(
                f"[ConfigHistory] Rollback failed: "
                f"version {target_version} not found"
            )
            return None
        
        # 롤백도 새 버전으로 저장
        return self.save_version(
            config_type=config_type,
            values=target.values,
            changed_by=rolled_back_by,
            reason=f"Rollback to version {target_version}",
        )
    
    def _compute_hash(self, values: Dict[str, Any]) -> str:
        """설정값 해시 계산."""
        import hashlib
        sorted_str = json.dumps(values, sort_keys=True)
        return hashlib.sha256(sorted_str.encode()).hexdigest()[:16]


# 싱글톤 인스턴스
_config_history_service: Optional[ConfigHistoryService] = None


def get_config_history_service() -> ConfigHistoryService:
    """ConfigHistoryService 싱글톤 반환."""
    global _config_history_service
    if _config_history_service is None:
        _config_history_service = ConfigHistoryService()
    return _config_history_service
```

#### Phase 2: Rollback API

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/views/config_history.py`

```python
"""
Config History & Rollback API Views.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from selfhealing.api.django.permissions import IsSelfHealingAdmin
from selfhealing.services.config_history import get_config_history_service


class ConfigHistoryView(APIView):
    """
    GET /api/self-healing/config/{config_type}/history/
    
    설정 변경 이력 조회.
    """
    permission_classes = [IsAuthenticated]  # Viewer도 조회 가능
    
    def get(self, request, config_type):
        service = get_config_history_service()
        limit = int(request.query_params.get('limit', 10))
        
        history = service.get_history(config_type, limit=limit)
        
        return Response({
            "config_type": config_type,
            "count": len(history),
            "versions": [
                {
                    "version": v.version,
                    "timestamp": v.timestamp,
                    "changed_by": v.changed_by,
                    "reason": v.reason,
                    "hash": v.hash,
                }
                for v in history
            ]
        })


class ConfigRollbackView(APIView):
    """
    POST /api/self-healing/config/{config_type}/rollback/
    
    특정 버전으로 롤백.
    """
    permission_classes = [IsAuthenticated, IsSelfHealingAdmin]
    
    def post(self, request, config_type):
        target_version = request.data.get('version')
        
        if not target_version:
            return Response(
                {"error": "version is required"},
                status=400
            )
        
        service = get_config_history_service()
        rolled_back = service.rollback(
            config_type=config_type,
            target_version=int(target_version),
            rolled_back_by=request.user.username,
        )
        
        if not rolled_back:
            return Response(
                {"error": f"Version {target_version} not found"},
                status=404
            )
        
        # 실제 설정 적용 (config_type별 핸들러 호출)
        self._apply_config(config_type, rolled_back.values)
        
        return Response({
            "status": "success",
            "rolled_back_to": target_version,
            "new_version": rolled_back.version,
            "applied_by": request.user.username,
        })
    
    def _apply_config(self, config_type: str, values: dict):
        """롤백된 설정을 실제로 적용."""
        from selfhealing.services.runtime_config import get_runtime_config
        
        config = get_runtime_config()
        
        if config_type == "circuit_breaker":
            config.update_circuit_breaker(**values)
        elif config_type == "dlq":
            config.update_dlq(**values)
        elif config_type == "retry":
            config.update_retry(**values)
        # ... 추가 config_type
```

### 4.4 URL 설정

```python
# urls.py에 추가
path(
    "config/<str:config_type>/history/",
    ConfigHistoryView.as_view(),
    name="config-history"
),
path(
    "config/<str:config_type>/rollback/",
    ConfigRollbackView.as_view(),
    name="config-rollback"
),
```

---

## 5. API 미노출 설정 추가

### 5.1 현재 미노출 설정 목록

| 설정 | 현재 | 목표 |
|------|------|------|
| `SELFHEALING_SLACK_BLOCK_TEXT_LIMIT` | env만 | API 추가 |
| `SELFHEALING_MAX_STACK_FRAMES` | env만 | API 추가 |
| `SELFHEALING_DLQ_LOG_LEVEL` | env만 | API 추가 |
| `SELFHEALING_CB_LOG_LEVEL` | env만 | API 추가 |
| Chaos Blast Radius | env만 | API 추가 |

### 5.2 구현 계획

#### Phase 1: ForensicConfig Serializer

```python
class ForensicConfigSerializer(ApplyStrategyMixin):
    """Forensic 설정 Serializer."""
    
    slack_block_text_limit = serializers.IntegerField(
        required=False, min_value=100, max_value=10000
    )
    max_stack_frames = serializers.IntegerField(
        required=False, min_value=10, max_value=200
    )
    max_context_size_bytes = serializers.IntegerField(
        required=False, min_value=1024, max_value=1048576
    )
```

#### Phase 2: LoggingConfig Serializer

```python
class LoggingConfigSerializer(ApplyStrategyMixin):
    """로깅 레벨 설정 Serializer."""
    
    LEVEL_CHOICES = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    
    dlq_log_level = serializers.ChoiceField(
        required=False, choices=LEVEL_CHOICES
    )
    circuit_breaker_log_level = serializers.ChoiceField(
        required=False, choices=LEVEL_CHOICES
    )
    replay_log_level = serializers.ChoiceField(
        required=False, choices=LEVEL_CHOICES
    )
    sla_log_level = serializers.ChoiceField(
        required=False, choices=LEVEL_CHOICES
    )
```

#### Phase 3: View 및 URL 추가

```python
class ForensicConfigView(BaseConfigView):
    """GET/PUT /api/self-healing/config/forensic/"""
    permission_classes = [IsAuthenticated, IsSelfHealingAdmin]
    serializer_class = ForensicConfigSerializer
    config_name = "forensic"


class LoggingConfigView(BaseConfigView):
    """GET/PUT /api/self-healing/config/logging/"""
    permission_classes = [IsAuthenticated, IsSelfHealingAdmin]
    serializer_class = LoggingConfigSerializer
    config_name = "logging"
```

---

## 6. Fail-Safe Default 강화

### 6.1 현재 상태

일부 설정에 Safe Default 있지만 불완전.

### 6.2 구현 계획

#### Phase 1: Safe Default 정의

**파일**: `packages/selfhealing-python/src/selfhealing/core/safe_defaults.py`

```python
"""
Safe Default Values for Self-Healing Configuration.

모든 설정에 대해 안전한 기본값 정의.
설정 오류 시 이 값으로 폴백.
"""

SAFE_DEFAULTS = {
    # Circuit Breaker - 보수적 설정 (더 빨리 열림)
    "circuit_breaker": {
        "enabled": True,  # 항상 활성화
        "failure_threshold": 5,  # 낮게 유지
        "recovery_timeout": 60,
        "success_threshold": 2,
        "half_open_max_calls": 3,
    },
    
    # DLQ - 보수적 설정 (더 오래 보관)
    "dlq": {
        "enabled": True,
        "max_retries": 3,
        "expiry_hours": 72,
        "retention_days": 30,
    },
    
    # Retry - 보수적 설정 (덜 공격적)
    "retry": {
        "max_attempts": 3,
        "backoff_strategy": "exponential",
        "max_delay": 300.0,
        "jitter": True,
    },
    
    # Rate Limit
    "rate_limit": {
        "enabled": True,
        "max_requests_per_minute": 100,
        "window_seconds": 60,
    },
    
    # Chaos - 보수적 설정 (최소 영향)
    "chaos": {
        "enabled": False,  # 기본 비활성화
        "max_blast_radius": 0.05,  # 5%로 제한
        "dry_run": True,  # 기본 Dry Run
    },
}


def get_safe_default(config_type: str, key: str):
    """
    안전한 기본값 반환.
    
    Args:
        config_type: 설정 유형
        key: 설정 키
        
    Returns:
        안전한 기본값 또는 None
    """
    defaults = SAFE_DEFAULTS.get(config_type, {})
    return defaults.get(key)


def validate_with_safe_fallback(config_type: str, values: dict) -> dict:
    """
    설정값 검증 후 안전한 값으로 폴백.
    
    잘못된 값은 Safe Default로 대체.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    result = {}
    defaults = SAFE_DEFAULTS.get(config_type, {})
    
    for key, value in values.items():
        if not _is_valid_value(config_type, key, value):
            safe_value = defaults.get(key, value)
            logger.warning(
                f"[SafeDefault] Invalid {config_type}.{key}={value}, "
                f"using safe default: {safe_value}"
            )
            result[key] = safe_value
        else:
            result[key] = value
    
    return result


def _is_valid_value(config_type: str, key: str, value) -> bool:
    """값 유효성 검증."""
    # 기본 타입 체크
    if value is None:
        return False
    
    # 숫자 범위 체크
    if config_type == "circuit_breaker":
        if key == "failure_threshold" and (value < 1 or value > 100):
            return False
        if key == "recovery_timeout" and (value < 1 or value > 3600):
            return False
    
    if config_type == "chaos":
        if key == "max_blast_radius" and (value < 0 or value > 0.5):
            return False  # 50% 초과 불가
    
    return True
```

#### Phase 2: Serializer에 Safe Default 적용

```python
class CircuitBreakerConfigSerializer(ApplyStrategyMixin):
    """Circuit Breaker 설정 - Safe Default 포함."""
    
    def validate(self, attrs):
        """검증 + Safe Default 폴백."""
        from selfhealing.core.safe_defaults import validate_with_safe_fallback
        
        validated = super().validate(attrs)
        return validate_with_safe_fallback("circuit_breaker", validated)
```

#### Phase 3: 시작 시 Safe Default 검증

```python
# AppConfig.ready()에 추가
def validate_startup_config():
    """시작 시 설정 검증 + Safe Default 적용."""
    from selfhealing.core.safe_defaults import SAFE_DEFAULTS
    from selfhealing.core.config import get_config
    
    config = get_config()
    
    for config_type, defaults in SAFE_DEFAULTS.items():
        for key, safe_value in defaults.items():
            current = getattr(config, key, None)
            if not _is_valid_value(config_type, key, current):
                logger.warning(
                    f"[Startup] Invalid {key}={current}, "
                    f"applying safe default: {safe_value}"
                )
                setattr(config, key, safe_value)
```

---

## 전체 체크리스트

### Phase 4: Config Versioning
- [ ] `config_history.py` 서비스 생성
- [ ] `ConfigHistoryView`, `ConfigRollbackView` 생성
- [ ] URL 등록
- [ ] 테스트 작성

### Phase 5: API 미노출 설정
- [ ] `ForensicConfigSerializer` 생성
- [ ] `LoggingConfigSerializer` 생성
- [ ] View 및 URL 추가
- [ ] 테스트 작성

### Phase 6: Fail-Safe Default
- [ ] `safe_defaults.py` 생성
- [ ] Serializer에 Safe Default 검증 추가
- [ ] 시작 시 검증 로직 추가
- [ ] 테스트 작성

---

## 구현 순서 권장

```
Week 1:
├── Day 1-2: RBAC (Part 1)
├── Day 3: 환경변수 Audit (Part 1)
└── Day 4: API Rate Limit (Part 1)

Week 2:
├── Day 1-2: Config Versioning (Part 2)
├── Day 3: API 미노출 설정 (Part 2)
└── Day 4: Fail-Safe Default (Part 2)

Week 3:
├── Day 1-2: 통합 테스트
└── Day 3: 문서 업데이트
```

---

## 관련 문서

- [16_GOVERNANCE_IMPLEMENTATION_PART1.md](16_GOVERNANCE_IMPLEMENTATION_PART1.md) - Part 1 (RBAC, Audit, Rate Limit)
- [09_CONFIGURATION.md](09_CONFIGURATION.md) - 설정 레퍼런스
- [07_CONTROL_API.md](07_CONTROL_API.md) - Control API 보안
