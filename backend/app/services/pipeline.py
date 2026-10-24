"""The mail pipeline. Rules run before any model call. Uncertain mail stays normal."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.constants import BODY_TRUNCATE, BUILTIN_CATEGORIES
from app.models import (
    Category,
    Fingerprint,
    Message,
    Notification,
    Opportunity,
    OpportunityEvent,
    Reminder,
    Rule,
    Tag,
    utcnow,
)
from app.services import opportunities as opp_service
from app.services.folders import FolderError, file_thread
from app.services.grouping import assign_group
from app.services.heuristics import suggest
from app.services.llm import EmailDoc, ai_status, run_extract, run_match_choice
from app.services.notify import create_notification
from app.services.rules import MailView, action_type, first_match, needs_body
from app.services.settings_store import get_setting
from app.services.textutil import (
    RawMessage,
    authenticated_domain,
    fingerprint_hash,
    header,
    normalize_subject,
    prepare_body,
    redact,
)
from app.models import Folder, MailTag


def process_message(db: Session, account, raw: RawMessage, *, provider=None) -> Message:
    existing = (
        db.query(Message)
        .filter(Message.account_id == account.id, Message.gmail_id == raw.gmail_id)
        .one_or_none()
    )
    if existing:
        return existing

    if raw.authenticated_domain is None:
        raw.authenticated_domain = authenticated_domain(header(raw.headers, "Authentication-Results"))

    rules = (
        db.query(Rule)
        .filter(Rule.user_id == account.user_id, Rule.enabled.is_(True))
        .all()
    )
    cleaned = prepare_body(raw.body_text, raw.body_html, 20_000)
    view = MailView.from_raw(account.id, raw, cleaned if (cleaned or not needs_body(rules)) else raw.body_text)
    if needs_body(rules) and view.body is None:
        view.body = cleaned or ""

    template = normalize_subject(raw.subject, [raw.sender_name or ""])
    domain = (raw.authenticated_domain or raw.sender_domain or "").lower()
    digest = fingerprint_hash(domain, template)
    trace: list[dict] = []
    matched = first_match(rules, view)

    llm_called = False
    llm_skipped = None
    extraction = None
    category = "other"
    priority = "normal"
    reason = ""
    action = {}

    if matched:
        matched.hit_count = (matched.hit_count or 0) + 1
        matched.last_hit_at = utcnow()
        action = matched.action or {}
        kind = action_type(action)
        if action.get("category"):
            category = action["category"]
        trace.append({"step": "rule", "result": "matched", "detail": matched.name, "action": kind})
        if kind == "send_to_llm":
            extraction, llm_called, llm_skipped, priority, category, reason = _classify(
                db, account, raw, cleaned, provider
            )
            if action.get("category"):
                category = action["category"]
            reason = f"Rule “{matched.name}” asked for a model. {reason}"
            trace.append({"step": "llm", "result": "called" if llm_called else "skipped", "detail": llm_skipped or "called"})
        else:
            priority, reason, llm_skipped = _from_rule(kind, matched.name)
            trace.append({"step": "heuristic", "result": "skipped", "detail": "A rule already decided"})
            trace.append({"step": "fingerprint", "result": "skipped", "detail": "A rule already decided"})
            trace.append({"step": "llm", "result": "skipped", "detail": llm_skipped})
    else:
        trace.append({"step": "rule", "result": "none", "detail": "No rule matched"})
        learned = (
            db.query(Fingerprint)
            .filter(Fingerprint.user_id == account.user_id, Fingerprint.fingerprint == digest)
            .one_or_none()
        )
        suggestion = suggest(view)
        if learned and learned.last_action in {"silent", "ignored"}:
            priority = "silent"
            llm_skipped = "fingerprint"
            reason = "This subject pattern was silenced by a rule or by you earlier"
            trace.append({"step": "fingerprint", "result": "applied", "detail": learned.last_action})
            trace.append({"step": "heuristic", "result": "skipped", "detail": "A learned silence applied"})
            trace.append({"step": "llm", "result": "skipped", "detail": "fingerprint"})
        elif suggestion:
            priority = suggestion["priority"]
            llm_skipped = "heuristic"
            reason = suggestion["reason"]
            trace.append({"step": "heuristic", "result": suggestion["priority"], "detail": suggestion["reason"]})
            trace.append({"step": "fingerprint", "result": "none", "detail": "No learned silence"})
            trace.append({"step": "llm", "result": "skipped", "detail": "heuristic"})
        else:
            trace.append({"step": "heuristic", "result": "none", "detail": "Nothing confident enough to hide"})
            trace.append({"step": "fingerprint", "result": "none", "detail": "No learned silence"})
            extraction, llm_called, llm_skipped, priority, category, reason = _classify(
                db, account, raw, cleaned, provider
            )
            trace.append({"step": "llm", "result": "called" if llm_called else "skipped", "detail": llm_skipped or reason})

    if priority not in {"urgent", "normal", "digest", "silent"}:
        priority = "normal"
    if priority == "silent" and not matched and llm_skipped not in {"fingerprint", "heuristic", "rule"}:
        priority = "normal"

    opportunity_id = None
    ask_company = False
    if extraction is not None:
        opportunity_id, ask_company, opp_trace = _attach_opportunity(
            db, account, raw, extraction, provider
        )
        trace.append(opp_trace)
    else:
        trace.append({"step": "opportunity", "result": "skipped", "detail": "No extraction to match"})

    store_body = bool(get_setting(db, account.user_id, "store_body_text"))
    snippet_source = cleaned or raw.snippet or ""
    message = Message(
        user_id=account.user_id,
        account_id=account.id,
        gmail_id=raw.gmail_id,
        thread_id=raw.thread_id or raw.gmail_id,
        message_id_header=raw.message_id_header,
        sender_name=raw.sender_name,
        sender_email=raw.sender_email,
        sender_domain=raw.sender_domain,
        authenticated_domain=raw.authenticated_domain,
        to_addrs=raw.to_addrs,
        cc_addrs=raw.cc_addrs,
        subject=raw.subject or "",
        snippet=snippet_source[:180],
        body_text=cleaned if store_body else None,
        body_html=raw.body_html if store_body else None,
        received_at=raw.received_at,
        labels=list(raw.labels or []),
        size_bytes=raw.size_bytes,
        has_attachment=raw.has_attachment,
        attachments=list(raw.attachments or []),
        list_unsubscribe=header(raw.headers, "List-Unsubscribe"),
        list_id=header(raw.headers, "List-Id"),
        headers=raw.headers or {},
        category=category or "other",
        priority=priority,
        matched_rule_id=matched.id if matched else None,
        decision_reason=reason,
        decision_trace=trace,
        extraction=extraction.model_dump() if extraction is not None else None,
        llm_called=llm_called,
        llm_skipped_reason=llm_skipped,
        llm_pending=llm_skipped == "cap",
        is_read=bool(action.get("mark_read")) or "UNREAD" not in {label.upper() for label in (raw.labels or ["UNREAD"])},
        is_starred=bool(action.get("star")) or "STARRED" in {label.upper() for label in (raw.labels or [])},
        opportunity_id=opportunity_id,
        ask_company=ask_company,
        fingerprint=digest,
        subject_template=template,
        status="processed",
    )
    if "UNREAD" in {label.upper() for label in (raw.labels or [])} and not action.get("mark_read"):
        message.is_read = False
    db.add(message)
    db.flush()

    _remember_fingerprint(db, account.user_id, digest, domain, template, matched, action)
    _apply_side_effects(db, account, message, action)
    if extraction and extraction.deadline:
        _deadlines(db, account.user_id, message, extraction.deadline)
    if opportunity_id and extraction is not None:
        _ensure_event(db, opportunity_id, message, extraction)

    duplicate = _already_notified(db, account.user_id, raw.message_id_header, message.id)
    create_notification(db, message, duplicate=duplicate)
    assign_group(db, message)
    return message


def _from_rule(kind: str, name: str) -> tuple[str, str, str]:
    mapping = {
        "ignore": ("silent", f"Rule “{name}” ignored it", "rule"),
        "store_silent": ("silent", f"Rule “{name}” stored it silently", "rule"),
        "digest": ("digest", f"Rule “{name}” put it in the digest", "rule"),
        "notify_normal": ("normal", f"Rule “{name}” marked it normal", "rule"),
        "notify_urgent": ("urgent", f"Rule “{name}” marked it urgent", "rule"),
        "file_in_folder": ("silent", f"Rule “{name}” filed it", "rule"),
        "add_tag": ("normal", f"Rule “{name}” tagged it", "rule"),
        "mark_read": ("normal", f"Rule “{name}” marked it read", "rule"),
        "star": ("normal", f"Rule “{name}” starred it", "rule"),
        "assign_category": ("normal", f"Rule “{name}” set the category", "rule"),
    }
    return mapping.get(kind, ("normal", f"Rule “{name}” matched", "rule"))


def _classify(db, account, raw: RawMessage, cleaned: str, provider):
    status = ai_status(db, account.user_id)
    if status != "on":
        explanations = {
            "no_key": "No API key saved, so this stays normal priority",
            "invalid": "The API key is invalid, so this stays normal priority",
            "quota": "The provider quota is exhausted, so this stays normal priority",
            "cap": "Today's token cap is reached, so this stays normal priority",
        }
        return None, False, status, "normal", "other", explanations.get(status, "AI is off, so this stays normal priority")

    categories = [
        (row.name, row.definition)
        for row in db.query(Category).filter(Category.user_id == account.user_id).all()
    ] or BUILTIN_CATEGORIES
    doc = EmailDoc(
        sender=f"{raw.sender_name or ''} <{raw.sender_email or ''}>".strip(),
        subject=redact(raw.subject),
        body=redact(cleaned[:BODY_TRUNCATE]),
        categories=categories,
    )
    extraction = run_extract(db, account.user_id, doc, account_id=account.id, provider=provider)
    if extraction is None:
        return None, True, "llm_failed", "normal", "other", "The model reply could not be used, so this stays normal priority"
    summary = extraction.summary or "Classified"
    return extraction, True, None, extraction.priority, extraction.category or "other", summary


def _attach_opportunity(db, account, raw: RawMessage, extraction, provider) -> tuple[str | None, bool, dict]:
    company = extraction.company
    links = [item.url for item in extraction.links]
    opportunities = db.query(Opportunity).filter(Opportunity.user_id == account.user_id, Opportunity.status == "active").all()
    messages = db.query(Message).filter(Message.user_id == account.user_id, Message.opportunity_id.isnot(None)).all()
    events = (
        db.query(OpportunityEvent)
        .join(Opportunity, Opportunity.id == OpportunityEvent.opportunity_id)
        .filter(Opportunity.user_id == account.user_id)
        .all()
    )
    llm_choice = None
    fuzzy, _score = opp_service.fuzzy_company(company, opportunities)
    if company and fuzzy is None and ai_status(db, account.user_id) == "on":
        llm_choice = run_match_choice(
            db,
            account.user_id,
            company,
            [{"id": opp.id, "company": opp.company, "aliases": opp.aliases} for opp in opportunities],
            provider=provider,
        )
    decision = opp_service.decide_match(
        company=company,
        opportunities=opportunities,
        thread_id=raw.thread_id,
        account_id=account.id,
        messages=messages,
        links=links,
        events=events,
        llm_choice=llm_choice,
        looks_like_assessment=extraction.category == "assessment_link",
    )
    opportunity_id = decision["opportunity_id"]
    if decision["create_company"]:
        opp = Opportunity(user_id=account.user_id, company=decision["create_company"], aliases=[])
        db.add(opp)
        db.flush()
        opportunity_id = opp.id
    if opportunity_id and decision.get("alias"):
        opp = db.get(Opportunity, opportunity_id)
        if opp is not None:
            alias = decision["alias"]
            if alias and alias.lower() not in {opp.company.lower(), *(a.lower() for a in opp.aliases or [])}:
                opp.aliases = [*(opp.aliases or []), alias]
            opp.updated_at = utcnow()
    if extraction.deadline and opportunity_id:
        parsed = _parse_deadline(extraction.deadline)
        opp = db.get(Opportunity, opportunity_id) if opportunity_id else None
        if opp is not None and parsed and (opp.deadline is None or parsed < opp.deadline):
            opp.deadline = parsed
            opp.updated_at = utcnow()
    return opportunity_id, bool(decision["ask"]), {"step": "opportunity", "result": "ask" if decision["ask"] else "matched" if opportunity_id else "none", "detail": decision["reason"]}


def _ensure_event(db, opportunity_id: str, message: Message, extraction) -> None:
    event_type = extraction.category if extraction.category in {
        "placement_announcement", "registration", "assessment_link", "interview", "result", "recruiter"
    } else "note"
    mapping = {
        "placement_announcement": "announcement",
        "registration": "registration",
        "assessment_link": "assessment",
        "interview": "interview",
        "result": "result",
        "recruiter": "recruiter",
    }
    db.add(
        OpportunityEvent(
            opportunity_id=opportunity_id,
            message_id=message.id,
            event_type=mapping.get(extraction.category, "note"),
            details={
                "subject": message.subject,
                "summary": extraction.summary,
                "links": [item.model_dump() for item in extraction.links],
                "deadline": extraction.deadline,
                "platform": extraction.platform,
            },
            added_by="ai",
        )
    )


def _remember_fingerprint(db, user_id, digest, domain, template, matched, action) -> None:
    row = (
        db.query(Fingerprint)
        .filter(Fingerprint.user_id == user_id, Fingerprint.fingerprint == digest)
        .one_or_none()
    )
    if row is None:
        row = Fingerprint(
            user_id=user_id,
            fingerprint=digest,
            sender_domain=domain,
            subject_template=template,
            seen_count=0,
        )
        db.add(row)
    row.seen_count += 1
    row.last_seen_at = utcnow()
    row.subject_template = template
    if matched is not None:
        kind = action_type(action)
        if kind == "ignore":
            row.last_action = "ignored"
        elif kind in {"store_silent", "file_in_folder"}:
            row.last_action = "silent"


def _apply_side_effects(db, account, message: Message, action: dict) -> None:
    folder_id = action.get("folder_id")
    if folder_id:
        folder = db.get(Folder, folder_id)
        if folder and folder.user_id == account.user_id:
            try:
                file_thread(db, folder, message.account_id, message.thread_id)
            except FolderError:
                pass
    tag_name = action.get("tag")
    if tag_name:
        tag = db.query(Tag).filter(Tag.user_id == account.user_id, Tag.name == tag_name).one_or_none()
        if tag is None:
            tag = Tag(user_id=account.user_id, name=tag_name)
            db.add(tag)
            db.flush()
        db.add(MailTag(tag_id=tag.id, message_id=message.id))


def _deadlines(db, user_id: str, message: Message, raw_deadline: str) -> None:
    when = _parse_deadline(raw_deadline)
    if when is None:
        return
    now = datetime.now(timezone.utc)
    for hours, kind in ((24, "deadline_24h"), (2, "deadline_2h")):
        remind_at = when - timedelta(hours=hours)
        if remind_at > now:
            db.add(
                Reminder(
                    user_id=user_id,
                    message_id=message.id,
                    opportunity_id=message.opportunity_id,
                    remind_at=remind_at,
                    kind=kind,
                    note=message.subject or "Deadline",
                )
            )


def _parse_deadline(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _already_notified(db, user_id: str, message_id_header: str | None, current_id: str) -> bool:
    if not message_id_header:
        return False
    prior = (
        db.query(Notification)
        .join(Message, Notification.message_id == Message.id)
        .filter(
            Message.user_id == user_id,
            Message.message_id_header == message_id_header,
            Message.id != current_id,
            Notification.delivery_status.in_(["pending", "sent", "stored", "held", "failed"]),
        )
        .first()
    )
    return prior is not None
