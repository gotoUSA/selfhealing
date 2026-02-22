#!/usr/bin/env python3
"""
Audit Log Export CLI Tool.

감사 로그를 외부 시스템으로 내보내기 위한 CLI 도구.

사용 시나리오:
1. 평소: 로컬 JSONL 파일에 저장 (비침투)
2. 감사 시점: 이 도구로 S3/Parquet/외부 시스템으로 전송

비침투 원칙:
- 메인 앱은 로컬 파일만 사용
- 외부 전송은 이 도구로 필요 시점에만 수행
- 감사관이 직접 실행하거나, cron/CI에서 주기적 실행

사용법:
    # 기본: JSONL을 표준출력으로
    python -m selfhealing.audit.export --input /var/log/audit/*.jsonl

    # S3로 내보내기
    python -m selfhealing.audit.export --input /var/log/audit/*.jsonl --target s3 --bucket my-bucket

    # Parquet으로 변환 (분석용)
    python -m selfhealing.audit.export --input /var/log/audit/*.jsonl --format parquet --output audit.parquet

    # 특정 기간만 추출
    python -m selfhealing.audit.export --input /var/log/audit/*.jsonl --start 2025-01-01 --end 2025-01-31
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

import structlog

logger = structlog.get_logger()


class ExportFormat(str, Enum):
    """내보내기 형식."""

    JSONL = "jsonl"  # JSON Lines (기본)
    JSON = "json"  # JSON Array
    CSV = "csv"  # CSV (감사관 친화적)
    PARQUET = "parquet"  # Apache Parquet (분석용)


class ExportTarget(str, Enum):
    """내보내기 대상."""

    STDOUT = "stdout"  # 표준출력
    FILE = "file"  # 로컬 파일
    S3 = "s3"  # AWS S3
    HTTP = "http"  # HTTP POST


@dataclass
class ExportOptions:
    """내보내기 옵션."""

    # 입력
    input_paths: list[str]

    # 필터링
    start_time: datetime | None = None
    end_time: datetime | None = None
    actions: list[str] | None = None
    actor_ids: list[str] | None = None

    # 출력
    format: ExportFormat = ExportFormat.JSONL
    target: ExportTarget = ExportTarget.STDOUT
    output_path: str | None = None

    # S3 옵션
    s3_bucket: str | None = None
    s3_prefix: str = "audit-export/"
    s3_region: str = "ap-northeast-2"

    # HTTP 옵션
    http_endpoint: str | None = None
    http_headers: dict[str, str] | None = None

    # 무결성 검증
    verify_integrity: bool = True

    # 기타
    verbose: bool = False


@dataclass
class ExportStats:
    """내보내기 통계."""

    total_files: int = 0
    total_entries: int = 0
    filtered_entries: int = 0
    exported_entries: int = 0
    integrity_errors: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None


class AuditExporter:
    """감사 로그 내보내기 도구."""

    def __init__(self, options: ExportOptions):
        self._options = options
        self._stats = ExportStats()

    def export(self) -> ExportStats:
        """내보내기 실행."""
        self._stats.start_time = datetime.now(timezone.utc)

        # 입력 파일 수집
        input_files = self._collect_input_files()
        self._stats.total_files = len(input_files)

        if not input_files:
            logger.warning("audit_export.no_input_files")
            self._stats.end_time = datetime.now(timezone.utc)
            return self._stats

        # 엔트리 읽기 및 필터링
        entries = self._read_and_filter_entries(input_files)

        # 무결성 검증 (옵션)
        if self._options.verify_integrity:
            entries = self._verify_integrity(entries)

        # 내보내기
        self._export_entries(entries)

        self._stats.end_time = datetime.now(timezone.utc)
        return self._stats

    def _collect_input_files(self) -> list[Path]:
        """입력 파일 수집."""
        files = []
        for pattern in self._options.input_paths:
            matched = glob.glob(pattern, recursive=True)
            for path_str in matched:
                path = Path(path_str)
                if path.is_file() and path.suffix in (".jsonl", ".json", ".log"):
                    files.append(path)
        return sorted(files)

    def _read_and_filter_entries(
        self, input_files: list[Path]
    ) -> Iterator[dict[str, Any]]:
        """엔트리 읽기 및 필터링."""
        for file_path in input_files:
            try:
                with open(file_path, encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue

                        self._stats.total_entries += 1

                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError as e:
                            logger.warning(
                                "invalid_json",
                                file_path=file_path,
                                error=e,
                            )
                            continue

                        if self._matches_filters(entry):
                            self._stats.filtered_entries += 1
                            yield entry

            except Exception as e:
                logger.exception(
                    "failed_read",
                    file_path=file_path,
                    error=e,
                )

    def _matches_filters(self, entry: dict[str, Any]) -> bool:
        """필터 조건 확인."""
        # 시간 필터
        if self._options.start_time or self._options.end_time:
            timestamp_str = entry.get("timestamp")
            if timestamp_str:
                try:
                    # ISO format 파싱
                    if timestamp_str.endswith("Z"):
                        timestamp_str = timestamp_str[:-1] + "+00:00"
                    timestamp = datetime.fromisoformat(timestamp_str)

                    if (
                        self._options.start_time
                        and timestamp < self._options.start_time
                    ):
                        return False
                    if self._options.end_time and timestamp > self._options.end_time:
                        return False
                except ValueError:
                    pass

        # 액션 필터
        if self._options.actions:
            action = entry.get("action")
            if action not in self._options.actions:
                return False

        # Actor 필터
        if self._options.actor_ids:
            actor_id = entry.get("actor_id")
            if actor_id not in self._options.actor_ids:
                return False

        return True

    def _verify_integrity(
        self, entries: Iterator[dict[str, Any]]
    ) -> Iterator[dict[str, Any]]:
        """무결성 검증 (해시 체인)."""
        prev_hash = None

        for entry in entries:
            # 체크섬 검증
            checksum = entry.get("checksum")
            prev_hash_in_entry = entry.get("prev_hash")

            if prev_hash is not None and prev_hash_in_entry:
                if prev_hash != prev_hash_in_entry:
                    self._stats.integrity_errors += 1
                    logger.warning(
                        "hash_chain_broken_expected",
                        entry=entry.get('audit_id'),
                        prev_hash=prev_hash,
                        prev_hash_in_entry=prev_hash_in_entry,
                    )

            prev_hash = checksum
            yield entry

    def _export_entries(self, entries: Iterator[dict[str, Any]]) -> None:
        """엔트리 내보내기."""
        target = self._options.target

        if target == ExportTarget.STDOUT:
            self._export_to_stdout(entries)
        elif target == ExportTarget.FILE:
            self._export_to_file(entries)
        elif target == ExportTarget.S3:
            self._export_to_s3(entries)
        elif target == ExportTarget.HTTP:
            self._export_to_http(entries)

    def _export_to_stdout(self, entries: Iterator[dict[str, Any]]) -> None:
        """표준출력으로 내보내기."""
        self._write_entries(entries, sys.stdout)

    def _export_to_file(self, entries: Iterator[dict[str, Any]]) -> None:
        """파일로 내보내기."""
        output_path = self._options.output_path
        if not output_path:
            raise ValueError("--output is required for file target")

        with open(output_path, "w", encoding="utf-8") as f:
            self._write_entries(entries, f)

    def _write_entries(self, entries: Iterator[dict[str, Any]], output: TextIO) -> None:
        """엔트리 쓰기 (형식별)."""
        format_type = self._options.format

        if format_type == ExportFormat.JSONL:
            for entry in entries:
                output.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
                self._stats.exported_entries += 1

        elif format_type == ExportFormat.JSON:
            entries_list = list(entries)
            self._stats.exported_entries = len(entries_list)
            json.dump(entries_list, output, default=str, ensure_ascii=False, indent=2)
            output.write("\n")

        elif format_type == ExportFormat.CSV:
            import csv

            entries_list = list(entries)
            if not entries_list:
                return

            # CSV 헤더 추출
            headers = set()
            for entry in entries_list:
                headers.update(entry.keys())
            headers = sorted(headers)

            writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for entry in entries_list:
                writer.writerow(
                    {k: str(v) if v is not None else "" for k, v in entry.items()}
                )
                self._stats.exported_entries += 1

        elif format_type == ExportFormat.PARQUET:
            raise NotImplementedError(
                "Parquet format requires pyarrow. "
                "Install with: pip install pyarrow\n"
                "Then use export_to_parquet() method directly."
            )

    def _export_to_s3(self, entries: Iterator[dict[str, Any]]) -> None:
        """S3로 내보내기."""
        if not self._options.s3_bucket:
            raise ValueError("--s3-bucket is required for S3 target")

        # 임시 파일에 쓰기
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name
            self._write_entries(entries, f)

        try:
            # S3 업로드 (boto3 필요)
            self._upload_to_s3(temp_path)
        finally:
            Path(temp_path).unlink()

    def _upload_to_s3(self, file_path: str) -> None:
        """S3에 파일 업로드."""
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "S3 export requires boto3. Install with: pip install boto3"
            )

        s3 = boto3.client("s3", region_name=self._options.s3_region)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        key = f"{self._options.s3_prefix}audit_export_{timestamp}.jsonl"

        s3.upload_file(file_path, self._options.s3_bucket, key)
        logger.info(
            "uploaded",
            self=self._options.s3_bucket,
            key=key,
        )

    def _export_to_http(self, entries: Iterator[dict[str, Any]]) -> None:
        """HTTP로 내보내기."""
        import urllib.error
        import urllib.request

        if not self._options.http_endpoint:
            raise ValueError("--http-endpoint is required for HTTP target")

        entries_list = list(entries)
        self._stats.exported_entries = len(entries_list)

        data = json.dumps(entries_list, default=str, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            **(self._options.http_headers or {}),
        }

        req = urllib.request.Request(
            self._options.http_endpoint,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                logger.info(
                    "http_export_completed",
                    response=response.status,
                )
        except urllib.error.URLError as e:
            raise RuntimeError(f"HTTP export failed: {e}") from e

    def export_to_parquet(
        self,
        input_files: list[Path],
        output_path: str,
    ) -> None:
        """
        Parquet으로 내보내기.

        Parquet은 분석 도구(Athena, Spark, Pandas)에서 효율적으로 쿼리 가능.
        대용량 감사 로그 분석에 적합.

        사용법:
            exporter = AuditExporter(options)
            exporter.export_to_parquet(
                input_files=[Path("/var/log/audit/*.jsonl")],
                output_path="audit.parquet"
            )
        """
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            raise ImportError(
                "Parquet export requires pyarrow. Install with: pip install pyarrow"
            )

        # 모든 엔트리 수집
        entries = []
        for file_path in self._collect_input_files():
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        entries.append(json.loads(line))

        if not entries:
            logger.warning("audit_export.no_entries")
            return

        # PyArrow Table 생성
        table = pa.Table.from_pylist(entries)

        # Parquet 쓰기
        pq.write_table(table, output_path, compression="snappy")
        logger.info(
            "exported_entries",
            count=len(entries),
            output_path=output_path,
        )


def parse_datetime(value: str) -> datetime:
    """날짜/시간 문자열 파싱."""
    formats = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Invalid datetime format: {value}")


def main(args: list[str] | None = None) -> int:
    """CLI 엔트리포인트."""
    parser = argparse.ArgumentParser(
        prog="selfhealing.audit.export",
        description="Export audit logs to external systems",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Export to stdout (JSONL)
  %(prog)s --input /var/log/audit/*.jsonl

  # Export to file (JSON array)
  %(prog)s --input /var/log/audit/*.jsonl --format json --target file --output audit.json

  # Export to CSV
  %(prog)s --input /var/log/audit/*.jsonl --format csv --target file --output audit.csv

  # Export to S3
  %(prog)s --input /var/log/audit/*.jsonl --target s3 --s3-bucket my-audit-bucket

  # Filter by date range
  %(prog)s --input /var/log/audit/*.jsonl --start 2025-01-01 --end 2025-01-31

  # Filter by action
  %(prog)s --input /var/log/audit/*.jsonl --actions governance_blocked,cb_force_open
""",
    )

    # 입력 옵션
    parser.add_argument(
        "--input",
        "-i",
        dest="input_paths",
        nargs="+",
        required=True,
        help="Input file paths (glob patterns supported)",
    )

    # 필터 옵션
    parser.add_argument(
        "--start",
        type=parse_datetime,
        help="Start time (ISO format: 2025-01-01 or 2025-01-01T00:00:00)",
    )
    parser.add_argument(
        "--end",
        type=parse_datetime,
        help="End time (ISO format)",
    )
    parser.add_argument(
        "--actions",
        help="Filter by actions (comma-separated)",
    )
    parser.add_argument(
        "--actors",
        help="Filter by actor IDs (comma-separated)",
    )

    # 출력 옵션
    parser.add_argument(
        "--format",
        "-f",
        type=ExportFormat,
        choices=list(ExportFormat),
        default=ExportFormat.JSONL,
        help="Output format (default: jsonl)",
    )
    parser.add_argument(
        "--target",
        "-t",
        type=ExportTarget,
        choices=list(ExportTarget),
        default=ExportTarget.STDOUT,
        help="Export target (default: stdout)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output file path (for file target)",
    )

    # S3 옵션
    parser.add_argument("--s3-bucket", help="S3 bucket name")
    parser.add_argument("--s3-prefix", default="audit-export/", help="S3 key prefix")
    parser.add_argument("--s3-region", default="ap-northeast-2", help="AWS region")

    # HTTP 옵션
    parser.add_argument("--http-endpoint", help="HTTP endpoint URL")

    # 기타 옵션
    parser.add_argument(
        "--skip-integrity",
        action="store_true",
        help="Skip hash chain integrity verification",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output",
    )

    parsed = parser.parse_args(args)

    # 로깅 설정
    logging.basicConfig(
        level=logging.DEBUG if parsed.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # 옵션 구성
    options = ExportOptions(
        input_paths=parsed.input_paths,
        start_time=parsed.start,
        end_time=parsed.end,
        actions=parsed.actions.split(",") if parsed.actions else None,
        actor_ids=parsed.actors.split(",") if parsed.actors else None,
        format=parsed.format,
        target=parsed.target,
        output_path=parsed.output,
        s3_bucket=parsed.s3_bucket,
        s3_prefix=parsed.s3_prefix,
        s3_region=parsed.s3_region,
        http_endpoint=parsed.http_endpoint,
        verify_integrity=not parsed.skip_integrity,
        verbose=parsed.verbose,
    )

    # 내보내기 실행
    exporter = AuditExporter(options)

    try:
        stats = exporter.export()

        if parsed.verbose or parsed.target != ExportTarget.STDOUT:
            print("\n=== Export Statistics ===", file=sys.stderr)
            print(f"Files processed: {stats.total_files}", file=sys.stderr)
            print(f"Total entries: {stats.total_entries}", file=sys.stderr)
            print(f"Filtered entries: {stats.filtered_entries}", file=sys.stderr)
            print(f"Exported entries: {stats.exported_entries}", file=sys.stderr)
            if stats.integrity_errors > 0:
                print(f"⚠️  Integrity errors: {stats.integrity_errors}", file=sys.stderr)

        return 0

    except Exception as e:
        logger.exception(
            "export_failed",
            error=e,
        )
        if parsed.verbose:
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
