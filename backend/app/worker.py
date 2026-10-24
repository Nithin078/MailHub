"""Separate worker process. Uses the same drain loop as the API's inline worker.

Run with `python -m app.worker`. With REDIS_URL set, a wake list lets pushes
run immediately. `arq app.worker.WorkerSettings` is the Redis-native entry.
"""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.main import _wait
from app.services.jobs import drain, maintenance


def main() -> None:
    init_db()
    while True:
        db = SessionLocal()
        try:
            drain(db)
            maintenance(db)
        except Exception:
            db.rollback()
        finally:
            db.close()
        _wait()


async def drain_job(ctx) -> None:
    def _go() -> None:
        db = SessionLocal()
        try:
            drain(db)
            maintenance(db)
        finally:
            db.close()

    await asyncio.to_thread(_go)


def _worker_settings():
    from arq.connections import RedisSettings
    from arq import cron

    settings = get_settings()
    return {
        "functions": [drain_job],
        "redis_settings": RedisSettings.from_dsn(settings.redis_url or "redis://localhost:6379/0"),
        "cron_jobs": [cron(drain_job, second={0, 30})],
    }


class WorkerSettings:
    functions = [drain_job]
    cron_jobs = []
    redis_settings = None

    @staticmethod
    def on_startup(ctx) -> None:
        init_db()


def __getattr__(name: str):
    if name == "redis_settings":
        from arq.connections import RedisSettings

        return RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379/0")
    raise AttributeError(name)


if __name__ == "__main__":
    main()
