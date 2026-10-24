"""Tunable product limits. Raise the folder depth here and nowhere else."""

MAX_FOLDER_DEPTH = 10
MIN_GROUP_SIZE = 3
GROUP_WINDOW_DAYS = 30
SUBJECT_SIMILARITY = 0.6
GROUP_PROMPT_MAX_DISMISSALS = 3
FINGERPRINT_SUGGEST_AT = 5
BODY_TRUNCATE = 1500
RULE_PREVIEW_LIMIT = 500
FUZZY_COMPANY_THRESHOLD = 0.86
OPPORTUNITY_RECENT_DAYS = 14
DEFAULT_TOKEN_CAP = 20_000
DEFAULT_RESEND_MINUTES = 5
DEFAULT_RESEND_LIMIT = 3
LLM_CONFIDENCE_FLOOR = 0.5

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"

# Categories the extractor is allowed to emit, plus any the user defines.
BUILTIN_CATEGORIES: list[tuple[str, str]] = [
    ("placement_announcement", "A campus or company drive announcement, registration opening, or eligibility mail."),
    ("registration", "A request or confirmation to register for a drive, test, or event."),
    ("assessment_link", "A test, OA, or assessment link such as HirePro, HackerRank, or Codility."),
    ("interview", "Interview scheduling, a panel, or a call slot."),
    ("result", "A selection, rejection, shortlist, or offer result."),
    ("recruiter", "A person writing about a role, not an automated platform."),
    ("action_needed", "The recipient has to do something, and it is not one of the categories above."),
    ("fyi", "Useful context that needs nothing from the recipient."),
    ("other", "Anything that does not fit, including mail the model is unsure about."),
]

GMAIL_MAILBOXES = {
    "inbox",
    "spam",
    "trash",
    "anywhere",
    "archive",
    "drafts",
    "sent",
    "snoozed",
    "unread",
    "read",
    "starred",
    "important",
}
GMAIL_CATEGORIES = {"primary", "social", "promotions", "updates", "forums", "reservations", "purchases"}

GMAIL_OPERATORS = {
    "from",
    "to",
    "cc",
    "bcc",
    "subject",
    "label",
    "in",
    "is",
    "has",
    "filename",
    "category",
    "list",
    "deliveredto",
    "rfc822msgid",
    "size",
    "larger",
    "smaller",
    "after",
    "before",
    "older",
    "newer",
    "older_than",
    "newer_than",
}

LOCAL_OPERATORS = {
    "folder",
    "priority",
    "tag",
    "rule",
    "opportunity",
    "account",
    "note",
    "silenced",
    "notified",
    "group",
    "snoozed",
    "ia",
}
