"""Per-user and per-account settings stored as JSON values."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.constants import DEFAULT_RESEND_LIMIT, DEFAULT_RESEND_MINUTES
from app.models import Setting

DEFAULTS: dict = {
    "grouping_enabled": "unset",  # unset | yes | no
    "grouping_prompt_dismissals": 0,
    "quiet_hours": {
        "enabled": False,
        "start": "23:00",
        "end": "07:00",
        "tz": "Asia/Kolkata",
        "allow_urgent": True,
    },
    "focus_mode": False,
    "category_mutes": [],
    "store_body_text": False,
    "timezone": "Asia/Kolkata",
    "resend_after_minutes": DEFAULT_RESEND_MINUTES,
    "resend_limit": DEFAULT_RESEND_LIMIT,
    "last_used_folder_id": None,
    "signature": "",
    "notify_enabled": True,
}


def get_setting(db: Session, user_id: str, key: str, account_id: str = ""):
    row = (
        db.query(Setting)
        .filter(Setting.user_id == user_id, Setting.account_id == (account_id or ""), Setting.key == key)
        .one_or_none()
    )
    if row is None:
        if account_id:
            return get_setting(db, user_id, key, "")
        return DEFAULTS.get(key)
    value = row.value
    if isinstance(value, dict) and "v" in value and len(value) == 1:
        return value["v"]
    return value


def set_setting(db: Session, user_id: str, key: str, value, account_id: str = "") -> None:
    account_id = account_id or ""
    row = (
        db.query(Setting)
        .filter(Setting.user_id == user_id, Setting.account_id == account_id, Setting.key == key)
        .one_or_none()
    )
    stored = value if isinstance(value, dict) else {"v": value}
    if row is None:
        db.add(Setting(user_id=user_id, account_id=account_id, key=key, value=stored))
    else:
        row.value = stored


def grouping_enabled(db: Session, user_id: str) -> str:
    value = get_setting(db, user_id, "grouping_enabled")
    if value not in {"unset", "yes", "no"}:
        return "unset"
    return value
