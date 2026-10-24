"""First-match rule engine. Sender leaves match the authenticated domain only."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.textutil import RawMessage, domain_of, header


@dataclass
class MailView:
    account_id: str
    sender_email: str | None
    sender_name: str | None
    authenticated_domain: str | None
    subject: str
    body: str | None
    labels: list[str]
    headers: dict[str, str]
    has_attachment: bool
    received_at: datetime
    to_addrs: list[str] = field(default_factory=list)
    cc_addrs: list[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, account_id: str, raw: RawMessage, body: str | None = None) -> "MailView":
        return cls(
            account_id=account_id,
            sender_email=raw.sender_email,
            sender_name=raw.sender_name,
            authenticated_domain=raw.authenticated_domain,
            subject=raw.subject or "",
            body=body if body is not None else raw.body_text,
            labels=list(raw.labels or []),
            headers={k: v for k, v in (raw.headers or {}).items()},
            has_attachment=raw.has_attachment,
            received_at=raw.received_at,
            to_addrs=list(raw.to_addrs or []),
            cc_addrs=list(raw.cc_addrs or []),
        )


TERMINAL_ACTIONS = {
    "ignore",
    "store_silent",
    "digest",
    "notify_normal",
    "notify_urgent",
    "send_to_llm",
}


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).lower() for v in value]
    return [str(value).lower()]


def _sender_domain_ok(view: MailView) -> bool:
    """True only when the visible From domain is the domain that passed DKIM or SPF."""
    auth = (view.authenticated_domain or "").lower()
    visible = (domain_of(view.sender_email) or "").lower()
    if not auth or not visible:
        return False
    return auth == visible


def _regex_ok(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, text, re.I) is not None
    except re.error:
        return False


def _time_in_window(when: datetime, spec: dict) -> bool:
    tz_name = spec.get("tz") or "Asia/Kolkata"
    try:
        local = when.astimezone(ZoneInfo(tz_name))
    except Exception:
        local = when
    start = spec.get("start") or "00:00"
    end = spec.get("end") or "23:59"
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


def eval_leaf(node: dict, view: MailView) -> bool:
    field_name = (node.get("field") or "").lower()
    op = (node.get("op") or "eq").lower()
    value = node.get("value")
    subject = view.subject or ""
    body = view.body or ""

    if field_name in {"sender_email", "sender_domain", "sender"}:
        if not _sender_domain_ok(view):
            return False
        email = (view.sender_email or "").lower()
        domain = (view.authenticated_domain or "").lower()
        if field_name == "sender_domain":
            options = _as_list(value)
            if op in {"eq", "in"}:
                return domain in options
            if op == "contains":
                return any(opt in domain for opt in options)
            return False
        if op == "eq":
            return email == str(value).lower()
        if op == "contains":
            return str(value).lower() in email
        if op == "regex":
            return _regex_ok(str(value), email)
        if op == "in":
            return email in _as_list(value)
        return False

    if field_name == "subject":
        if op in {"contains", "keyword"}:
            return str(value).lower() in subject.lower()
        if op == "regex":
            return _regex_ok(str(value), subject)
        if op == "eq":
            return subject.lower() == str(value).lower()
        return False

    if field_name in {"body", "keywords"}:
        if view.body is None and op != "empty":
            return False
        if op in {"contains", "keyword"}:
            return str(value).lower() in body.lower()
        if op == "regex":
            return _regex_ok(str(value), body)
        return False

    if field_name == "label":
        labels = {item.lower() for item in view.labels}
        return str(value).lower() in labels

    if field_name == "header":
        if op == "present":
            return header(view.headers, str(value)) is not None
        if op == "contains":
            found = header(view.headers, str(node.get("name") or "")) or ""
            return str(value).lower() in found.lower()
        return header(view.headers, str(value)) is not None

    if field_name == "has_attachment":
        expected = bool(value) if not isinstance(value, str) else value.lower() in {"1", "true", "yes"}
        return view.has_attachment is expected

    if field_name == "time_window":
        if not isinstance(value, dict):
            return False
        return _time_in_window(view.received_at, value)

    if field_name == "account":
        return view.account_id in {str(v) for v in (value if isinstance(value, list) else [value])}

    return False


def eval_node(node: dict | None, view: MailView) -> bool:
    if not node:
        return False
    if "all" in node:
        children = node.get("all") or []
        if not children:
            return True
        return all(eval_node(child, view) for child in children)
    if "any" in node:
        return any(eval_node(child, view) for child in (node.get("any") or []))
    if "not" in node:
        return not eval_node(node.get("not") or {}, view)
    if "field" in node:
        return eval_leaf(node, view)
    return False


def rule_matches(rule, view: MailView) -> bool:
    if not getattr(rule, "enabled", True):
        return False
    scope = getattr(rule, "account_scope", None) or []
    if scope and view.account_id not in scope:
        return False
    conditions = getattr(rule, "conditions", None) or {}
    if not eval_node(conditions, view):
        return False
    exceptions = getattr(rule, "exceptions", None)
    if exceptions and eval_node(exceptions, view):
        return False
    return True


def first_match(rules: list, view: MailView):
    ordered = sorted(rules, key=lambda rule: (getattr(rule, "position", 0), getattr(rule, "created_at", 0) or 0))
    for rule in ordered:
        if rule_matches(rule, view):
            return rule
    return None


def needs_body(rules: list) -> bool:
    def walk(node) -> bool:
        if not isinstance(node, dict):
            return False
        if node.get("field") in {"body", "keywords"}:
            return True
        for key in ("all", "any"):
            for child in node.get(key) or []:
                if walk(child):
                    return True
        if "not" in node and walk(node.get("not")):
            return True
        return False

    for rule in rules:
        if not getattr(rule, "enabled", True):
            continue
        if walk(getattr(rule, "conditions", None) or {}) or walk(getattr(rule, "exceptions", None) or {}):
            return True
    return False


def action_type(action: dict | None) -> str:
    if not action:
        return "notify_normal"
    name = (action.get("type") or "notify_normal").lower()
    if name in {"silent", "store_silently"}:
        return "store_silent"
    if name in {"notify", "normal"}:
        return "notify_normal"
    if name in {"urgent", "notify_urgent"}:
        return "notify_urgent"
    if name == "file_in_folder" and "type" in action:
        return "store_silent"
    return name


def describe_action(action: dict | None) -> str:
    kind = action_type(action)
    labels = {
        "ignore": "Ignore",
        "store_silent": "Store silently",
        "digest": "Digest",
        "notify_normal": "Notify, normal",
        "notify_urgent": "Notify, urgent",
        "send_to_llm": "Send to the model",
    }
    return labels.get(kind, kind)
