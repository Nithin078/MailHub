"""Job queue. Postgres is the record. Redis wakes the worker when it is configured."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Draft, GmailAccount, Job, Message, Notification, Reminder, utcnow
from app.services.gmail import GmailError, get_message, list_history, start_watch
from app.services.notify import deliver, flush_held, resend_due
from app.services.pipeline import process_message


def enqueue(db: Session, kind: str, payload: dict, *, delay_seconds: int = 0) -> Job:
    job = Job(
        kind=kind,
        payload=payload,
        run_at=datetime.now(timezone.utc) + timedelta(seconds=delay_seconds),
        status="pending",
    )
    db.add(job)
    db.flush()
    _wake()
    return job


def _wake() -> None:
    """Ping Redis so the arq worker drains immediately. Postgres remains the source of truth."""
    settings = get_settings()
    if settings.resolved_queue_backend() != "redis" or not settings.redis_url:
        return
    try:
        import redis

        redis.Redis.from_url(settings.redis_url).lpush("inboxagent:wake", "1")
    except Exception:
        return


def drain(db: Session, limit: int = 25) -> int:
    now = datetime.now(timezone.utc)
    jobs = (
        db.query(Job)
        .filter(Job.status == "pending", Job.run_at <= now)
        .order_by(Job.run_at.asc())
        .limit(limit)
        .all()
    )
    done = 0
    for job in jobs:
        job.status = "running"
        job.attempts += 1
        db.commit()
        try:
            _run(db, job)
            job.status = "done"
            job.last_error = None
            done += 1
        except Exception as exc:  # noqa: BLE001 — a failed job must not kill the worker
            job.last_error = str(exc)[:500]
            job.status = "pending" if job.attempts < job.max_attempts else "failed"
            if job.status == "pending":
                job.run_at = datetime.now(timezone.utc) + timedelta(seconds=15 * job.attempts)
        db.commit()
    return done


def _run(db: Session, job: Job) -> None:
    if job.kind == "gmail_push":
        handle_push(db, job.payload.get("email") or "", job.payload.get("history_id"))
    elif job.kind == "send_later":
        _send_draft(db, job.payload.get("draft_id"))
    elif job.kind == "renew_watches":
        renew_watches(db)


def handle_push(db: Session, email: str, history_id: str | None) -> None:
    account = db.query(GmailAccount).filter(GmailAccount.email == email).one_or_none()
    if account is None or account.provider != "gmail":
        return
    if account.status != "active":
        return
    start = account.history_id or history_id
    if not start:
        return
    try:
        ids, newest = list_history(account, start)
    except GmailError as exc:
        if exc.status in {"needs_reconnect", "blocked"}:
            account.status = exc.status
            account.status_message = _status_text(exc.status, str(exc))
        elif exc.status == "history_expired":
            account.history_id = history_id or account.history_id
        return
    failed = False
    for message_id in ids:
        try:
            raw = get_message(account, message_id, fmt="full")
            process_message(db, account, raw)
        except GmailError as exc:
            if exc.status in {"needs_reconnect", "blocked"}:
                account.status = exc.status
                account.status_message = _status_text(exc.status, str(exc))
            failed = True
            break
        except Exception:
            failed = True
            break
    if not failed and newest:
        account.history_id = newest


def _status_text(status: str, detail: str) -> str:
    if status == "blocked":
        return "This account may be blocked by its administrator (common for college or work accounts)."
    return detail[:300] or "Reconnect this account."


def renew_watches(db: Session) -> None:
    soon = datetime.now(timezone.utc) + timedelta(hours=24)
    accounts = db.query(GmailAccount).filter(GmailAccount.provider == "gmail", GmailAccount.status == "active").all()
    for account in accounts:
        expiry = account.watch_expiry
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry and expiry > soon:
            continue
        try:
            start_watch(account)
        except GmailError as exc:
            if exc.status in {"needs_reconnect", "blocked"}:
                account.status = exc.status
                account.status_message = _status_text(exc.status, str(exc))


def fire_reminders(db: Session) -> int:
    now = datetime.now(timezone.utc)
    due = db.query(Reminder).filter(Reminder.done.is_(False), Reminder.remind_at <= now).all()
    count = 0
    for reminder in due:
        if reminder.message_id is None:
            reminder.done = True
            continue
        already = (
            db.query(Notification)
            .filter(Notification.reminder_id == reminder.id)
            .one_or_none()
        )
        if already:
            continue
        message = db.get(Message, reminder.message_id)
        if message is None:
            reminder.done = True
            continue
        row = Notification(
            user_id=reminder.user_id,
            message_id=message.id,
            reminder_id=reminder.id,
            priority="urgent",
            title="Reminder",
            body=(reminder.note or message.subject or "Reminder")[:180],
            delivery_status="pending",
            payload={
                "notification_id": "",
                "message_id": message.id,
                "priority": "urgent",
                "title": "Reminder",
                "body": (reminder.note or message.subject or "")[:180],
                "account_label": "",
                "category": "action_needed",
                "ask_company": "0",
                "link": "",
            },
        )
        db.add(row)
        db.flush()
        row.payload = {**row.payload, "notification_id": row.id}
        deliver(db, row)
        count += 1
    return count


def send_due_drafts(db: Session) -> None:
    now = datetime.now(timezone.utc)
    drafts = db.query(Draft).filter(Draft.send_at.isnot(None), Draft.send_at <= now).all()
    for draft in drafts:
        _send_draft(db, draft.id)


def _send_draft(db: Session, draft_id: str | None) -> None:
    if not draft_id:
        return
    draft = db.get(Draft, draft_id)
    if draft is None:
        return
    from app.services.outgoing import deliver_draft

    deliver_draft(db, draft)


def maintenance(db: Session) -> None:
    resend_due(db)
    flush_held(db)
    fire_reminders(db)
    send_due_drafts(db)
    renew_watches(db)
    db.commit()


def decode_push(body: dict) -> tuple[str, str | None]:
    message = body.get("message") or {}
    raw = message.get("data") or ""
    if not raw:
        return "", None
    padded = raw + "=" * (-len(raw) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return "", None
    return payload.get("emailAddress") or "", str(payload.get("historyId") or "") or None
