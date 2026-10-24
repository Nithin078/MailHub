"""Notification records and FCM delivery. The phone builds the visible notification."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import jwt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Device, Message, Notification, aware, utcnow
from app.services.settings_store import get_setting


def _in_quiet_hours(spec: dict, when: datetime) -> bool:
    if not spec or not spec.get("enabled"):
        return False
    try:
        local = when.astimezone(ZoneInfo(spec.get("tz") or "Asia/Kolkata"))
    except Exception:
        local = when
    start = spec.get("start") or "23:00"
    end = spec.get("end") or "07:00"
    try:
        sh, sm = [int(p) for p in start.split(":")[:2]]
        eh, em = [int(p) for p in end.split(":")[:2]]
    except ValueError:
        return False
    minutes = local.hour * 60 + local.minute
    start_m = sh * 60 + sm
    end_m = eh * 60 + em
    if start_m <= end_m:
        return start_m <= minutes <= end_m
    return minutes >= start_m or minutes <= end_m


def create_notification(db: Session, message: Message, *, duplicate: bool = False) -> Notification | None:
    if message.priority not in {"urgent", "normal", "digest"}:
        return None
    existing = db.query(Notification).filter(Notification.message_id == message.id).one_or_none()
    if existing:
        return existing
    if duplicate:
        note = Notification(
            user_id=message.user_id,
            message_id=message.id,
            priority=message.priority,
            title=message.subject or "(no subject)",
            body="Already notified from another account",
            delivery_status="skipped_duplicate",
            sent_at=utcnow(),
        )
        db.add(note)
        return note

    account_quiet = get_setting(db, message.user_id, "quiet_hours", message.account_id) or {}
    quiet = account_quiet if account_quiet.get("enabled") else (get_setting(db, message.user_id, "quiet_hours") or {})
    focus = bool(get_setting(db, message.user_id, "focus_mode"))
    mutes = get_setting(db, message.user_id, "category_mutes") or []
    notify_enabled = get_setting(db, message.user_id, "notify_enabled", message.account_id)
    if notify_enabled is None:
        notify_enabled = True

    status = "pending"
    held_until = None
    now = datetime.now(timezone.utc)
    if not notify_enabled:
        status = "muted"
    elif message.category in mutes:
        status = "muted"
    elif focus and message.priority != "urgent":
        status = "held"
    elif _in_quiet_hours(quiet, now) and not (message.priority == "urgent" and quiet.get("allow_urgent", True)):
        status = "held"
        held_until = now + timedelta(hours=8)

    account_label = ""
    if message.account_id:
        from app.models import GmailAccount

        account = db.get(GmailAccount, message.account_id)
        account_label = (account.label or account.email) if account else ""

    payload = {
        "notification_id": "",
        "message_id": message.id,
        "priority": message.priority,
        "title": (message.sender_name or message.sender_email or "New mail")[:120],
        "body": (message.subject or message.snippet or "")[:180],
        "account_label": account_label,
        "category": message.category or "other",
        "ask_company": "1" if message.ask_company else "0",
        "link": "",
    }
    extraction = message.extraction or {}
    links = extraction.get("links") or []
    if links:
        payload["link"] = links[0].get("url") or ""

    row = Notification(
        user_id=message.user_id,
        message_id=message.id,
        priority=message.priority,
        title=payload["title"],
        body=payload["body"],
        delivery_status=status,
        held_until=held_until,
        payload=payload,
    )
    db.add(row)
    db.flush()
    payload["notification_id"] = row.id
    row.payload = payload
    if status == "pending":
        deliver(db, row)
    return row


def deliver(db: Session, row: Notification) -> None:
    devices = db.query(Device).filter(Device.user_id == row.user_id).all()
    if not devices:
        row.delivery_status = "stored"
        row.sent_at = utcnow()
        row.fcm_error = None
        return
    errors = []
    sent = False
    for device in devices:
        try:
            send_fcm(device.fcm_token, {k: str(v) for k, v in (row.payload or {}).items()})
            sent = True
            device.last_seen_at = utcnow()
        except FCMError as exc:
            errors.append(str(exc)[:200])
    row.sent_at = utcnow()
    if sent:
        row.delivery_status = "sent"
        row.fcm_error = None
    else:
        row.delivery_status = "failed"
        row.fcm_error = "; ".join(errors)[:500]


class FCMError(Exception):
    pass


_cached_token: dict[str, float | str] = {}


def send_fcm(token: str, data: dict[str, str]) -> None:
    settings = get_settings()
    if not settings.fcm_project_id or not settings.fcm_service_account_file:
        raise FCMError("FCM is not configured")
    access = _fcm_access_token(settings.fcm_service_account_file)
    url = f"https://fcm.googleapis.com/v1/projects/{settings.fcm_project_id}/messages:send"
    # Data-only, high priority. The app must display a notification or Android may deprioritize later sends.
    body = {"message": {"token": token, "data": data, "android": {"priority": "HIGH"}}}
    response = httpx.post(url, headers={"Authorization": f"Bearer {access}"}, json=body, timeout=15)
    if response.status_code >= 400:
        raise FCMError(response.text[:300])


def _fcm_access_token(path: str) -> str:
    now = time.time()
    cached = _cached_token.get("token")
    exp = _cached_token.get("exp") or 0
    if isinstance(cached, str) and isinstance(exp, (int, float)) and exp - 60 > now:
        return cached
    with open(path, encoding="utf-8") as handle:
        info = json.load(handle)
    issued = int(now)
    assertion = jwt.encode(
        {
            "iss": info["client_email"],
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": issued,
            "exp": issued + 3600,
        },
        info["private_key"],
        algorithm="RS256",
    )
    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
        timeout=15,
    )
    if response.status_code >= 400:
        raise FCMError("Could not mint an FCM access token")
    token = response.json()["access_token"]
    _cached_token["token"] = token
    _cached_token["exp"] = issued + int(response.json().get("expires_in") or 3600)
    return token


def resend_due(db: Session) -> int:
    now = datetime.now(timezone.utc)
    rows = (
        db.query(Notification)
        .filter(
            Notification.priority == "urgent",
            Notification.acknowledged_at.is_(None),
            Notification.delivery_status.in_(["sent", "stored", "failed"]),
        )
        .all()
    )
    count = 0
    for row in rows:
        minutes = int(get_setting(db, row.user_id, "resend_after_minutes") or 5)
        limit = int(get_setting(db, row.user_id, "resend_limit") or 3)
        sent_at = aware(row.sent_at)
        if row.resend_count >= limit or sent_at is None:
            continue
        if sent_at + timedelta(minutes=minutes) > now:
            continue
        row.resend_count += 1
        deliver(db, row)
        count += 1
    return count


def flush_held(db: Session) -> int:
    now = datetime.now(timezone.utc)
    rows = db.query(Notification).filter(Notification.delivery_status == "held").all()
    count = 0
    for row in rows:
        message = db.get(Message, row.message_id)
        if message is None:
            continue
        focus = bool(get_setting(db, row.user_id, "focus_mode"))
        if focus and row.priority != "urgent":
            continue
        quiet = get_setting(db, row.user_id, "quiet_hours") or {}
        if _in_quiet_hours(quiet, now) and not (row.priority == "urgent" and quiet.get("allow_urgent", True)):
            continue
        row.delivery_status = "pending"
        deliver(db, row)
        count += 1
    return count
