"""
Asyncpg connection pool — singleton managed by FastAPI lifespan.

Usage:
    from db.pool import create_pool, close_pool, get_pool

    # In lifespan startup:
    await create_pool(dsn)

    # In lifespan shutdown:
    await close_pool()

    # Anywhere else:
    pool = get_pool()  # None if not configured
"""
from __future__ import annotations

import logging

try:
    import asyncpg
    _ASYNCPG_AVAILABLE = True
except ImportError:
    _ASYNCPG_AVAILABLE = False

logger = logging.getLogger("orion.db")

_pool = None  # asyncpg.Pool | None


async def create_pool(dsn: str) -> object | None:
    """Create and store the global asyncpg pool.

    Returns the pool on success, None if asyncpg is unavailable or DSN is empty.
    Logs the connected host (credentials are stripped from the log line).
    """
    global _pool

    if not dsn:
        logger.info("DB pool disabled (ORION_DB_DSN not set)")
        return None

    if not _ASYNCPG_AVAILABLE:
        logger.warning("asyncpg not installed — episode watcher disabled. Run: pip install asyncpg")
        return None

    # Strip credentials from log (keep host:port/db only)
    safe_dsn = dsn.rsplit("@", 1)[-1] if "@" in dsn else dsn

    try:
        _pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=3,
            command_timeout=10,
            statement_cache_size=0,  # safe default for pgbouncer compatibility
        )
        logger.info("DB pool ready → %s", safe_dsn)
    except Exception as exc:
        logger.error("DB pool creation failed (%s): %s", safe_dsn, exc)
        _pool = None

    return _pool


async def close_pool() -> None:
    """Gracefully close the pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("DB pool closed")


def get_pool() -> object | None:
    """Return the active pool, or None if not configured / failed."""
    return _pool
