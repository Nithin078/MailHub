from datetime import datetime, timezone

from app.services.rules import MailView, first_match, rule_matches
from tests.conftest import mail


class Rule:
    def __init__(self, name, conditions, action, exceptions=None, position=10, enabled=True, scope=None):
        self.name = name
        self.conditions = conditions
        self.action = action
        self.exceptions = exceptions
        self.position = position
        self.enabled = enabled
        self.account_scope = scope
        self.created_at = datetime.now(timezone.utc)
        self.id = name


def view_of(raw, account_id="acc"):
    return MailView.from_raw(account_id, raw, raw.body_text)


BANK = Rule(
    "Silence bank transactions",
    {"all": [
        {"field": "sender_domain", "op": "in", "value": ["kotak.com", "sliceit.com"]},
        {"field": "subject", "op": "regex", "value": "debit|credit|spent|upi"},
    ]},
    {"type": "store_silent"},
    exceptions={"any": [{"field": "subject", "op": "regex", "value": "fraud|blocked|suspicious"}]},
    position=10,
)


def test_bank_debit_matches_and_fraud_is_the_exception():
    debit = view_of(mail(subject="INR 20 debited via UPI"))
    fraud = view_of(mail(subject="Alert: suspicious transaction blocked on your card"))
    assert rule_matches(BANK, debit)
    assert not rule_matches(BANK, fraud)


def test_spoofed_sender_does_not_match():
    raw = mail(subject="INR 20 debited via UPI", auth="evil.com")
    assert not rule_matches(BANK, view_of(raw))


def test_display_name_is_not_enough():
    raw = mail(sender_name="Kotak Bank", sender_email="thief@evil.com", subject="INR 20 debited via UPI", auth="evil.com")
    assert not rule_matches(BANK, view_of(raw))


def test_first_match_wins_and_disabled_rules_are_skipped():
    urgent = Rule("VIP", {"all": [{"field": "sender_domain", "op": "eq", "value": "kotak.com"}]}, {"type": "notify_urgent"}, position=0)
    disabled = Rule("nope", {"all": [{"field": "sender_domain", "op": "eq", "value": "kotak.com"}]}, {"type": "ignore"}, position=-5, enabled=False)
    winner = first_match([BANK, disabled, urgent], view_of(mail()))
    assert winner.name == "VIP"


def test_or_not_and_account_scope():
    rule = Rule(
        "scoped",
        {"any": [
            {"not": {"field": "subject", "op": "contains", "value": "newsletter"}},
            {"field": "label", "op": "eq", "value": "IMPORTANT"},
        ]},
        {"type": "notify_normal"},
        scope=["acc-1"],
    )
    raw = mail(subject="Hello there", labels=["INBOX"])
    assert rule_matches(rule, view_of(raw, "acc-1"))
    assert not rule_matches(rule, view_of(raw, "acc-2"))
    newsletter = mail(subject="Weekly newsletter")
    # NOT subject contains newsletter is false, and label IMPORTANT is absent, so OR fails.
    assert not rule_matches(rule, view_of(newsletter, "acc-1"))


def test_body_keyword_and_attachment_and_header():
    rule = Rule(
        "body",
        {"all": [
            {"field": "body", "op": "contains", "value": "hirepro"},
            {"field": "has_attachment", "op": "eq", "value": False},
            {"field": "header", "op": "present", "value": "List-Unsubscribe"},
        ]},
        {"type": "digest"},
    )
    raw = mail(body="Open the HirePro link", headers={"List-Unsubscribe": "<mailto:x@y>"})
    assert rule_matches(rule, view_of(raw))
    raw.attachments = [{"filename": "a.pdf"}]
    assert not rule_matches(rule, view_of(raw))
