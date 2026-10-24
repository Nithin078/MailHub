"""Free suggestions. They never override a user rule, and they never hide uncertain mail.

Promotions and social mail become digest. Silent is reserved for bulk no-reply mail
that also lacks urgency words (fraud, OTP, deadline, assessment, interview).
"""

from __future__ import annotations

import re

from app.services.rules import MailView
from app.services.textutil import header

_URGENCY = re.compile(
    r"fraud|suspicious|blocked card|unauthorized|otp|one[- ]time|password|"
    r"verify your|deadline|interview|assessment|shortlist|selected|offer letter|"
    r"action required|urgent|security alert|account locked",
    re.I,
)
_NO_REPLY = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer-daemon|notifications?)@",
    re.I,
)


def _labels(view: MailView) -> set[str]:
    return {item.upper() for item in view.labels}


def _bulk(view: MailView) -> bool:
    precedence = (header(view.headers, "Precedence") or "").lower()
    if precedence in {"bulk", "list", "junk"}:
        return True
    if header(view.headers, "List-Unsubscribe") or header(view.headers, "List-Id"):
        return True
    return False


def _urgent_text(view: MailView) -> bool:
    blob = f"{view.subject}\n{view.body or ''}"
    return _URGENCY.search(blob) is not None


def suggest(view: MailView) -> dict | None:
    """Return {priority: digest|silent, reason: str} or None when nothing is confident."""
    if _urgent_text(view):
        return None
    labels = _labels(view)
    sender = (view.sender_email or "").lower()
    no_reply = bool(_NO_REPLY.search(sender))
    bulk = _bulk(view)

    if "CATEGORY_PROMOTIONS" in labels or "CATEGORY_SOCIAL" in labels or "CATEGORY_FORUMS" in labels:
        return {"priority": "digest", "reason": "Gmail filed this as promotions, social, or forums"}
    if bulk and no_reply:
        return {"priority": "silent", "reason": "Bulk no-reply mail with no urgency words"}
    if "CATEGORY_UPDATES" in labels and (bulk or no_reply):
        return {"priority": "digest", "reason": "Gmail update from a bulk or no-reply sender"}
    return None
