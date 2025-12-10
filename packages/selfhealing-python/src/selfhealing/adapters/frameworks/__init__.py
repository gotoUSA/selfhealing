"""
Framework adapters for the self-healing system.

This module contains adapters for different web frameworks.
"""

from selfhealing.interfaces.web_framework import (
    WebFrameworkInterface,
    RequestContext,
    ResponseContext,
    HttpMethod,
    HandlerFunc,
)

__all__ = [
    "WebFrameworkInterface",
    "RequestContext",
    "ResponseContext",
    "HttpMethod",
    "HandlerFunc",
]

# Conditionally import adapters based on available dependencies
try:
    from selfhealing.adapters.frameworks.fastapi_adapter import FastAPIAdapter

    __all__.append("FastAPIAdapter")
except ImportError:
    pass

try:
    from selfhealing.adapters.frameworks.django_adapter import DjangoRESTAdapter

    __all__.append("DjangoRESTAdapter")
except ImportError:
    pass
