"""Display-only groups. Nothing here writes back to Gmail or changes notifications.

A group row is created when a second matching mail arrives, then later mail joins it.
Existing members stay where they are. The feed hides groups smaller than MIN_GROUP_SIZE.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.constants import GROUP_WINDOW_DAYS, MIN_GROUP_SIZE, SUBJECT_SIMILARITY
from app.models import GroupMember, GroupPreference, MailGroup, Message, aware, utcnow
from app.services.settings_store import grouping_enabled
from app.services.textutil import normalize_subject, trigram_similarity


def assign_group(db: Session, message: Message) -> MailGroup | None:
    if grouping_enabled(db, message.user_id) != "yes":
        return None
    if db.query(GroupMember).filter(GroupMember.message_id == message.id).one_or_none():
        return None
    if _blocked(db, message, "never"):
        return None

    choice = _pick(db, message)
    if choice is None:
        return None
    group, kind, key, anchor, title, siblings = choice
    if group is None:
        group = MailGroup(
            user_id=message.user_id,
            title=(title or "Mail")[:300],
            kind=kind,
            key=key[:400],
            anchor=anchor or "",
            last_activity=message.received_at or utcnow(),
        )
        db.add(group)
        db.flush()
        for sibling in siblings:
            if db.query(GroupMember).filter(GroupMember.message_id == sibling.id).one_or_none():
                continue
            db.add(
                GroupMember(
                    group_id=group.id,
                    account_id=sibling.account_id,
                    message_id=sibling.id,
                    added_by="rule",
                )
            )
    group.last_activity = max(aware(group.last_activity), aware(message.received_at) or aware(group.last_activity))
    db.add(
        GroupMember(
            group_id=group.id,
            account_id=message.account_id,
            message_id=message.id,
            added_by="rule",
        )
    )
    db.flush()
    return group


def _blocked(db: Session, message: Message, mode: str) -> bool:
    email = (message.sender_email or "").lower()
    domain = (message.sender_domain or "").lower()
    prefs = (
        db.query(GroupPreference)
        .filter(GroupPreference.user_id == message.user_id, GroupPreference.mode == mode)
        .all()
    )
    for pref in prefs:
        if pref.kind == "sender" and pref.value.lower() == email:
            return True
        if pref.kind == "domain" and pref.value.lower() == domain:
            return True
    return False


def _window_start(when: datetime) -> datetime:
    return when - timedelta(days=GROUP_WINDOW_DAYS)


def _open_group(db: Session, user_id: str, key: str, when: datetime) -> MailGroup | None:
    return (
        db.query(MailGroup)
        .filter(
            MailGroup.user_id == user_id,
            MailGroup.key == key,
            MailGroup.last_activity >= _window_start(when),
        )
        .order_by(MailGroup.last_activity.desc())
        .first()
    )


def _ungrouped(db: Session, messages: list[Message]) -> list[Message]:
    if not messages:
        return []
    ids = [m.id for m in messages]
    taken = {
        row.message_id
        for row in db.query(GroupMember).filter(GroupMember.message_id.in_(ids)).all()
    }
    return [m for m in messages if m.id not in taken]


def _pick(db: Session, message: Message):
    when = message.received_at or datetime.now(timezone.utc)
    template = message.subject_template or normalize_subject(message.subject, [message.sender_name or ""])
    title = (message.subject or template or "Mail")[:300]

    if message.opportunity_id:
        key = f"opp:{message.opportunity_id}"
        siblings = (
            db.query(Message)
            .filter(
                Message.user_id == message.user_id,
                Message.opportunity_id == message.opportunity_id,
                Message.id != message.id,
                Message.received_at >= _window_start(when),
            )
            .all()
        )
        group = _open_group(db, message.user_id, key, when)
        if group or siblings:
            return group, "opportunity", key, message.opportunity_id, title, _ungrouped(db, siblings)
        return None

    if message.list_id:
        key = f"list:{message.list_id.lower()}"
        siblings = (
            db.query(Message)
            .filter(
                Message.user_id == message.user_id,
                Message.list_id == message.list_id,
                Message.id != message.id,
                Message.received_at >= _window_start(when),
            )
            .all()
        )
        group = _open_group(db, message.user_id, key, when)
        if group or siblings:
            return group, "list", key, message.list_id, message.list_id, _ungrouped(db, siblings)
        return None

    if _blocked(db, message, "always"):
        email = (message.sender_email or message.sender_domain or "").lower()
        key = f"always:{email}"
        group = _open_group(db, message.user_id, key, when)
        siblings = (
            db.query(Message)
            .filter(
                Message.user_id == message.user_id,
                Message.sender_email == message.sender_email,
                Message.id != message.id,
                Message.received_at >= _window_start(when),
            )
            .all()
        )
        return group, "always", key, template, title, _ungrouped(db, siblings)

    if message.sender_email and message.fingerprint:
        key = f"fp:{message.sender_email.lower()}:{message.fingerprint}"
        group = _open_group(db, message.user_id, key, when)
        siblings = (
            db.query(Message)
            .filter(
                Message.user_id == message.user_id,
                Message.sender_email == message.sender_email,
                Message.fingerprint == message.fingerprint,
                Message.id != message.id,
                Message.received_at >= _window_start(when),
            )
            .all()
        )
        if group or siblings:
            return group, "fingerprint", key, template, title, _ungrouped(db, siblings)

    domain = (message.authenticated_domain or message.sender_domain or "").lower()
    if domain and template:
        key = f"dom:{domain}"
        groups = (
            db.query(MailGroup)
            .filter(
                MailGroup.user_id == message.user_id,
                MailGroup.kind == "domain",
                MailGroup.key == key,
                MailGroup.last_activity >= _window_start(when),
            )
            .all()
        )
        for group in groups:
            if trigram_similarity(template, group.anchor or "") >= SUBJECT_SIMILARITY:
                return group, "domain", key, group.anchor, group.title, []
        recent = (
            db.query(Message)
            .filter(
                Message.user_id == message.user_id,
                Message.id != message.id,
                Message.received_at >= _window_start(when),
                Message.sender_domain == domain,
            )
            .all()
        )
        similar = [
            other
            for other in recent
            if trigram_similarity(template, other.subject_template or "") >= SUBJECT_SIMILARITY
        ]
        if similar:
            return None, "domain", key, template, title, _ungrouped(db, similar)
    return None


def group_counts(db: Session, group_id: str) -> dict:
    rows = (
        db.query(Message)
        .join(GroupMember, GroupMember.message_id == Message.id)
        .filter(GroupMember.group_id == group_id)
        .all()
    )
    latest = max(rows, key=lambda row: row.received_at) if rows else None
    return {
        "count": len(rows),
        "unread": sum(1 for row in rows if not row.is_read),
        "urgent": sum(1 for row in rows if row.priority == "urgent"),
        "silent": sum(1 for row in rows if row.priority == "silent"),
        "latest": latest,
        "messages": rows,
    }


def visible_group(counts: dict) -> bool:
    return counts["count"] >= MIN_GROUP_SIZE


def backfill(db: Session, user_id: str) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=GROUP_WINDOW_DAYS)
    messages = (
        db.query(Message)
        .filter(Message.user_id == user_id, Message.received_at >= cutoff)
        .order_by(Message.received_at.asc())
        .all()
    )
    made = 0
    for message in messages:
        if assign_group(db, message):
            made += 1
    return made
