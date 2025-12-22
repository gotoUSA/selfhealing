"""
IP and PII Masking Utilities.

Provides privacy-preserving data handling for audit logs.
Implements Privacy-by-Design principles for GDPR/CCPA compliance.
"""

import hashlib
import re
from typing import Optional


def mask_ip(ip: str, mask_last_octets: int = 2) -> str:
    """
    Mask an IP address for privacy compliance.

    IPv4: Masks last N octets with ***
    IPv6: Masks last N groups with ***

    Args:
        ip: The IP address to mask
        mask_last_octets: Number of octets/groups to mask (default: 2)

    Returns:
        Masked IP address (e.g., "192.168.***.***")

    Examples:
        >>> mask_ip("192.168.1.100")
        '192.168.***.***'
        >>> mask_ip("192.168.1.100", mask_last_octets=1)
        '192.168.1.***'
        >>> mask_ip("2001:0db8:85a3:0000:0000:8a2e:0370:7334")
        '2001:0db8:85a3:0000:0000:8a2e:***:***'
    """
    if not ip:
        return "unknown"

    ip = ip.strip()

    # Handle IPv6
    if ":" in ip:
        parts = ip.split(":")
        if len(parts) > mask_last_octets:
            masked_parts = parts[: -mask_last_octets] + ["***"] * mask_last_octets
            return ":".join(masked_parts)
        return ip

    # Handle IPv4
    parts = ip.split(".")
    if len(parts) == 4:
        if mask_last_octets >= 4:
            return "***.***.***.***"
        masked_parts = parts[: -mask_last_octets] + ["***"] * mask_last_octets
        return ".".join(masked_parts)

    # Unknown format, return as-is with partial masking
    return ip[:len(ip) // 2] + "***"


def mask_email(email: str) -> str:
    """
    Mask an email address for privacy compliance.

    Args:
        email: The email address to mask

    Returns:
        Masked email (e.g., "a***n@example.com")

    Examples:
        >>> mask_email("admin@example.com")
        'a***n@example.com'
        >>> mask_email("ab@example.com")
        'a***b@example.com'
    """
    if not email or "@" not in email:
        return "***@***.***"

    local, domain = email.rsplit("@", 1)

    if len(local) <= 2:
        masked_local = local[0] + "***" if local else "***"
    else:
        masked_local = local[0] + "***" + local[-1]

    return f"{masked_local}@{domain}"


def hash_for_audit(value: str, salt: Optional[str] = None) -> str:
    """
    Create a SHA-256 hash of a value for audit purposes.

    This allows for later verification without storing the original value.
    Use a secret salt stored securely for reversibility in investigations.

    Args:
        value: The value to hash
        salt: Optional salt for the hash (store securely!)

    Returns:
        SHA-256 hash prefixed with "sha256:"

    Examples:
        >>> hash_for_audit("192.168.1.100")
        'sha256:a1b2c3...'
    """
    if not value:
        return "sha256:empty"

    data = value
    if salt:
        data = f"{salt}:{value}"

    hash_value = hashlib.sha256(data.encode()).hexdigest()
    return f"sha256:{hash_value[:16]}"  # Truncate for readability


def mask_sensitive_fields(data, sensitive_keys: Optional[list] = None):
    """
    Mask sensitive fields in a dictionary.

    Args:
        data: Data containing potentially sensitive fields (dict, list, or primitive)
        sensitive_keys: List of keys to mask (default: common sensitive keys)

    Returns:
        Data with sensitive values masked
    """
    # Handle non-dict types
    if data is None:
        return None
    if not isinstance(data, (dict, list)):
        return data
    if isinstance(data, list):
        return [mask_sensitive_fields(item, sensitive_keys) for item in data]

    if sensitive_keys is None:
        sensitive_keys = [
            "password",
            "secret",
            "token",
            "api_key",
            "apikey",
            "authorization",
            "auth",
            "credential",
            "private_key",
            "credit_card",
            "ssn",
            "social_security",
        ]

    result = {}
    for key, value in data.items():
        key_lower = key.lower()

        # Check if key matches sensitive patterns
        is_sensitive = any(s in key_lower for s in sensitive_keys)

        if is_sensitive:
            result[key] = "***REDACTED***"
        elif isinstance(value, dict):
            result[key] = mask_sensitive_fields(value, sensitive_keys)
        elif isinstance(value, list):
            result[key] = [
                mask_sensitive_fields(item, sensitive_keys) if isinstance(item, dict) else item for item in value
            ]
        else:
            result[key] = value

    return result


def extract_ip_from_request(request) -> str:
    """
    Extract client IP from a Django request, handling proxies.

    Args:
        request: Django HttpRequest object

    Returns:
        Client IP address
    """
    # Check X-Forwarded-For first (common proxy header)
    x_forwarded_for = getattr(request, "META", {}).get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        # Take the first IP (original client)
        return x_forwarded_for.split(",")[0].strip()

    # Check X-Real-IP (nginx)
    x_real_ip = getattr(request, "META", {}).get("HTTP_X_REAL_IP")
    if x_real_ip:
        return x_real_ip.strip()

    # Fall back to REMOTE_ADDR
    return getattr(request, "META", {}).get("REMOTE_ADDR", "unknown")
