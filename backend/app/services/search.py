"""Gmail-syntax parser that splits a query into a Gmail part and local-only filters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.constants import GMAIL_CATEGORIES, GMAIL_MAILBOXES, GMAIL_OPERATORS, LOCAL_OPERATORS


class QueryError(ValueError):
    pass


@dataclass
class Term:
    op: str | None
    value: str
    neg: bool = False

    def is_text(self) -> bool:
        return self.op is None


@dataclass
class Node:
    kind: str  # and | or | term
    children: list["Node"] = field(default_factory=list)
    term: Term | None = None


@dataclass
class ParsedQuery:
    tree: Node
    gmail_q: str
    sort: str = "newest"
    errors: list[str] = field(default_factory=list)


def _tokenize(query: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    text = query.strip()
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "(){}":
            tokens.append(ch)
            i += 1
            continue
        if ch == '"':
            j = i + 1
            buf = []
            while j < len(text) and text[j] != '"':
                buf.append(text[j])
                j += 1
            tokens.append('"' + "".join(buf) + '"')
            i = j + 1 if j < len(text) else j
            continue
        j = i
        while j < len(text) and not text[j].isspace() and text[j] not in "(){}":
            j += 1
        tokens.append(text[i:j])
        i = j
    return tokens


def _parse_term(token: str) -> Term:
    neg = False
    if token.startswith("-") and len(token) > 1:
        neg = True
        token = token[1:]
    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        return Term(op=None, value=token[1:-1], neg=neg)
    if ":" in token:
        op, value = token.split(":", 1)
        op_l = op.lower()
        if op_l in GMAIL_OPERATORS or op_l in LOCAL_OPERATORS or op_l == "sort":
            if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                value = value[1:-1]
            return Term(op=op_l, value=value, neg=neg)
    return Term(op=None, value=token, neg=neg)


class _Parser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.i = 0

    def peek(self) -> str | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def pop(self) -> str:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def parse(self) -> Node:
        node = self.parse_or()
        return node

    def parse_or(self) -> Node:
        left = self.parse_and()
        items = [left]
        while self.peek() and self.peek().upper() == "OR":
            self.pop()
            items.append(self.parse_and())
        if len(items) == 1:
            return left
        return Node(kind="or", children=items)

    def parse_and(self) -> Node:
        items: list[Node] = []
        while True:
            token = self.peek()
            if token is None or token in {")", "}"} or token.upper() == "OR":
                break
            if token.upper() == "AROUND":
                # "left AROUND n right" — the left term is already consumed.
                self.pop()
                distance = self.pop() if self.peek() and self.peek()[:1].isdigit() else "1"
                right = self.parse_primary()
                left = items.pop() if items else Node(kind="term", term=Term(None, ""))
                around = Node(
                    kind="term",
                    term=Term(op="around", value=f"{_term_text(left)} AROUND {distance} {_term_text(right)}"),
                )
                items.append(around)
                continue
            items.append(self.parse_unary())
        if not items:
            return Node(kind="and", children=[])
        if len(items) == 1:
            return items[0]
        return Node(kind="and", children=items)

    def parse_unary(self) -> Node:
        token = self.peek()
        if token and token.upper() in {"NOT", "-"} and token.upper() == "NOT":
            self.pop()
            node = self.parse_unary()
            return _negate(node)
        return self.parse_primary()

    def parse_primary(self) -> Node:
        token = self.pop() if self.peek() is not None else ""
        if token == "(":
            node = self.parse_or()
            if self.peek() == ")":
                self.pop()
            return node
        if token == "{":
            children = []
            while self.peek() and self.peek() != "}":
                children.append(self.parse_primary())
            if self.peek() == "}":
                self.pop()
            if not children:
                return Node(kind="and", children=[])
            if len(children) == 1:
                return children[0]
            return Node(kind="or", children=children)
        term = _parse_term(token)
        if term.neg:
            term.neg = False
            return _negate(Node(kind="term", term=term))
        return Node(kind="term", term=term)


def _term_text(node: Node) -> str:
    if node.term and node.term.value:
        return node.term.value
    if node.children:
        return " ".join(_term_text(child) for child in node.children)
    return ""


def _negate(node: Node) -> Node:
    return _negate_tree(node)


def _negate_tree(node: Node) -> Node:
    if node.kind == "term" and node.term:
        return Node(kind="term", term=Term(node.term.op, node.term.value, neg=not node.term.neg))
    if node.kind == "or":
        return Node(kind="and", children=[_negate_tree(child) for child in node.children])
    if node.kind == "and":
        return Node(kind="or", children=[_negate_tree(child) for child in node.children])
    return node


def _is_gmail_term(term: Term) -> bool:
    if term.op is None or term.op == "around":
        return True
    if term.op in LOCAL_OPERATORS or term.op == "sort":
        return False
    if term.op == "category" and term.value.lower() not in GMAIL_CATEGORIES:
        return False
    if term.op == "in" and term.value.lower() not in GMAIL_MAILBOXES:
        return False
    if term.op in GMAIL_OPERATORS:
        return True
    return False


def _serialize_term(term: Term) -> str:
    prefix = "-" if term.neg else ""
    if term.op is None:
        if " " in term.value or term.value == "":
            return f'{prefix}"{term.value}"'
        return f"{prefix}{term.value}"
    if term.op == "around":
        return f"{prefix}{term.value}"
    value = term.value
    if any(ch.isspace() for ch in value):
        value = f'"{value}"'
    return f"{prefix}{term.op}:{value}"


def _serialize_gmail(node: Node) -> str | None:
    if node.kind == "term" and node.term:
        if not _is_gmail_term(node.term):
            return None
        if node.term.op == "sort":
            return None
        return _serialize_term(node.term)
    if node.kind == "and":
        parts = [part for child in node.children if (part := _serialize_gmail(child))]
        return " ".join(parts) if parts else None
    if node.kind == "or":
        parts = [part for child in node.children if (part := _serialize_gmail(child))]
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        return "(" + " OR ".join(parts) + ")"
    return None


def _find_sort(node: Node) -> str:
    if node.kind == "term" and node.term and node.term.op == "sort":
        value = node.term.value.lower()
        if value in {"newest", "oldest", "sender", "size", "relevance"}:
            return value
    for child in node.children:
        found = _find_sort(child)
        if found:
            return found
    return "newest"


def page_through(pages, predicate, limit: int) -> tuple[list, int]:
    """Read provider pages until `limit` local matches remain, or the source ends."""
    found: list = []
    used = 0
    for page in pages:
        used += 1
        for item in page:
            if predicate(item):
                found.append(item)
                if len(found) >= limit:
                    return found, used
    return found, used


def parse_query(query: str) -> ParsedQuery:
    tokens = _tokenize(query or "")
    if not tokens:
        tree = Node(kind="and", children=[])
        return ParsedQuery(tree=tree, gmail_q="", sort="newest")
    tree = _Parser(tokens).parse()
    gmail_q = _serialize_gmail(tree) or ""
    return ParsedQuery(tree=tree, gmail_q=gmail_q.strip(), sort=_find_sort(tree))


def local_filters(node: Node) -> list[Term]:
    found: list[Term] = []

    def walk(current: Node) -> None:
        if current.kind == "term" and current.term and not _is_gmail_term(current.term):
            found.append(current.term)
        for child in current.children:
            walk(child)

    walk(node)
    return found


def _parse_rel(value: str) -> timedelta | None:
    match = re_full = __import__("re").fullmatch(r"(\d+)([dmy])", value.lower())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        return timedelta(days=amount)
    if unit == "m":
        return timedelta(days=30 * amount)
    return timedelta(days=365 * amount)


def message_matches(view: dict, node: Node, now: datetime | None = None) -> bool:
    """Evaluate the full query against a local view dict.

    The view carries the fields our database knows. Gmail operators are applied
    to those same fields so demo mail and tests behave without calling Google.
    A connected account still sends `gmail_q` to users.messages.list.
    """
    now = now or datetime.now(timezone.utc)
    return _eval(view, node, now)


def _contains(hay: str, needle: str) -> bool:
    return needle.lower() in (hay or "").lower()


def _eval(view: dict, node: Node, now: datetime) -> bool:
    if node.kind == "and":
        if not node.children:
            return True
        return all(_eval(view, child, now) for child in node.children)
    if node.kind == "or":
        return any(_eval(view, child, now) for child in node.children)
    term = node.term or Term(None, "")
    ok = _eval_term(view, term, now)
    return (not ok) if term.neg else ok


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _eval_term(view: dict, term: Term, now: datetime) -> bool:
    op = term.op
    value = term.value
    subject = view.get("subject") or ""
    sender = view.get("sender_email") or ""
    sender_name = view.get("sender_name") or ""
    domain = view.get("sender_domain") or ""
    body = view.get("body") or view.get("snippet") or ""
    labels = [item.lower() for item in view.get("labels") or []]
    if op is None or op == "around":
        blob = " ".join([subject, sender, sender_name, body])
        if op == "around":
            words = [w for w in value.split() if w.upper() != "AROUND" and not w.isdigit()]
            return all(word.lower() in blob.lower() for word in words)
        return _contains(blob, value)
    if op == "from":
        return _contains(sender, value) or _contains(sender_name, value) or _contains(domain, value)
    if op == "to":
        return any(_contains(item, value) for item in view.get("to") or [])
    if op == "cc":
        return any(_contains(item, value) for item in view.get("cc") or [])
    if op == "bcc":
        return any(_contains(item, value) for item in view.get("bcc") or [])
    if op == "subject":
        return _contains(subject, value)
    if op == "label":
        return value.lower() in labels
    if op == "filename":
        names = " ".join(a.get("filename", "") for a in view.get("attachments") or [])
        return _contains(names, value)
    if op == "has":
        kind = value.lower()
        if kind == "attachment":
            return bool(view.get("has_attachment"))
        return any(kind in (a.get("mime") or "").lower() or kind in (a.get("filename") or "").lower() for a in view.get("attachments") or [])
    if op == "is":
        flag = value.lower()
        mapping = {
            "unread": not view.get("is_read"),
            "read": bool(view.get("is_read")),
            "starred": bool(view.get("is_starred")),
            "important": "important" in labels,
        }
        if flag in mapping:
            return mapping[flag]
        return flag in labels
    if op == "in":
        mailbox = value.lower()
        if mailbox in GMAIL_MAILBOXES:
            if mailbox == "inbox":
                return not view.get("is_archived") and not view.get("is_trashed") and not view.get("is_spam")
            if mailbox == "trash":
                return bool(view.get("is_trashed"))
            if mailbox == "spam":
                return bool(view.get("is_spam"))
            if mailbox == "archive":
                return bool(view.get("is_archived"))
            return True
        folders = [name.lower() for name in view.get("folders") or []]
        return mailbox in folders or any(mailbox in name for name in folders)
    if op == "folder":
        folders = [name.lower() for name in view.get("folders") or []]
        if value.lower() in {"none", "nofolder"}:
            return not folders
        return value.lower() in folders
    if op == "category":
        if value.lower() in GMAIL_CATEGORIES:
            return f"category_{value.lower()}" in labels
        return (view.get("category") or "").lower() == value.lower()
    if op == "priority":
        return (view.get("priority") or "").lower() == value.lower()
    if op == "tag":
        return value.lower() in [t.lower() for t in view.get("tags") or []]
    if op == "account":
        return value.lower() in {(view.get("account_email") or "").lower(), (view.get("account_id") or "").lower(), (view.get("account_label") or "").lower()}
    if op == "opportunity":
        return _contains(view.get("opportunity") or "", value)
    if op == "rule":
        return value.lower() in {(view.get("rule_id") or "").lower(), (view.get("rule_name") or "").lower()}
    if op == "note":
        has = bool(view.get("has_note"))
        if value.lower() in {"yes", "true", "1"}:
            return has
        return _contains(view.get("note") or "", value)
    if op == "silenced":
        return (view.get("priority") == "silent") is (value.lower() in {"yes", "true", "1"})
    if op == "notified":
        return bool(view.get("notified")) is (value.lower() in {"yes", "true", "1"})
    if op == "group":
        return bool(view.get("in_group")) is (value.lower() in {"yes", "true", "1"})
    if op == "snoozed":
        return bool(view.get("snoozed")) is (value.lower() in {"yes", "true", "1"})
    if op in {"newer_than", "older_than"}:
        delta = _parse_rel(value)
        received = view.get("received_at")
        if not delta or not isinstance(received, datetime):
            return False
        received = _as_utc(received)
        if op == "newer_than":
            return received >= now - delta
        return received < now - delta
    if op in {"after", "before", "newer", "older"}:
        received = view.get("received_at")
        if not isinstance(received, datetime):
            return False
        received = _as_utc(received)
        try:
            point = datetime.fromisoformat(value)
            if point.tzinfo is None:
                point = point.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        if op in {"after", "newer"}:
            return received >= point
        return received <= point
    if op in {"larger", "smaller", "size"}:
        size = view.get("size_bytes") or 0
        try:
            limit = _parse_size(value)
        except ValueError:
            return False
        if op == "larger":
            return size > limit
        if op == "smaller":
            return size < limit
        return size == limit
    if op == "list":
        return _contains(view.get("list_id") or "", value)
    if op == "rfc822msgid":
        return _contains(view.get("message_id_header") or "", value)
    if op == "sort":
        return True
    return False


def _parse_size(value: str) -> int:
    raw = value.strip().lower()
    mult = 1
    if raw.endswith("k"):
        mult = 1024
        raw = raw[:-1]
    elif raw.endswith("m"):
        mult = 1024 * 1024
        raw = raw[:-1]
    return int(float(raw) * mult)
