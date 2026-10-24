"""Gmail behind a small provider interface. Only Gmail is implemented.

Watch expiry, history cursors, and OAuth errors are handled here so a bad
account cannot stop the others.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import httpx

from app.config import get_settings
from app.constants import GMAIL_MODIFY, GMAIL_READONLY
from app.crypto import decrypt, encrypt
from app.models import GmailAccount, utcnow
from app.services.textutil import (
    RawMessage,
    authenticated_domain,
    header,
    parse_address,
    parse_address_list,
    parse_date,
)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

BLOCKED_MARKERS = ("admin_policy_enforced", "policy_enforced", "org_internal")


class GmailError(Exception):
    def __init__(self, status: str, message: str):
        self.status = status  # needs_reconnect | blocked | temporary
        super().__init__(message)


def classify_auth_error(code: str | None, description: str | None = None) -> str:
    blob = f"{code or ''} {description or ''}".lower()
    if any(marker in blob for marker in BLOCKED_MARKERS):
        return "blocked"
    if (code or "").lower() == "access_denied":
        return "cancelled"
    return "needs_reconnect"


def auth_url(state: str, scopes: list[str] | None = None) -> str:
    settings = get_settings()
    scope = " ".join(scopes or [GMAIL_READONLY])
    query = httpx.QueryParams(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": scope,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


def exchange_code(code: str) -> dict:
    settings = get_settings()
    response = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    data = response.json()
    if response.status_code >= 400:
        raise GmailError(classify_auth_error(data.get("error"), data.get("error_description")), data.get("error_description") or "OAuth failed")
    return data


def _refresh(account: GmailAccount) -> str:
    refresh = decrypt(account.encrypted_refresh_token)
    if not refresh:
        raise GmailError("needs_reconnect", "This account has no refresh token.")
    settings = get_settings()
    response = httpx.post(
        TOKEN_URL,
        data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    data = response.json()
    if response.status_code >= 400:
        status = classify_auth_error(data.get("error"), data.get("error_description"))
        if data.get("error") == "invalid_grant":
            status = "needs_reconnect"
        raise GmailError(status, data.get("error_description") or "Refresh failed")
    account.encrypted_access_token = encrypt(data["access_token"])
    account.access_token_expiry = utcnow() + timedelta(seconds=int(data.get("expires_in") or 3600) - 60)
    return data["access_token"]


def access_token(account: GmailAccount) -> str:
    expiry = account.access_token_expiry
    if account.encrypted_access_token and expiry:
        exp = expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)
        if exp > datetime.now(timezone.utc) + timedelta(seconds=30):
            token = decrypt(account.encrypted_access_token)
            if token:
                return token
    return _refresh(account)


def _request(account: GmailAccount, method: str, path: str, **kwargs) -> httpx.Response:
    token = access_token(account)
    response = httpx.request(
        method,
        f"{GMAIL_API}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
        **kwargs,
    )
    if response.status_code == 401:
        token = _refresh(account)
        response = httpx.request(
            method,
            f"{GMAIL_API}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            **kwargs,
        )
    if response.status_code >= 400:
        status, message = _error_status(response)
        raise GmailError(status, message)
    return response


def _error_status(response: httpx.Response) -> tuple[str, str]:
    try:
        body = response.json()
    except Exception:
        body = {}
    error = body.get("error") if isinstance(body, dict) else {}
    if isinstance(error, dict):
        reasons = " ".join(item.get("reason", "") for item in error.get("errors") or [])
        message = error.get("message") or response.text[:200]
        blob = f"{reasons} {message}"
    else:
        blob = str(error or response.text[:200])
        message = blob
    if response.status_code in {401, 400} or "invalid_grant" in blob.lower():
        return classify_auth_error(blob, message), message
    if response.status_code == 403 and any(marker in blob.lower() for marker in BLOCKED_MARKERS):
        return "blocked", message
    if response.status_code == 403:
        return "needs_reconnect", message
    return "temporary", message


def get_profile(account: GmailAccount) -> dict:
    return _request(account, "GET", "/users/me/profile").json()


def start_watch(account: GmailAccount) -> dict | None:
    topic = get_settings().pubsub_topic
    if not topic:
        return None
    data = _request(
        account,
        "POST",
        "/users/me/watch",
        json={"topicName": topic, "labelIds": ["INBOX"], "labelFilterBehavior": "INCLUDE"},
    ).json()
    expiry_ms = int(data.get("expiration") or 0)
    if expiry_ms:
        account.watch_expiry = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc)
    if data.get("historyId"):
        account.history_id = str(data["historyId"])
    return data


def stop_watch(account: GmailAccount) -> None:
    try:
        _request(account, "POST", "/users/me/stop")
    except GmailError:
        return


def list_history(account: GmailAccount, start_history_id: str) -> tuple[list[str], str | None]:
    ids: list[str] = []
    page = None
    newest = None
    while True:
        params = {"startHistoryId": start_history_id, "historyTypes": "messageAdded"}
        if page:
            params["pageToken"] = page
        response = httpx.request(
            "GET",
            f"{GMAIL_API}/users/me/history",
            headers={"Authorization": f"Bearer {access_token(account)}"},
            params=params,
            timeout=30,
        )
        if response.status_code == 404:
            raise GmailError("history_expired", "Stored historyId is too old.")
        if response.status_code >= 400:
            status, message = _error_status(response)
            raise GmailError(status, message)
        data = response.json()
        newest = str(data.get("historyId") or newest or start_history_id)
        for item in data.get("history") or []:
            for added in item.get("messagesAdded") or []:
                message = added.get("message") or {}
                if message.get("id"):
                    ids.append(message["id"])
        page = data.get("nextPageToken")
        if not page:
            break
    # Preserve order, drop duplicates.
    seen = set()
    ordered = []
    for item in ids:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered, newest


def get_message(account: GmailAccount, message_id: str, fmt: str = "full") -> RawMessage:
    params = [("format", fmt)]
    if fmt == "metadata":
        for name in (
            "From",
            "To",
            "Cc",
            "Subject",
            "Date",
            "Message-ID",
            "List-Unsubscribe",
            "List-Id",
            "Authentication-Results",
            "Precedence",
        ):
            params.append(("metadataHeaders", name))
    data = _request(account, "GET", f"/users/me/messages/{message_id}", params=params).json()
    return parse_gmail_message(data)


def list_message_ids(account: GmailAccount, query: str, page_token: str | None = None, limit: int = 50) -> dict:
    params: dict = {"q": query, "maxResults": limit}
    if page_token:
        params["pageToken"] = page_token
    return _request(account, "GET", "/users/me/messages", params=params).json()


def send_message(account: GmailAccount, *, to: list[str], subject: str, body: str, cc: list[str] | None = None, in_reply_to: str | None = None, thread_id: str | None = None) -> dict:
    if GMAIL_MODIFY not in (account.scopes or []) and "https://www.googleapis.com/auth/gmail.send" not in (account.scopes or []):
        raise GmailError("needs_scope", "Sending needs the compose scope. Grant it from Accounts.")
    mime = MIMEText(body, "plain", "utf-8")
    mime["To"] = ", ".join(to)
    if cc:
        mime["Cc"] = ", ".join(cc)
    mime["Subject"] = subject
    if in_reply_to:
        mime["In-Reply-To"] = in_reply_to
        mime["References"] = in_reply_to
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    payload: dict = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    return _request(account, "POST", "/users/me/messages/send", json=payload).json()


def modify_labels(account: GmailAccount, message_id: str, *, add: list[str] | None = None, remove: list[str] | None = None) -> None:
    if GMAIL_MODIFY not in (account.scopes or []):
        raise GmailError("needs_scope", "Changing Gmail labels needs the modify scope.")
    _request(
        account,
        "POST",
        f"/users/me/messages/{message_id}/modify",
        json={"addLabelIds": add or [], "removeLabelIds": remove or []},
    )


def trash_message(account: GmailAccount, message_id: str) -> None:
    if GMAIL_MODIFY not in (account.scopes or []):
        raise GmailError("needs_scope", "Trash needs the modify scope.")
    _request(account, "POST", f"/users/me/messages/{message_id}/trash")


def parse_gmail_message(data: dict) -> RawMessage:
    headers = {item["name"]: item["value"] for item in (data.get("payload") or {}).get("headers") or []}
    name, email = parse_address(headers.get("From"))
    received = parse_date(headers.get("Date"))
    if received is None and data.get("internalDate"):
        received = datetime.fromtimestamp(int(data["internalDate"]) / 1000, tz=timezone.utc)
    if received is None:
        received = datetime.now(timezone.utc)
    text, html, attachments = _walk_parts(data.get("payload") or {})
    auth = authenticated_domain(headers.get("Authentication-Results"))
    return RawMessage(
        gmail_id=data.get("id") or "",
        thread_id=data.get("threadId") or data.get("id") or "",
        message_id_header=headers.get("Message-ID") or headers.get("Message-Id"),
        sender_name=name,
        sender_email=email,
        to_addrs=parse_address_list(headers.get("To")),
        cc_addrs=parse_address_list(headers.get("Cc")),
        subject=headers.get("Subject") or "",
        received_at=received,
        labels=list(data.get("labelIds") or []),
        headers=headers,
        snippet=data.get("snippet") or "",
        body_text=text or None,
        body_html=html or None,
        size_bytes=data.get("sizeEstimate"),
        attachments=attachments,
        authenticated_domain=auth,
    )


def _walk_parts(part: dict) -> tuple[str, str, list[dict]]:
    text, html, attachments = "", "", []
    mime = part.get("mimeType") or ""
    body = part.get("body") or {}
    filename = part.get("filename") or ""
    if filename:
        attachments.append(
            {
                "filename": filename,
                "mime": mime,
                "size": body.get("size") or 0,
                "attachment_id": body.get("attachmentId"),
            }
        )
    elif mime == "text/plain":
        text += _decode(body.get("data"))
    elif mime == "text/html":
        html += _decode(body.get("data"))
    for child in part.get("parts") or []:
        child_text, child_html, child_attachments = _walk_parts(child)
        text += child_text
        html += child_html
        attachments.extend(child_attachments)
    return text, html, attachments


def _decode(data: str | None) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")
    except Exception:
        return ""


def has_modify_scope(account: GmailAccount) -> bool:
    return GMAIL_MODIFY in (account.scopes or [])


def header_value(raw: RawMessage, name: str) -> str | None:
    return header(raw.headers, name)
