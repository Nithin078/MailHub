"""Inbox Agent API."""

from __future__ import annotations

import os
import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.db import SessionLocal, init_db

app = FastAPI(title="Inbox Agent", version="1.0.0")
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list() + [settings.web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")


def _worker_loop() -> None:
    from app.services.jobs import drain, maintenance

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


def _wait() -> None:
    settings = get_settings()
    if settings.redis_url:
        try:
            import redis

            redis.Redis.from_url(settings.redis_url).brpop("inboxagent:wake", timeout=5)
            return
        except Exception:
            pass
    time.sleep(5)


@app.on_event("startup")
def startup() -> None:
    if os.environ.get("TESTING") == "1":
        return
    init_db()
    if get_settings().demo_mode:
        from app.services.seed import seed_demo

        db = SessionLocal()
        try:
            seed_demo(db)
            db.commit()
        finally:
            db.close()
    if get_settings().inline_worker:
        thread = threading.Thread(target=_worker_loop, name="inbox-worker", daemon=True)
        thread.start()
