"""Header parsing, subject fingerprints, redaction, and HTML sanitizing.

Subject normalization is deterministic: amounts, dates, ids, and known names
become placeholders so "INR 20 debited" and "INR 9,999 debited" share a fingerprint.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", re.I)
_AMOUNT = re.compile(
    r"(?:[₹$€£]\s?\d[\d,]*(?:\.\d+)?"
    r"|\b(?:rs\.?|inr|usd|eur|gbp)\s*\d[\d,]*(?:\.\d+)?"
    r"|\d[\d,]*(?:\.\d+)?\s*(?:rs\.?|inr|usd|eur|gbp)\b)",
    re.I,
)
_DATE = re.compile(
    r"\b(?:"
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,?\s+\d{4})?"
    r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?(?:,?\s+\d{4})?"
    r")\b",
    re.I,
)
_PHONE = re.compile(r"(?:\+\d{1,3}[\s-]*)?(?:\d[\s-]*){10,12}")
_ROLL = re.compile(
    r"\b(?:"
    r"\d{1,2}[a-z]{2}\d{2}[a-z]{2}\d{3,4}"
    r"|\d{4}[a-z]{2,10}\d{2,6}"
    r")\b",
    re.I,
)
_AADHAAR = re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b")
_LONG_ID = re.compile(r"\b\d{6,}\b")
_PREFIX = re.compile(r"^\s*((re|fw|fwd)\s*:\s*)+", re.I)


def domain_of(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower().strip(">")


def parse_address(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    match = _EMAIL.search(value)
    if not match:
        return value.strip() or None, None
    email = match.group(0).lower()
    name = value[: match.start()].strip().strip('"').strip()
    name = re.sub(r"\s*<\s*$", "", name).strip()
    return (name or None), email


def parse_address_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [m.group(0).lower() for m in _EMAIL.finditer(value)]


def authenticated_domain(header: str | None) -> str | None:
    """Domain that actually passed DKIM or SPF. Display names are ignored.

    A From header that claims kotak.com does not count unless Authentication-Results
    says dkim=pass or spf=pass for that domain. Spoofed mail must not hit sender rules.
    """
    if not header:
        return None
    if re.search(r"dkim\s*=\s*pass", header, re.I):
        match = re.search(r"header\.d\s*=\s*([a-z0-9.-]+)", header, re.I)
        if match:
            return match.group(1).lower().rstrip(".")
    if re.search(r"spf\s*=\s*pass", header, re.I):
        match = re.search(r"smtp\.mailfrom\s*=\s*<?([^>\s;]+)>?", header, re.I)
        if match:
            mailfrom = match.group(1).lower()
            return domain_of(mailfrom) or mailfrom
    return None


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_subject(subject: str | None, names: list[str] | None = None) -> str:
    text = (subject or "").strip().lower()
    text = _PREFIX.sub("", text)
    text = _EMAIL.sub(" {email} ", text)
    text = _AMOUNT.sub(" {amount} ", text)
    text = _DATE.sub(" {date} ", text)
    text = _PHONE.sub(" {phone} ", text)
    for name in names or []:
        token = name.strip().lower()
        if len(token) >= 3:
            text = re.sub(rf"\b{re.escape(token)}\b", " {name} ", text)
    text = re.sub(r"\d+", "{n}", text)
    text = re.sub(r"[^\w\s{}]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fingerprint_hash(sender_domain: str | None, template: str) -> str:
    raw = f"{(sender_domain or '').lower()}|{template}"
    return hashlib.sha256(raw.encode()).hexdigest()


def redact(text: str | None) -> str:
    """Strip identifiers before text is sent to an external model."""
    if not text:
        return ""
    out = _EMAIL.sub("[email]", text)
    out = _PHONE.sub("[phone]", out)
    out = _AADHAAR.sub("[id]", out)
    out = _ROLL.sub("[roll]", out)
    out = _LONG_ID.sub("[id]", out)
    return out


def html_to_text(html: str | None) -> str:
    if not html:
        return ""

    class Extractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []
            self.skip = 0

        def handle_starttag(self, tag, attrs):  # type: ignore[no-untyped-def]
            if tag in {"script", "style"}:
                self.skip += 1
            if tag in {"p", "div", "br", "tr", "li", "h1", "h2", "h3"}:
                self.parts.append("\n")

        def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
            if tag in {"script", "style"} and self.skip:
                self.skip -= 1

        def handle_data(self, data):  # type: ignore[no-untyped-def]
            if not self.skip:
                self.parts.append(data)

    parser = Extractor()
    parser.feed(html)
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()


def strip_quotes_and_signature(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "--" or stripped.startswith("-- "):
            break
        if re.match(r"^on .+ wrote:$", stripped, re.I):
            break
        if stripped.startswith(">"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def prepare_body(text: str | None, html: str | None, limit: int) -> str:
    raw = text or html_to_text(html)
    raw = strip_quotes_and_signature(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
    if len(raw) > limit:
        raw = raw[:limit].rstrip() + "…"
    return raw


def trigrams(value: str) -> set[str]:
    padded = f"  {(value or '').lower()}  "
    if len(padded) < 3:
        return set()
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def trigram_similarity(left: str, right: str) -> float:
    a, b = trigrams(left), trigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def normalize_company(name: str | None) -> str:
    text = (name or "").lower()
    text = re.sub(
        r"\b(pvt\.?|private|ltd\.?|limited|inc\.?|incorporated|corp\.?|corporation|llc|llp|"
        r"technologies|technology|tech|labs|solutions|india|group|company|co)\b",
        " ",
        text,
    )
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class _Sanitizer(HTMLParser):
    DROP = {"script", "style", "iframe", "object", "embed", "form", "link", "meta", "base", "svg", "math"}
    ALLOW = {
        "a", "p", "br", "div", "span", "table", "tr", "td", "th", "thead", "tbody", "ul", "ol", "li",
        "b", "strong", "i", "em", "blockquote", "pre", "code", "h1", "h2", "h3", "h4", "hr", "img",
        "small", "sup", "sub", "center",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):  # type: ignore[no-untyped-def]
        if tag in self.DROP:
            self.skip += 1
            return
        if self.skip or tag not in self.ALLOW:
            return
        attr_map = {k.lower(): v for k, v in attrs if k}
        if tag == "a":
            href = attr_map.get("href") or ""
            if href.lower().startswith(("http://", "https://", "mailto:")):
                safe = href.replace('"', "%22")
                self.out.append(f'<a href="{safe}" rel="noreferrer noopener" target="_blank">')
            else:
                self.out.append("<a>")
            return
        if tag == "img":
            src = (attr_map.get("src") or "").lower()
            if src.startswith(("http://", "https://", "//")):
                self.out.append('<span class="blocked-image">[remote image blocked]</span>')
            elif src.startswith("cid:"):
                alt = (attr_map.get("alt") or "image").replace("<", "")
                self.out.append(f'<span class="cid-image">[{alt}]</span>')
            return
        if tag == "br":
            self.out.append("<br>")
            return
        if tag == "hr":
            self.out.append("<hr>")
            return
        self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
        if tag in self.DROP:
            if self.skip:
                self.skip -= 1
            return
        if self.skip or tag not in self.ALLOW or tag in {"br", "hr", "img"}:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):  # type: ignore[no-untyped-def]
        if self.skip:
            return
        self.out.append(data.replace("<", "&lt;").replace(">", "&gt;"))


def sanitize_html(html: str | None) -> str:
    if not html:
        return ""
    parser = _Sanitizer()
    parser.feed(html)
    parser.close()
    return "".join(parser.out)


@dataclass
class RawMessage:
    gmail_id: str
    thread_id: str
    message_id_header: str | None = None
    sender_name: str | None = None
    sender_email: str | None = None
    to_addrs: list[str] = field(default_factory=list)
    cc_addrs: list[str] = field(default_factory=list)
    subject: str = ""
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    labels: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    snippet: str = ""
    body_text: str | None = None
    body_html: str | None = None
    size_bytes: int | None = None
    attachments: list[dict] = field(default_factory=list)
    authenticated_domain: str | None = None

    @property
    def sender_domain(self) -> str | None:
        return domain_of(self.sender_email)

    @property
    def has_attachment(self) -> bool:
        return bool(self.attachments) or "has-attachment" in {label.lower() for label in self.labels}


def header(headers: dict[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None
