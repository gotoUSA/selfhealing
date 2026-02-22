#!/usr/bin/env python
"""
Kafka + WAL 통합 데모: 데이터 유실 0% 증명.

Kafka 클러스터 완전 중단 후 100% 복구를 시연합니다.

시연 시나리오:
1. 이벤트 1000개 생성
2. Kafka 클러스터 중단 (500개 전송 후)
3. 나머지 500개 → WAL에 보관
4. Kafka 클러스터 복구
5. WAL에서 500개 자동 재전송
6. 최종 확인: 1000개 모두 도착

Usage:
    python -m selfhealing.scripts.demo_zero_data_loss

    # Mock 모드 (Kafka 없이 테스트)
    python -m selfhealing.scripts.demo_zero_data_loss --mock
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import structlog

logger = structlog.get_logger()


def _configure_demo_logging() -> None:
    """데모 스크립트 전용 로깅 설정. __main__에서만 호출."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


class DemoStats:
    """데모 통계."""

    def __init__(self):
        self.kafka_sent = 0
        self.kafka_failed = 0
        self.wal_written = 0
        self.wal_replayed = 0
        self.total_events = 0


def create_audit_entry(index: int) -> dict[str, Any]:
    """테스트용 감사 이벤트 생성."""
    return {
        "id": f"demo-event-{index}",
        "action": "DEMO_EVENT",
        "target_type": "demo",
        "target_id": str(index),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": {"index": index, "demo": True},
    }


def run_demo_with_mock():
    """Mock 모드 데모 (Kafka 없이 실행)."""
    from selfhealing.audit.wal import WALConfig, WriteAheadLog
    from selfhealing.interfaces.audit_adapter import AuditEntry

    stats = DemoStats()
    stats.total_events = 1000

    with tempfile.TemporaryDirectory() as tmp_dir:
        # WAL 설정
        wal_config = WALConfig(
            wal_dir=tmp_dir,
            sync_on_write=True,
            max_files=100,
        )
        wal = WriteAheadLog(config=wal_config)

        # Mock Kafka Producer
        mock_producer = MagicMock()
        kafka_online = True

        def mock_produce(**kwargs):
            nonlocal kafka_online
            if not kafka_online:
                raise Exception("Kafka cluster is down")
            stats.kafka_sent += 1

        mock_producer.produce = mock_produce
        mock_producer.flush = MagicMock(return_value=0)

        print("\n" + "=" * 60)
        print("🚀 PHASE 1: 정상 전송 (이벤트 1-500)")
        print("=" * 60)

        # Phase 1: 정상 전송 (500개)
        for i in range(1, 501):
            entry_dict = create_audit_entry(i)

            # WAL 기록 (항상)
            seq = wal.write(entry_dict)
            stats.wal_written += 1

            # Kafka 전송
            try:
                mock_producer.produce(
                    topic="selfhealing.audit.events",
                    value=json.dumps(entry_dict).encode("utf-8"),
                )
            except Exception:
                stats.kafka_failed += 1

            if i % 100 == 0:
                print(f"  ✅ {i}개 전송 완료 (Kafka: {stats.kafka_sent}, WAL: {stats.wal_written})")

        print(f"\n📊 Phase 1 결과: Kafka={stats.kafka_sent}, WAL={stats.wal_written}")

        print("\n" + "=" * 60)
        print("🔴 PHASE 2: Kafka 클러스터 중단!")
        print("=" * 60)
        kafka_online = False
        time.sleep(0.5)  # 시각적 효과

        print("\n" + "=" * 60)
        print("📝 PHASE 3: WAL 폴백 (이벤트 501-1000)")
        print("=" * 60)

        # Phase 3: WAL 폴백 (500개)
        for i in range(501, 1001):
            entry_dict = create_audit_entry(i)

            # WAL 기록 (항상)
            seq = wal.write(entry_dict)
            stats.wal_written += 1

            # Kafka 전송 시도 (실패)
            try:
                mock_producer.produce(
                    topic="selfhealing.audit.events",
                    value=json.dumps(entry_dict).encode("utf-8"),
                )
            except Exception:
                stats.kafka_failed += 1

            if i % 100 == 0:
                print(f"  📦 {i}개 기록 (WAL에 보관 중...)")

        print(f"\n📊 Phase 3 결과: Kafka 실패={stats.kafka_failed}, WAL={stats.wal_written}")
        print(f"📦 WAL에 미전송 이벤트 보관 중: {stats.wal_written - stats.kafka_sent}개")

        print("\n" + "=" * 60)
        print("🟢 PHASE 4: Kafka 클러스터 복구!")
        print("=" * 60)
        kafka_online = True
        time.sleep(0.5)  # 시각적 효과

        print("\n" + "=" * 60)
        print("🔄 PHASE 5: WAL에서 재전송")
        print("=" * 60)

        # Phase 5: WAL에서 미전송 이벤트 재전송
        unprocessed = wal.recover_unprocessed(last_processed_seq=500)
        print(f"  📋 WAL에서 {len(unprocessed)}개 미처리 이벤트 발견")

        for entry in unprocessed:
            try:
                mock_producer.produce(
                    topic="selfhealing.audit.events",
                    value=json.dumps(entry.data).encode("utf-8"),
                )
                stats.wal_replayed += 1
            except Exception:
                pass

            if stats.wal_replayed % 100 == 0:
                print(f"  🔄 {stats.wal_replayed}개 재전송 완료")

        print(f"\n📊 Phase 5 결과: WAL에서 {stats.wal_replayed}개 재전송")

        print("\n" + "=" * 60)
        print("🎉 FINAL RESULT: 데이터 유실 0% 증명!")
        print("=" * 60)

        total_delivered = stats.kafka_sent + stats.wal_replayed
        print(
            f"""
        📈 최종 통계:
        ─────────────────────────────────
        총 이벤트 수:      {stats.total_events}
        Kafka 직접 전송:   {stats.kafka_sent}
        WAL 재전송:        {stats.wal_replayed}
        ─────────────────────────────────
        총 전달 완료:      {total_delivered}
        데이터 유실:       {stats.total_events - total_delivered}
        ─────────────────────────────────
        """
        )

        if total_delivered == stats.total_events:
            print("✅ SUCCESS: 모든 이벤트가 정상 전달되었습니다!")
            return 0
        else:
            print(f"❌ FAILURE: {stats.total_events - total_delivered}개 이벤트 유실!")
            return 1


def run_demo_with_kafka():
    """실제 Kafka 연동 데모."""
    print("⚠️ 실제 Kafka 연동 데모는 Kafka 클러스터가 필요합니다.")
    print("  Mock 모드로 실행하려면: --mock 옵션을 사용하세요.")
    print()
    print("Kafka 클러스터 설정:")
    print("  - SELFHEALING_KAFKA_AUDIT_BOOTSTRAP_SERVERS=localhost:9092")
    print("  - SELFHEALING_KAFKA_AUDIT_TOPIC=selfhealing.audit.events")
    print()

    try:
        from selfhealing.adapters.audit.kafka_adapter import KafkaAuditAdapter
        from selfhealing.settings.kafka import KafkaAuditSettings

        settings = KafkaAuditSettings()
        print(f"📡 Kafka 브로커: {settings.bootstrap_servers}")
        print(f"📋 토픽: {settings.topic}")

        # 실제 연동 시도
        adapter = KafkaAuditAdapter(settings=settings)
        print("✅ Kafka Producer 초기화 성공")

        # 간단한 테스트 메시지
        from selfhealing.interfaces.audit_adapter import AuditEntry

        entry = AuditEntry(
            action="DEMO_TEST",
            target_type="demo",
            target_id="test",
        )
        adapter.log(entry)
        adapter.flush(timeout=5.0)
        print("✅ 테스트 메시지 전송 성공")

        adapter.close()
        return 0

    except ImportError as e:
        print(f"❌ confluent-kafka 미설치: {e}")
        print("  설치: pip install 'selfhealing[kafka]'")
        return 1
    except Exception as e:
        print(f"❌ Kafka 연결 실패: {e}")
        return 1


def main():
    """데모 메인 함수."""
    parser = argparse.ArgumentParser(
        description="Kafka + WAL 통합 데모: 데이터 유실 0% 증명",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Mock 모드로 실행 (Kafka 없이 테스트)",
    )
    args = parser.parse_args()

    print()
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  Kafka + WAL 통합 데모: 데이터 유실 0% 증명                  ║")
    print("║  Self-Healing Audit System                                  ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print()

    if args.mock:
        print("🔧 Mock 모드로 실행합니다 (Kafka 없이 테스트)")
        return run_demo_with_mock()
    else:
        return run_demo_with_kafka()


if __name__ == "__main__":
    _configure_demo_logging()
    sys.exit(main())
