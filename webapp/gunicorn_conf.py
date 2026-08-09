"""Gunicorn configuration for the deployed console.

Exists mainly to keep the health check out of the access log. App Runner polls
/healthz every 20 seconds, which is 4,320 log lines a day of pure noise — it
buries real requests and bills CloudWatch ingestion for nothing.
"""

from __future__ import annotations

import logging

bind = "0.0.0.0:8080"

# Single worker, deliberately: run state and the threads executing each phase
# live in the process. Threads give concurrency without a second process that
# would not share that state. See infrastructure/cloudformation/app.yaml.
workers = 1
threads = 8

timeout = 120
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = "info"


class _SkipHealthCheck(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/healthz" not in record.getMessage()


def when_ready(server):  # noqa: ARG001 - gunicorn hook signature
    logging.getLogger("gunicorn.access").addFilter(_SkipHealthCheck())


def post_worker_init(worker):  # noqa: ARG001 - gunicorn hook signature
    # Workers are forked; re-apply so the filter survives in each of them.
    logging.getLogger("gunicorn.access").addFilter(_SkipHealthCheck())
