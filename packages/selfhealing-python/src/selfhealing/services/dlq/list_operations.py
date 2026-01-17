"""
DLQ List Operations Mixin.

Provides methods for listing and getting DLQ entries.
Uses Repository pattern for domain-free architecture.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ListOperationsMixin:
    """Mixin providing DLQ list operations using Repository pattern."""

    def list_entries(
        self,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """
        Get paginated list of DLQ entries.

        Uses Repository.find_by_status() with manual pagination.

        Args:
            filters: Dictionary with filter conditions
                - status: Filter by status
                - domain: Filter by domain
            page: Page number (default 1)
            page_size: Items per page (default 20, max 100)

        Returns:
            Dict with entries and pagination info:
                - results: List of entry dicts
                - page: Current page
                - page_size: Items per page
                - total_pages: Total number of pages
                - total_count: Total number of items
                - has_next: Whether there's a next page
                - has_previous: Whether there's a previous page
        """
        filters = filters or {}
        page_size = min(page_size, 100)
        page = max(page, 1)
        
        status_filter = filters.get("status")
        domain_filter = filters.get("domain")
        
        # Get all entries matching filters (we'll paginate manually)
        # For production, add list_paginated to Repository interface
        try:
            if status_filter:
                entries = self.repository.find_by_status(
                    status=status_filter,
                    domain=domain_filter,
                    limit=10000,  # Get all for manual pagination
                )
            else:
                # Get all entries - use statistics if available
                entries = self._get_all_entries_for_listing(domain_filter)
            
            # Manual pagination
            total_count = len(entries)
            total_pages = max(1, (total_count + page_size - 1) // page_size)
            
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            page_entries = entries[start_idx:end_idx]
            
            # Convert to dicts
            results = []
            for entry in page_entries:
                results.append({
                    "id": entry.id,
                    "domain": entry.domain,
                    "failure_type": entry.failure_type,
                    "status": entry.status,
                    "retry_count": entry.retry_count,
                    "created_at": entry.created_at.isoformat() if entry.created_at else None,
                    "resolved_at": entry.resolved_at.isoformat() if entry.resolved_at else None,
                })
            
            return {
                "results": results,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "total_count": total_count,
                "has_next": page < total_pages,
                "has_previous": page > 1,
            }
            
        except Exception as e:
            logger.error(f"[DLQService] List failed: {e}")
            return {
                "results": [],
                "page": page,
                "page_size": page_size,
                "total_pages": 0,
                "total_count": 0,
                "has_next": False,
                "has_previous": False,
            }

    def _get_all_entries_for_listing(
        self,
        domain: Optional[str] = None,
    ) -> List[Any]:
        """
        Get all entries for listing (internal helper).
        
        Combines pending, resolved, and archived entries.
        """
        all_entries = []
        
        for status in ["pending", "resolved", "archived", "replaying"]:
            try:
                entries = self.repository.find_by_status(
                    status=status,
                    domain=domain,
                    limit=10000,
                )
                all_entries.extend(entries)
            except Exception:
                pass
        
        # Sort by created_at descending
        all_entries.sort(
            key=lambda e: e.created_at if e.created_at else e.id,
            reverse=True,
        )
        
        return all_entries
