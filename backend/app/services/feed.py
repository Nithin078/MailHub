"""Feed assembly. Priority mail is never hidden inside a group."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import MailGroup, Message, aware
from app.services.grouping import group_counts, visible_group
from app.services.settings_store import grouping_enabled
from app.services.views import account_map, iso, message_out


def build_feed(db: Session, user_id: str, *, lane: str = "all", account_id: str | None = None, limit: int = 60) -> dict:
    now = datetime.now(timezone.utc)
    query = db.query(Message).filter(
        Message.user_id == user_id,
        Message.is_trashed.is_(False),
        Message.is_spam.is_(False),
    )
    if account_id:
        query = query.filter(Message.account_id == account_id)
    if lane == "silenced":
        query = query.filter(Message.priority == "silent")
    elif lane == "urgent":
        query = query.filter(Message.priority == "urgent", Message.is_archived.is_(False))
    elif lane == "normal":
        query = query.filter(Message.priority == "normal", Message.is_archived.is_(False))
    elif lane == "digest":
        query = query.filter(Message.priority == "digest", Message.is_archived.is_(False))
    elif lane == "starred":
        query = query.filter(Message.is_starred.is_(True))
    else:
        query = query.filter(Message.priority != "silent", Message.is_archived.is_(False))

    rows = query.order_by(Message.received_at.desc()).limit(800).all()
    rows = [row for row in rows if not _snoozed(row, now)]
    accounts = account_map(db, user_id)
    grouping = grouping_enabled(db, user_id) == "yes" and lane in {"all", "digest", "silenced"}

    latest: dict[tuple[str, str], Message] = {}
    counts: dict[tuple[str, str], int] = {}
    urgent_counts: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row.account_id, row.thread_id)
        counts[key] = counts.get(key, 0) + 1
        if row.priority == "urgent":
            urgent_counts[key] = urgent_counts.get(key, 0) + 1
        if key not in latest:
            latest[key] = row

    hidden: set[str] = set()
    group_items = []
    if grouping:
        groups = db.query(MailGroup).filter(MailGroup.user_id == user_id).order_by(MailGroup.last_activity.desc()).all()
        for group in groups:
            stats = group_counts(db, group.id)
            if not visible_group(stats):
                continue
            members = stats["messages"]
            if account_id and all(member.account_id != account_id for member in members):
                continue
            silent_group = all(member.priority == "silent" for member in members)
            if lane == "silenced" and not silent_group:
                continue
            if lane != "silenced" and silent_group:
                continue
            latest_member = stats["latest"]
            group_items.append(
                {
                    "type": "group",
                    "id": group.id,
                    "sort_at": iso(group.last_activity),
                    "group": {
                        "id": group.id,
                        "title": group.title,
                        "kind": group.kind,
                        "count": stats["count"],
                        "unread_count": stats["unread"],
                        "urgent_count": stats["urgent"],
                        "latest_sender": (latest_member.sender_name or latest_member.sender_email) if latest_member else "",
                        "latest_subject": latest_member.subject if latest_member else group.title,
                        "latest_at": iso(latest_member.received_at) if latest_member else iso(group.last_activity),
                        "collapsed": group.collapsed,
                    },
                }
            )
            for member in members:
                if lane == "silenced" or member.priority not in {"urgent", "normal"}:
                    hidden.add(member.id)

    items = []
    for row in latest.values():
        if row.id in hidden and row.priority not in {"urgent", "normal"}:
            continue
        if lane == "silenced" and row.id in hidden:
            continue
        if lane not in {"all", "silenced", "digest"} and row.priority not in {"urgent", "normal", "digest", "silent"}:
            continue
        data = message_out(row, accounts, thread_count=counts.get((row.account_id, row.thread_id), 1))
        data["thread_urgent"] = urgent_counts.get((row.account_id, row.thread_id), 0)
        items.append({"type": "message", "id": row.id, "sort_at": iso(row.received_at), "pinned": row.pinned, "message": data})

    if lane in {"urgent", "normal", "starred"}:
        group_items = []

    combined = items + group_items
    combined.sort(key=lambda item: (not item.get("pinned", False), item.get("sort_at") or ""), reverse=True)
    # Pinned should win. The key above sorts pinned True first because `not True` is False, and reverse puts False last.
    # Flip: sort pinned first explicitly.
    combined.sort(key=lambda item: (1 if item.get("pinned") else 0, item.get("sort_at") or ""), reverse=True)
    base = db.query(Message).filter(
        Message.user_id == user_id,
        Message.is_trashed.is_(False),
        Message.is_spam.is_(False),
        Message.is_archived.is_(False),
    )
    if account_id:
        base = base.filter(Message.account_id == account_id)
    return {
        "items": combined[:limit],
        "lane": lane,
        "grouping": grouping_enabled(db, user_id),
        "counts": {
            "urgent": base.filter(Message.priority == "urgent").count(),
            "normal": base.filter(Message.priority == "normal").count(),
            "digest": base.filter(Message.priority == "digest").count(),
            "silent": db.query(Message).filter(Message.user_id == user_id, Message.priority == "silent", Message.is_trashed.is_(False)).count(),
        },
    }


def _snoozed(message: Message, now: datetime) -> bool:
    until = aware(message.snoozed_until)
    return bool(until and until > now)
