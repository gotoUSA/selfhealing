"""
Governance Checks - Backward Compatibility Shim.

.. deprecated:: 2.1.0
    Import from ``selfhealing.services.governance.checks`` instead.
    This shim will be removed in v3.0.0.
"""

import sys
import warnings

warnings.warn(
    "Importing from 'selfhealing.services.governance_checks' is deprecated. "
    "Use 'selfhealing.services.governance.checks' instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

from selfhealing.services.governance import checks as _module  # noqa: E402

_module.__deprecated__ = True
sys.modules[__name__] = _module
