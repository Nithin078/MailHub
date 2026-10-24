from datetime import datetime, timedelta, timezone

from app.crypto import encrypt
from app.models import LLMCredential, Notification, Opportunity, Rule
from app.services.llm import MockProvider
from app.services.pipeline import process_message
from app.services.settings_store import set_setting
from tests.conftest import mail


class Spy(MockProvider):
    def __init__(self):
        self.calls = 0

    def complete(self, system: str, user: str, *, max_tokens: int = 700):
        self.calls += 1
        return super().complete(system, user, max_tokens=max_tokens)


def bank_rule(db, user):
    rule = Rule(
        user_id=user.id,
        name="Silence bank transactions",
        conditions={"all": [
            {"field": "sender_domain", "op": "in", "value": ["kotak.com", "sliceit.com"]},
            {"field": "subject", "op": "regex", "value": "debit|credit|spent|upi"},
        ]},
        exceptions={"any": [{"field": "subject", "op": "regex", "value": "fraud|blocked|suspicious"}]},
        action={"type": "store_silent"},
        position=10,
        origin="editor",
    )
    db.add(rule)
    db.commit()
    return rule


def test_bank_mail_is_silent_and_never_calls_the_model(db, user, account):
    bank_rule(db, user)
    spy = Spy()
    message = process_message(
        db,
        account,
        mail(subject="INR 2,450.00 debited from A/c XX9012 via UPI", body="UPI to SWIGGY"),
        provider=spy,
    )
    db.commit()
    assert message.priority == "silent"
    assert message.llm_called is False
    assert spy.calls == 0
    assert db.query(Notification).filter(Notification.message_id == message.id, Notification.delivery_status != "skipped_duplicate").count() == 0


def test_fraud_exception_notifies_and_still_skips_the_model(db, user, account):
    bank_rule(db, user)
    spy = Spy()
    message = process_message(
        db,
        account,
        mail(gmail_id="fraud", subject="Alert: suspicious transaction blocked on your debit card"),
        provider=spy,
    )
    db.commit()
    assert message.priority == "urgent" or message.priority == "normal"
    # No rule matched, AI is off, so uncertain mail is normal — unless a VIP rule exists.
    # The exception only stops the silence rule. With no key this must notify, not hide.
    assert message.priority in {"urgent", "normal"}
    assert message.llm_called is False
    assert spy.calls == 0
    note = db.query(Notification).filter(Notification.message_id == message.id).one()
    assert note.delivery_status in {"stored", "sent", "pending"}


def test_ai_off_delivers_unruled_mail_as_normal_with_zero_calls(db, user, account):
    spy = Spy()
    message = process_message(
        db,
        account,
        mail(
            gmail_id="drive",
            sender_name="Placements",
            sender_email="placements@vtu.ac.in",
            subject="Accenture campus drive for the 2026 batch",
            auth="vtu.ac.in",
            body="Register by Friday. https://college.example/drive",
        ),
        provider=spy,
    )
    db.commit()
    assert message.priority == "normal"
    assert message.category == "other"
    assert message.llm_called is False
    assert spy.calls == 0
    assert "No API key" in message.decision_reason


def test_processing_twice_does_not_notify_or_call_twice(db, user, account):
    cred = LLMCredential(
        user_id=user.id,
        provider="mock",
        model="inbox-agent-demo",
        encrypted_api_key=encrypt("demo-key"),
        key_last4="key",
        status="valid",
    )
    db.add(cred)
    db.commit()
    spy = Spy()
    raw = mail(
        gmail_id="once",
        sender_name="Priya",
        sender_email="priya@razorpay.com",
        subject="Opportunity at Razorpay for a backend intern",
        auth="razorpay.com",
        body="Recruiter note. Opportunity at Razorpay.",
    )
    first = process_message(db, account, raw, provider=spy)
    calls_after_first = spy.calls
    second = process_message(db, account, raw, provider=spy)
    db.commit()
    assert first.id == second.id
    assert spy.calls == calls_after_first
    assert spy.calls >= 1
    assert db.query(Notification).filter(Notification.message_id == first.id).count() == 1


def test_same_message_id_on_two_accounts_notifies_once(db, user, account):
    other = type(account)(
        user_id=user.id,
        email="nithin.personal@gmail.com",
        label="Personal",
        provider="demo",
        status="active",
    )
    db.add(other)
    db.commit()
    raw = mail(
        gmail_id="a",
        sender_email="placements@vtu.ac.in",
        sender_name="TPO",
        subject="Accenture drive",
        auth="vtu.ac.in",
        message_id="<drive-1@vtu.ac.in>",
    )
    copy = mail(
        gmail_id="b",
        sender_email="placements@vtu.ac.in",
        sender_name="TPO",
        subject="Accenture drive",
        auth="vtu.ac.in",
        message_id="<drive-1@vtu.ac.in>",
    )
    first = process_message(db, account, raw, provider=Spy())
    second = process_message(db, other, copy, provider=Spy())
    db.commit()
    assert first.id != second.id
    real = (
        db.query(Notification)
        .filter(Notification.delivery_status.in_(["stored", "sent", "pending"]))
        .all()
    )
    assert len(real) == 1
    skipped = db.query(Notification).filter(Notification.delivery_status == "skipped_duplicate").count()
    assert skipped == 1


def test_mock_model_links_hirepro_onto_the_same_company(db, user, account):
    db.add(
        LLMCredential(
            user_id=user.id,
            provider="mock",
            model="inbox-agent-demo",
            encrypted_api_key=encrypt("demo-key"),
            key_last4="key",
            status="valid",
        )
    )
    db.commit()
    spy = Spy()
    drive = process_message(
        db,
        account,
        mail(
            gmail_id="drive",
            sender_email="placements@vtu.ac.in",
            sender_name="TPO",
            subject="Accenture campus drive for batch 2026",
            auth="vtu.ac.in",
            body="Accenture placement drive. Register.",
        ),
        provider=spy,
    )
    assessment = process_message(
        db,
        account,
        mail(
            gmail_id="oa",
            sender_email="noreply@hirepro.in",
            sender_name="HirePro",
            subject="Accenture | complete your online assessment",
            auth="hirepro.in",
            body="Your Accenture assessment is on HirePro. https://tests.hirepro.in/accenture",
        ),
        provider=spy,
    )
    db.commit()
    assert drive.llm_called and assessment.llm_called
    assert drive.opportunity_id
    assert assessment.opportunity_id == drive.opportunity_id
    company = db.get(Opportunity, drive.opportunity_id)
    assert company.company.lower() == "accenture"
    assert drive.priority == "urgent"
    assert assessment.category == "assessment_link"


def test_learned_fingerprint_skips_the_model(db, user, account):
    bank_rule(db, user)
    spy = Spy()
    process_message(db, account, mail(gmail_id="b1", subject="INR 10 debited via UPI"), provider=spy)
    # Remove the rule. The fingerprint must still silence the next one with no model call.
    db.query(Rule).delete()
    db.commit()
    spy.calls = 0
    nxt = process_message(db, account, mail(gmail_id="b2", subject="INR 99 debited via UPI"), provider=spy)
    db.commit()
    assert nxt.priority == "silent"
    assert nxt.llm_called is False
    assert spy.calls == 0
    assert nxt.llm_skipped_reason == "fingerprint"


def test_token_cap_notifies_normal_without_a_call(db, user, account):
    from app.models import TokenUsage

    db.add(
        LLMCredential(
            user_id=user.id,
            provider="mock",
            model="inbox-agent-demo",
            encrypted_api_key=encrypt("demo-key"),
            key_last4="key",
            status="valid",
            daily_token_cap=10,
        )
    )
    db.add(
        TokenUsage(
            user_id=user.id,
            provider="mock",
            model="x",
            input_tokens=8,
            output_tokens=8,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    spy = Spy()
    message = process_message(
        db,
        account,
        mail(gmail_id="capped", sender_email="a@vtu.ac.in", subject="Please read", auth="vtu.ac.in"),
        provider=spy,
    )
    db.commit()
    assert spy.calls == 0
    assert message.priority == "normal"
    assert message.llm_pending is True
    assert db.query(Notification).filter(Notification.message_id == message.id).count() == 1


def test_prompt_sent_to_the_model_is_redacted(db, user, account):
    db.add(
        LLMCredential(
            user_id=user.id,
            provider="mock",
            model="inbox-agent-demo",
            encrypted_api_key=encrypt("demo"),
            key_last4="demo",
            status="valid",
        )
    )
    db.commit()
    seen = {}

    class Capture(Spy):
        def complete(self, system, user, *, max_tokens=700):
            seen["user"] = user
            return super().complete(system, user, max_tokens=max_tokens)

    process_message(
        db,
        account,
        mail(
            gmail_id="pii",
            sender_email="tpo@vtu.ac.in",
            subject="Drive",
            auth="vtu.ac.in",
            body="Candidate 1MS21CS001 phone +91 98450 11223",
        ),
        provider=Capture(),
    )
    assert "98450" not in seen["user"]
    assert "1MS21CS001" not in seen["user"]
    assert "BEGIN UNTRUSTED EMAIL" in seen["user"]


def test_quiet_hours_hold_normal_but_not_urgent(db, user, account):
    set_setting(
        db,
        user.id,
        "quiet_hours",
        {"enabled": True, "start": "00:00", "end": "23:59", "tz": "UTC", "allow_urgent": True},
    )
    db.add(
        Rule(
            user_id=user.id,
            name="all urgent",
            conditions={"all": [{"field": "subject", "op": "contains", "value": "urgent-mark"}]},
            action={"type": "notify_urgent"},
            position=0,
        )
    )
    db.commit()
    normal = process_message(db, account, mail(gmail_id="n", subject="hello", sender_email="a@vtu.ac.in", auth="vtu.ac.in"))
    urgent = process_message(db, account, mail(gmail_id="u", subject="urgent-mark please", sender_email="a@vtu.ac.in", auth="vtu.ac.in"))
    db.commit()
    assert normal.priority == "normal"
    normal_note = db.query(Notification).filter(Notification.message_id == normal.id).one()
    urgent_note = db.query(Notification).filter(Notification.message_id == urgent.id).one()
    assert normal_note.delivery_status == "held"
    assert urgent_note.delivery_status == "stored"
