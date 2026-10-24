"""Sending mail. Demo accounts stay local. Gmail is used only after the user grants send scope."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Draft, GmailAccount, Message, Reminder, User, utcnow
from app.services.gmail import GmailError, has_modify_scope, send_message
from app.services.settings_store import get_setting


def deliver_draft(db: Session, draft: Draft) -> Message:
    account = db.get(GmailAccount, draft.account_id)
    user = db.get(User, draft.user_id)
    if account is None or user is None:
        raise RuntimeError("Draft is missing an account")
    signature = get_setting(db, user.id, "signature", account.id) or ""
    body = draft.body or ""
    if signature and signature not in body:
        body = f"{body}\n\n{signature}"
    reply = db.get(Message, draft.in_reply_to) if draft.in_reply_to else None
    if account.provider == "gmail":
        if not has_modify_scope(account) and "https://www.googleapis.com/auth/gmail.send" not in (account.scopes or []):
            raise GmailError("needs_scope", "Sending needs the compose scope. Grant it from Accounts.")
        sent = send_message(
            account,
            to=list(draft.to_addrs or []),
            cc=list(draft.cc_addrs or []),
            subject=draft.subject,
            body=body,
            in_reply_to=(reply.message_id_header if reply else None),
            thread_id=(reply.thread_id if reply else None),
        )
        gmail_id = sent.get("id") or f"sent-{uuid4()}"
        thread_id = sent.get("threadId") or (reply.thread_id if reply else gmail_id)
    else:
        gmail_id = f"sent-{uuid4()}"
        thread_id = reply.thread_id if reply else gmail_id
    message = Message(
        user_id=user.id,
        account_id=account.id,
        gmail_id=gmail_id,
        thread_id=thread_id,
        sender_name=user.name or account.email,
        sender_email=account.email,
        sender_domain=account.email.split("@")[-1],
        authenticated_domain=account.email.split("@")[-1],
        to_addrs=list(draft.to_addrs or []),
        cc_addrs=list(draft.cc_addrs or []),
        subject=draft.subject,
        snippet=body[:180],
        body_text=body,
        received_at=datetime.now(timezone.utc),
        labels=["SENT"],
        category="other",
        priority="silent",
        decision_reason="Sent from Inbox Agent",
        decision_trace=[{"step": "send", "result": "sent", "detail": account.email}],
        llm_called=False,
        is_read=True,
        status="processed",
    )
    db.add(message)
    db.flush()
    if draft.follow_up_days:
        db.add(
            Reminder(
                user_id=user.id,
                message_id=message.id,
                remind_at=utcnow() + timedelta(days=int(draft.follow_up_days)),
                kind="follow_up",
                note=f"No reply yet to “{draft.subject}”",
            )
        )
    db.delete(draft)
    return message
