"""Demo inbox. Every sample goes through the real pipeline, with AI off.

The Accenture timeline is attached by hand afterwards, which is the AI-off path
from the brief. Turn on the demo model to classify mail that arrives after that.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.constants import BUILTIN_CATEGORIES
from app.models import (
    Category,
    FolderItem,
    GmailAccount,
    GroupMember,
    MailGroup,
    MailNote,
    MailTag,
    Message,
    Notification,
    Opportunity,
    OpportunityEvent,
    Reminder,
    Rule,
    Tag,
    User,
)
from app.security import hash_password
from app.services.folders import create_folder, file_thread
from app.services.pipeline import process_message
from app.services.settings_store import set_setting
from app.services.textutil import RawMessage

DEMO_EMAIL = "demo@inboxagent.local"
DEMO_PASSWORD = "demopass"


def seed_demo(db: Session) -> User:
    existing = db.query(User).filter(User.email == DEMO_EMAIL).one_or_none()
    if existing:
        return existing
    user = User(email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD), name="Nithin")
    db.add(user)
    db.flush()
    college = GmailAccount(
        user_id=user.id,
        email="nithin.college@gmail.com",
        label="College",
        color="#243e73",
        provider="demo",
        status="active",
        history_id="100",
    )
    personal = GmailAccount(
        user_id=user.id,
        email="nithin.personal@gmail.com",
        label="Personal",
        color="#9a4030",
        provider="demo",
        status="active",
        history_id="100",
    )
    db.add_all([college, personal])
    db.flush()
    set_setting(db, user.id, "store_body_text", True)
    set_setting(db, user.id, "timezone", "Asia/Kolkata")
    for name, definition in BUILTIN_CATEGORIES:
        db.add(Category(user_id=user.id, name=name, definition=definition, builtin=True))
    placements = create_folder(db, user.id, "Placements", color="#243e73", icon="briefcase")
    create_folder(db, user.id, "Money", color="#9a6b22", icon="bank")
    _rules(db, user.id, placements.id)
    db.flush()

    drive = _ingest(
        db,
        college,
        _raw(
            "drive",
            "Placements Office <placements@vtu.ac.in>",
            "Accenture campus drive for the 2026 batch",
            "vtu.ac.in",
            "Registration closes 28 Oct 2026, 6:00 PM IST.\n"
            "Eligible branches: CSE, ISE, ECE. CGPA 7.0 and above.\n"
            "Register: https://campus.vtu.ac.in/accenture-2026\n"
            "Carry your college ID. The test vendor will mail a separate link.",
            thread="accenture-drive",
            message_id="<accenture-drive-2026@vtu.ac.in>",
            minutes_ago=180,
        ),
    )
    _ingest(
        db,
        personal,
        _raw(
            "drive-copy",
            "Placements Office <placements@vtu.ac.in>",
            "Accenture campus drive for the 2026 batch",
            "vtu.ac.in",
            "Forwarded copy of the same announcement.",
            thread="accenture-drive-personal",
            message_id="<accenture-drive-2026@vtu.ac.in>",
            minutes_ago=170,
        ),
    )
    hirepro = _ingest(
        db,
        college,
        _raw(
            "hirepro",
            "HirePro Assessments <noreply@hirepro.in>",
            "Accenture | complete your online assessment",
            "hirepro.in",
            "Your Accenture online assessment is ready.\n"
            "Open https://tests.hirepro.in/accenture and finish it before 28 Oct 2026, 6:00 PM IST.\n"
            "A stable connection is required. Do not forward this link.",
            thread="accenture-oa",
            minutes_ago=40,
        ),
    )
    follow = _ingest(
        db,
        college,
        _raw(
            "follow",
            "Priya Sharma <priya.sharma@accenture.com>",
            "Accenture drive — next steps for your application",
            "accenture.com",
            "Hello Nithin,\n\n"
            "We received your registration for the Accenture campus drive. "
            "Please complete the HirePro assessment before the deadline.\n\n"
            "Priya Sharma\nEarly Careers, Accenture",
            thread="accenture-drive",
            minutes_ago=25,
        ),
    )
    _ingest(
        db,
        college,
        _raw(
            "fraud",
            "Kotak Bank <alerts@kotak.com>",
            "Alert: suspicious transaction blocked on your debit card",
            "kotak.com",
            "We blocked a card transaction that did not match your usual pattern. "
            "If this was you, no action is needed. If not, call the number on your card.",
            minutes_ago=15,
        ),
    )
    for index, amount in enumerate((540, 120, 2499, 80, 65, 999, 149, 2100)):
        _ingest(
            db,
            personal,
            _raw(
                f"kotak-{index}",
                "Kotak Bank <alerts@kotak.com>",
                f"INR {amount}.00 debited from A/c XX9012 via UPI",
                "kotak.com",
                f"UPI debit of INR {amount}.00. This is an automated alert.",
                minutes_ago=600 - index * 30,
            ),
        )
    _ingest(
        db,
        personal,
        _raw(
            "slice",
            "Slice <no-reply@sliceit.com>",
            "You spent Rs 499.00 at Swiggy",
            "sliceit.com",
            "Slice card spend of Rs 499.00 at Swiggy. Automated alert.",
            minutes_ago=90,
        ),
    )
    _ingest(
        db,
        personal,
        _raw(
            "linkedin",
            "LinkedIn <jobs-noreply@linkedin.com>",
            "12 new jobs for backend intern",
            "linkedin.com",
            "Jobs you might like. Unsubscribe at the link below.",
            labels=["CATEGORY_PROMOTIONS", "INBOX"],
            extra_headers={
                "List-Unsubscribe": "<https://linkedin.com/unsub>, <mailto:unsubscribe@linkedin.com>",
                "Precedence": "bulk",
                "List-Id": "linkedin-jobs.linkedin.com",
            },
            minutes_ago=300,
        ),
    )
    _ingest(
        db,
        personal,
        _raw(
            "amazon",
            "Amazon.in <noreply@amazon.in>",
            "Your Amazon.in order of headphones has shipped",
            "amazon.in",
            "Order 402-2219384 has shipped. Automated update.",
            labels=["CATEGORY_UPDATES", "INBOX"],
            extra_headers={"Precedence": "bulk"},
            minutes_ago=500,
            attachments=[{"filename": "invoice.pdf", "mime": "application/pdf", "size": 84000}],
        ),
    )
    razor = _ingest(
        db,
        college,
        _raw(
            "razor",
            "Ankit Rao <ankit.rao@razorpay.com>",
            "Backend intern — Razorpay",
            "razorpay.com",
            "Hi Nithin, I came across your profile. We have a backend intern opening in Bengaluru. "
            "Would you have 20 minutes this week?",
            minutes_ago=50,
        ),
    )

    deadline = datetime(2026, 10, 28, 12, 30, tzinfo=timezone.utc)  # 18:00 IST
    accenture = Opportunity(
        user_id=user.id,
        company="Accenture",
        aliases=["Accenture India"],
        status="active",
        deadline=deadline,
        notes="Campus drive. Assessment is on HirePro.",
    )
    db.add(accenture)
    db.flush()
    for message, event_type, summary in (
        (drive, "announcement", "Campus drive announced"),
        (hirepro, "assessment", "HirePro assessment link"),
        (follow, "recruiter", "Recruiter follow-up"),
    ):
        message.opportunity_id = accenture.id
        db.add(
            OpportunityEvent(
                opportunity_id=accenture.id,
                message_id=message.id,
                event_type=event_type,
                details={"subject": message.subject, "summary": summary, "links": []},
                added_by="user",
            )
        )
    db.add(
        Reminder(
            user_id=user.id,
            message_id=drive.id,
            opportunity_id=accenture.id,
            remind_at=deadline - timedelta(hours=24),
            kind="deadline_24h",
            note="Accenture registration closes in 24 hours",
        )
    )
    db.add(MailNote(user_id=user.id, message_id=drive.id, body="Need marksheet PDF before registering."))
    file_thread(db, placements, college.id, drive.thread_id)
    razor.pinned = True
    follow.is_read = False
    db.commit()
    return user


def simulate(db: Session, user: User, kind: str) -> Message:
    account = (
        db.query(GmailAccount)
        .filter(GmailAccount.user_id == user.id, GmailAccount.provider == "demo")
        .order_by(GmailAccount.created_at.asc())
        .first()
    )
    if account is None:
        raise ValueError("No demo account")
    stamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    catalog = {
        "bank": (
            f"Kotak Bank <alerts@kotak.com>",
            f"INR {100 + int(stamp[-3:])}.00 debited from A/c XX9012 via UPI",
            "kotak.com",
            "Automated UPI debit alert.",
        ),
        "fraud": (
            "Kotak Bank <alerts@kotak.com>",
            "Alert: suspicious transaction blocked on your debit card",
            "kotak.com",
            "A card transaction was blocked.",
        ),
        "placement": (
            "Placements Office <placements@vtu.ac.in>",
            "Infosys campus drive for the 2026 batch",
            "vtu.ac.in",
            "Infosys placement drive. Register: https://campus.vtu.ac.in/infosys",
        ),
        "assessment": (
            "HirePro Assessments <noreply@hirepro.in>",
            "Infosys | complete your online assessment",
            "hirepro.in",
            "Infosys assessment on HirePro. https://tests.hirepro.in/infosys",
        ),
        "recruiter": (
            "Meera Iyer <meera.iyer@walmart.com>",
            "Opportunity at Walmart for a backend intern",
            "walmart.com",
            "Recruiter note. Opportunity at Walmart. Can we talk this week?",
        ),
    }
    if kind not in catalog:
        raise ValueError("Unknown sample")
    sender, subject, domain, body = catalog[kind]
    message = process_message(
        db,
        account,
        _raw(f"sim-{kind}-{stamp}", sender, subject, domain, body, minutes_ago=0),
    )
    db.commit()
    return message


def reset_demo(db: Session) -> User:
    existing = db.query(User).filter(User.email == DEMO_EMAIL).one_or_none()
    if existing:
        _wipe(db, existing.id)
        db.commit()
    return seed_demo(db)


def _wipe(db: Session, user_id: str) -> None:
    from app.models import (
        Device,
        Draft,
        Fingerprint,
        Folder,
        GroupPreference,
        LLMCredential,
        RecentSearch,
        SavedSearch,
        Setting,
        TokenUsage,
    )

    message_ids = [row.id for row in db.query(Message.id).filter(Message.user_id == user_id)]
    if message_ids:
        db.query(Notification).filter(Notification.message_id.in_(message_ids)).delete(synchronize_session=False)
        db.query(MailTag).filter(MailTag.message_id.in_(message_ids)).delete(synchronize_session=False)
        db.query(MailNote).filter(MailNote.message_id.in_(message_ids)).delete(synchronize_session=False)
        db.query(GroupMember).filter(GroupMember.message_id.in_(message_ids)).delete(synchronize_session=False)
        db.query(OpportunityEvent).filter(OpportunityEvent.message_id.in_(message_ids)).delete(synchronize_session=False)
    db.query(GroupMember).filter(
        GroupMember.group_id.in_(db.query(MailGroup.id).filter(MailGroup.user_id == user_id))
    ).delete(synchronize_session=False)
    db.query(FolderItem).filter(
        FolderItem.folder_id.in_(db.query(Folder.id).filter(Folder.user_id == user_id))
    ).delete(synchronize_session=False)
    db.query(OpportunityEvent).filter(
        OpportunityEvent.opportunity_id.in_(db.query(Opportunity.id).filter(Opportunity.user_id == user_id))
    ).delete(synchronize_session=False)
    for model in (
        Message,
        Rule,
        Category,
        Opportunity,
        Fingerprint,
        Reminder,
        TokenUsage,
        LLMCredential,
        Folder,
        Tag,
        SavedSearch,
        RecentSearch,
        Draft,
        MailGroup,
        GroupPreference,
        Setting,
        Device,
        GmailAccount,
    ):
        db.query(model).filter(model.user_id == user_id).delete(synchronize_session=False)
    db.query(User).filter(User.id == user_id).delete(synchronize_session=False)
    db.flush()


def _rules(db: Session, user_id: str, placements_id: str) -> None:
    db.add_all(
        [
            Rule(
                user_id=user_id,
                name="Bank fraud is urgent",
                conditions={"all": [
                    {"field": "sender_domain", "op": "in", "value": ["kotak.com", "sliceit.com"]},
                    {"field": "subject", "op": "regex", "value": "fraud|blocked|suspicious"},
                ]},
                action={"type": "notify_urgent", "category": "action_needed"},
                position=0,
                origin="editor",
            ),
            Rule(
                user_id=user_id,
                name="Silence bank transactions",
                conditions={"all": [
                    {"field": "sender_domain", "op": "in", "value": ["kotak.com", "sliceit.com"]},
                    {"field": "subject", "op": "regex", "value": "debit|credit|spent|upi"},
                ]},
                exceptions={"any": [{"field": "subject", "op": "regex", "value": "fraud|blocked|suspicious"}]},
                action={"type": "store_silent"},
                position=10,
                origin="editor",
            ),
            Rule(
                user_id=user_id,
                name="College placement mail is urgent",
                conditions={"all": [{"field": "sender_domain", "op": "eq", "value": "vtu.ac.in"}]},
                action={
                    "type": "notify_urgent",
                    "category": "placement_announcement",
                    "folder_id": placements_id,
                    "tag": "drive",
                },
                position=20,
                origin="editor",
            ),
            Rule(
                user_id=user_id,
                name="HirePro links are urgent",
                conditions={"all": [{"field": "sender_domain", "op": "eq", "value": "hirepro.in"}]},
                action={"type": "notify_urgent", "category": "assessment_link"},
                position=30,
                origin="editor",
            ),
        ]
    )


def _ingest(db: Session, account: GmailAccount, raw: RawMessage) -> Message:
    return process_message(db, account, raw)


def _raw(
    gmail_id: str,
    sender: str,
    subject: str,
    domain: str,
    body: str,
    *,
    thread: str | None = None,
    message_id: str | None = None,
    minutes_ago: int = 0,
    labels: list[str] | None = None,
    extra_headers: dict | None = None,
    attachments: list | None = None,
) -> RawMessage:
    from app.services.textutil import parse_address

    name, email = parse_address(sender)
    received = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    headers = {
        "From": sender,
        "To": "nithin@example.com",
        "Subject": subject,
        "Date": received.strftime("%a, %d %b %Y %H:%M:%S +0000"),
        "Message-ID": message_id or f"<{gmail_id}@demo.inboxagent>",
        "Authentication-Results": (
            f"mx.google.com; dkim=pass header.d={domain} header.s=demo; "
            f"spf=pass smtp.mailfrom={email}; dmarc=pass"
        ),
    }
    if extra_headers:
        headers.update(extra_headers)
    return RawMessage(
        gmail_id=gmail_id,
        thread_id=thread or gmail_id,
        message_id_header=headers["Message-ID"],
        sender_name=name,
        sender_email=email,
        to_addrs=["nithin.college@gmail.com"],
        subject=subject,
        received_at=received,
        labels=labels or ["INBOX", "UNREAD"],
        headers=headers,
        snippet=body[:180],
        body_text=body,
        authenticated_domain=domain,
        attachments=attachments or [],
        size_bytes=len(body) + 800,
    )
