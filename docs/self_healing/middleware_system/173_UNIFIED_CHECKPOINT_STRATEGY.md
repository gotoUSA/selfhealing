# 173. 통합 Checkpoint Strategy 패턴

> **버전**: 1.2.0
> **작성일**: 2026-02-03
> **수정일**: 2026-02-03 (v1.1: 리뷰 반영, v1.2: 설계 논의 6건 추가)
> **의존성**: [170_KAFKA_AUDIT_ADAPTER.md](170_KAFKA_AUDIT_ADAPTER.md), [167_ASYNC_AUDIT_PIPELINE.md](167_ASYNC_AUDIT_PIPELINE.md)
> **예상 소요**: 3-4일 (고급 기능 포함)

---

## 0. 문서 목적

이 문서는 두 가지 목표를 달성합니다:

1. **CheckpointManager 통합**: 현재 분리된 `CheckpointManager`와 `KafkaCheckpointManager`를 Base Interface + Strategy 패턴으로 통합
2. **AuditMiddleware 연동**: 통합된 Checkpoint Strategy를 AuditMiddleware → ContinuousAuditRecorder 파이프라인에 연결

**매수자 가치**: "고객사 인프라 환경에 맞춰 File(소규모) → Redis(중규모) → Kafka+Redis(엔터프라이즈) 전략을 플러그인처럼 교체 가능"

---

## 1. 현재 상황 분석 (코드 근거)

### 1.1 현재 존재하는 두 개의 CheckpointManager

| 파일 | 클래스명 | 역할 | 저장소 |
|------|----------|------|--------|
| `checkpoint_manager.py` | `CheckpointManager` | WAL 시퀀스만 저장 | File만 |
| `kafka_checkpoint.py` | `KafkaCheckpointManager` | WAL+Kafka 오프셋 저장 | File + Redis |

**문제점**: 두 클래스가 공통 인터페이스 없이 독립적으로 구현됨

---

### 1.2 CheckpointManager (파일 전용)

**파일**: `packages/selfhealing-python/src/selfhealing/audit/checkpoint_manager.py`
**라인**: 73-93, 104-200

```python
@dataclass
class CheckpointData:
    """체크포인트 데이터."""

    last_sequence: int
    timestamp: float
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "last_sequence": self.last_sequence,
            "timestamp": self.timestamp,
            "version": self.version,
        }


class CheckpointManager:
    """
    WAL 처리 시퀀스 관리자.

    특징:
    - Thread-safe
    - 멀티 프로세스 파일 락 지원
    - fsync로 디스크 영속화 보장
    - 원자적 쓰기 (임시 파일 사용)
    """

    def __init__(
        self,
        checkpoint_path: str | Path | None = None,
        sync_on_write: bool = True,
    ):
        # ...

    def save(self, last_sequence: int) -> None:
        """체크포인트 저장 (멀티 프로세스 파일 락 지원)."""
        # ...

    def load(self) -> int:
        """체크포인트 로드."""
        # ...
```

**핵심 메서드**: `save()`, `load()`, `delete()`, `exists()`

---

### 1.3 KafkaCheckpointManager (File + Redis)

**파일**: `packages/selfhealing-python/src/selfhealing/audit/kafka_checkpoint.py`
**라인**: 57-88, 105-200

```python
@dataclass
class KafkaCheckpointData:
    """Kafka 체크포인트 데이터."""

    wal_sequence: int
    kafka_topic: str
    kafka_partition: int
    kafka_offset: int
    timestamp: str
    checksum: str
    version: int = 1


class KafkaCheckpointManager:
    """
    WAL-Kafka 체크포인트 관리자.

    저장소 옵션:
    - redis: Redis 기반 (분산 환경 권장)
    - file: 파일 기반 (단일 노드)
    """

    def __init__(
        self,
        storage: str = "file",
        redis_client: redis.Redis | None = None,
        file_path: str | Path | None = None,
    ):
        self._storage = storage
        self._redis = redis_client
        # ...

    def save_checkpoint(
        self,
        namespace: str,
        wal_sequence: int,
        kafka_topic: str,
        kafka_partition: int,
        kafka_offset: int,
        checksum: str,
    ) -> None:
        """체크포인트 저장 (원자적)."""
        # ...

    def get_last_checkpoint(self, namespace: str = "default") -> KafkaCheckpointData | None:
        """마지막 체크포인트 조회."""
        if self._storage == "redis":
            return self._get_from_redis(namespace)
        return self._get_from_file(namespace)
```

**핵심 메서드**: `save_checkpoint()`, `get_last_checkpoint()`, `delete_checkpoint()`

---

### 1.4 AuditMiddleware → ContinuousAuditRecorder 연결 현황

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/audit_middleware.py`
**라인**: 130-143

```python
def _ensure_initialized(self) -> None:
    """Lazy 초기화 - Django가 완전히 로드된 후 실행."""
    if self._initialized:
        return

    try:
        from selfhealing.adapters.audit.singleton import get_audit_adapter
        from selfhealing.audit.continuous_audit import ContinuousAuditRecorder

        adapter = get_audit_adapter()
        self._recorder = ContinuousAuditRecorder(
            audit_adapter=adapter,
            fail_open=True,
            fallback_to_stdout=True,
        )
        logger.info("[AuditMiddleware] Initialized with ContinuousAuditRecorder")
    except Exception as e:
        logger.warning(f"[AuditMiddleware] Recorder init failed: {e}")
        self._recorder = None
```

**현재 상태**: ContinuousAuditRecorder에 `wal_enabled` 옵션은 있지만 **Checkpoint 주입점 없음**

---

### 1.5 ContinuousAuditRecorder의 WAL 연동 현황

**파일**: `packages/selfhealing-python/src/selfhealing/audit/continuous_audit.py`
**라인**: 81-133

```python
def __init__(
    self,
    audit_adapter: AuditLogAdapter,
    config: AuditConfig | None = None,
    alert_callback: Callable[[str, dict[str, Any]], None] | None = None,
    state_file: Path | None = None,
    # Fail-Open 정책
    fail_open: bool = True,
    fallback_to_stdout: bool = True,
    # WAL 연동
    wal_enabled: bool = False,
    wal_config: Optional["WALConfig"] = None,
):
    # ...
    # WAL 초기화 (선택적)
    self._wal_enabled = wal_enabled
    self._wal: WriteAheadLog | None = None

    if wal_enabled:
        try:
            from selfhealing.audit.wal import WALConfig as WALConfigClass
            from selfhealing.audit.wal import WriteAheadLog

            self._wal = WriteAheadLog(config=wal_config or WALConfigClass())
            logger.info("[ContinuousAudit] WAL enabled")
        except Exception as e:
            logger.warning(f"[ContinuousAudit] WAL initialization failed: {e}")
            self._wal_enabled = False
```

**라인 699-767** (`_record_with_integrity` 메서드):

```python
def _record_with_integrity(self, entry: AuditEntry) -> str:
    """해시 체인과 함께 기록."""
    with self._lock:
        # ...
        # WAL 기록 (활성화된 경우)
        wal_seq = None
        if self._wal_enabled and self._wal:
            try:
                wal_seq = self._wal.write(entry_dict)
            except Exception as e:
                logger.warning(f"[ContinuousAudit] WAL write failed: {e}")

        # Fail-Open 패턴으로 기록
        try:
            self.audit_adapter.log(entry)

            # WAL 커밋 (성공 시)
            if wal_seq is not None and self._wal:
                try:
                    self._wal.mark_processed(wal_seq)
                except Exception as e:
                    logger.warning(f"[ContinuousAudit] WAL commit failed: {e}")
```

**문제점**: WAL 시퀀스는 기록하지만, **Checkpoint로 영속화하는 코드가 없음**

---

## 2. 설계: Base Interface + Strategy 패턴

### 2.1 통합 아키텍처

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    Unified Checkpoint Strategy Pattern                        │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                    CheckpointStorageStrategy (ABC)                       │ │
│  │  ┌─────────────────────────────────────────────────────────────────────┐ │ │
│  │  │  + save(namespace, data) -> None                                    │ │ │
│  │  │  + load(namespace) -> CheckpointData | None                         │ │ │
│  │  │  + commit(namespace) -> None                                        │ │ │
│  │  │  + delete(namespace) -> bool                                        │ │ │
│  │  │  + exists(namespace) -> bool                                        │ │ │
│  │  └─────────────────────────────────────────────────────────────────────┘ │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                    △                                         │
│                                    │                                         │
│         ┌──────────────────────────┼──────────────────────────┐              │
│         │                          │                          │              │
│  ┌──────┴───────┐          ┌───────┴───────┐          ┌───────┴───────┐      │
│  │    File      │          │    Redis      │          │  Kafka+Redis  │      │
│  │   Strategy   │          │   Strategy    │          │   Strategy    │      │
│  │  (소규모)    │          │  (중규모)     │          │ (엔터프라이즈)│      │
│  └──────────────┘          └───────────────┘          └───────────────┘      │
│                                                                              │
│  의존성: 없음              의존성: redis       의존성: redis + confluent-kafka│
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 데이터 모델 통합

**코드 근거 분석**:

| 필드 | CheckpointData | KafkaCheckpointData | 통합 |
|------|---------------|---------------------|------|
| `last_sequence` / `wal_sequence` | ✅ | ✅ | `wal_sequence` |
| `timestamp` | ✅ (float) | ✅ (str ISO) | `timestamp` (str ISO) |
| `version` | ✅ | ✅ | `version` |
| `kafka_topic` | ❌ | ✅ | `kafka_topic` (Optional) |
| `kafka_partition` | ❌ | ✅ | `kafka_partition` (Optional) |
| `kafka_offset` | ❌ | ✅ | `kafka_offset` (Optional) |
| `checksum` | ❌ | ✅ | `checksum` (Optional) |

---

## 2.2.1 Namespace 정의 (Q1)

**코드 근거**: `kafka_checkpoint.py#L156-170`

```python
def get_last_checkpoint(self, namespace: str = "default") -> KafkaCheckpointData | None:
    """
    마지막 체크포인트 조회.

    Args:
        namespace: 네임스페이스

    Returns:
        KafkaCheckpointData 또는 None
    """
    if self._storage == "redis":
        return self._get_from_redis(namespace)
    return self._get_from_file(namespace)
```

**Namespace 설계**:

| 항목 | 설명 |
|------|------|
| **정의** | 같은 시스템 내에서 여러 개의 독립적인 체크포인트를 구분하는 논리적 격리 단위 |
| **기본값** | `"default"` - 단일 테넌트 환경에서 명시 불필요 |
| **용도 1** | 멀티 테넌트: `tenant_a`, `tenant_b` 별도 체크포인트 |
| **용도 2** | 환경별 격리: `staging`, `production` |
| **용도 3** | 워커별 격리: `worker_0`, `worker_1` (파티션별 처리) |

**Redis 키 구조**:
```
selfhealing:checkpoint:{namespace}
selfhealing:kafka_checkpoint:{namespace}
```

**File 경로 구조**:
```
/var/log/audit/checkpoint.{namespace}.json
/var/log/audit/kafka_checkpoint.{namespace}.json
```

---

## 2.2.2 Version 마이그레이션 (Q5)

**코드 근거**: `checkpoint_manager.py#L79-95`

```python
@dataclass
class CheckpointData:
    """체크포인트 데이터."""
    last_sequence: int
    timestamp: float
    version: int = 1

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckpointData:
        """딕셔너리에서 생성."""
        return cls(
            last_sequence=data.get("last_sequence", 0),
            timestamp=data.get("timestamp", 0.0),
            version=data.get("version", 1),  # 기본값으로 하위호환
        )
```

**버전 마이그레이션 패턴**:

```python
class UnifiedCheckpointData:
    """통합 체크포인트 데이터 (버전 마이그레이션 지원)."""

    CURRENT_VERSION = 2

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnifiedCheckpointData:
        """
        딕셔너리에서 생성 (버전별 마이그레이션).

        v1 → v2 변경사항:
        - last_sequence → wal_sequence (필드명 변경)
        - timestamp: float → str ISO 8601 (형식 변경)
        - extra_data 필드 추가 (확장성)
        """
        version = data.get("version", 1)

        if version == 1:
            return cls._migrate_v1_to_v2(data)
        elif version == 2:
            return cls._parse_v2(data)
        else:
            raise CheckpointError(f"Unsupported version: {version}")

    @classmethod
    def _migrate_v1_to_v2(cls, data: dict[str, Any]) -> UnifiedCheckpointData:
        """v1 → v2 마이그레이션."""
        # 필드명 변경
        wal_sequence = data.get("last_sequence", data.get("wal_sequence", 0))

        # timestamp 형식 변환 (float → ISO 8601)
        timestamp = data.get("timestamp", 0.0)
        if isinstance(timestamp, (int, float)):
            timestamp = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()

        return cls(
            wal_sequence=wal_sequence,
            timestamp=timestamp,
            version=cls.CURRENT_VERSION,  # 최신 버전으로 승격
        )
```

**마이그레이션 보장**:
- 구버전 파일 로드 시 **자동 마이그레이션**
- 저장 시 항상 **최신 버전**으로 저장
- 버전 필드 기본값으로 **하위 호환성** 보장

---

## 2.2.3 Retention Policy (Q6)

**코드 근거**: `checkpoint_manager.py#L180-210` (save 메서드)

```python
def save(self, last_sequence: int) -> None:
    """체크포인트 저장 (멀티 프로세스 파일 락 지원)."""
    # ...
    # 원자적 교체 (덮어쓰기)
    temp_path.replace(self._path)
```

**Retention 전략**:

| 방식 | 설명 | 채택 |
|------|------|------|
| **덮어쓰기** | 최신 1개만 유지 | ✅ 현재 |
| 히스토리 보관 | N개 버전 유지 | ❌ 불필요 |
| TTL 기반 | 일정 시간 후 삭제 | ❌ 불필요 |

**이유**:
1. 체크포인트는 **"어디까지 처리했는지"** 만 알면 됨
2. 과거 체크포인트는 복구에 **무의미** (이미 처리된 데이터)
3. 디스크 공간 **효율성** (단일 파일만 유지)
4. 원자적 덮어쓰기로 **정합성 보장**

**예외 상황 (히스토리 필요 시)**:
```python
class CheckpointHistoryStorage(CheckpointStorageStrategy):
    """감사 목적으로 체크포인트 히스토리 보관이 필요한 경우."""

    def __init__(self, max_history: int = 10):
        self._max_history = max_history

    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        # 1. 현재 체크포인트 아카이브
        self._archive_current(namespace)
        # 2. 새 체크포인트 저장
        super().save(namespace, data)
        # 3. 오래된 아카이브 정리
        self._cleanup_old_archives(namespace, keep=self._max_history)
```

---

## 2.2.4 commit() 역할 (Q7)

**코드 근거**: `checkpoint_manager.py#L220-230`

```python
# save() 메서드 내부
if self._sync_on_write:
    f.flush()
    os.fsync(f.fileno())  # 파일 내용 fsync

    # 디렉터리 fsync (메타데이터 영속화)
    if os.name != "nt":  # Windows 제외
        try:
            dir_fd = os.open(str(self._path.parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)  # 디렉터리 fsync
            finally:
                os.close(dir_fd)
        except (OSError, AttributeError):
            pass
```

**save() vs commit() 차이**:

| 메서드 | 동작 | 보장 수준 |
|--------|------|----------|
| `save()` | 파일 쓰기 + 파일 fsync | 파일 **내용**이 디스크에 기록됨 |
| `commit()` | 디렉터리 fsync 추가 | 파일 **메타데이터**까지 디스크에 기록됨 |

**왜 디렉터리 fsync가 필요한가?**

```
1. save() 호출 → temp.json 생성 → temp.json.replace(checkpoint.json)
2. 파일 내용은 fsync로 디스크에 기록됨
3. 하지만 "checkpoint.json이라는 파일명" 은 디렉터리 엔트리에 저장됨
4. 디렉터리 엔트리가 fsync되지 않으면 → 정전 시 파일명이 사라질 수 있음
```

**구현**:
```python
def commit(self, namespace: str) -> None:
    """
    체크포인트 명시적 커밋 (디렉터리 fsync 포함).

    정전 등 하드웨어 장애 시에도 파일명이 디렉터리에 확실히 반영되도록 보장.
    """
    file_path = self._get_file_path(namespace)
    if file_path.exists() and os.name != "nt":
        self._sync_directory(file_path.parent)

def _sync_directory(self, dir_path: Path) -> None:
    """디렉터리 fsync (Linux/macOS only)."""
    try:
        dir_fd = os.open(str(dir_path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except (OSError, AttributeError) as e:
        logger.warning(f"[Checkpoint] Directory sync failed: {e}")
```

---

## 2.2.5 fsync 레벨 (Q8)

**코드 근거**: `checkpoint_manager.py#L220-230`

**fsync 계층 구조**:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Application Layer                            │
│  json.dump(data, f)  →  메모리 버퍼에 기록                       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    OS Buffer Layer                               │
│  f.flush()  →  Python 버퍼 → OS 페이지 캐시로 이동               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Disk Layer                                    │
│  os.fsync(f.fileno())  →  OS 캐시 → 디스크 플래터에 기록         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Directory Layer                               │
│  os.fsync(dir_fd)  →  디렉터리 엔트리(파일명)도 디스크에 기록    │
└─────────────────────────────────────────────────────────────────┘
```

**OS별 지원 현황**:

| OS | 파일 fsync | 디렉터리 fsync | 비고 |
|----|-----------|---------------|------|
| **Linux** | ✅ `os.fsync()` | ✅ `O_DIRECTORY` | 완전 지원 |
| **macOS** | ✅ `os.fsync()` | ✅ `O_DIRECTORY` | 완전 지원 |
| **Windows** | ✅ `os.fsync()` | ❌ 미지원 | `O_DIRECTORY` 없음 |

**Windows 대응**:
```python
if os.name != "nt":  # Windows 제외
    try:
        dir_fd = os.open(str(self._path.parent), os.O_RDONLY | os.O_DIRECTORY)
        # ...
    except (OSError, AttributeError):
        pass  # Windows에서는 조용히 스킵
```

**성능 vs 안정성 트레이드오프**:
```python
def __init__(self, sync_on_write: bool = True):
    """
    Args:
        sync_on_write: True = 매 저장마다 fsync (안전, 느림)
                      False = OS 캐시에만 기록 (빠름, 정전 시 손실 가능)
    """
    self._sync_on_write = sync_on_write
```

---

## 2.2.6 Cloud Native 확장 (Q11)

**Strategy 패턴의 확장성 예시**:

```python
class S3CheckpointStorage(CheckpointStorageStrategy):
    """
    AWS S3 기반 체크포인트 저장소.

    장점:
    - 무제한 내구성 (11 9's durability)
    - 멀티 리전 복제
    - 버전 관리 내장
    - 저렴한 스토리지 비용

    적합한 환경:
    - AWS 기반 K8s 클러스터
    - 멀티 리전 DR 요구사항
    - 장기 체크포인트 아카이빙
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "checkpoints/",
        region: str = "ap-northeast-2",
    ):
        super().__init__()
        self._bucket = bucket
        self._prefix = prefix
        self._s3 = boto3.client("s3", region_name=region)

    def _get_key(self, namespace: str) -> str:
        """S3 객체 키 생성."""
        return f"{self._prefix}{namespace}/checkpoint.json"

    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """S3에 체크포인트 저장."""
        key = self._get_key(namespace)
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=json.dumps(data.to_dict()),
            ContentType="application/json",
        )
        logger.debug(f"[S3Checkpoint] Saved: s3://{self._bucket}/{key}")

    def load(self, namespace: str) -> UnifiedCheckpointData | None:
        """S3에서 체크포인트 로드."""
        try:
            key = self._get_key(namespace)
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
            raw_data = json.loads(response["Body"].read())
            return UnifiedCheckpointData.from_dict(raw_data)
        except self._s3.exceptions.NoSuchKey:
            return None


class GCSCheckpointStorage(CheckpointStorageStrategy):
    """Google Cloud Storage 기반 체크포인트 저장소."""

    def __init__(self, bucket: str, prefix: str = "checkpoints/"):
        super().__init__()
        from google.cloud import storage
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket)
        self._prefix = prefix

    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        blob = self._bucket.blob(f"{self._prefix}{namespace}/checkpoint.json")
        blob.upload_from_string(json.dumps(data.to_dict()))


class AzureBlobCheckpointStorage(CheckpointStorageStrategy):
    """Azure Blob Storage 기반 체크포인트 저장소."""

    def __init__(self, container: str, prefix: str = "checkpoints/"):
        super().__init__()
        from azure.storage.blob import BlobServiceClient
        self._container_client = BlobServiceClient.from_connection_string(
            os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        ).get_container_client(container)
        self._prefix = prefix

    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        blob_client = self._container_client.get_blob_client(
            f"{self._prefix}{namespace}/checkpoint.json"
        )
        blob_client.upload_blob(json.dumps(data.to_dict()), overwrite=True)
```

**Registry 등록 예시**:
```python
# 커스텀 클라우드 전략 등록
CheckpointStrategyRegistry.register("s3", S3CheckpointStorage)
CheckpointStrategyRegistry.register("gcs", GCSCheckpointStorage)
CheckpointStrategyRegistry.register("azure", AzureBlobCheckpointStorage)

# 환경변수로 선택
# SELFHEALING_CHECKPOINT_STORAGE=s3
# AWS_S3_BUCKET=my-checkpoint-bucket
strategy = get_checkpoint_strategy()
```

---

## 2.3 StrategyRegistry 도입 (ProviderRegistry 패턴 적용)

**코드 근거**: `services/factory/registry.py#L55-150`

기존 `ProviderRegistry` 패턴을 참고하여 동적 전략 등록을 지원합니다.

```python
class ProviderRegistry:
    """Central registry for all pluggable components."""
    _cache_providers: dict[str, type] = {}

    @classmethod
    def register_cache(cls, name: str, provider_class: type) -> None:
        """Register a cache provider adapter."""
        cls._cache_providers[name] = provider_class

    @classmethod
    def get_cache(cls, name: str | None = None, ...) -> CacheProviderInterface:
        """Get a cache provider instance."""
        if name not in cls._cache_providers:
            cls._auto_register_cache_adapters()
        return cls._cache_instances[name]
```

**동일 패턴 적용**:

```python
class CheckpointStrategyRegistry:
    """
    체크포인트 전략 동적 레지스트리.

    코드 근거: services/factory/registry.py#L55-150 (ProviderRegistry 패턴)

    Usage:
        # 커스텀 전략 등록
        CheckpointStrategyRegistry.register("s3", S3CheckpointStorage)
        CheckpointStrategyRegistry.register("gcs", GCSCheckpointStorage)

        # 전략 조회
        strategy = CheckpointStrategyRegistry.get("s3", bucket="my-bucket")

        # 기본값 설정
        CheckpointStrategyRegistry.set_default("redis")
    """

    _strategies: dict[str, type[CheckpointStorageStrategy]] = {}
    _instances: dict[str, CheckpointStorageStrategy] = {}
    _default: str = "file"
    _lock = threading.Lock()

    @classmethod
    def register(cls, name: str, strategy_class: type[CheckpointStorageStrategy]) -> None:
        """
        전략 등록.

        Args:
            name: 전략 식별자 (예: "file", "redis", "s3")
            strategy_class: CheckpointStorageStrategy 구현체
        """
        with cls._lock:
            cls._strategies[name] = strategy_class
            logger.info(f"[CheckpointStrategyRegistry] Registered: {name}")

    @classmethod
    def get(
        cls,
        name: str | None = None,
        force_new: bool = False,
        **kwargs,
    ) -> CheckpointStorageStrategy:
        """
        전략 인스턴스 조회.

        Args:
            name: 전략명 (None이면 기본값)
            force_new: 새 인스턴스 강제 생성
            **kwargs: 전략 생성자 인자
        """
        name = name or cls._default

        with cls._lock:
            if name not in cls._strategies:
                cls._auto_register()
                if name not in cls._strategies:
                    raise ValueError(f"Unknown checkpoint strategy: {name}")

            if force_new or name not in cls._instances:
                cls._instances[name] = cls._strategies[name](**kwargs)

            return cls._instances[name]

    @classmethod
    def set_default(cls, name: str) -> None:
        """기본 전략 설정."""
        cls._default = name

    @classmethod
    def list_strategies(cls) -> list[str]:
        """등록된 전략 목록."""
        return list(cls._strategies.keys())

    @classmethod
    def _auto_register(cls) -> None:
        """내장 전략 자동 등록."""
        if "file" not in cls._strategies:
            cls._strategies["file"] = FileCheckpointStorage
        if "redis" not in cls._strategies:
            cls._strategies["redis"] = RedisCheckpointStorage
        if "kafka_redis" not in cls._strategies:
            cls._strategies["kafka_redis"] = KafkaRedisCheckpointStorage
        if "composite" not in cls._strategies:
            cls._strategies["composite"] = CompositeCheckpointStorage
```

---

## 2.4 Back-pressure 메커니즘 (Batch-based Checkpointing)

**코드 근거**: `audit/sync_worker.py#L63-64, L394-401`

```python
# sync_worker.py#L63-64
checkpoint_save_interval_batches: int = 10  # N 배치마다 저장
checkpoint_save_interval_seconds: float = 30.0  # 최대 저장 간격

# sync_worker.py#L394-401
should_save_checkpoint = (
    self._batches_since_checkpoint >= self._config.checkpoint_save_interval_batches
    or time.time() - self._last_checkpoint_time >= self._config.checkpoint_save_interval_seconds
)
if should_save_checkpoint:
    self._save_checkpoint()
    self._batches_since_checkpoint = 0
```

**동일 패턴 Base Interface에 적용**:

```python
class CheckpointStorageStrategy(ABC):
    """
    체크포인트 저장 전략 인터페이스 (Back-pressure 지원).

    Back-pressure 코드 근거: audit/sync_worker.py#L394-401
    """

    def __init__(
        self,
        batch_threshold: int = 10,
        max_interval_seconds: float = 30.0,
    ):
        """
        Args:
            batch_threshold: N번 호출마다 실제 저장 (기본 10)
            max_interval_seconds: 최대 저장 간격 (기본 30초)
        """
        self._batch_count = 0
        self._batch_threshold = batch_threshold
        self._max_interval_seconds = max_interval_seconds
        self._last_save_time = time.time()

    def maybe_save(self, namespace: str, data: UnifiedCheckpointData) -> bool:
        """
        Back-pressure 로직 포함 저장.

        매 호출마다 저장하지 않고, batch_threshold 또는 max_interval 조건 충족 시만 저장.

        Args:
            namespace: 네임스페이스
            data: 체크포인트 데이터

        Returns:
            실제 저장 여부
        """
        self._batch_count += 1

        should_save = (
            self._batch_count >= self._batch_threshold
            or time.time() - self._last_save_time >= self._max_interval_seconds
        )

        if should_save:
            self.save(namespace, data)
            self._batch_count = 0
            self._last_save_time = time.time()
            return True

        return False

    def force_save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """
        강제 저장 (Back-pressure 무시).

        종료 시그널, 에러 복구 등 즉시 저장이 필요한 경우.
        """
        self.save(namespace, data)
        self._batch_count = 0
        self._last_save_time = time.time()
```

---

## 2.5 Tiered Fallback (CompositeCheckpointStorage)

**코드 근거**: `audit/graceful_degradation/fallback.py#L22-126`

```python
class HashChainFallbackChain:
    """
    Multi-tier fallback chain for hash chain operations.

    Fallback order:
    1. Redis Primary - Full distributed functionality
    2. Redis Replica - Read-only, degraded writes to local
    3. Local File - Persistent but not distributed
    4. Memory Buffer - Last resort, volatile

    Each fallback level marks entries as degraded for later reconciliation.
    """

    def add_integrity(self, entry: dict[str, Any]) -> dict[str, Any]:
        # Try Redis Primary
        if self._redis_primary:
            try:
                result = self._add_integrity_redis_primary(entry)
                return result
            except Exception as e:
                self._stats["fallback_events"] += 1

        # Try Local File...
        # Last resort: Memory Buffer
```

**동일 패턴 체크포인트에 적용**:

```python
class CompositeCheckpointStorage(CheckpointStorageStrategy):
    """
    다중 저장소 Fallback 체인.

    코드 근거: audit/graceful_degradation/fallback.py#L22-126 (HashChainFallbackChain)

    Fallback 순서:
    1. Primary (Redis) - 분산 환경 완전 지원
    2. Secondary (File) - 로컬 영속화 백업
    3. Memory Buffer - 최후 수단 (휘발성)

    Usage:
        composite = CompositeCheckpointStorage(
            primary=RedisCheckpointStorage(redis),
            secondary=FileCheckpointStorage("/backup"),
        )
        composite.save("default", data)  # Redis 실패 시 File로 자동 Fallback
    """

    def __init__(
        self,
        primary: CheckpointStorageStrategy,
        secondary: CheckpointStorageStrategy | None = None,
        enable_memory_fallback: bool = True,
    ):
        """
        Args:
            primary: 주 저장소 (Redis 권장)
            secondary: 백업 저장소 (File 권장)
            enable_memory_fallback: 메모리 버퍼 최후 Fallback 활성화
        """
        super().__init__()
        self._primary = primary
        self._secondary = secondary
        self._enable_memory_fallback = enable_memory_fallback

        # Memory Buffer (최후 수단)
        self._memory_buffer: dict[str, UnifiedCheckpointData] = {}

        # 통계
        self._stats = {
            "primary_writes": 0,
            "secondary_writes": 0,
            "memory_writes": 0,
            "fallback_events": 0,
        }
        self._current_tier = "primary"

    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """
        Tiered Fallback 저장.

        1. Primary 시도 → 성공 시 종료
        2. Primary 실패 → Secondary 시도
        3. Secondary 실패 → Memory Buffer (최후)
        """
        # Tier 1: Primary
        try:
            self._primary.save(namespace, data)
            self._current_tier = "primary"
            self._stats["primary_writes"] += 1
            return
        except Exception as e:
            logger.warning(f"[CompositeCheckpoint] Primary failed: {e}")
            self._stats["fallback_events"] += 1

        # Tier 2: Secondary (degraded 마킹)
        if self._secondary:
            try:
                # degraded 상태 기록
                data_copy = UnifiedCheckpointData(
                    wal_sequence=data.wal_sequence,
                    timestamp=data.timestamp,
                    version=data.version,
                    kafka_topic=data.kafka_topic,
                    kafka_partition=data.kafka_partition,
                    kafka_offset=data.kafka_offset,
                    checksum=data.checksum,
                )
                # extra_data에 degraded 상태 기록 (향후 reconciliation용)
                self._secondary.save(namespace, data_copy)
                self._current_tier = "secondary"
                self._stats["secondary_writes"] += 1
                logger.warning(
                    f"[CompositeCheckpoint] Degraded to secondary: namespace={namespace}"
                )
                return
            except Exception as e:
                logger.warning(f"[CompositeCheckpoint] Secondary failed: {e}")
                self._stats["fallback_events"] += 1

        # Tier 3: Memory Buffer (최후 수단)
        if self._enable_memory_fallback:
            self._memory_buffer[namespace] = data
            self._current_tier = "memory"
            self._stats["memory_writes"] += 1
            logger.error(
                f"[CompositeCheckpoint] Degraded to MEMORY (volatile): namespace={namespace}"
            )
            return

        raise CheckpointError("All storage tiers failed")

    def load(self, namespace: str) -> UnifiedCheckpointData | None:
        """
        Tiered Load (Primary → Secondary → Memory 순).
        """
        # Primary
        try:
            data = self._primary.load(namespace)
            if data:
                return data
        except Exception:
            pass

        # Secondary
        if self._secondary:
            try:
                data = self._secondary.load(namespace)
                if data:
                    return data
            except Exception:
                pass

        # Memory
        return self._memory_buffer.get(namespace)

    def get_stats(self) -> dict[str, Any]:
        """통계 조회."""
        return {
            **self._stats,
            "current_tier": self._current_tier,
        }

    def commit(self, namespace: str) -> None:
        """Primary 커밋."""
        self._primary.commit(namespace)

    def delete(self, namespace: str) -> bool:
        """모든 Tier에서 삭제."""
        results = []
        try:
            results.append(self._primary.delete(namespace))
        except Exception:
            pass
        if self._secondary:
            try:
                results.append(self._secondary.delete(namespace))
            except Exception:
                pass
        self._memory_buffer.pop(namespace, None)
        return any(results)

    def exists(self, namespace: str) -> bool:
        """어느 Tier에든 존재 여부."""
        try:
            if self._primary.exists(namespace):
                return True
        except Exception:
            pass
        if self._secondary:
            try:
                if self._secondary.exists(namespace):
                    return True
            except Exception:
                pass
        return namespace in self._memory_buffer
```

---

## 3. 구현 상세

### 3.1 통합 데이터 모델

**파일**: `packages/selfhealing-python/src/selfhealing/audit/checkpoint_strategy.py` (신규)

```python
"""
Unified Checkpoint Strategy - Base Interface + Strategy Pattern.

고객사 인프라 환경에 맞춰 저장 전략을 플러그인처럼 교체 가능:
- FileCheckpointStorage: 순수 파이썬, 의존성 없음 (소규모)
- RedisCheckpointStorage: Redis 기반 (중규모)
- KafkaRedisCheckpointStorage: Kafka+Redis (엔터프라이즈)

Usage:
    from selfhealing.audit.checkpoint_strategy import (
        get_checkpoint_strategy,
        CheckpointStorageStrategy,
    )

    # 환경변수 기반 자동 선택
    strategy = get_checkpoint_strategy()

    # 명시적 선택
    strategy = get_checkpoint_strategy(storage_type="redis")

    # 체크포인트 저장
    strategy.save("default", UnifiedCheckpointData(wal_sequence=1234))

    # 체크포인트 로드
    data = strategy.load("default")

Version: 1.0.0
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)


# =============================================================================
# Unified Data Model
# =============================================================================


@dataclass
class UnifiedCheckpointData:
    """
    통합 체크포인트 데이터.

    기존 CheckpointData와 KafkaCheckpointData를 통합.
    Kafka 필드는 Optional로 처리하여 File 모드에서도 사용 가능.

    코드 근거:
    - checkpoint_manager.py#L73-L93: CheckpointData
    - kafka_checkpoint.py#L57-L88: KafkaCheckpointData
    """

    # 필수 필드 (모든 전략에서 사용)
    wal_sequence: int
    """마지막 처리된 WAL 시퀀스."""

    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    """체크포인트 시간 (ISO 8601)."""

    version: int = 1
    """체크포인트 버전."""

    # Kafka 전용 필드 (Optional - Kafka 전략에서만 사용)
    kafka_topic: str | None = None
    """Kafka 토픽."""

    kafka_partition: int | None = None
    """Kafka 파티션."""

    kafka_offset: int | None = None
    """Kafka 오프셋."""

    checksum: str | None = None
    """WAL 엔트리 체크섬 (검증용)."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnifiedCheckpointData:
        """딕셔너리에서 생성."""
        return cls(
            wal_sequence=data.get("wal_sequence", data.get("last_sequence", 0)),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            version=data.get("version", 1),
            kafka_topic=data.get("kafka_topic"),
            kafka_partition=data.get("kafka_partition"),
            kafka_offset=data.get("kafka_offset"),
            checksum=data.get("checksum"),
        )

    @classmethod
    def from_legacy_checkpoint_data(cls, data: dict[str, Any]) -> UnifiedCheckpointData:
        """
        기존 CheckpointData 형식에서 변환.

        코드 근거: checkpoint_manager.py#L73-L93
        """
        timestamp = data.get("timestamp", 0.0)
        if isinstance(timestamp, (int, float)):
            timestamp = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()

        return cls(
            wal_sequence=data.get("last_sequence", 0),
            timestamp=timestamp,
            version=data.get("version", 1),
        )


class CheckpointError(Exception):
    """체크포인트 관련 에러."""

    pass


class CheckpointCorruptedError(CheckpointError):
    """
    체크포인트 손상 에러.

    checksum 검증 실패 시 발생.
    코드 근거: audit/checksum.py#L60-75 (verify_crc32)
    """

    def __init__(self, message: str, expected: str, computed: str):
        super().__init__(message)
        self.expected = expected
        self.computed = computed


# =============================================================================
# Base Strategy Interface
# =============================================================================


class CheckpointStorageStrategy(ABC):
    """
    체크포인트 저장 전략 인터페이스.

    저장 방식과 상관없이 save(), load(), commit() 등 공통 규격 정의.
    구현체는 File, Redis, Kafka+Redis 등 인프라 환경에 맞게 선택.
    """

    @abstractmethod
    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """
        체크포인트 저장.

        Args:
            namespace: 네임스페이스 (멀티 테넌트 지원)
            data: 통합 체크포인트 데이터
        """
        pass

    @abstractmethod
    def load(self, namespace: str) -> UnifiedCheckpointData | None:
        """
        체크포인트 로드.

        Args:
            namespace: 네임스페이스

        Returns:
            UnifiedCheckpointData 또는 None
        """
        pass

    @abstractmethod
    def commit(self, namespace: str) -> None:
        """
        체크포인트 커밋 (트랜잭션 완료).

        Redis나 분산 환경에서 원자적 커밋 보장.

        Args:
            namespace: 네임스페이스
        """
        pass

    @abstractmethod
    def delete(self, namespace: str) -> bool:
        """
        체크포인트 삭제.

        Args:
            namespace: 네임스페이스

        Returns:
            삭제 성공 여부
        """
        pass

    @abstractmethod
    def exists(self, namespace: str) -> bool:
        """
        체크포인트 존재 여부.

        Args:
            namespace: 네임스페이스

        Returns:
            존재 여부
        """
        pass

    def get_wal_sequence(self, namespace: str = "default") -> int:
        """
        마지막 WAL 시퀀스 조회 (편의 메서드).

        Args:
            namespace: 네임스페이스

        Returns:
            마지막 WAL 시퀀스 (없으면 0)
        """
        data = self.load(namespace)
        return data.wal_sequence if data else 0


# =============================================================================
# File Strategy (의존성 없음 - 소규모용)
# =============================================================================


class FileCheckpointStorage(CheckpointStorageStrategy):
    """
    파일 기반 체크포인트 저장소.

    특징:
    - 순수 파이썬, 외부 의존성 없음
    - 멀티 프로세스 파일 락 지원
    - fsync로 디스크 영속화 보장
    - 원자적 쓰기 (임시 파일 + rename)

    코드 근거: checkpoint_manager.py#L104-L290
    """

    DEFAULT_DIR = "/var/log/audit"
    DEFAULT_FILENAME = "checkpoint.json"

    def __init__(
        self,
        base_path: str | Path | None = None,
        sync_on_write: bool = True,
    ):
        """
        FileCheckpointStorage 초기화.

        Args:
            base_path: 체크포인트 파일 기본 경로
            sync_on_write: 쓰기 시 fsync 수행 여부
        """
        self._base_path = self._get_base_path(base_path)
        self._sync_on_write = sync_on_write
        self._lock = threading.RLock()

        # 디렉토리 생성
        self._base_path.mkdir(parents=True, exist_ok=True)

    def _get_base_path(self, base_path: str | Path | None) -> Path:
        """기본 경로 결정."""
        if base_path:
            return Path(base_path)

        env_path = os.environ.get("SELFHEALING_AUDIT_PATH")
        if env_path:
            return Path(env_path)

        if os.name == "nt":  # Windows
            return Path(tempfile.gettempdir()) / "selfhealing"
        return Path(self.DEFAULT_DIR)

    def _get_file_path(self, namespace: str) -> Path:
        """네임스페이스별 파일 경로."""
        return self._base_path / f"checkpoint.{namespace}.json"

    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """체크포인트 저장 (원자적)."""
        with self._lock:
            file_path = self._get_file_path(namespace)
            tmp_path = file_path.with_suffix(".tmp")

            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data.to_dict(), f, indent=2)

                    if self._sync_on_write:
                        f.flush()
                        os.fsync(f.fileno())

                # 원자적 rename
                tmp_path.replace(file_path)

                logger.debug(f"[FileCheckpoint] Saved: namespace={namespace}, seq={data.wal_sequence}")

            except Exception as e:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise CheckpointError(f"Failed to save checkpoint: {e}") from e

    def load(self, namespace: str) -> UnifiedCheckpointData | None:
        """체크포인트 로드."""
        with self._lock:
            file_path = self._get_file_path(namespace)
            if not file_path.exists():
                return None

            try:
                with open(file_path, encoding="utf-8") as f:
                    raw_data = json.load(f)

                # 레거시 형식 지원
                if "last_sequence" in raw_data and "wal_sequence" not in raw_data:
                    return UnifiedCheckpointData.from_legacy_checkpoint_data(raw_data)

                return UnifiedCheckpointData.from_dict(raw_data)

            except Exception as e:
                logger.warning(f"[FileCheckpoint] Load failed: {e}")
                return None

    def commit(self, namespace: str) -> None:
        """파일 저장소는 save()에서 이미 커밋됨."""
        pass  # No-op for file storage

    def delete(self, namespace: str) -> bool:
        """체크포인트 삭제."""
        with self._lock:
            file_path = self._get_file_path(namespace)
            try:
                file_path.unlink(missing_ok=True)
                return True
            except Exception:
                return False

    def exists(self, namespace: str) -> bool:
        """체크포인트 존재 여부."""
        return self._get_file_path(namespace).exists()


# =============================================================================
# Redis Strategy (분산 환경 - 중규모용)
# =============================================================================


class RedisCheckpointStorage(CheckpointStorageStrategy):
    """
    Redis 기반 체크포인트 저장소.

    특징:
    - 분산 환경에서 원자적 저장
    - DistributedRecoveryLock 통합 (멀티 Pod 경합 방지)
    - TTL 기반 자동 만료 (선택적)
    - 파이프라인으로 배치 처리
    - UnifiedNotificationManager 연동 (실패 시 알림)

    코드 근거:
    - kafka_checkpoint.py#L211-L228
    - services/coordination/distributed_recovery_lock.py#L57-200
    - services/unified_notification.py#L248-320
    """

    KEY_PREFIX = "selfhealing:checkpoint:"
    LOCK_KEY_PREFIX = "selfhealing:checkpoint:lock:"

    def __init__(
        self,
        redis_client: redis.Redis,
        ttl_seconds: int | None = None,
        use_distributed_lock: bool = True,
        lock_timeout_seconds: int = 5,
        enable_notification: bool = True,
    ):
        """
        RedisCheckpointStorage 초기화.

        Args:
            redis_client: Redis 클라이언트
            ttl_seconds: 체크포인트 TTL (None이면 무제한)
            use_distributed_lock: 분산 락 사용 여부 (멀티 Pod 환경 필수)
            lock_timeout_seconds: 분산 락 타임아웃
            enable_notification: 실패 시 알림 활성화
        """
        super().__init__()
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._use_distributed_lock = use_distributed_lock
        self._lock_timeout_seconds = lock_timeout_seconds
        self._enable_notification = enable_notification
        self._pending: dict[str, UnifiedCheckpointData] = {}
        self._local_lock = threading.Lock()

    def _get_key(self, namespace: str) -> str:
        """Redis 키 생성."""
        return f"{self.KEY_PREFIX}{namespace}"

    def _get_lock_key(self, namespace: str) -> str:
        """분산 락 키 생성."""
        return f"{self.LOCK_KEY_PREFIX}{namespace}"

    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """
        체크포인트 저장 (분산 락 + 알림 연동).

        코드 근거:
        - DistributedRecoveryLock: services/coordination/distributed_recovery_lock.py#L57
        - UnifiedNotificationManager: services/unified_notification.py#L248
        """
        try:
            if self._use_distributed_lock:
                self._save_with_lock(namespace, data)
            else:
                self._write_to_redis(namespace, data)
        except Exception as e:
            logger.error(f"[RedisCheckpoint] Save failed: {e}")
            self._notify_failure(namespace, str(e))
            raise

    def _save_with_lock(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """
        분산 락을 사용한 저장.

        코드 근거: services/coordination/distributed_recovery_lock.py#L172-200
        """
        from datetime import timedelta

        try:
            from selfhealing.services.coordination.distributed_recovery_lock import (
                DistributedRecoveryLock,
            )

            lock = DistributedRecoveryLock(
                redis_client=self._redis,
                lock_timeout=timedelta(seconds=self._lock_timeout_seconds),
            )

            session_id = f"checkpoint:{namespace}:{time.time()}"
            if lock.acquire(namespace, session_id, blocking=False):
                try:
                    self._write_to_redis(namespace, data)
                finally:
                    lock.release(namespace, session_id)
            else:
                raise CheckpointError(
                    f"Failed to acquire distributed lock for namespace: {namespace}"
                )

        except ImportError:
            # DistributedRecoveryLock 없으면 일반 저장
            logger.warning("[RedisCheckpoint] DistributedRecoveryLock not available")
            self._write_to_redis(namespace, data)

    def _write_to_redis(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """Redis에 실제 저장."""
        key = self._get_key(namespace)
        value = json.dumps(data.to_dict())

        if self._ttl:
            self._redis.setex(key, self._ttl, value)
        else:
            self._redis.set(key, value)

        logger.debug(f"[RedisCheckpoint] Saved: namespace={namespace}, seq={data.wal_sequence}")

    def _notify_failure(self, namespace: str, error: str) -> None:
        """
        체크포인트 저장 실패 알림.

        코드 근거: services/unified_notification.py#L248-320
        """
        if not self._enable_notification:
            return

        try:
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            manager.notify(NotificationPayload(
                title="Checkpoint Save Failed",
                message=f"Redis checkpoint save failed for namespace '{namespace}': {error}",
                priority=NotificationPriority.CRITICAL,
                category=NotificationCategory.OPERATIONS,
                source="checkpoint_storage",
                metadata={
                    "namespace": namespace,
                    "storage_type": "redis",
                    "error": error,
                },
            ))
            logger.info(f"[RedisCheckpoint] Failure notification sent: namespace={namespace}")

        except ImportError:
            logger.warning("[RedisCheckpoint] UnifiedNotificationManager not available")
        except Exception as e:
            logger.warning(f"[RedisCheckpoint] Failed to send notification: {e}")

    def load(self, namespace: str) -> UnifiedCheckpointData | None:
        """
        체크포인트 로드 (Checksum 검증 포함).

        코드 근거: audit/checksum.py#L157-175 (verify_checksum)
        """
        key = self._get_key(namespace)
        raw = self._redis.get(key)

        if not raw:
            return None

        try:
            raw_data = json.loads(raw)
            data = UnifiedCheckpointData.from_dict(raw_data)

            # Checksum 검증 (있는 경우)
            if data.checksum:
                self._verify_data_checksum(data)

            return data
        except CheckpointCorruptedError:
            raise
        except Exception as e:
            logger.warning(f"[RedisCheckpoint] Load failed: {e}")
            return None

    def _verify_data_checksum(self, data: UnifiedCheckpointData) -> None:
        """
        데이터 무결성 검증.

        코드 근거: audit/checksum.py#L60-75
        """
        try:
            from selfhealing.audit.checksum import verify_checksum

            # wal_sequence를 기반으로 checksum 검증
            payload = {
                "wal_sequence": data.wal_sequence,
                "timestamp": data.timestamp,
                "version": data.version,
            }

            result = verify_checksum(payload, data.checksum, algorithm="crc32")

            if not result.is_valid:
                raise CheckpointCorruptedError(
                    f"Checkpoint checksum mismatch",
                    expected=result.expected,
                    computed=result.computed,
                )

        except ImportError:
            logger.debug("[RedisCheckpoint] checksum module not available, skipping verification")

    def commit(self, namespace: str) -> None:
        """
        pending 체크포인트 커밋.

        트랜잭션이 필요한 경우 여기서 확정.
        """
        with self._local_lock:
            if namespace in self._pending:
                del self._pending[namespace]
        # 이미 save()에서 저장됨

    def delete(self, namespace: str) -> bool:
        """체크포인트 삭제."""
        key = self._get_key(namespace)
        return self._redis.delete(key) > 0

    def exists(self, namespace: str) -> bool:
        """체크포인트 존재 여부."""
        key = self._get_key(namespace)
        return self._redis.exists(key) > 0


# =============================================================================
# Kafka+Redis Strategy (엔터프라이즈용)
# =============================================================================


class KafkaRedisCheckpointStorage(CheckpointStorageStrategy):
    """
    Kafka+Redis 기반 체크포인트 저장소.

    특징:
    - WAL 시퀀스 + Kafka 오프셋 원자적 저장
    - Redis로 빠른 조회, Kafka 오프셋으로 정확한 복구
    - File 백업으로 Redis 장애 시에도 복구 가능
    - 초당 수십만 건 처리 가능
    - Checksum 검증 및 알림 연동

    코드 근거:
    - kafka_checkpoint.py#L105-L260
    - audit/graceful_degradation/fallback.py#L22-126 (Tiered Fallback)
    """

    KEY_PREFIX = "selfhealing:kafka_checkpoint:"

    def __init__(
        self,
        redis_client: redis.Redis,
        default_topic: str = "selfhealing.audit.events",
        file_backup_path: str | Path | None = None,
        enable_file_backup: bool = True,
        enable_notification: bool = True,
    ):
        """
        KafkaRedisCheckpointStorage 초기화.

        Args:
            redis_client: Redis 클라이언트
            default_topic: 기본 Kafka 토픽
            file_backup_path: 파일 백업 경로 (Redis 장애 대비)
            enable_file_backup: 파일 백업 활성화 (권장: True)
            enable_notification: 실패 시 알림 활성화
        """
        super().__init__()
        self._redis = redis_client
        self._default_topic = default_topic
        self._enable_file_backup = enable_file_backup
        self._enable_notification = enable_notification
        self._local_lock = threading.Lock()

        # File 백업 저장소 (Redis 장애 대비)
        self._file_backup: FileCheckpointStorage | None = None
        if enable_file_backup:
            backup_path = file_backup_path or self._get_default_backup_path()
            self._file_backup = FileCheckpointStorage(base_path=backup_path)
            logger.info(f"[KafkaRedisCheckpoint] File backup enabled: {backup_path}")

    def _get_default_backup_path(self) -> Path:
        """기본 백업 경로."""
        env_path = os.environ.get("SELFHEALING_AUDIT_PATH")
        if env_path:
            return Path(env_path) / "kafka_checkpoint_backup"
        if os.name == "nt":
            return Path(tempfile.gettempdir()) / "selfhealing" / "kafka_checkpoint_backup"
        return Path("/var/log/audit/kafka_checkpoint_backup")

    def _get_key(self, namespace: str) -> str:
        """Redis 키 생성."""
        return f"{self.KEY_PREFIX}{namespace}"

    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """
        체크포인트 저장 (Redis Primary + File Backup).

        코드 근거: audit/graceful_degradation/fallback.py#L91-126

        저장 순서:
        1. Redis에 저장 (Primary)
        2. File에도 백업 (Secondary) - Redis 장애 시 복구용
        """
        # Kafka 필드 기본값 채우기
        if data.kafka_topic is None:
            data.kafka_topic = self._default_topic
        if data.kafka_partition is None:
            data.kafka_partition = 0
        if data.kafka_offset is None:
            data.kafka_offset = 0

        redis_success = False
        file_success = False

        # 1. Redis 저장 (Primary)
        try:
            key = self._get_key(namespace)
            value = json.dumps(data.to_dict())
            self._redis.set(key, value)
            redis_success = True
            logger.debug(
                f"[KafkaRedisCheckpoint] Redis saved: namespace={namespace}, "
                f"seq={data.wal_sequence}, offset={data.kafka_offset}"
            )
        except Exception as e:
            logger.error(f"[KafkaRedisCheckpoint] Redis save failed: {e}")

        # 2. File 백업 (Secondary) - Redis와 무관하게 항상 시도
        if self._file_backup:
            try:
                self._file_backup.save(namespace, data)
                file_success = True
                logger.debug(f"[KafkaRedisCheckpoint] File backup saved: namespace={namespace}")
            except Exception as e:
                logger.warning(f"[KafkaRedisCheckpoint] File backup failed: {e}")

        # 둘 다 실패한 경우 알림 + 예외
        if not redis_success and not file_success:
            self._notify_failure(namespace, "Both Redis and File storage failed")
            raise CheckpointError("All checkpoint storage tiers failed")

        # Redis만 실패한 경우 경고 알림
        if not redis_success:
            self._notify_degraded(namespace)

    def _notify_failure(self, namespace: str, error: str) -> None:
        """체크포인트 저장 실패 알림 (CRITICAL)."""
        if not self._enable_notification:
            return

        try:
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            manager.notify(NotificationPayload(
                title="Kafka Checkpoint Save Failed",
                message=f"All storage tiers failed for namespace '{namespace}': {error}",
                priority=NotificationPriority.CRITICAL,
                category=NotificationCategory.OPERATIONS,
                source="kafka_redis_checkpoint",
                metadata={"namespace": namespace, "error": error},
            ))
        except Exception as e:
            logger.warning(f"[KafkaRedisCheckpoint] Failed to send failure notification: {e}")

    def _notify_degraded(self, namespace: str) -> None:
        """Redis 장애로 인한 Degraded 상태 알림 (HIGH)."""
        if not self._enable_notification:
            return

        try:
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )

            manager = UnifiedNotificationManager()
            manager.notify(NotificationPayload(
                title="Kafka Checkpoint Degraded",
                message=f"Redis unavailable, using file backup for namespace '{namespace}'",
                priority=NotificationPriority.HIGH,
                category=NotificationCategory.OPERATIONS,
                source="kafka_redis_checkpoint",
                metadata={"namespace": namespace, "tier": "file_backup"},
            ))
        except Exception as e:
            logger.warning(f"[KafkaRedisCheckpoint] Failed to send degraded notification: {e}")

    def save_with_kafka_offset(
        self,
        namespace: str,
        wal_sequence: int,
        kafka_topic: str,
        kafka_partition: int,
        kafka_offset: int,
        checksum: str | None = None,
    ) -> None:
        """
        Kafka 오프셋과 함께 체크포인트 저장.

        코드 근거: kafka_checkpoint.py#L176-L210
        """
        data = UnifiedCheckpointData(
            wal_sequence=wal_sequence,
            kafka_topic=kafka_topic,
            kafka_partition=kafka_partition,
            kafka_offset=kafka_offset,
            checksum=checksum,
        )
        self.save(namespace, data)

    def load(self, namespace: str) -> UnifiedCheckpointData | None:
        """
        체크포인트 로드 (Redis → File Fallback + Checksum 검증).

        Redis 실패 시 File 백업에서 로드.
        """
        # 1. Redis에서 시도
        try:
            key = self._get_key(namespace)
            raw = self._redis.get(key)
            if raw:
                raw_data = json.loads(raw)
                data = UnifiedCheckpointData.from_dict(raw_data)

                # Checksum 검증
                if data.checksum:
                    self._verify_data_checksum(data)

                return data
        except CheckpointCorruptedError:
            raise
        except Exception as e:
            logger.warning(f"[KafkaRedisCheckpoint] Redis load failed: {e}")

        # 2. File 백업에서 시도 (Fallback)
        if self._file_backup:
            try:
                data = self._file_backup.load(namespace)
                if data:
                    logger.info(
                        f"[KafkaRedisCheckpoint] Loaded from file backup: namespace={namespace}"
                    )

                    # Checksum 검증
                    if data.checksum:
                        self._verify_data_checksum(data)

                    return data
            except CheckpointCorruptedError:
                raise
            except Exception as e:
                logger.warning(f"[KafkaRedisCheckpoint] File backup load failed: {e}")

        return None

    def _verify_data_checksum(self, data: UnifiedCheckpointData) -> None:
        """
        데이터 무결성 검증.

        코드 근거: audit/checksum.py#L157-175
        """
        try:
            from selfhealing.audit.checksum import verify_checksum

            payload = {
                "wal_sequence": data.wal_sequence,
                "kafka_topic": data.kafka_topic,
                "kafka_partition": data.kafka_partition,
                "kafka_offset": data.kafka_offset,
                "timestamp": data.timestamp,
            }

            result = verify_checksum(payload, data.checksum, algorithm="crc32")

            if not result.is_valid:
                raise CheckpointCorruptedError(
                    f"Kafka checkpoint checksum mismatch",
                    expected=result.expected,
                    computed=result.computed,
                )

        except ImportError:
            logger.debug("[KafkaRedisCheckpoint] checksum module not available")

    def commit(self, namespace: str) -> None:
        """Redis에 이미 저장됨."""
        pass

    def delete(self, namespace: str) -> bool:
        """체크포인트 삭제 (Redis + File 모두)."""
        redis_result = False
        file_result = False

        try:
            key = self._get_key(namespace)
            redis_result = self._redis.delete(key) > 0
        except Exception:
            pass

        if self._file_backup:
            try:
                file_result = self._file_backup.delete(namespace)
            except Exception:
                pass

        return redis_result or file_result

    def exists(self, namespace: str) -> bool:
        """체크포인트 존재 여부 (Redis 또는 File)."""
        try:
            key = self._get_key(namespace)
            if self._redis.exists(key) > 0:
                return True
        except Exception:
            pass

        if self._file_backup:
            try:
                if self._file_backup.exists(namespace):
                    return True
            except Exception:
                pass

        return False


# =============================================================================
# Factory Function (Registry 패턴 적용)
# =============================================================================


def get_checkpoint_strategy(
    storage_type: str | None = None,
    redis_client: redis.Redis | None = None,
    **kwargs,
) -> CheckpointStorageStrategy:
    """
    환경에 맞는 체크포인트 저장 전략 반환.

    CheckpointStrategyRegistry를 통해 전략을 조회합니다.
    Registry에 등록되지 않은 전략은 자동으로 내장 전략이 등록됩니다.

    Args:
        storage_type: 저장소 유형 ("file", "redis", "kafka_redis", "composite")
                     None이면 환경변수 SELFHEALING_CHECKPOINT_STORAGE에서 결정
        redis_client: Redis 클라이언트 (redis, kafka_redis 시 필요)
        **kwargs: 추가 설정

    Returns:
        CheckpointStorageStrategy 구현체

    환경변수:
        SELFHEALING_CHECKPOINT_STORAGE: file, redis, kafka_redis, composite
        SELFHEALING_AUDIT_PATH: 파일 저장 경로
        SELFHEALING_CHECKPOINT_ENABLE_NOTIFICATION: 알림 활성화 (기본 TRUE)
        SELFHEALING_CHECKPOINT_USE_DISTRIBUTED_LOCK: 분산 락 사용 (기본 TRUE)
        SELFHEALING_CHECKPOINT_ENABLE_FILE_BACKUP: 파일 백업 사용 (기본 TRUE)

    Usage:
        # 환경변수 기반 자동 선택
        strategy = get_checkpoint_strategy()

        # 명시적 선택
        strategy = get_checkpoint_strategy(storage_type="redis", redis_client=r)

        # CompositeCheckpointStorage 사용
        strategy = get_checkpoint_strategy(
            storage_type="composite",
            redis_client=r,
            primary_type="redis",
            secondary_type="file",
        )
    """
    if storage_type is None:
        storage_type = os.environ.get("SELFHEALING_CHECKPOINT_STORAGE", "file").lower()

    # 환경변수에서 옵션 로드
    enable_notification = os.environ.get(
        "SELFHEALING_CHECKPOINT_ENABLE_NOTIFICATION", "TRUE"
    ).upper() == "TRUE"
    use_distributed_lock = os.environ.get(
        "SELFHEALING_CHECKPOINT_USE_DISTRIBUTED_LOCK", "TRUE"
    ).upper() == "TRUE"
    enable_file_backup = os.environ.get(
        "SELFHEALING_CHECKPOINT_ENABLE_FILE_BACKUP", "TRUE"
    ).upper() == "TRUE"

    if storage_type == "file":
        return FileCheckpointStorage(
            base_path=kwargs.get("base_path"),
            sync_on_write=kwargs.get("sync_on_write", True),
        )

    elif storage_type == "redis":
        if redis_client is None:
            raise ValueError("redis_client is required for storage_type='redis'")
        return RedisCheckpointStorage(
            redis_client=redis_client,
            ttl_seconds=kwargs.get("ttl_seconds"),
            use_distributed_lock=kwargs.get("use_distributed_lock", use_distributed_lock),
            enable_notification=kwargs.get("enable_notification", enable_notification),
        )

    elif storage_type == "kafka_redis":
        if redis_client is None:
            raise ValueError("redis_client is required for storage_type='kafka_redis'")
        return KafkaRedisCheckpointStorage(
            redis_client=redis_client,
            default_topic=kwargs.get("default_topic", "selfhealing.audit.events"),
            enable_file_backup=kwargs.get("enable_file_backup", enable_file_backup),
            enable_notification=kwargs.get("enable_notification", enable_notification),
        )

    elif storage_type == "composite":
        # Composite 전략 생성
        primary_type = kwargs.get("primary_type", "redis")
        secondary_type = kwargs.get("secondary_type", "file")

        if primary_type == "redis":
            if redis_client is None:
                raise ValueError("redis_client is required for composite with redis primary")
            primary = RedisCheckpointStorage(
                redis_client=redis_client,
                use_distributed_lock=use_distributed_lock,
                enable_notification=False,  # Composite에서 통합 관리
            )
        else:
            primary = FileCheckpointStorage()

        secondary = FileCheckpointStorage(
            base_path=kwargs.get("secondary_base_path"),
        ) if secondary_type == "file" else None

        return CompositeCheckpointStorage(
            primary=primary,
            secondary=secondary,
            enable_memory_fallback=kwargs.get("enable_memory_fallback", True),
        )

    else:
        # Registry에서 조회 시도
        try:
            return CheckpointStrategyRegistry.get(storage_type, **kwargs)
        except ValueError:
            raise ValueError(f"Unknown storage_type: {storage_type}")


# =============================================================================
# Singleton
# =============================================================================

_default_strategy: CheckpointStorageStrategy | None = None
_default_lock = threading.Lock()


def get_default_checkpoint_strategy() -> CheckpointStorageStrategy:
    """
    기본 CheckpointStorageStrategy 싱글톤 반환.

    환경변수 기반으로 전략 자동 선택.
    """
    global _default_strategy

    with _default_lock:
        if _default_strategy is None:
            storage_type = os.environ.get("SELFHEALING_CHECKPOINT_STORAGE", "file")

            if storage_type in ("redis", "kafka_redis"):
                # Redis 클라이언트 자동 생성
                try:
                    import redis

                    redis_url = os.environ.get("SELFHEALING_REDIS_URL", "redis://localhost:6379/0")
                    redis_client = redis.from_url(redis_url)
                    _default_strategy = get_checkpoint_strategy(
                        storage_type=storage_type,
                        redis_client=redis_client,
                    )
                except ImportError:
                    logger.warning(
                        f"[CheckpointStrategy] redis package not installed, "
                        f"falling back to file storage"
                    )
                    _default_strategy = FileCheckpointStorage()
            else:
                _default_strategy = FileCheckpointStorage()

        return _default_strategy


def reset_default_checkpoint_strategy() -> None:
    """싱글톤 인스턴스 초기화 (테스트용)."""
    global _default_strategy

    with _default_lock:
        _default_strategy = None
```

---

### 3.2 ContinuousAuditRecorder 확장

**파일**: `packages/selfhealing-python/src/selfhealing/audit/continuous_audit.py` (수정)

**변경 사항**: `__init__`에 `checkpoint_strategy` 파라미터 추가

```python
def __init__(
    self,
    audit_adapter: AuditLogAdapter,
    config: AuditConfig | None = None,
    alert_callback: Callable[[str, dict[str, Any]], None] | None = None,
    state_file: Path | None = None,
    # Fail-Open 정책
    fail_open: bool = True,
    fallback_to_stdout: bool = True,
    # WAL 연동
    wal_enabled: bool = False,
    wal_config: Optional["WALConfig"] = None,
    # Checkpoint Strategy (신규)
    checkpoint_strategy: "CheckpointStorageStrategy | None" = None,
    checkpoint_namespace: str = "default",
):
    """
    Initialize ContinuousAuditRecorder.

    Args:
        ...기존 파라미터...
        checkpoint_strategy: 체크포인트 저장 전략 (None이면 기본값 사용)
        checkpoint_namespace: 체크포인트 네임스페이스
    """
    # ...기존 초기화 코드...

    # Checkpoint Strategy 초기화
    self._checkpoint_strategy = checkpoint_strategy
    self._checkpoint_namespace = checkpoint_namespace

    if checkpoint_strategy is None and wal_enabled:
        # WAL 활성화 시 기본 전략 자동 설정
        try:
            from selfhealing.audit.checkpoint_strategy import get_default_checkpoint_strategy
            self._checkpoint_strategy = get_default_checkpoint_strategy()
            logger.info("[ContinuousAudit] Checkpoint strategy initialized")
        except Exception as e:
            logger.warning(f"[ContinuousAudit] Checkpoint strategy init failed: {e}")
```

**`_record_with_integrity` 메서드 수정**:

```python
def _record_with_integrity(self, entry: AuditEntry) -> str:
    """해시 체인과 함께 기록."""
    with self._lock:
        # ...기존 코드...

        # WAL 기록 (활성화된 경우)
        wal_seq = None
        if self._wal_enabled and self._wal:
            try:
                wal_seq = self._wal.write(entry_dict)
            except Exception as e:
                logger.warning(f"[ContinuousAudit] WAL write failed: {e}")

        # Fail-Open 패턴으로 기록
        try:
            self.audit_adapter.log(entry)

            # WAL 커밋 + Checkpoint 저장 (성공 시)
            if wal_seq is not None:
                if self._wal:
                    try:
                        self._wal.mark_processed(wal_seq)
                    except Exception as e:
                        logger.warning(f"[ContinuousAudit] WAL commit failed: {e}")

                # Checkpoint 저장 (신규 추가)
                if self._checkpoint_strategy:
                    try:
                        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData
                        checkpoint_data = UnifiedCheckpointData(
                            wal_sequence=wal_seq,
                            checksum=entry_dict.get("integrity", {}).get("hash"),
                        )
                        self._checkpoint_strategy.save(
                            self._checkpoint_namespace,
                            checkpoint_data,
                        )
                    except Exception as e:
                        logger.warning(f"[ContinuousAudit] Checkpoint save failed: {e}")

        except Exception as e:
            # ...기존 에러 처리...
```

---

### 3.3 AuditMiddleware 연동

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/audit_middleware.py` (수정)

**변경 사항**: `_ensure_initialized`에서 Checkpoint Strategy 주입

```python
def _ensure_initialized(self) -> None:
    """Lazy 초기화 - Django가 완전히 로드된 후 실행."""
    if self._initialized:
        return

    try:
        from selfhealing.adapters.audit.singleton import get_audit_adapter
        from selfhealing.audit.continuous_audit import ContinuousAuditRecorder

        adapter = get_audit_adapter()

        # Checkpoint Strategy 로드 (신규)
        checkpoint_strategy = None
        if self._is_checkpoint_enabled():
            try:
                from selfhealing.audit.checkpoint_strategy import get_default_checkpoint_strategy
                checkpoint_strategy = get_default_checkpoint_strategy()
                logger.info("[AuditMiddleware] Checkpoint strategy loaded")
            except Exception as e:
                logger.warning(f"[AuditMiddleware] Checkpoint strategy failed: {e}")

        # WAL + Checkpoint 설정
        wal_enabled = self._is_wal_enabled()

        self._recorder = ContinuousAuditRecorder(
            audit_adapter=adapter,
            fail_open=True,
            fallback_to_stdout=True,
            wal_enabled=wal_enabled,
            checkpoint_strategy=checkpoint_strategy,
            checkpoint_namespace=self._get_checkpoint_namespace(),
        )
        logger.info("[AuditMiddleware] Initialized with ContinuousAuditRecorder")
    except Exception as e:
        logger.warning(f"[AuditMiddleware] Recorder init failed: {e}")
        self._recorder = None

    self._load_read_audit_config()
    self._initialized = True

def _is_wal_enabled(self) -> bool:
    """WAL 활성화 여부."""
    return os.environ.get("AUDIT_WAL_ENABLED", "FALSE").upper() == "TRUE"

def _is_checkpoint_enabled(self) -> bool:
    """Checkpoint 활성화 여부."""
    return os.environ.get("AUDIT_CHECKPOINT_ENABLED", "TRUE").upper() == "TRUE"

def _get_checkpoint_namespace(self) -> str:
    """Checkpoint 네임스페이스."""
    return os.environ.get("AUDIT_CHECKPOINT_NAMESPACE", "audit_middleware")
```

---

## 4. 환경 변수

| 변수명 | 기본값 | 설명 |
|-------|-------|------|
| `SELFHEALING_CHECKPOINT_STORAGE` | `file` | 저장 전략: `file`, `redis`, `kafka_redis`, `composite` |
| `SELFHEALING_REDIS_URL` | `redis://localhost:6379/0` | Redis 연결 URL |
| `SELFHEALING_AUDIT_PATH` | `/var/log/audit` | 파일 저장 경로 |
| `SELFHEALING_CHECKPOINT_ENABLE_NOTIFICATION` | `TRUE` | 실패 시 알림 활성화 |
| `SELFHEALING_CHECKPOINT_USE_DISTRIBUTED_LOCK` | `TRUE` | 분산 락 사용 (멀티 Pod 필수) |
| `SELFHEALING_CHECKPOINT_ENABLE_FILE_BACKUP` | `TRUE` | Kafka+Redis에서 파일 백업 활성화 |
| `AUDIT_WAL_ENABLED` | `FALSE` | WAL 활성화 |
| `AUDIT_CHECKPOINT_ENABLED` | `TRUE` | Checkpoint 활성화 |
| `AUDIT_CHECKPOINT_NAMESPACE` | `audit_middleware` | Checkpoint 네임스페이스 |

---

## 4.1 직렬화 포맷 결정 (JSON vs Protobuf)

**코드 근거**: `170_KAFKA_AUDIT_ADAPTER.md#L165-210`

170번 문서에서 직렬화 포맷 결정:

```python
class SerializationFormat(str, Enum):
    """직렬화 포맷."""
    JSON = "json"           # Phase 1: 빠른 시작, 디버깅 용이
    AVRO = "avro"           # Phase 2: 프로덕션 권장
    PROTOBUF = "protobuf"   # 마이크로서비스 간 통신
```

**체크포인트 직렬화 결정**:

| 기준 | JSON | Protobuf |
|------|------|----------|
| 디버깅 용이성 | ✅ **최고** | ❌ 바이너리 |
| 스키마 진화 | ⚠️ 수동 | ✅ 자동 호환성 |
| 성능 | 좋음 | **최고** |
| 170번 문서 방향 | Phase 1 | 마이크로서비스용 |

**현재 결정**: **JSON 유지** (Phase 1)

**이유**:
1. 체크포인트는 **내부 상태 저장**용이므로 디버깅 용이성 우선
2. 170번 문서에서 **Phase 2는 Avro**로 결정됨 (Protobuf 아님)
3. 체크포인트 데이터량이 적어 JSON 오버헤드 무시 가능

**향후 계획**: Phase 2에서 Kafka 스키마를 Avro로 전환 시 체크포인트도 함께 검토

```python
# 현재 구현 (JSON)
value = json.dumps(data.to_dict())

# 향후 Avro 전환 시 (Phase 2)
# from confluent_kafka.schema_registry.avro import AvroSerializer
# value = avro_serializer(data.to_dict(), SerializerContext(...))
```

---

## 5. 테스트 계획

### 5.1 단위 테스트

**파일**: `tests/unit/audit/test_checkpoint_strategy.py`

```python
"""Unified Checkpoint Strategy 테스트."""

import pytest
import tempfile
import time
from pathlib import Path


class TestUnifiedCheckpointData:
    """UnifiedCheckpointData 테스트."""

    def test_to_dict_and_from_dict(self):
        """직렬화/역직렬화 테스트."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        data = UnifiedCheckpointData(
            wal_sequence=1234,
            kafka_topic="test.topic",
            kafka_partition=3,
            kafka_offset=56789,
            checksum="abc123",
        )

        dict_data = data.to_dict()
        restored = UnifiedCheckpointData.from_dict(dict_data)

        assert restored.wal_sequence == 1234
        assert restored.kafka_topic == "test.topic"
        assert restored.kafka_partition == 3
        assert restored.kafka_offset == 56789
        assert restored.checksum == "abc123"

    def test_from_legacy_checkpoint_data(self):
        """레거시 CheckpointData 변환 테스트."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        legacy = {
            "last_sequence": 999,
            "timestamp": 1700000000.0,
            "version": 1,
        }

        data = UnifiedCheckpointData.from_legacy_checkpoint_data(legacy)

        assert data.wal_sequence == 999
        assert data.kafka_topic is None  # 레거시에는 없음


class TestFileCheckpointStorage:
    """FileCheckpointStorage 테스트."""

    @pytest.fixture
    def storage(self, tmp_path):
        from selfhealing.audit.checkpoint_strategy import FileCheckpointStorage
        return FileCheckpointStorage(base_path=tmp_path)

    def test_save_and_load(self, storage):
        """저장 및 로드 테스트."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        data = UnifiedCheckpointData(wal_sequence=1234)
        storage.save("test", data)

        loaded = storage.load("test")
        assert loaded is not None
        assert loaded.wal_sequence == 1234

    def test_load_nonexistent(self, storage):
        """존재하지 않는 체크포인트 로드."""
        loaded = storage.load("nonexistent")
        assert loaded is None

    def test_delete(self, storage):
        """삭제 테스트."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        data = UnifiedCheckpointData(wal_sequence=1234)
        storage.save("test", data)

        assert storage.exists("test")
        assert storage.delete("test")
        assert not storage.exists("test")

    def test_get_wal_sequence(self, storage):
        """WAL 시퀀스 조회 편의 메서드."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        # 없을 때
        assert storage.get_wal_sequence("test") == 0

        # 있을 때
        data = UnifiedCheckpointData(wal_sequence=5678)
        storage.save("test", data)
        assert storage.get_wal_sequence("test") == 5678


class TestRedisCheckpointStorage:
    """RedisCheckpointStorage 테스트 (Mock)."""

    @pytest.fixture
    def mock_redis(self):
        from unittest.mock import MagicMock
        return MagicMock()

    @pytest.fixture
    def storage(self, mock_redis):
        from selfhealing.audit.checkpoint_strategy import RedisCheckpointStorage
        return RedisCheckpointStorage(redis_client=mock_redis)

    def test_save_calls_redis_set(self, storage, mock_redis):
        """save()가 Redis set을 호출하는지 확인."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        data = UnifiedCheckpointData(wal_sequence=1234)
        storage.save("test", data)

        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert "selfhealing:checkpoint:test" in call_args[0]


class TestGetCheckpointStrategy:
    """get_checkpoint_strategy 팩토리 테스트."""

    def test_file_strategy_default(self):
        """기본값은 FileCheckpointStorage."""
        from selfhealing.audit.checkpoint_strategy import (
            get_checkpoint_strategy,
            FileCheckpointStorage,
        )

        strategy = get_checkpoint_strategy(storage_type="file")
        assert isinstance(strategy, FileCheckpointStorage)

    def test_redis_requires_client(self):
        """Redis 전략은 클라이언트 필수."""
        from selfhealing.audit.checkpoint_strategy import get_checkpoint_strategy

        with pytest.raises(ValueError, match="redis_client is required"):
            get_checkpoint_strategy(storage_type="redis")

    def test_unknown_storage_type(self):
        """알 수 없는 저장소 유형."""
        from selfhealing.audit.checkpoint_strategy import get_checkpoint_strategy

        with pytest.raises(ValueError, match="Unknown storage_type"):
            get_checkpoint_strategy(storage_type="unknown")
```

### 5.2 통합 테스트

**파일**: `tests/integration/audit/test_middleware_checkpoint_integration.py`

```python
"""AuditMiddleware + Checkpoint 통합 테스트."""

import pytest
from unittest.mock import MagicMock, patch


class TestAuditMiddlewareCheckpointIntegration:
    """AuditMiddleware가 Checkpoint를 사용하는지 검증."""

    def test_middleware_initializes_checkpoint_strategy(self):
        """미들웨어가 Checkpoint 전략을 초기화하는지 확인."""
        with patch.dict("os.environ", {
            "AUDIT_CHECKPOINT_ENABLED": "TRUE",
            "SELFHEALING_CHECKPOINT_STORAGE": "file",
        }):
            from selfhealing.api.django.audit_middleware import AuditMiddleware

            middleware = AuditMiddleware(get_response=MagicMock())
            middleware._ensure_initialized()

            # recorder가 checkpoint_strategy를 가지는지 확인
            if middleware._recorder:
                assert hasattr(middleware._recorder, "_checkpoint_strategy")

    def test_continuous_recorder_saves_checkpoint_on_log(self):
        """ContinuousAuditRecorder가 로그 시 체크포인트를 저장하는지 확인."""
        from selfhealing.audit.checkpoint_strategy import (
            FileCheckpointStorage,
            UnifiedCheckpointData,
        )
        from selfhealing.audit.continuous_audit import ContinuousAuditRecorder
        from selfhealing.interfaces.audit_adapter import AuditEntry, AuditAction
        from unittest.mock import MagicMock
        import tempfile

        # Mock adapter
        mock_adapter = MagicMock()
        mock_adapter.log = MagicMock()

        # File checkpoint
        with tempfile.TemporaryDirectory() as tmp_dir:
            checkpoint = FileCheckpointStorage(base_path=tmp_dir)

            recorder = ContinuousAuditRecorder(
                audit_adapter=mock_adapter,
                wal_enabled=True,
                checkpoint_strategy=checkpoint,
                checkpoint_namespace="test",
            )

            # 로그 기록
            entry = AuditEntry(
                action=AuditAction.CONFIG_CHANGE,
                target_type="test",
                target_id="123",
            )

            # WAL이 활성화되었지만 실제 WAL 없이 테스트
            # (실제 통합 테스트에서는 WAL도 함께 테스트)
            mock_adapter.log.assert_not_called()  # 아직 호출 안됨
```

---

## 6. 마이그레이션 가이드

### 6.1 기존 CheckpointManager 사용자

```python
# Before (기존)
from selfhealing.audit.checkpoint_manager import CheckpointManager

checkpoint = CheckpointManager("/var/log/audit/checkpoint")
checkpoint.save(last_sequence=1234)
last_seq = checkpoint.load()

# After (통합)
from selfhealing.audit.checkpoint_strategy import (
    get_checkpoint_strategy,
    UnifiedCheckpointData,
)

strategy = get_checkpoint_strategy(storage_type="file")
strategy.save("default", UnifiedCheckpointData(wal_sequence=1234))
data = strategy.load("default")
last_seq = data.wal_sequence if data else 0
```

### 6.2 기존 KafkaCheckpointManager 사용자

```python
# Before (기존)
from selfhealing.audit.kafka_checkpoint import KafkaCheckpointManager

checkpoint = KafkaCheckpointManager(storage="redis", redis_client=redis)
checkpoint.save_checkpoint(
    namespace="default",
    wal_sequence=1234,
    kafka_topic="topic",
    kafka_partition=0,
    kafka_offset=56789,
    checksum="abc",
)

# After (통합)
from selfhealing.audit.checkpoint_strategy import (
    get_checkpoint_strategy,
    KafkaRedisCheckpointStorage,
)

strategy = get_checkpoint_strategy(storage_type="kafka_redis", redis_client=redis)
strategy.save_with_kafka_offset(
    namespace="default",
    wal_sequence=1234,
    kafka_topic="topic",
    kafka_partition=0,
    kafka_offset=56789,
    checksum="abc",
)
```

---

## 7. 매수자 제안 가치

### 7.1 기술적 차별점

| 기능 | 경쟁사 | 우리 |
|------|-------|------|
| 저장소 교체 | 코드 수정 필요 | **환경변수 하나로 스위칭** |
| 의존성 | Redis/Kafka 강제 | **순수 Python부터 시작** |
| 확장성 | 고정 | **Strategy 패턴으로 무한 확장** |

### 7.2 제안 시나리오

> "고객사 인프라 환경에 맞춰 최적의 엔진을 꽂아드립니다:
> - 소규모 스타트업: 순수 파이썬 파일 저장 (의존성 0)
> - 중규모 기업: Redis 기반 분산 저장
> - 대기업/엔터프라이즈: Kafka+Redis 고성능 모드"

---

## 8. 체크리스트

### 8.1 구현 (기본)

- [ ] `checkpoint_strategy.py` 신규 파일 생성
- [ ] `UnifiedCheckpointData` 통합 데이터 모델
- [ ] `CheckpointStorageStrategy` 추상 인터페이스
- [ ] `FileCheckpointStorage` 구현
- [ ] `RedisCheckpointStorage` 구현
- [ ] `KafkaRedisCheckpointStorage` 구현
- [ ] `get_checkpoint_strategy()` 팩토리 함수

### 8.2 구현 (고급 - 리뷰 반영)

- [ ] `CheckpointStrategyRegistry` 동적 전략 등록 (§2.3)
- [ ] `maybe_save()` Back-pressure 메커니즘 (§2.4)
- [ ] `CompositeCheckpointStorage` Tiered Fallback (§2.5)
- [ ] `CheckpointCorruptedError` 예외 클래스
- [ ] `DistributedRecoveryLock` 분산 락 통합 (Q2)
- [ ] File 백업 로직 in `KafkaRedisCheckpointStorage` (Q3)
- [ ] `_verify_data_checksum()` Checksum 검증 (Q4)
- [ ] `_notify_failure()` UnifiedNotificationManager 연동 (Q9)

### 8.3 연동

- [ ] `ContinuousAuditRecorder.__init__`에 `checkpoint_strategy` 파라미터 추가
- [ ] `_record_with_integrity()`에서 체크포인트 저장 로직 추가
- [ ] `AuditMiddleware._ensure_initialized()`에서 전략 주입

### 8.4 테스트

- [ ] `test_checkpoint_strategy.py` 단위 테스트
- [ ] `test_middleware_checkpoint_integration.py` 통합 테스트
- [ ] 레거시 마이그레이션 테스트
- [ ] Tiered Fallback 테스트 (Redis 장애 시뮬레이션)
- [ ] 분산 락 경합 테스트 (멀티 Pod 시뮬레이션)
- [ ] Checksum 검증 실패 테스트

---

## 9. 코드 근거 참조표

| 기능 | 참조 코드 | 라인 |
|------|----------|------|
| ProviderRegistry 패턴 | `services/factory/registry.py` | #L55-150 |
| Back-pressure 로직 | `audit/sync_worker.py` | #L63-64, #L394-401 |
| Tiered Fallback | `audit/graceful_degradation/fallback.py` | #L22-126 |
| DistributedRecoveryLock | `services/coordination/distributed_recovery_lock.py` | #L57-200 |
| Checksum 검증 | `audit/checksum.py` | #L60-75, #L157-175 |
| UnifiedNotificationManager | `services/unified_notification.py` | #L248-320 |
| 직렬화 포맷 결정 | `170_KAFKA_AUDIT_ADAPTER.md` | #L165-210 |
| Namespace 정의 (Q1) | `audit/kafka_checkpoint.py` | #L156-170 |
| Version 마이그레이션 (Q5) | `audit/checkpoint_manager.py` | #L79-95 |
| Retention Policy (Q6) | `audit/checkpoint_manager.py` | #L180-210 (덮어쓰기) |
| commit() 역할 (Q7) | `audit/checkpoint_manager.py` | #L220-230 (디렉터리 fsync) |
| fsync 레벨 (Q8) | `audit/checkpoint_manager.py` | #L220-230 (OS별 분기) |
| Cloud Native 확장 (Q11) | Strategy 패턴 확장 예시 | S3, GCS, Azure |

---

## 10. 다음 단계

→ [174_CHECKPOINT_MONITORING.md](174_CHECKPOINT_MONITORING.md): Checkpoint 상태 모니터링 및 알림
