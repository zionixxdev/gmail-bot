"""
workers/worker.py — RQ worker entry point.

Run with:
    python workers/worker.py

Or using the rq CLI directly:
    rq worker --url redis://localhost:6379/0 default

The worker listens to the 'default' queue and processes background tasks
defined in workers/tasks.py.
"""

from __future__ import annotations

import logging
import sys
import os

# Ensure project root is in PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
from rq import Queue, Worker
from rq.logutils import setup_loghandlers

from config import LOG_FILE, LOG_LEVEL, REDIS_URL

# ─── Logging setup ────────────────────────────────────────────────────────────

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
setup_loghandlers(level=getattr(logging, LOG_LEVEL.upper(), logging.INFO))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE),
    ],
)

logger = logging.getLogger(__name__)


def main() -> None:
    """Start the RQ worker and begin listening for jobs."""
    logger.info("Connecting to Redis at: %s", REDIS_URL)
    try:
        conn = redis.from_url(REDIS_URL)
        conn.ping()
        logger.info("Redis connection successful.")
    except Exception as exc:
        logger.critical("Cannot connect to Redis: %s", exc)
        sys.exit(1)

    queues = [Queue("default", connection=conn), Queue("high", connection=conn)]
    worker = Worker(queues, connection=conn)

    logger.info(
        "Worker started. Listening on queues: %s",
        [q.name for q in queues],
    )
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
