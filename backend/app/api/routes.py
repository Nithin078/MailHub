"""REST API used by the web UI and the Android app."""

from __future__ import annotations

import hmac
import re
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import FINGERPRINT_SUGGEST_AT, GMAIL_MODIFY, GMAIL_READONLY, MAX_FOLDER_DEPTH
from app.crypto import encrypt, last4
from app.db import get_db
from app.models import (
    Category,
    Device,
    Draft,
    Fingerprint,
    Folder,
    FolderItem,
    GmailAccount,
    GroupMember,
    GroupPreference,
    LLMCredential,
    MailGroup,
    MailNote,
    MailTag,
    Message,
    Notification,
    Opportunity,
    OpportunityEvent,
    RecentSearch,
    Reminder,
    Rule,
    SavedSearch,
    Tag,
    TokenUsage,
    User,
    utcnow,
)
from app.security import create_access_token, hash_password, verify_password
from app.services.feed import build_feed
from app.services.folders import FolderError, create_folder, delete_folder, file_thread, move_folder, unread_map
from app.services.gmail import (
    GmailError,
    auth_url,
    classify_auth_error,
    exchange_code,
    get_profile,
    has_modify_scope,
    list_message_ids,
    modify_labels,
    start_watch,
    stop_watch,
    trash_message,
)
from app.services.gmail import get_message as fetch_gmail_message
from app.services.grouping import backfill, group_counts
from app.services.jobs import decode_push, enqueue
from app.services.llm import ai_status, make_provider, tokens_today, validate_key
from app.services.llm import run_plain_rule, run_reply_draft
from app.services.llm import EmailDoc
from app.services.outgoing import deliver_draft
from app.services.rules import first_match, rule_matches
from app.services.search import message_matches, parse_query
from app.services.seed import DEMO_EMAIL, reset_demo, seed_demo, simulate
from app.services.settings_store import get_setting, grouping_enabled, set_setting
from app.services.textutil import normalize_subject, prepare_body, redact
from app.services.views import account_map, iso, message_detail, message_out, message_view, opportunity_out, rule_out
from app.api.deps import current_user
from app.constants import BUILTIN_CATEGORIES

router = APIRouter()


class RegisterIn(BaseModel):
    email: str
    password: str = Field(min_length=8)
    name: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class RuleIn(BaseModel):
    name: str = "Untitled rule"
    conditions: dict
    exceptions: dict | None = None
    action: dict
    enabled: bool = True
    account_scope: list[str] | None = None
    plain_english: str | None = None
    origin: str = "editor"


class FolderIn(BaseModel):
    name: str
    parent_id: str | None = None
    color: str = "#243e73"
    icon: str = "folder"


class SendIn(BaseModel):
    account_id: str
    to: list[str]
    cc: list[str] = []
    bcc: list[str] = []
    subject: str = ""
    body: str = ""
    reply_to_message_id: str | None = None
    mode: str = "compose"
    send_at: datetime | None = None
    follow_up_days: int | None = None


def _user_out(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name}


def _token(user: User) -> dict:
    return {"access_token": create_access_token(user.id), "token_type": "bearer", "user": _user_out(user)}


def _categories(db: Session, user_id: str) -> None:
    if db.query(Category).filter(Category.user_id == user_id).first():
        return
    for name, definition in BUILTIN_CATEGORIES:
        db.add(Category(user_id=user_id, name=name, definition=definition, builtin=True))


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(func.now() if False else __import__("sqlalchemy").text("SELECT 1"))
    settings = get_settings()
    return {
        "ok": True,
        "demo": settings.demo_mode,
        "queue": settings.resolved_queue_backend(),
        "google": bool(settings.google_client_id),
        "fcm": bool(settings.fcm_project_id and settings.fcm_service_account_file),
    }


@router.post("/auth/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "An account with that email already exists")
    user = User(email=email, password_hash=hash_password(body.password), name=body.name.strip())
    db.add(user)
    db.flush()
    _categories(db, user.id)
    db.commit()
    return _token(user)


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.strip().lower()).one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Email or password is wrong")
    return _token(user)


@router.post("/auth/demo")
def demo(db: Session = Depends(get_db)):
    if not get_settings().demo_mode:
        raise HTTPException(404, "Demo mode is off")
    user = seed_demo(db)
    db.commit()
    return _token(user)


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return _user_out(user)


@router.get("/feed")
def feed(lane: str = "all", account_id: str | None = None, limit: int = 60, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return build_feed(db, user.id, lane=lane, account_id=account_id, limit=min(limit, 200))


@router.get("/messages/{message_id}")
def message_one(message_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _owned_message(db, user, message_id)
    _ensure_body(db, message)
    return message_detail(db, message, account_map(db, user.id))


@router.post("/messages/{message_id}/read")
def mark_read(message_id: str, read: bool = True, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _owned_message(db, user, message_id)
    message.is_read = read
    _gmail_labels(db, message, add=[] if read else ["UNREAD"], remove=["UNREAD"] if read else [])
    db.commit()
    return {"ok": True}


@router.post("/messages/{message_id}/star")
def star(message_id: str, starred: bool = True, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _owned_message(db, user, message_id)
    message.is_starred = starred
    _gmail_labels(db, message, add=["STARRED"] if starred else [], remove=[] if starred else ["STARRED"])
    db.commit()
    return {"ok": True}


@router.post("/messages/{message_id}/pin")
def pin(message_id: str, pinned: bool = True, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _owned_message(db, user, message_id)
    message.pinned = pinned
    db.commit()
    return {"ok": True}


@router.post("/messages/{message_id}/archive")
def archive(message_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _owned_message(db, user, message_id)
    message.is_archived = True
    _gmail_labels(db, message, remove=["INBOX"])
    db.commit()
    return {"ok": True}


@router.post("/messages/{message_id}/trash")
def trash(message_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _owned_message(db, user, message_id)
    message.is_trashed = True
    account = db.get(GmailAccount, message.account_id)
    if account and account.provider == "gmail":
        _guard_scope(account)
        try:
            trash_message(account, message.gmail_id)
        except GmailError as exc:
            _apply_gmail_error(account, exc)
    db.commit()
    return {"ok": True}


@router.post("/messages/{message_id}/spam")
def spam(message_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _owned_message(db, user, message_id)
    message.is_spam = True
    _gmail_labels(db, message, add=["SPAM"], remove=["INBOX"])
    db.commit()
    return {"ok": True}


class SnoozeIn(BaseModel):
    until: datetime


@router.post("/messages/{message_id}/snooze")
def snooze(message_id: str, body: SnoozeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _owned_message(db, user, message_id)
    message.snoozed_until = body.until
    db.commit()
    return {"ok": True, "snoozed_until": iso(body.until)}


@router.post("/messages/{message_id}/ignore-like-this")
def ignore_like_this(message_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _owned_message(db, user, message_id)
    if not message.authenticated_domain:
        raise HTTPException(400, "This sender did not pass DKIM or SPF, so a sender rule would be unsafe.")
    template = message.subject_template or normalize_subject(message.subject)
    pattern = _template_regex(template)
    position = (db.query(func.max(Rule.position)).filter(Rule.user_id == user.id).scalar() or 0) + 10
    rule = Rule(
        user_id=user.id,
        name=f"Ignore mail like “{(message.subject or 'this')[:48]}”",
        conditions={"all": [
            {"field": "sender_domain", "op": "eq", "value": message.authenticated_domain},
            {"field": "subject", "op": "regex", "value": pattern},
        ]},
        action={"type": "store_silent"},
        position=position,
        origin="one_tap",
    )
    db.add(rule)
    fingerprint = db.query(Fingerprint).filter(Fingerprint.user_id == user.id, Fingerprint.fingerprint == message.fingerprint).one_or_none()
    if fingerprint:
        fingerprint.last_action = "ignored"
    db.commit()
    return rule_out(rule)


class NoteIn(BaseModel):
    body: str


@router.post("/messages/{message_id}/notes")
def add_note(message_id: str, body: NoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _owned_message(db, user, message_id)
    note = MailNote(user_id=user.id, message_id=message.id, body=body.body.strip())
    db.add(note)
    db.commit()
    return {"id": note.id, "body": note.body}


class AttachIn(BaseModel):
    opportunity_id: str | None = None
    company: str | None = None


@router.post("/messages/{message_id}/attach")
def attach(message_id: str, body: AttachIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _owned_message(db, user, message_id)
    if body.opportunity_id:
        opp = _owned_opp(db, user, body.opportunity_id)
    elif body.company:
        opp = Opportunity(user_id=user.id, company=body.company.strip(), status="active")
        db.add(opp)
        db.flush()
    else:
        raise HTTPException(400, "Choose a timeline or name a company")
    message.opportunity_id = opp.id
    message.ask_company = False
    db.add(
        OpportunityEvent(
            opportunity_id=opp.id,
            message_id=message.id,
            event_type=message.category if message.category not in {"other", "fyi"} else "note",
            details={"subject": message.subject, "summary": "Attached by you"},
            added_by="user",
        )
    )
    opp.updated_at = utcnow()
    db.commit()
    return opportunity_out(opp)


@router.post("/messages/{message_id}/reply-draft")
def reply_draft(message_id: str, body: NoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if ai_status(db, user.id) != "on":
        raise HTTPException(403, "Add an API key to enable")
    message = _owned_message(db, user, message_id)
    doc = EmailDoc(
        sender=message.sender_email or "",
        subject=redact(message.subject),
        body=redact(prepare_body(message.body_text, message.body_html, 1500)),
    )
    text = run_reply_draft(db, user.id, doc, instruction=body.body or "Write a short polite reply.")
    db.commit()
    if not text:
        raise HTTPException(502, "The model did not return a draft")
    return {"draft": text}


@router.post("/messages/{message_id}/unsubscribe")
def unsubscribe(message_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _owned_message(db, user, message_id)
    header = message.list_unsubscribe or ""
    if not header:
        raise HTTPException(400, "This mail has no List-Unsubscribe header")
    import httpx

    one_click = "one-click" in (message.headers or {}).get("List-Unsubscribe-Post", "").lower()
    https = re.findall(r"<(https?://[^>]+)>", header)
    mailto = re.findall(r"<(mailto:[^>]+)>", header)
    if https and one_click:
        try:
            httpx.post(https[0], data={"List-Unsubscribe": "One-Click"}, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Unsubscribe request failed ({exc.__class__.__name__})") from exc
        return {"ok": True, "action": "one-click"}
    if https:
        return {"ok": True, "action": "open", "url": https[0]}
    if mailto:
        return {"ok": True, "action": "mailto", "url": mailto[0]}
    raise HTTPException(400, "The unsubscribe header could not be read")


@router.get("/rules")
def list_rules(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rules = db.query(Rule).filter(Rule.user_id == user.id).order_by(Rule.position.asc()).all()
    return [rule_out(rule) for rule in rules]


@router.post("/rules")
def create_rule(body: RuleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    position = (db.query(func.max(Rule.position)).filter(Rule.user_id == user.id).scalar() or 0) + 10
    rule = Rule(
        user_id=user.id,
        name=body.name,
        conditions=body.conditions,
        exceptions=body.exceptions,
        action=body.action,
        enabled=body.enabled,
        account_scope=body.account_scope,
        plain_english=body.plain_english,
        origin=body.origin,
        position=position,
    )
    db.add(rule)
    db.commit()
    return rule_out(rule)


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: str, body: RuleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rule = _owned_rule(db, user, rule_id)
    for field in ("name", "conditions", "exceptions", "action", "enabled", "account_scope", "plain_english"):
        setattr(rule, field, getattr(body, field))
    db.commit()
    return rule_out(rule)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rule = _owned_rule(db, user, rule_id)
    db.delete(rule)
    db.commit()
    return {"ok": True}


class PreviewIn(BaseModel):
    conditions: dict
    exceptions: dict | None = None
    account_scope: list[str] | None = None


@router.post("/rules/preview")
def preview_rule(body: PreviewIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    probe = Rule(
        user_id=user.id,
        conditions=body.conditions,
        exceptions=body.exceptions,
        action={"type": "store_silent"},
        account_scope=body.account_scope,
        enabled=True,
    )
    rows = (
        db.query(Message)
        .filter(Message.user_id == user.id)
        .order_by(Message.received_at.desc())
        .limit(500)
        .all()
    )
    hits = [row for row in rows if rule_matches(probe, message_view(row))]
    accounts = account_map(db, user.id)
    return {
        "matched": len(hits),
        "scanned": len(rows),
        "samples": [
            {
                "id": row.id,
                "sender": row.sender_email,
                "subject": row.subject,
                "received_at": iso(row.received_at),
                "account_email": accounts[row.account_id].email if row.account_id in accounts else None,
            }
            for row in hits[:12]
        ],
    }


class PlainIn(BaseModel):
    text: str


@router.post("/rules/plain-english")
def plain_rule(body: PlainIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if ai_status(db, user.id) != "on":
        raise HTTPException(403, "Add an API key to enable")
    try:
        proposed = run_plain_rule(db, user.id, body.text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "The model could not build a rule") from exc
    db.commit()
    if not proposed:
        raise HTTPException(502, "The model could not build a rule")
    proposed["plain_english"] = body.text
    return proposed


class ReorderIn(BaseModel):
    ids: list[str]


@router.post("/rules/reorder")
def reorder(body: ReorderIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    for index, rule_id in enumerate(body.ids):
        rule = _owned_rule(db, user, rule_id)
        rule.position = index * 10
    db.commit()
    return {"ok": True}


@router.get("/rules/suggestions")
def suggestions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Fingerprint)
        .filter(
            Fingerprint.user_id == user.id,
            Fingerprint.seen_count >= FINGERPRINT_SUGGEST_AT,
            Fingerprint.last_action.is_(None),
        )
        .all()
    )
    found = []
    for row in rows:
        latest = (
            db.query(Message)
            .filter(Message.user_id == user.id, Message.fingerprint == row.fingerprint)
            .order_by(Message.received_at.desc())
            .first()
        )
        if latest and latest.priority in {"silent", "digest"}:
            found.append(
                {
                    "fingerprint": row.fingerprint,
                    "subject_template": row.subject_template,
                    "sender_domain": row.sender_domain,
                    "seen_count": row.seen_count,
                }
            )
    return found


@router.get("/categories")
def categories(user: User = Depends(current_user), db: Session = Depends(get_db)):
    _categories(db, user.id)
    db.commit()
    return [
        {"id": row.id, "name": row.name, "definition": row.definition, "builtin": row.builtin, "color": row.color}
        for row in db.query(Category).filter(Category.user_id == user.id).all()
    ]


class CategoryIn(BaseModel):
    name: str
    definition: str = ""
    color: str = "#5e584f"


@router.post("/categories")
def add_category(body: CategoryIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = Category(user_id=user.id, name=body.name.strip(), definition=body.definition, color=body.color, builtin=False)
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name}


@router.get("/opportunities")
def opportunities(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Opportunity).filter(Opportunity.user_id == user.id).order_by(Opportunity.updated_at.desc()).all()
    result = []
    for row in rows:
        data = opportunity_out(row)
        data["events"] = _events(db, row.id)
        result.append(data)
    return result


class OppIn(BaseModel):
    company: str
    aliases: list[str] = []
    status: str = "active"
    deadline: datetime | None = None
    notes: str = ""


@router.post("/opportunities")
def create_opp(body: OppIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = Opportunity(
        user_id=user.id,
        company=body.company.strip(),
        aliases=body.aliases,
        status=body.status,
        deadline=body.deadline,
        notes=body.notes,
    )
    db.add(row)
    db.commit()
    return opportunity_out(row)


@router.patch("/opportunities/{opp_id}")
def update_opp(opp_id: str, body: OppIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _owned_opp(db, user, opp_id)
    row.company = body.company.strip()
    row.aliases = body.aliases
    row.status = body.status
    row.deadline = body.deadline
    row.notes = body.notes
    row.updated_at = utcnow()
    db.commit()
    return opportunity_out(row)


@router.get("/opportunities/{opp_id}")
def one_opp(opp_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _owned_opp(db, user, opp_id)
    data = opportunity_out(row)
    data["events"] = _events(db, row.id)
    return data


@router.get("/notifications")
def notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Notification)
        .filter(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": row.id,
            "message_id": row.message_id,
            "priority": row.priority,
            "title": row.title,
            "body": row.body,
            "delivery_status": row.delivery_status,
            "sent_at": iso(row.sent_at),
            "acknowledged_at": iso(row.acknowledged_at),
            "resend_count": row.resend_count,
            "created_at": iso(row.created_at),
        }
        for row in rows
    ]


@router.post("/notifications/{note_id}/ack")
def ack(note_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(Notification, note_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    row.acknowledged_at = utcnow()
    db.commit()
    return {"ok": True}


class DeviceIn(BaseModel):
    fcm_token: str
    name: str = ""
    platform: str = "android"


@router.post("/devices")
def register_device(body: DeviceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.query(Device).filter(Device.fcm_token == body.fcm_token).one_or_none()
    if row is None:
        row = Device(user_id=user.id, fcm_token=body.fcm_token, name=body.name, platform=body.platform)
        db.add(row)
    else:
        row.user_id = user.id
        row.name = body.name or row.name
        row.last_seen_at = utcnow()
    db.commit()
    return {"id": row.id}


@router.get("/settings")
def read_settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _settings_out(db, user)


class SettingsIn(BaseModel):
    grouping_enabled: str | None = None
    grouping_prompt_dismissals: int | None = None
    quiet_hours: dict | None = None
    focus_mode: bool | None = None
    category_mutes: list[str] | None = None
    store_body_text: bool | None = None
    timezone: str | None = None
    resend_after_minutes: int | None = None
    resend_limit: int | None = None
    signature: str | None = None
    notify_enabled: bool | None = None
    account_id: str | None = None


@router.put("/settings")
def write_settings(body: SettingsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account_id = body.account_id or ""
    previous = grouping_enabled(db, user.id)
    payload = body.model_dump(exclude_none=True)
    payload.pop("account_id", None)
    for key, value in payload.items():
        set_setting(db, user.id, key, value, account_id)
    if body.grouping_enabled == "yes" and previous != "yes":
        backfill(db, user.id)
    db.commit()
    return _settings_out(db, user)


class LLMIn(BaseModel):
    provider: str = "gemini"
    model: str = ""
    api_key: str
    base_url: str | None = None
    daily_token_cap: int = 20000
    fallback_provider: str | None = None
    fallback_model: str | None = None
    fallback_base_url: str | None = None
    fallback_api_key: str | None = None


@router.get("/llm")
def llm_state(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _llm_out(db, user)


@router.put("/llm")
def save_llm(body: LLMIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    status, message = validate_key(body.provider, body.model, body.api_key, body.base_url)
    row = db.query(LLMCredential).filter(LLMCredential.user_id == user.id).one_or_none()
    if row is None:
        row = LLMCredential(user_id=user.id)
        db.add(row)
    row.provider = body.provider
    row.model = body.model
    row.base_url = body.base_url
    row.encrypted_api_key = encrypt(body.api_key)
    row.key_last4 = last4(body.api_key)
    row.status = status
    row.status_message = message
    row.last_validated_at = utcnow()
    row.daily_token_cap = body.daily_token_cap
    row.fallback_provider = body.fallback_provider
    row.fallback_model = body.fallback_model
    row.fallback_base_url = body.fallback_base_url
    if body.fallback_api_key:
        row.encrypted_fallback_key = encrypt(body.fallback_api_key)
    db.commit()
    return _llm_out(db, user)


@router.delete("/llm")
def delete_llm(user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.query(LLMCredential).filter(LLMCredential.user_id == user.id).delete()
    db.commit()
    return _llm_out(db, user)


@router.post("/llm/test")
def test_llm(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.crypto import decrypt

    row = db.query(LLMCredential).filter(LLMCredential.user_id == user.id).one_or_none()
    if row is None or not row.encrypted_api_key:
        raise HTTPException(400, "Add an API key to enable")
    status, message = validate_key(row.provider, row.model, decrypt(row.encrypted_api_key) or "", row.base_url)
    row.status = status
    row.status_message = message
    row.last_validated_at = utcnow()
    db.commit()
    return _llm_out(db, user)


@router.get("/llm/usage")
def usage(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(TokenUsage)
        .filter(TokenUsage.user_id == user.id)
        .order_by(TokenUsage.created_at.desc())
        .limit(200)
        .all()
    )
    return {
        "today": tokens_today(db, user.id),
        "cap": _cap(db, user.id),
        "calls": [
            {
                "provider": row.provider,
                "model": row.model,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "purpose": row.purpose,
                "created_at": iso(row.created_at),
            }
            for row in rows
        ],
    }


@router.get("/folders")
def folders(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Folder).filter(Folder.user_id == user.id).order_by(Folder.position.asc(), Folder.name.asc()).all()
    counts = unread_map(db, user.id)
    return {
        "max_depth": MAX_FOLDER_DEPTH,
        "folders": [
            {
                "id": row.id,
                "parent_id": row.parent_id,
                "name": row.name,
                "color": row.color,
                "icon": row.icon,
                "position": row.position,
                "notify_mode": row.notify_mode,
                "pinned": row.pinned,
                "is_smart": row.is_smart,
                "saved_search_id": row.saved_search_id,
                "unread": counts.get(row.id, 0),
            }
            for row in rows
        ],
    }


@router.post("/folders")
def add_folder(body: FolderIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        folder = create_folder(db, user.id, body.name, body.parent_id, color=body.color, icon=body.icon)
        db.commit()
    except FolderError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": folder.id, "name": folder.name}


class FolderPatch(BaseModel):
    name: str | None = None
    color: str | None = None
    icon: str | None = None
    parent_id: str | None = None
    notify_mode: str | None = None
    pinned: bool | None = None
    move: bool = False


@router.patch("/folders/{folder_id}")
def patch_folder(folder_id: str, body: FolderPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    folder = _owned_folder(db, user, folder_id)
    if body.name is not None:
        folder.name = body.name
    if body.color is not None:
        folder.color = body.color
    if body.icon is not None:
        folder.icon = body.icon
    if body.notify_mode is not None:
        folder.notify_mode = body.notify_mode
    if body.pinned is not None:
        folder.pinned = body.pinned
    if body.move:
        try:
            move_folder(db, folder, body.parent_id)
        except FolderError as exc:
            raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"ok": True}


@router.delete("/folders/{folder_id}")
def remove_folder(folder_id: str, subfolders: str = "promote", user: User = Depends(current_user), db: Session = Depends(get_db)):
    folder = _owned_folder(db, user, folder_id)
    delete_folder(db, folder, subfolders=subfolders)
    db.commit()
    return {"ok": True}


class FileIn(BaseModel):
    message_id: str | None = None
    account_id: str | None = None
    thread_id: str | None = None


@router.post("/folders/{folder_id}/file")
def file_mail(folder_id: str, body: FileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    folder = _owned_folder(db, user, folder_id)
    if body.message_id:
        message = _owned_message(db, user, body.message_id)
        account_id, thread_id = message.account_id, message.thread_id
    else:
        account_id, thread_id = body.account_id, body.thread_id
    if not account_id or not thread_id:
        raise HTTPException(400, "Say which thread to file")
    try:
        file_thread(db, folder, account_id, thread_id)
    except FolderError as exc:
        raise HTTPException(400, str(exc)) from exc
    set_setting(db, user.id, "last_used_folder_id", folder.id)
    db.commit()
    return {"ok": True}


@router.get("/folders/{folder_id}/messages")
def folder_messages(folder_id: str, include_subfolders: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    folder = _owned_folder(db, user, folder_id)
    ids = {folder.id}
    if include_subfolders:
        _collect_children(db, folder.id, ids)
    items = db.query(FolderItem).filter(FolderItem.folder_id.in_(ids)).all()
    accounts = account_map(db, user.id)
    messages = []
    for item in items:
        latest = (
            db.query(Message)
            .filter(Message.account_id == item.account_id, Message.thread_id == item.thread_id, Message.user_id == user.id)
            .order_by(Message.received_at.desc())
            .first()
        )
        if latest:
            messages.append(message_out(latest, accounts))
    messages.sort(key=lambda row: row["received_at"] or "", reverse=True)
    return {"folder": folder.name, "messages": messages}


@router.post("/search")
def search(body: PlainIn, account_id: str | None = None, limit: int = 30, offset: int = 0, user: User = Depends(current_user), db: Session = Depends(get_db)):
    parsed = parse_query(body.text)
    db.add(RecentSearch(user_id=user.id, query=body.text))
    accounts = account_map(db, user.id)
    rows = db.query(Message).filter(Message.user_id == user.id, Message.is_trashed.is_(False)).all()
    matched = []
    for row in rows:
        if account_id and row.account_id != account_id:
            continue
        if message_matches(_search_view(db, row, accounts), parsed.tree):
            matched.append(row)
    reverse = parsed.sort != "oldest"
    if parsed.sort == "sender":
        matched.sort(key=lambda row: (row.sender_email or ""))
    elif parsed.sort == "size":
        matched.sort(key=lambda row: row.size_bytes or 0, reverse=True)
    else:
        matched.sort(key=lambda row: row.received_at, reverse=reverse)
    page = matched[offset : offset + limit]
    gmail_note = None
    live_accounts = [account for account in accounts.values() if account.provider == "gmail" and account.status == "active"]
    if parsed.gmail_q and live_accounts:
        gmail_note = "Gmail will be queried with this string when you search a connected account."
    return {
        "query": body.text,
        "gmail_q": parsed.gmail_q,
        "sort": parsed.sort,
        "total": len(matched),
        "next_offset": offset + limit if offset + limit < len(matched) else None,
        "note": gmail_note,
        "results": [message_out(row, accounts) for row in page],
    }


@router.get("/search/suggestions")
def search_suggestions(q: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    needle = q.lower()
    senders = (
        db.query(Message.sender_email, Message.sender_name)
        .filter(Message.user_id == user.id, Message.sender_email.isnot(None))
        .distinct()
        .limit(200)
        .all()
    )
    folders = db.query(Folder).filter(Folder.user_id == user.id).all()
    tags = db.query(Tag).filter(Tag.user_id == user.id).all()
    saved = db.query(SavedSearch).filter(SavedSearch.user_id == user.id).all()
    recent = db.query(RecentSearch).filter(RecentSearch.user_id == user.id).order_by(RecentSearch.created_at.desc()).limit(8).all()

    def hit(value: str) -> bool:
        return not needle or needle in value.lower()

    return {
        "senders": [{"email": email, "name": name} for email, name in senders if email and hit(email)][:8],
        "folders": [{"id": row.id, "name": row.name} for row in folders if hit(row.name)][:8],
        "tags": [row.name for row in tags if hit(row.name)][:8],
        "saved": [{"id": row.id, "name": row.name, "query": row.query} for row in saved if hit(row.name) or hit(row.query)][:8],
        "recent": [row.query for row in recent],
    }


class SavedIn(BaseModel):
    name: str
    query: str
    pinned: bool = False
    notify: bool = False
    smart: bool = False


@router.post("/saved-searches")
def save_search(body: SavedIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = SavedSearch(user_id=user.id, name=body.name, query=body.query, pinned=body.pinned, notify=body.notify)
    db.add(row)
    db.flush()
    if body.smart:
        create_folder(db, user.id, body.name, None, is_smart=True, saved_search_id=row.id, icon="search")
    db.commit()
    return {"id": row.id}


@router.get("/groups")
def groups(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if grouping_enabled(db, user.id) != "yes":
        return []
    rows = db.query(MailGroup).filter(MailGroup.user_id == user.id).order_by(MailGroup.last_activity.desc()).all()
    result = []
    for row in rows:
        stats = group_counts(db, row.id)
        if stats["count"] < 3:
            continue
        latest = stats["latest"]
        result.append(
            {
                "id": row.id,
                "title": row.title,
                "kind": row.kind,
                "count": stats["count"],
                "unread_count": stats["unread"],
                "urgent_count": stats["urgent"],
                "latest_subject": latest.subject if latest else "",
                "latest_sender": (latest.sender_name or latest.sender_email) if latest else "",
                "collapsed": row.collapsed,
            }
        )
    return result


@router.get("/groups/{group_id}")
def group_detail(group_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    group = _owned_group(db, user, group_id)
    stats = group_counts(db, group.id)
    accounts = account_map(db, user.id)
    return {
        "id": group.id,
        "title": group.title,
        "kind": group.kind,
        "messages": [message_out(row, accounts) for row in sorted(stats["messages"], key=lambda item: item.received_at, reverse=True)],
    }


class GroupPatch(BaseModel):
    title: str | None = None
    collapsed: bool | None = None


@router.patch("/groups/{group_id}")
def patch_group(group_id: str, body: GroupPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    group = _owned_group(db, user, group_id)
    if body.title is not None:
        group.title = body.title
    if body.collapsed is not None:
        group.collapsed = body.collapsed
    db.commit()
    return {"ok": True}


@router.post("/groups/{group_id}/remove/{message_id}")
def ungroup(group_id: str, message_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _owned_group(db, user, group_id)
    db.query(GroupMember).filter(GroupMember.group_id == group_id, GroupMember.message_id == message_id).delete()
    db.commit()
    return {"ok": True}


class PrefIn(BaseModel):
    kind: str
    value: str
    mode: str


@router.post("/group-preferences")
def group_pref(body: PrefIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.mode not in {"always", "never"} or body.kind not in {"sender", "domain"}:
        raise HTTPException(400, "Preference not understood")
    row = (
        db.query(GroupPreference)
        .filter(GroupPreference.user_id == user.id, GroupPreference.kind == body.kind, GroupPreference.value == body.value)
        .one_or_none()
    )
    if row is None:
        row = GroupPreference(user_id=user.id, kind=body.kind, value=body.value, mode=body.mode)
        db.add(row)
    else:
        row.mode = body.mode
    db.commit()
    return {"ok": True}


@router.get("/accounts")
def accounts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(GmailAccount).filter(GmailAccount.user_id == user.id).all()
    result = []
    for row in rows:
        unread = (
            db.query(Message)
            .filter(Message.account_id == row.id, Message.is_read.is_(False), Message.is_trashed.is_(False))
            .count()
        )
        result.append(
            {
                "id": row.id,
                "email": row.email,
                "label": row.label,
                "color": row.color,
                "status": row.status,
                "status_message": row.status_message,
                "provider": row.provider,
                "watch_expiry": iso(row.watch_expiry),
                "scopes": row.scopes or [],
                "unread": unread,
            }
        )
    return result


@router.post("/accounts/connect/start")
def connect_start(upgrade: bool = False, account_id: str | None = None, user: User = Depends(current_user)):
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(400, "Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")
    state = jwt.encode(
        {"uid": user.id, "account_id": account_id, "exp": datetime.now(timezone.utc) + timedelta(minutes=20)},
        settings.secret_key,
        algorithm="HS256",
    )
    scopes = [GMAIL_READONLY, GMAIL_MODIFY] if upgrade else [GMAIL_READONLY]
    return {"url": auth_url(state, scopes)}


@router.get("/accounts/connect/callback")
def connect_callback(code: str | None = None, state: str | None = None, error: str | None = None, error_description: str | None = None, db: Session = Depends(get_db)):
    settings = get_settings()
    web = settings.web_origin.rstrip("/")
    if error:
        kind = classify_auth_error(error, error_description)
        if state:
            _mark_oauth_failure(db, state, kind, error_description or error)
        return Response(status_code=302, headers={"Location": f"{web}/accounts?error={kind}"})
    if not code or not state:
        raise HTTPException(400, "Missing OAuth code")
    try:
        payload = jwt.decode(state, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(400, "OAuth state expired") from exc
    user = db.get(User, payload.get("uid"))
    if user is None:
        raise HTTPException(400, "Unknown user")
    try:
        tokens = exchange_code(code)
    except GmailError as exc:
        _mark_account(db, user.id, payload.get("account_id"), exc.status, str(exc))
        db.commit()
        return Response(status_code=302, headers={"Location": f"{web}/accounts?error={exc.status}"})
    scopes = (tokens.get("scope") or GMAIL_READONLY).split()
    account = _account_for_callback(db, user.id, payload.get("account_id"))
    account.encrypted_refresh_token = encrypt(tokens.get("refresh_token")) or account.encrypted_refresh_token
    account.encrypted_access_token = encrypt(tokens.get("access_token"))
    account.access_token_expiry = utcnow() + timedelta(seconds=int(tokens.get("expires_in") or 3600) - 60)
    account.scopes = scopes
    account.status = "active"
    account.status_message = None
    try:
        profile = get_profile(account)
        account.email = profile.get("emailAddress") or account.email
        account.history_id = str(profile.get("historyId") or account.history_id or "")
        start_watch(account)
    except GmailError as exc:
        account.status = exc.status if exc.status in {"needs_reconnect", "blocked"} else "needs_reconnect"
        account.status_message = str(exc)[:300]
    if not account.label:
        account.label = account.email
    db.commit()
    return Response(status_code=302, headers={"Location": f"{web}/accounts?connected=1"})


@router.delete("/accounts/{account_id}")
def disconnect(account_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = db.get(GmailAccount, account_id)
    if account is None or account.user_id != user.id:
        raise HTTPException(404, "Account not found")
    if account.provider == "gmail":
        try:
            stop_watch(account)
        except GmailError:
            pass
    message_ids = [row.id for row in db.query(Message.id).filter(Message.account_id == account.id)]
    if message_ids:
        db.query(Notification).filter(Notification.message_id.in_(message_ids)).delete(synchronize_session=False)
        db.query(MailNote).filter(MailNote.message_id.in_(message_ids)).delete(synchronize_session=False)
        db.query(MailTag).filter(MailTag.message_id.in_(message_ids)).delete(synchronize_session=False)
        db.query(GroupMember).filter(GroupMember.message_id.in_(message_ids)).delete(synchronize_session=False)
    db.query(Message).filter(Message.account_id == account.id).delete(synchronize_session=False)
    db.query(FolderItem).filter(FolderItem.account_id == account.id).delete(synchronize_session=False)
    db.delete(account)
    db.commit()
    return {"ok": True}


@router.get("/accounts/{account_id}/senders")
def top_senders(account_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = db.get(GmailAccount, account_id)
    if account is None or account.user_id != user.id:
        raise HTTPException(404, "Account not found")
    since = datetime.now(timezone.utc) - timedelta(days=30)
    rows = (
        db.query(Message)
        .filter(Message.account_id == account.id, Message.received_at >= since)
        .all()
    )
    if account.provider == "gmail" and account.status == "active" and len(rows) < 5:
        rows = _remote_headers(account) or rows
    counts: dict[str, dict] = {}
    for row in rows:
        key = row.sender_email or "unknown"
        bucket = counts.setdefault(key, {"email": key, "name": row.sender_name, "count": 0, "domain": row.authenticated_domain})
        bucket["count"] += 1
    ordered = sorted(counts.values(), key=lambda item: item["count"], reverse=True)[:30]
    return ordered


class SenderAction(BaseModel):
    email: str
    action: str


class OnboardingIn(BaseModel):
    assignments: list[SenderAction]


@router.post("/accounts/{account_id}/onboarding")
def onboarding(account_id: str, body: OnboardingIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = db.get(GmailAccount, account_id)
    if account is None or account.user_id != user.id:
        raise HTTPException(404, "Account not found")
    actions = {
        "ignore": "store_silent",
        "silent": "store_silent",
        "digest": "digest",
        "normal": "notify_normal",
        "urgent": "notify_urgent",
    }
    made = []
    position = (db.query(func.max(Rule.position)).filter(Rule.user_id == user.id).scalar() or 0) + 10
    for item in body.assignments:
        domain = item.email.split("@")[-1]
        rule = Rule(
            user_id=user.id,
            name=f"{item.action} {item.email}",
            conditions={"all": [{"field": "sender_domain", "op": "eq", "value": domain}]},
            action={"type": actions.get(item.action, "notify_normal")},
            account_scope=[account.id],
            position=position,
            origin="onboarding",
        )
        position += 10
        db.add(rule)
        made.append(rule.name)
    db.commit()
    return {"created": made}


@router.get("/senders")
def senders(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Message).filter(Message.user_id == user.id).all()
    buckets: dict[str, dict] = {}
    for row in rows:
        key = row.sender_email or "unknown"
        bucket = buckets.setdefault(
            key,
            {"email": key, "name": row.sender_name, "count": 0, "last": None, "domain": row.authenticated_domain},
        )
        bucket["count"] += 1
        stamp = iso(row.received_at)
        if bucket["last"] is None or (stamp and stamp > bucket["last"]):
            bucket["last"] = stamp
    return sorted(buckets.values(), key=lambda item: item["count"], reverse=True)[:40]


@router.get("/todos")
def todos(user: User = Depends(current_user), db: Session = Depends(get_db)):
    accounts = account_map(db, user.id)
    actionable = {"placement_announcement", "registration", "assessment_link", "interview", "action_needed", "result"}
    rows = (
        db.query(Message)
        .filter(Message.user_id == user.id, Message.is_trashed.is_(False), Message.is_archived.is_(False))
        .order_by(Message.received_at.desc())
        .limit(300)
        .all()
    )
    messages = []
    for row in rows:
        extraction = row.extraction or {}
        if row.priority == "urgent" or row.category in actionable or extraction.get("action_needed"):
            messages.append(message_out(row, accounts))
    reminders = (
        db.query(Reminder)
        .filter(Reminder.user_id == user.id, Reminder.done.is_(False))
        .order_by(Reminder.remind_at.asc())
        .all()
    )
    return {
        "messages": messages[:40],
        "reminders": [
            {
                "id": row.id,
                "message_id": row.message_id,
                "note": row.note,
                "kind": row.kind,
                "remind_at": iso(row.remind_at),
                "opportunity_id": row.opportunity_id,
            }
            for row in reminders
        ],
    }


class ReminderIn(BaseModel):
    message_id: str
    remind_at: datetime
    note: str = ""


@router.post("/reminders")
def add_reminder(body: ReminderIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _owned_message(db, user, body.message_id)
    row = Reminder(user_id=user.id, message_id=message.id, remind_at=body.remind_at, note=body.note, kind="manual", opportunity_id=message.opportunity_id)
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.post("/reminders/{reminder_id}/done")
def done_reminder(reminder_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(Reminder, reminder_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Reminder not found")
    row.done = True
    db.commit()
    return {"ok": True}


@router.post("/messages/send")
def send(body: SendIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = db.get(GmailAccount, body.account_id)
    if account is None or account.user_id != user.id:
        raise HTTPException(404, "Account not found")
    draft = Draft(
        user_id=user.id,
        account_id=account.id,
        to_addrs=body.to,
        cc_addrs=body.cc,
        bcc_addrs=body.bcc,
        subject=body.subject,
        body=body.body,
        in_reply_to=body.reply_to_message_id,
        mode=body.mode,
        send_at=body.send_at,
        follow_up_days=body.follow_up_days,
    )
    db.add(draft)
    db.flush()
    if body.send_at and body.send_at > datetime.now(timezone.utc):
        enqueue(db, "send_later", {"draft_id": draft.id})
        db.commit()
        return {"scheduled": True, "draft_id": draft.id}
    try:
        message = deliver_draft(db, draft)
    except GmailError as exc:
        raise HTTPException(403 if exc.status == "needs_scope" else 400, str(exc)) from exc
    db.commit()
    return {"sent": True, "message_id": message.id}


@router.get("/stats")
def stats(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Message).filter(Message.user_id == user.id).all()
    per_day: dict[str, int] = {}
    senders: dict[str, int] = {}
    for row in rows:
        day = (iso(row.received_at) or "")[:10]
        per_day[day] = per_day.get(day, 0) + 1
        if row.sender_email:
            senders[row.sender_email] = senders.get(row.sender_email, 0) + 1
    unread = sum(1 for row in rows if not row.is_read and not row.is_trashed)
    return {
        "per_day": [{"date": key, "count": per_day[key]} for key in sorted(per_day)][-14:],
        "top_senders": [{"email": key, "count": value} for key, value in sorted(senders.items(), key=lambda item: item[1], reverse=True)[:8]],
        "unread": unread,
        "stored": len(rows),
    }


@router.get("/backup")
def backup(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {
        "folders": [
            {"name": row.name, "parent": row.parent_id, "color": row.color, "icon": row.icon, "id": row.id}
            for row in db.query(Folder).filter(Folder.user_id == user.id, Folder.is_smart.is_(False))
        ],
        "rules": [rule_out(row) for row in db.query(Rule).filter(Rule.user_id == user.id)],
        "tags": [{"name": row.name, "color": row.color} for row in db.query(Tag).filter(Tag.user_id == user.id)],
        "categories": [
            {"name": row.name, "definition": row.definition}
            for row in db.query(Category).filter(Category.user_id == user.id, Category.builtin.is_(False))
        ],
        "settings": _settings_out(db, user),
    }


@router.post("/demo/reset")
def demo_reset(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.email != DEMO_EMAIL:
        raise HTTPException(403, "Only the demo user can reset the sample inbox")
    reset_demo(db)
    fresh = db.query(User).filter(User.email == DEMO_EMAIL).one()
    return _token(fresh)


@router.post("/demo/simulate/{kind}")
def demo_simulate(kind: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not get_settings().demo_mode:
        raise HTTPException(404, "Demo mode is off")
    try:
        message = simulate(db, user, kind)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return message_out(message, account_map(db, user.id))


@router.post("/gmail/push", status_code=204)
async def gmail_push(request: Request, db: Session = Depends(get_db)):
    if not _push_authorized(request):
        raise HTTPException(401, "Pub/Sub authentication failed")
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=204)
    email, history_id = decode_push(body if isinstance(body, dict) else {})
    if email:
        enqueue(db, "gmail_push", {"email": email, "history_id": history_id})
        db.commit()
        if get_settings().inline_worker:
            from app.services.jobs import drain

            drain(db)
    return Response(status_code=204)


def _push_authorized(request: Request) -> bool:
    settings = get_settings()
    token = request.query_params.get("token") or ""
    expected = settings.push_token()
    if expected and hmac.compare_digest(token, expected):
        return True
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer ") and settings.pubsub_audience:
        raw = header.split(" ", 1)[1]
        try:
            jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs").get_signing_key_from_jwt(raw)
            payload = jwt.decode(raw, options={"verify_signature": False})
        except Exception:
            return False
        # Signature is checked by decoding with the signing key when the network is available.
        try:
            key = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs").get_signing_key_from_jwt(raw)
            payload = jwt.decode(raw, key.key, algorithms=["RS256"], audience=settings.pubsub_audience)
        except Exception:
            return False
        issuer = payload.get("iss")
        if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
            return False
        if settings.pubsub_service_account and payload.get("email") != settings.pubsub_service_account:
            return False
        return True
    return False


def _owned_message(db: Session, user: User, message_id: str) -> Message:
    message = db.get(Message, message_id)
    if message is None or message.user_id != user.id:
        raise HTTPException(404, "Mail not found")
    return message


def _owned_rule(db: Session, user: User, rule_id: str) -> Rule:
    rule = db.get(Rule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(404, "Rule not found")
    return rule


def _owned_opp(db: Session, user: User, opp_id: str) -> Opportunity:
    row = db.get(Opportunity, opp_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Timeline not found")
    return row


def _owned_folder(db: Session, user: User, folder_id: str) -> Folder:
    row = db.get(Folder, folder_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Folder not found")
    return row


def _owned_group(db: Session, user: User, group_id: str) -> MailGroup:
    row = db.get(MailGroup, group_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Group not found")
    return row


def _events(db: Session, opp_id: str) -> list[dict]:
    rows = db.query(OpportunityEvent).filter(OpportunityEvent.opportunity_id == opp_id).order_by(OpportunityEvent.created_at.asc()).all()
    return [
        {
            "id": row.id,
            "message_id": row.message_id,
            "event_type": row.event_type,
            "details": row.details,
            "added_by": row.added_by,
            "created_at": iso(row.created_at),
        }
        for row in rows
    ]


def _settings_out(db: Session, user: User) -> dict:
    keys = [
        "grouping_enabled",
        "grouping_prompt_dismissals",
        "quiet_hours",
        "focus_mode",
        "category_mutes",
        "store_body_text",
        "timezone",
        "resend_after_minutes",
        "resend_limit",
        "signature",
        "notify_enabled",
        "last_used_folder_id",
    ]
    data = {key: get_setting(db, user.id, key) for key in keys}
    data["ai"] = _llm_out(db, user)
    return data


def _llm_out(db: Session, user: User) -> dict:
    row = db.query(LLMCredential).filter(LLMCredential.user_id == user.id).one_or_none()
    status = ai_status(db, user.id)
    return {
        "enabled": status == "on",
        "status": status,
        "provider": row.provider if row else None,
        "model": row.model if row else None,
        "key_last4": row.key_last4 if row else None,
        "message": row.status_message if row else None,
        "daily_token_cap": row.daily_token_cap if row else 20000,
        "tokens_today": tokens_today(db, user.id),
        "fallback_provider": row.fallback_provider if row else None,
    }


def _cap(db: Session, user_id: str) -> int:
    row = db.query(LLMCredential).filter(LLMCredential.user_id == user_id).one_or_none()
    return row.daily_token_cap if row else 20000


def _template_regex(template: str) -> str:
    parts = re.split(r"(\{[a-z]+\})", template or "")
    return "".join(".+" if re.fullmatch(r"\{[a-z]+\}", part) else re.escape(part) for part in parts) or ".+"


def _gmail_labels(db: Session, message: Message, *, add: list[str] | None = None, remove: list[str] | None = None) -> None:
    account = db.get(GmailAccount, message.account_id)
    if account is None or account.provider != "gmail":
        return
    if not has_modify_scope(account):
        return
    try:
        modify_labels(account, message.gmail_id, add=add, remove=remove)
    except GmailError as exc:
        _apply_gmail_error(account, exc)


def _guard_scope(account: GmailAccount) -> None:
    if account.provider == "demo":
        return
    if not has_modify_scope(account):
        raise HTTPException(403, "Changing mail in Gmail needs the modify scope. Grant it from Accounts.")


def _apply_gmail_error(account: GmailAccount, exc: GmailError) -> None:
    if exc.status in {"needs_reconnect", "blocked"}:
        account.status = exc.status
        account.status_message = str(exc)[:300]


def _ensure_body(db: Session, message: Message) -> None:
    if message.body_text or message.body_html:
        return
    account = db.get(GmailAccount, message.account_id)
    if account is None or account.provider != "gmail" or account.status != "active":
        return
    try:
        raw = fetch_gmail_message(account, message.gmail_id, fmt="full")
    except GmailError as exc:
        _apply_gmail_error(account, exc)
        return
    cleaned = prepare_body(raw.body_text, raw.body_html, 20000)
    message.body_text = cleaned
    message.body_html = raw.body_html
    if get_setting(db, message.user_id, "store_body_text"):
        db.commit()


def _search_view(db: Session, message: Message, accounts: dict) -> dict:
    folders = [
        row.name
        for row in db.query(Folder)
        .join(FolderItem, FolderItem.folder_id == Folder.id)
        .filter(FolderItem.account_id == message.account_id, FolderItem.thread_id == message.thread_id)
    ]
    tags = [row.name for row in db.query(Tag).join(MailTag, MailTag.tag_id == Tag.id).filter(MailTag.message_id == message.id)]
    account = accounts.get(message.account_id)
    opp = db.get(Opportunity, message.opportunity_id) if message.opportunity_id else None
    note = db.query(MailNote).filter(MailNote.message_id == message.id).first()
    notified = db.query(Notification).filter(Notification.message_id == message.id, Notification.delivery_status.in_(["sent", "stored", "pending"])).first()
    in_group = db.query(GroupMember).filter(GroupMember.message_id == message.id).first()
    rule = db.get(Rule, message.matched_rule_id) if message.matched_rule_id else None
    return {
        "subject": message.subject,
        "sender_email": message.sender_email,
        "sender_name": message.sender_name,
        "sender_domain": message.sender_domain,
        "body": message.body_text or message.snippet,
        "snippet": message.snippet,
        "labels": message.labels or [],
        "folders": folders,
        "tags": tags,
        "priority": message.priority,
        "category": message.category,
        "received_at": message.received_at,
        "is_read": message.is_read,
        "is_starred": message.is_starred,
        "is_archived": message.is_archived,
        "is_trashed": message.is_trashed,
        "is_spam": message.is_spam,
        "has_attachment": message.has_attachment,
        "attachments": message.attachments or [],
        "size_bytes": message.size_bytes,
        "to": message.to_addrs or [],
        "cc": message.cc_addrs or [],
        "account_email": account.email if account else "",
        "account_id": message.account_id,
        "account_label": account.label if account else "",
        "opportunity": opp.company if opp else "",
        "rule_name": rule.name if rule else "",
        "rule_id": message.matched_rule_id,
        "has_note": note is not None,
        "note": note.body if note else "",
        "notified": notified is not None,
        "in_group": in_group is not None,
        "snoozed": message.snoozed_until is not None,
        "list_id": message.list_id,
        "message_id_header": message.message_id_header,
    }


def _collect_children(db: Session, parent_id: str, ids: set[str]) -> None:
    children = db.query(Folder).filter(Folder.parent_id == parent_id).all()
    for child in children:
        if child.id in ids:
            continue
        ids.add(child.id)
        _collect_children(db, child.id, ids)


def _remote_headers(account: GmailAccount) -> list[Message]:
    try:
        data = list_message_ids(account, "newer_than:30d", limit=40)
    except GmailError:
        return []
    found = []
    for item in (data.get("messages") or [])[:40]:
        try:
            raw = fetch_gmail_message(account, item["id"], fmt="metadata")
        except GmailError:
            continue
        found.append(
            Message(
                user_id=account.user_id,
                account_id=account.id,
                gmail_id=raw.gmail_id,
                thread_id=raw.thread_id,
                sender_name=raw.sender_name,
                sender_email=raw.sender_email,
                authenticated_domain=raw.authenticated_domain,
                subject=raw.subject,
                received_at=raw.received_at,
            )
        )
    return found


def _mark_oauth_failure(db: Session, state: str, kind: str, detail: str) -> None:
    try:
        payload = jwt.decode(state, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return
    if kind == "cancelled":
        return
    _mark_account(db, payload.get("uid"), payload.get("account_id"), kind, detail)
    db.commit()


def _mark_account(db: Session, user_id: str | None, account_id: str | None, kind: str, detail: str) -> None:
    if not account_id:
        return
    account = db.get(GmailAccount, account_id)
    if account is None or account.user_id != user_id:
        return
    if kind == "blocked":
        account.status = "blocked"
        account.status_message = "This account may be blocked by its administrator (common for college or work accounts)."
    elif kind == "needs_reconnect":
        account.status = "needs_reconnect"
        account.status_message = detail[:300]


def _account_for_callback(db: Session, user_id: str, account_id: str | None) -> GmailAccount:
    if account_id:
        account = db.get(GmailAccount, account_id)
        if account and account.user_id == user_id:
            return account
    account = GmailAccount(user_id=user_id, email=f"pending-{user_id[:8]}@gmail.com", provider="gmail", status="active")
    db.add(account)
    db.flush()
    return account
