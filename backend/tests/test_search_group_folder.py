from datetime import datetime, timedelta, timezone

from app.models import GroupPreference, Rule
from app.services.folders import FolderError, create_folder, delete_folder, file_thread, move_folder
from app.services.grouping import assign_group, group_counts, visible_group
from app.services.opportunities import decide_match, fuzzy_company
from app.services.pipeline import process_message
from app.services.search import message_matches, page_through, parse_query
from app.services.settings_store import set_setting
from app.services.textutil import normalize_subject
from tests.conftest import mail


def test_query_split_keeps_gmail_operators_and_drops_local_ones():
    parsed = parse_query('from:kotak.com -subject:fraud folder:Bank priority:urgent newer_than:7d')
    assert "from:kotak.com" in parsed.gmail_q
    assert "-subject:fraud" in parsed.gmail_q
    assert "newer_than:7d" in parsed.gmail_q
    assert "folder:" not in parsed.gmail_q
    assert "priority:" not in parsed.gmail_q


def test_or_parens_braces_and_around_round_trip_into_gmail():
    parsed = parse_query('(from:a@b.com OR from:c@d.com) subject:"placement drive"')
    assert "OR" in parsed.gmail_q
    assert "subject:" in parsed.gmail_q
    braces = parse_query("{alpha beta}")
    assert "OR" in braces.gmail_q
    around = parse_query("assessment AROUND 3 hirepro")
    assert "AROUND" in around.gmail_q


def test_local_filter_and_gmail_text_combine():
    now = datetime.now(timezone.utc)
    view = {
        "subject": "INR 20 debited via UPI",
        "sender_email": "alerts@kotak.com",
        "sender_domain": "kotak.com",
        "priority": "silent",
        "folders": ["Bank"],
        "received_at": now - timedelta(days=1),
        "is_read": False,
        "labels": ["INBOX"],
    }
    query = parse_query("from:kotak.com -subject:fraud folder:Bank newer_than:7d")
    assert message_matches(view, query.tree, now)
    fraud = dict(view, subject="suspicious fraud alert")
    assert not message_matches(fraud, query.tree, now)
    outside = dict(view, folders=["Other"])
    assert not message_matches(outside, query.tree, now)


def test_paging_keeps_reading_until_enough_local_matches_remain():
    now = datetime.now(timezone.utc)
    parsed = parse_query("from:vtu.ac.in")

    def row(i, domain):
        return {"subject": f"note {i}", "sender_email": f"a@{domain}", "received_at": now, "labels": [], "priority": "normal"}

    pages = [
        [row(0, "other.com"), row(1, "other.com")],
        [row(2, "vtu.ac.in"), row(3, "other.com")],
        [row(4, "vtu.ac.in"), row(5, "vtu.ac.in")],
    ]
    found, used = page_through(pages, lambda view: message_matches(view, parsed.tree, now), limit=2)
    assert [item["subject"] for item in found] == ["note 2", "note 4"]
    assert used == 3


def test_fuzzy_company_and_unsure_asks():
    class Opp:
        def __init__(self, id, company, aliases=None):
            self.id = id
            self.company = company
            self.aliases = aliases or []
            self.status = "active"
            self.updated_at = datetime.now(timezone.utc)
            self.created_at = self.updated_at

    known = Opp("1", "Accenture India Pvt Ltd")
    found, score = fuzzy_company("Accenture", [known])
    assert found.id == "1"
    assert score >= 0.86
    decision = decide_match(
        company="InMobi",
        opportunities=[known],
        thread_id="t",
        account_id="a",
        messages=[],
        links=[],
        events=[],
        llm_choice={"choice": "unsure"},
        looks_like_assessment=False,
    )
    assert decision["ask"] is True
    assert decision["opportunity_id"] is None


def test_twenty_bank_alerts_group_and_fraud_stays_separate(db, user, account):
    set_setting(db, user.id, "grouping_enabled", "yes")
    db.add(
        Rule(
            user_id=user.id,
            name="Silence bank transactions",
            conditions={"all": [
                {"field": "sender_domain", "op": "eq", "value": "kotak.com"},
                {"field": "subject", "op": "regex", "value": "debited"},
            ]},
            exceptions={"any": [{"field": "subject", "op": "regex", "value": "suspicious|fraud|blocked"}]},
            action={"type": "store_silent"},
            position=5,
        )
    )
    db.commit()
    debits = []
    for i in range(20):
        debits.append(
            process_message(
                db,
                account,
                mail(
                    gmail_id=f"d{i}",
                    subject=f"INR {10 + i}.00 debited from A/c XX9012 via UPI",
                    minutes_ago=20 - i,
                ),
            )
        )
    fraud = process_message(
        db,
        account,
        mail(gmail_id="fraud", subject="Alert: suspicious transaction blocked on your debit card", minutes_ago=0),
    )
    db.commit()
    grouped = [m for m in debits if m.id]
    # Re-load membership via group counts.
    from app.models import GroupMember, MailGroup

    groups = db.query(MailGroup).all()
    assert len(groups) == 1
    counts = group_counts(db, groups[0].id)
    assert visible_group(counts)
    assert counts["count"] == 20
    assert all(row.priority == "silent" for row in counts["messages"])
    assert db.query(GroupMember).filter(GroupMember.message_id == fraud.id).count() == 0
    assert fraud.priority in {"normal", "urgent"}
    assert fraud.llm_called is False


def test_grouping_off_computes_nothing(db, user, account):
    set_setting(db, user.id, "grouping_enabled", "no")
    first = process_message(db, account, mail(gmail_id="a", subject="INR 10 debited via UPI"))
    second = process_message(db, account, mail(gmail_id="b", subject="INR 12 debited via UPI"))
    db.commit()
    from app.models import MailGroup

    assert db.query(MailGroup).count() == 0
    assert assign_group(db, first) is None
    assert assign_group(db, second) is None


def test_never_group_preference(db, user, account):
    set_setting(db, user.id, "grouping_enabled", "yes")
    db.add(GroupPreference(user_id=user.id, kind="sender", value="alerts@kotak.com", mode="never"))
    db.commit()
    process_message(db, account, mail(gmail_id="a", subject="INR 10 debited via UPI"))
    process_message(db, account, mail(gmail_id="b", subject="INR 12 debited via UPI"))
    db.commit()
    from app.models import MailGroup

    assert db.query(MailGroup).count() == 0


def test_folder_depth_cycle_and_smart_folder(db, user, account):
    parent = None
    current = None
    for i in range(10):
        current = create_folder(db, user.id, f"L{i}", parent)
        parent = current.id
    try:
        create_folder(db, user.id, "too deep", parent)
        raised = False
    except FolderError as exc:
        raised = exc.code == "folder_depth"
    assert raised
    move_target = create_folder(db, user.id, "Root sibling", None)
    try:
        move_folder(db, move_target, current.id)
        cycle = False
    except FolderError as exc:
        cycle = exc.code == "folder_depth"
    assert cycle
    top = db.query(type(current)).filter_by(name="L0").one()
    try:
        move_folder(db, top, current.id)
        assert False
    except FolderError as exc:
        assert exc.code == "folder_cycle"
    smart = create_folder(db, user.id, "Smart", None, is_smart=True)
    try:
        file_thread(db, smart, account.id, "t1")
        assert False
    except FolderError as exc:
        assert exc.code == "smart_folder"
    normal = create_folder(db, user.id, "Placements", None)
    file_thread(db, normal, account.id, "thread-1")
    file_thread(db, normal, account.id, "thread-1")
    from app.models import FolderItem

    assert db.query(FolderItem).count() == 1
    child = create_folder(db, user.id, "child", normal.id)
    delete_folder(db, normal, subfolders="promote")
    db.flush()
    assert child.parent_id is None


def test_subject_templates_used_by_the_group_are_equal():
    assert normalize_subject("INR 10.00 debited from A/c XX9012 via UPI") == normalize_subject(
        "INR 999.00 debited from A/c XX9012 via UPI"
    )
