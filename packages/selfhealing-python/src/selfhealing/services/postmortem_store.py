"""
Post-mortem Incident Storage Service - Backward Compatibility Shim.

.. deprecated:: 2.0.0
    Import from ``selfhealing.services.postmortem.store`` instead.
    This shim will be removed in v3.0.0.

실제 구현은 selfhealing.services.postmortem.store 패키지로 이전되었습니다.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "Importing from 'selfhealing.services.postmortem_store' is deprecated. "
    "Use 'selfhealing.services.postmortem.store' instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export all from canonical location
from selfhealing.services.postmortem.store import (  # noqa: F401
    LOCK_KEY_POSTMORTEM_GENERATE,
    LOCK_KEY_POSTMORTEM_GROUP,
    LOCK_TTL_POSTMORTEM_GENERATE,
    LOCK_TTL_POSTMORTEM_GROUP,
    acquire_group_close_lock,
    add_healing_incident,
    add_healing_incident_with_lock,
    build_timeline,
    clear_healing_incidents,
    collect_service_states,
    generate_postmortem_data,
    get_db_persistence_enabled,
    get_healing_incidents,
    get_healing_incidents_count,
    get_incident_by_id,
    set_db_persistence_enabled,
    update_incident_fields,
)

__all__ = [
    # Storage functions
    "add_healing_incident",
    "add_healing_incident_with_lock",
    "get_healing_incidents",
    "get_healing_incidents_count",
    "get_incident_by_id",
    "update_incident_fields",
    "clear_healing_incidents",
    "set_db_persistence_enabled",
    "get_db_persistence_enabled",
    # Distributed lock functions
    "acquire_group_close_lock",
    # Lock key patterns
    "LOCK_KEY_POSTMORTEM_GENERATE",
    "LOCK_KEY_POSTMORTEM_GROUP",
    "LOCK_TTL_POSTMORTEM_GENERATE",
    "LOCK_TTL_POSTMORTEM_GROUP",
    # Helper functions
    "collect_service_states",
    "build_timeline",
    "generate_postmortem_data",
]
