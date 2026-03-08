"""Process model detection utilities for fork-safety.

Gunicorn Workers must not register their own SIGTERM/SIGINT handlers
because Gunicorn Master (Arbiter) manages process lifecycle via signals.
Overwriting Gunicorn's handlers causes shutdown conflicts.

Instead, cleanup logic runs via Gunicorn hooks (worker_exit, post_fork)
defined in gunicorn.conf.py.
"""

from __future__ import annotations

import os


def is_gunicorn_worker() -> bool:
    """Return True if the current process is a Gunicorn Worker.

    Detection relies on the GUNICORN_WORKER environment variable,
    which is set by the post_worker_init hook in gunicorn.conf.py.
    """
    return os.environ.get("GUNICORN_WORKER") == "1"
