"""
4-Eyes Approval Workflow Mixin.

Provides approval request management for critical configuration changes.
"""

from __future__ import annotations

import structlog
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .constants import STORAGE_KEYS

logger = structlog.get_logger()


class ApprovalMixin:
    """Mixin providing 4-Eyes approval workflow methods."""

    # =========================================================================
    # 4-Eyes Approval Workflow
    # =========================================================================

    def get_approval_requests(
        self, status: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Get all approval requests.

        Args:
            status: Filter by status (PENDING, APPROVED, REJECTED, EXPIRED)

        Returns:
            list: List of approval requests
        """
        storage_key = STORAGE_KEYS["approval_requests"]
        with self._lock:
            stored = self._backend.get(storage_key)
            requests = stored if stored else []

            if status:
                requests = [r for r in requests if r.get("status") == status]

            return requests

    def create_approval_request(
        self,
        request_type: str,
        description: str,
        requested_by: str,
        payload: dict[str, Any],
        expiry_hours: int = 24,
    ) -> dict[str, Any]:
        """
        Create a new approval request (4-Eyes Principle).

        Args:
            request_type: Type of request (config_change, mode_change, emergency_action)
            description: Human-readable description
            requested_by: Username of requester
            payload: Request data
            expiry_hours: Hours until expiry (default 24)

        Returns:
            dict: Created approval request
        """
        storage_key = STORAGE_KEYS["approval_requests"]
        with self._lock:
            requests = self.get_approval_requests()

            now = datetime.now(timezone.utc)
            request = {
                "id": str(uuid.uuid4()),
                "request_type": request_type,
                "description": description,
                "requested_by": requested_by,
                "requested_at": now.isoformat(),
                "approved_by": "",
                "approved_at": "",
                "status": "PENDING",
                "payload": payload,
                "expires_at": (now + timedelta(hours=expiry_hours)).isoformat(),
            }

            requests.append(request)
            self._backend.set(storage_key, requests)
            logger.info(
                f"[RuntimeConfig] Created approval request: {request['id']} by {requested_by}"
            )
            return request

    def approve_request(
        self,
        request_id: str,
        approved_by: str,
    ) -> dict[str, Any] | None:
        """
        Approve an approval request.

        Args:
            request_id: ID of request to approve
            approved_by: Username of approver (must be different from requester)

        Returns:
            dict: Updated request or None if not found
        """
        storage_key = STORAGE_KEYS["approval_requests"]
        with self._lock:
            requests = self.get_approval_requests()

            for request in requests:
                if request["id"] == request_id:
                    if request["status"] != "PENDING":
                        logger.warning(
                            f"[RuntimeConfig] Request {request_id} is not PENDING"
                        )
                        return None

                    # 4-Eyes: Approver must be different from requester
                    if request["requested_by"] == approved_by:
                        logger.warning(
                            f"[RuntimeConfig] Self-approval not allowed: {approved_by}"
                        )
                        return None

                    # Check expiry
                    expires_at = datetime.fromisoformat(request["expires_at"])
                    if datetime.now(timezone.utc) > expires_at:
                        request["status"] = "EXPIRED"
                        self._backend.set(storage_key, requests)
                        logger.warning(
                            f"[RuntimeConfig] Request {request_id} has expired"
                        )
                        return None

                    request["status"] = "APPROVED"
                    request["approved_by"] = approved_by
                    request["approved_at"] = datetime.now(timezone.utc).isoformat()

                    self._backend.set(storage_key, requests)
                    logger.info(
                        f"[RuntimeConfig] Approved request {request_id} by {approved_by}"
                    )
                    return request

            return None

    def reject_request(
        self,
        request_id: str,
        rejected_by: str,
        reason: str = "",
    ) -> dict[str, Any] | None:
        """
        Reject an approval request.

        Args:
            request_id: ID of request to reject
            rejected_by: Username of rejector
            reason: Rejection reason

        Returns:
            dict: Updated request or None if not found
        """
        storage_key = STORAGE_KEYS["approval_requests"]
        with self._lock:
            requests = self.get_approval_requests()

            for request in requests:
                if request["id"] == request_id:
                    if request["status"] != "PENDING":
                        logger.warning(
                            f"[RuntimeConfig] Request {request_id} is not PENDING"
                        )
                        return None

                    request["status"] = "REJECTED"
                    request["approved_by"] = (
                        rejected_by  # Using same field for rejector
                    )
                    request["approved_at"] = datetime.now(timezone.utc).isoformat()
                    request["rejection_reason"] = reason

                    self._backend.set(storage_key, requests)
                    logger.info(
                        f"[RuntimeConfig] Rejected request {request_id} by {rejected_by}"
                    )
                    return request

            return None

    def expire_old_requests(self) -> int:
        """
        Expire old pending requests.

        Returns:
            int: Number of expired requests
        """
        storage_key = STORAGE_KEYS["approval_requests"]
        with self._lock:
            requests = self.get_approval_requests()
            now = datetime.now(timezone.utc)
            expired_count = 0

            for request in requests:
                if request["status"] == "PENDING":
                    expires_at = datetime.fromisoformat(request["expires_at"])
                    if now > expires_at:
                        request["status"] = "EXPIRED"
                        expired_count += 1

            if expired_count > 0:
                self._backend.set(storage_key, requests)
                logger.info(
                    f"[RuntimeConfig] Expired {expired_count} approval requests"
                )

            return expired_count

    def get_pending_requests_for_user(self, username: str) -> list[dict[str, Any]]:
        """
        Get pending requests that a user can approve.

        Args:
            username: Username to check

        Returns:
            list: Pending requests (excluding ones created by this user)
        """
        pending = self.get_approval_requests(status="PENDING")
        # 4-Eyes: Can't approve own requests
        return [r for r in pending if r["requested_by"] != username]
