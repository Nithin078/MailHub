"""JSON shapes shared by the API. Bodies are included only on the detail route."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    Folder,
    FolderItem,
    GmailAccount,
    MailNote,
    MailTag,
    Message,
    Notification,
    Opportunity,
    Rule,
    Tag,
    aware,
)
from app.services.rules import MailView
from app.services.textutil import sanitize_html


def iso(value: datetime | None) -> str | None:
    value = aware(value)
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def account_map(db: Session, user_id: str) -> dict[str, GmailAccount]:
    return {row.id: row for row in db.query(GmailAccount).filter(GmailAccount.user_id == user_id).all()}


def message_view(message: Message) -> MailView:
    return MailView(
        account_id=message.account_id,
        sender_email=message.sender_email,
        sender_name=message.sender_name,
        authenticated_domain=message.authenticated_domain,
        subject=message.subject or "",
        body=message.body_text,
        labels=list(message.labels or []),
        headers=dict(message.headers or {}),
        has_attachment=bool(message.has_attachment),
        received_at=aware(message.received_at) or message.received_at,
        to_addrs=list(message.to_addrs or []),
        cc_addrs=list(message.cc_addrs or []),
    )


def message_out(message: Message, accounts: dict[str, GmailAccount], *, thread_count: int | None = None) -> dict:
    account = accounts.get(message.account_id)
    return {
        "id": message.id,
        "account_id": message.account_id,
        "account_email": account.email if account else None,
        "account_label": (account.label or account.email) if account else None,
        "account_color": account.color if account else "#243e73",
        "gmail_id": message.gmail_id,
        "thread_id": message.thread_id,
        "sender_name": message.sender_name,
        "sender_email": message.sender_email,
        "authenticated_domain": message.authenticated_domain,
        "subject": message.subject,
        "snippet": message.snippet,
        "received_at": iso(message.received_at),
        "category": message.category,
        "priority": message.priority,
        "decision_reason": message.decision_reason,
        "is_read": message.is_read,
        "is_starred": message.is_starred,
        "is_archived": message.is_archived,
        "pinned": message.pinned,
        "has_attachment": message.has_attachment,
        "opportunity_id": message.opportunity_id,
        "ask_company": message.ask_company,
        "llm_called": message.llm_called,
        "llm_skipped_reason": message.llm_skipped_reason,
        "matched_rule_id": message.matched_rule_id,
        "labels": message.labels or [],
        "thread_count": thread_count or 1,
        "list_unsubscribe": bool(message.list_unsubscribe),
    }


def message_detail(db: Session, message: Message, accounts: dict[str, GmailAccount]) -> dict:
    data = message_out(message, accounts)
    siblings = (
        db.query(Message)
        .filter(Message.account_id == message.account_id, Message.thread_id == message.thread_id)
        .order_by(Message.received_at.asc())
        .all()
    )
    notes = db.query(MailNote).filter(MailNote.message_id == message.id).order_by(MailNote.created_at.asc()).all()
    tags = (
        db.query(Tag)
        .join(MailTag, MailTag.tag_id == Tag.id)
        .filter(MailTag.message_id == message.id)
        .all()
    )
    folders = (
        db.query(Folder)
        .join(FolderItem, FolderItem.folder_id == Folder.id)
        .filter(FolderItem.account_id == message.account_id, FolderItem.thread_id == message.thread_id)
        .all()
    )
    also = []
    if message.message_id_header:
        also = (
            db.query(Message)
            .filter(
                Message.user_id == message.user_id,
                Message.message_id_header == message.message_id_header,
                Message.id != message.id,
            )
            .all()
        )
    html = sanitize_html(message.body_html) if message.body_html else ""
    if not html and message.body_text:
        html = "<p>" + sanitize_html(message.body_text).replace("\n", "<br>") + "</p>"
    data.update(
        {
            "body_html": html,
            "body_text": message.body_text or message.snippet or "",
            "to": message.to_addrs or [],
            "cc": message.cc_addrs or [],
            "attachments": message.attachments or [],
            "headers": {
                key: value
                for key, value in (message.headers or {}).items()
                if key.lower() in {"from", "to", "cc", "subject", "date", "message-id", "list-unsubscribe", "authentication-results"}
            },
            "decision_trace": message.decision_trace or [],
            "extraction": message.extraction,
            "notes": [{"id": note.id, "body": note.body, "created_at": iso(note.created_at)} for note in notes],
            "tags": [{"id": tag.id, "name": tag.name, "color": tag.color} for tag in tags],
            "folders": [{"id": folder.id, "name": folder.name, "color": folder.color} for folder in folders],
            "thread": [message_out(item, accounts) for item in siblings],
            "also_in": [
                {"id": item.id, "account_email": accounts.get(item.account_id).email if accounts.get(item.account_id) else None}
                for item in also
            ],
            "notifications": [
                {
                    "id": row.id,
                    "priority": row.priority,
                    "delivery_status": row.delivery_status,
                    "sent_at": iso(row.sent_at),
                    "acknowledged_at": iso(row.acknowledged_at),
                    "resend_count": row.resend_count,
                }
                for row in db.query(Notification).filter(Notification.message_id == message.id).all()
            ],
        }
    )
    return data


def rule_out(rule: Rule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "conditions": rule.conditions,
        "exceptions": rule.exceptions,
        "action": rule.action,
        "position": rule.position,
        "enabled": rule.enabled,
        "account_scope": rule.account_scope,
        "origin": rule.origin,
        "plain_english": rule.plain_english,
        "hit_count": rule.hit_count,
        "last_hit_at": iso(rule.last_hit_at),
    }


def opportunity_out(opp: Opportunity) -> dict:
    return {
        "id": opp.id,
        "company": opp.company,
        "aliases": opp.aliases or [],
        "status": opp.status,
        "deadline": iso(opp.deadline),
        "notes": opp.notes,
        "created_at": iso(opp.created_at),
        "updated_at": iso(opp.updated_at),
    }
