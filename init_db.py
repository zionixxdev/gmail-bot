"""
init_db.py — Standalone script to initialize the database.

Creates all tables defined in db/models.py using SQLAlchemy metadata.
Safe to run multiple times (uses CREATE TABLE IF NOT EXISTS semantics).

Usage:
    python init_db.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    from db.session import init_db, dispose_engine
    from config import DATABASE_URL

    logger.info("Target database: %s", DATABASE_URL)
    logger.info("Creating tables…")

    await init_db()
    logger.info("✅ All tables created successfully.")

    await dispose_engine()
    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
