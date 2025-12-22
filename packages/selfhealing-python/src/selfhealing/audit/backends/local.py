"""
Local File Audit Backend.

Writes audit logs to local JSON Lines files with hash chain integrity.
This is the default backend that is always active.
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from selfhealing.audit.backends.base import AuditBackend, BackendHealth, BackendStatus
from selfhealing.audit.integrity import HashChainManager

logger = logging.getLogger(__name__)


class LocalFileBackend(AuditBackend):
    """
    Local file audit backend with hash chain integrity.

    Features:
    - JSON Lines format for easy parsing
    - Hash chain for tamper detection
    - Automatic log rotation by date
    - Append-only design
    """

    DEFAULT_LOG_DIR = "logs/audit"
    DEFAULT_FILENAME_PATTERN = "audit_{date}.jsonl"

    def __init__(
        self,
        log_dir: Optional[str] = None,
        filename_pattern: Optional[str] = None,
        enable_hash_chain: bool = True,
        rotate_daily: bool = True,
    ):
        """
        Initialize local file backend.

        Args:
            log_dir: Directory for audit logs
            filename_pattern: Pattern for log filenames (use {date} placeholder)
            enable_hash_chain: Enable hash chain integrity (recommended)
            rotate_daily: Create new file each day
        """
        self._log_dir = Path(log_dir or self.DEFAULT_LOG_DIR)
        self._filename_pattern = filename_pattern or self.DEFAULT_FILENAME_PATTERN
        self._enable_hash_chain = enable_hash_chain
        self._rotate_daily = rotate_daily
        self._lock = threading.RLock()
        self._current_file: Optional[Path] = None
        self._file_handle = None
        self._last_success: Optional[datetime] = None
        self._last_error: Optional[str] = None

        # Initialize hash chain manager
        if enable_hash_chain:
            state_file = self._log_dir / ".hash_chain_state.json"
            self._hash_chain = HashChainManager(state_file)
        else:
            self._hash_chain = None

        # Ensure directory exists
        self._log_dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        """Return backend name."""
        return "LocalFile"

    def _get_current_log_file(self) -> Path:
        """Get the current log file path."""
        if self._rotate_daily:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            filename = self._filename_pattern.format(date=date_str)
        else:
            filename = self._filename_pattern.format(date="all")

        return self._log_dir / filename

    def _ensure_file_open(self) -> bool:
        """Ensure the log file is open and ready for writing."""
        try:
            target_file = self._get_current_log_file()

            # Check if we need to rotate
            if self._current_file != target_file:
                self._close_file()
                self._current_file = target_file
                self._file_handle = open(target_file, "a", encoding="utf-8")
                logger.debug(f"[LocalFileBackend] Opened log file: {target_file}")

            return True
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"[LocalFileBackend] Failed to open log file: {e}")
            return False

    def _close_file(self) -> None:
        """Close the current log file."""
        if self._file_handle:
            try:
                self._file_handle.flush()
                self._file_handle.close()
            except Exception:
                pass
            self._file_handle = None

    def write(self, entry: Dict[str, Any]) -> bool:
        """
        Write an audit log entry.

        Args:
            entry: The audit log entry

        Returns:
            True if successful
        """
        with self._lock:
            try:
                # Add hash chain integrity if enabled
                if self._hash_chain:
                    entry = self._hash_chain.add_integrity(entry)

                # Ensure file is open
                if not self._ensure_file_open():
                    return False

                # Write as JSON line
                json_line = json.dumps(entry, default=str, ensure_ascii=False)
                self._file_handle.write(json_line + "\n")
                self._file_handle.flush()  # Ensure immediate write

                self._last_success = datetime.now(timezone.utc)
                return True

            except Exception as e:
                self._last_error = str(e)
                logger.error(f"[LocalFileBackend] Failed to write entry: {e}")
                return False

    def health_check(self) -> BackendHealth:
        """Check backend health."""
        try:
            # Check if we can write to the directory
            test_file = self._log_dir / ".health_check"
            test_file.write_text("ok")
            test_file.unlink()

            return BackendHealth(
                status=BackendStatus.ACTIVE,
                message="Local file backend operational",
                last_success=self._last_success,
            )
        except Exception as e:
            return BackendHealth(
                status=BackendStatus.UNAVAILABLE,
                message=f"Cannot write to log directory: {e}",
                last_error=str(e),
            )

    def flush(self) -> bool:
        """Flush file buffer."""
        with self._lock:
            if self._file_handle:
                try:
                    self._file_handle.flush()
                    os.fsync(self._file_handle.fileno())
                    return True
                except Exception as e:
                    logger.error(f"[LocalFileBackend] Failed to flush: {e}")
                    return False
        return True

    def close(self) -> None:
        """Close the backend."""
        with self._lock:
            self._close_file()
            if self._hash_chain:
                self._hash_chain._save_state()

    def _parse_entry(self, line: str) -> Optional[Dict[str, Any]]:
        """Parse a JSON line into an entry dict. Returns None if invalid."""
        line = line.strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def _entry_matches_config_type(self, entry: Dict[str, Any], config_type: str) -> bool:
        """Check if entry matches config_type filter."""
        return entry.get("change", {}).get("config_type") == config_type

    def _entry_matches_user(self, entry: Dict[str, Any], user: str) -> bool:
        """Check if entry matches user filter."""
        return entry.get("actor", {}).get("user") == user

    def _entry_matches_time_range(
        self,
        entry: Dict[str, Any],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> bool:
        """Check if entry matches time range filter."""
        entry_time_str = entry.get("timestamp")
        if not entry_time_str:
            return True  # No timestamp, include by default

        try:
            entry_time = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
            if start_time and entry_time < start_time:
                return False
            if end_time and entry_time > end_time:
                return False
            return True
        except ValueError:
            return True  # Invalid timestamp, include by default

    def _entry_matches_filters(
        self,
        entry: Dict[str, Any],
        config_type: Optional[str],
        user: Optional[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> bool:
        """Check if entry matches all filters."""
        if config_type and not self._entry_matches_config_type(entry, config_type):
            return False
        if user and not self._entry_matches_user(entry, user):
            return False
        if not self._entry_matches_time_range(entry, start_time, end_time):
            return False
        return True

    def query(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        config_type: Optional[str] = None,
        user: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query audit logs from local files."""
        results = []

        try:
            log_files = sorted(self._log_dir.glob("audit_*.jsonl"), reverse=True)

            for log_file in log_files:
                if len(results) >= limit:
                    break

                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if len(results) >= limit:
                            break

                        entry = self._parse_entry(line)
                        if entry is None:
                            continue

                        if self._entry_matches_filters(entry, config_type, user, start_time, end_time):
                            results.append(entry)

        except Exception as e:
            logger.error(f"[LocalFileBackend] Query failed: {e}")

        return results

        return results

    def verify_integrity(self) -> tuple:
        """Verify integrity of all local log files."""
        from selfhealing.audit.integrity import verify_audit_log_integrity

        issues = []
        for log_file in self._log_dir.glob("audit_*.jsonl"):
            is_valid, file_issues = verify_audit_log_integrity(log_file)
            if not is_valid:
                issues.append({"file": str(log_file), "issues": file_issues})

        return len(issues) == 0, issues

    def get_chain_state(self) -> Dict[str, Any]:
        """Get current hash chain state."""
        if self._hash_chain:
            return self._hash_chain.get_state()
        return {"enabled": False}
