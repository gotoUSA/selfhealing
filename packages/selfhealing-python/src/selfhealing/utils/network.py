"""
Network Utilities.

Provides canonical IP extraction for the self-healing system.
All modules requiring client IP should use ``extract_client_ip``
to ensure consistent behaviour across audit, permission, actor context,
and canary feature-flag subsystems.

Header resolution order:
    1. ``X-Forwarded-For`` – de-facto standard for reverse proxies / LB
    2. ``X-Real-IP`` – commonly set by nginx
    3. ``REMOTE_ADDR`` – direct connection fallback

Why unified?
    Before this module each subsystem had its own copy with subtle
    differences (missing ``X-Real-IP``, different defaults, inconsistent
    ``strip()``).  A single canonical function eliminates IP discrepancy
    bugs where audit records and permission checks disagree on the same
    request's origin.
"""

from __future__ import annotations

from typing import Any


def extract_client_ip(request: Any, *, default: str | None = None) -> str | None:
    """
    Extract the client IP address from a Django ``HttpRequest``.

    Safely handles objects that may not have a ``META`` attribute
    (e.g. test doubles, DRF ``Request`` wrappers) by using ``getattr``
    with an empty-dict fallback.

    Args:
        request: A Django ``HttpRequest`` (or DRF ``Request``).
        default: Value returned when no IP can be determined.
                 Callers that need a non-``None`` sentinel (e.g. audit
                 masking) can pass ``default="unknown"``.

    Returns:
        The resolved client IP string, or *default* if unavailable.
    """
    meta = getattr(request, "META", None) or {}

    # 1) X-Forwarded-For – first entry is the original client
    x_forwarded_for = meta.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()

    # 2) X-Real-IP (nginx convention)
    x_real_ip = meta.get("HTTP_X_REAL_IP")
    if x_real_ip:
        return x_real_ip.strip()

    # 3) Direct connection
    remote_addr = meta.get("REMOTE_ADDR")
    if remote_addr:
        return remote_addr

    return default
