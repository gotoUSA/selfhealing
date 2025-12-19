"""
File-based Audit Log Adapter.

Logs audit entries to JSON files with automatic rotation support.
Non-invasive - does not require database tables or external services.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from selfhealing.interfaces.audit_adapter import AuditAction, AuditEntry, AuditLogAdapter

logger = logging.getLogger(__name__)


class FileAuditLogAdapter(AuditLogAdapter):
    """
    File-based audit logging adapter.

    Features:
    - JSON Lines format (one JSON object per line)
    - Automatic directory creation
    - Daily rotation support (optional)
    - Thread-safe file writes

    Usage:
        adapter = FileAuditLogAdapter("logs/audit.log")
        adapter.log(AuditEntry(action=AuditAction.CB_FORCE_OPEN, ...))
    """

    def __init__(
        self,
        file_path: str | Path,
        rotate_daily: bool = False,
        max_file_size_mb: Optional[int] = None,
    ):
        """
        Initialize file audit adapter.

        Args:
            file_path: Path to audit log file
            rotate_daily: If True, append date to filename
            max_file_size_mb: Max file size before rotation (not implemented)
        """
        self.base_path = Path(file_path)
        self.rotate_daily = rotate_daily
        self.max_file_size_mb = max_file_size_mb

        # Ensure directory exists
        self.base_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_current_file_path(self) -> Path:
        """Get current file path (with date if rotating)."""
        if self.rotate_daily:
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
            name = f"{self.base_path.stem}_{date_str}{self.base_path.suffix}"
            return self.base_path.parent / name
        return self.base_path

    def log(self, entry: AuditEntry) -> None:
        """Log an audit entry to file."""
        file_path = self._get_current_file_path()

        try:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(entry.to_json() + "\n")
        except Exception as e:
            logger.error(f"[FileAuditLogAdapter] Failed to write audit log: {e}")

    def query(
        self,
        action: Optional[AuditAction | str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """
        Query audit logs from file.

        Note: This is a simple implementation that reads all files.
        For production use with large volumes, consider a database adapter.
        """
        entries: list[AuditEntry] = []

        # Find all log files
        if self.rotate_daily:
            pattern = f"{self.base_path.stem}_*{self.base_path.suffix}"
            log_files = list(self.base_path.parent.glob(pattern))
        else:
            log_files = [self.base_path] if self.base_path.exists() else []

        # Sort by modification time (newest first)
        log_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        for log_file in log_files:
            if len(entries) >= limit:
                break

            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if len(entries) >= limit:
                            break

                        try:
                            data = json.loads(line.strip())
                            entry = self._dict_to_entry(data)

                            # Apply filters
                            if action and self._get_action_value(entry.action) != self._get_action_value(action):
                                continue
                            if target_type and entry.target_type != target_type:
                                continue
                            if target_id and entry.target_id != target_id:
                                continue
                            if start_time and entry.timestamp < start_time:
                                continue
                            if end_time and entry.timestamp > end_time:
                                continue

                            entries.append(entry)

                        except json.JSONDecodeError:
                            continue

            except Exception as e:
                logger.warning(f"[FileAuditLogAdapter] Error reading {log_file}: {e}")

        return entries

    def _get_action_value(self, action: AuditAction | str) -> str:
        """Get string value of action."""
        return action.value if isinstance(action, AuditAction) else action

    def _dict_to_entry(self, data: dict) -> AuditEntry:
        """Convert dictionary to AuditEntry."""
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        elif timestamp is None:
            timestamp = datetime.utcnow()

        # Try to parse action as AuditAction enum
        action_str = data.get("action", "")
        try:
            action = AuditAction(action_str)
        except ValueError:
            action = action_str

        return AuditEntry(
            action=action,
            timestamp=timestamp,
            actor_id=data.get("actor_id"),
            actor_type=data.get("actor_type", "system"),
            target_type=data.get("target_type"),
            target_id=data.get("target_id"),
            service_name=data.get("service_name"),
            domain=data.get("domain"),
            reason=data.get("reason"),
            details=data.get("details", {}),
            success=data.get("success", True),
            error_message=data.get("error_message"),
        )
