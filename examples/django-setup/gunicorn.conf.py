import os

workers = int(os.environ.get("GUNICORN_WORKERS", 4))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", 4))
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

_env = os.environ.get("DEPLOYMENT_ENV", "development")
preload_app = _env != "development"
reload = _env == "development"


def post_fork(server, worker):
    from django.db import connections

    for conn in connections.all():
        conn.close()
    from selfhealing.server import post_fork_reset

    post_fork_reset(worker)


def post_worker_init(worker):
    from selfhealing.server import post_worker_init_start

    post_worker_init_start(worker)


def worker_exit(server, worker):
    from selfhealing.server import worker_exit_cleanup

    worker_exit_cleanup(worker)
