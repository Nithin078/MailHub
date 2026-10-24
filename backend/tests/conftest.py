from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import GmailAccount, User
from app.security import hash_password
from app.services.textutil import RawMessage


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def user(db):
    row = User(email="nithin@example.com", password_hash=hash_password("secret"), name="Nithin")
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def account(db, user):
    row = GmailAccount(
        user_id=user.id,
        email="nithin.college@gmail.com",
        label="College",
        provider="demo",
        status="active",
    )
    db.add(row)
    db.commit()
    return row


def mail(
    *,
    gmail_id="m1",
    thread_id=None,
    sender_name="Kotak Bank",
    sender_email="alerts@kotak.com",
    subject="INR 2,450.00 debited via UPI",
    auth="kotak.com",
    body="",
    labels=None,
    headers=None,
    message_id=None,
    minutes_ago=0,
):
    received = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    hdrs = {
        "From": f"{sender_name} <{sender_email}>",
        "Subject": subject,
        "Authentication-Results": f"mx.google.com; dkim=pass header.d={auth}; spf=pass smtp.mailfrom={sender_email};",
    }
    if headers:
        hdrs.update(headers)
    return RawMessage(
        gmail_id=gmail_id,
        thread_id=thread_id or gmail_id,
        message_id_header=message_id or f"<{gmail_id}@test>",
        sender_name=sender_name,
        sender_email=sender_email,
        subject=subject,
        received_at=received,
        labels=labels or ["INBOX", "UNREAD"],
        headers=hdrs,
        body_text=body,
        authenticated_domain=auth,
    )
