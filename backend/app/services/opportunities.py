"""Company timelines. Fuzzy match first; the model is only asked when a key exists."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.constants import FUZZY_COMPANY_THRESHOLD, OPPORTUNITY_RECENT_DAYS
from app.services.textutil import normalize_company, trigram_similarity


def fuzzy_company(company: str | None, opportunities: list) -> tuple[object | None, float]:
    target = normalize_company(company)
    if not target:
        return None, 0.0
    best = None
    best_score = 0.0
    for opp in opportunities:
        names = [opp.company, *(opp.aliases or [])]
        for name in names:
            norm = normalize_company(name)
            if not norm:
                continue
            score = 1.0 if norm == target else trigram_similarity(target, norm)
            if target in norm or norm in target:
                score = max(score, 0.9)
            if score > best_score:
                best_score = score
                best = opp
    if best is not None and best_score >= FUZZY_COMPANY_THRESHOLD:
        return best, best_score
    return None, best_score


def thread_opportunity(messages: list, thread_id: str, account_id: str):
    for message in messages:
        if message.thread_id == thread_id and message.account_id == account_id and message.opportunity_id:
            return message.opportunity_id
    return None


def link_domain_match(links: list[str], events: list, opportunities: list) -> str | None:
    domains = set()
    for link in links:
        host = link.split("//", 1)[-1].split("/", 1)[0].lower()
        if host:
            domains.add(host)
    if not domains:
        return None
    for event in events:
        details = event.details or {}
        for link in details.get("links") or []:
            url = link.get("url") if isinstance(link, dict) else str(link)
            host = url.split("//", 1)[-1].split("/", 1)[0].lower()
            if host and host in domains:
                return event.opportunity_id
    return None


def recent_open_assessment(opportunities: list, events: list, now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=OPPORTUNITY_RECENT_DAYS)
    assessed = {event.opportunity_id for event in events if event.event_type == "assessment"}
    candidates = [
        opp
        for opp in opportunities
        if opp.status == "active" and (opp.updated_at or opp.created_at) >= cutoff and opp.id not in assessed
    ]
    if len(candidates) == 1:
        return candidates[0].id
    return None


def decide_match(
    *,
    company: str | None,
    opportunities: list,
    thread_id: str | None,
    account_id: str | None,
    messages: list,
    links: list[str],
    events: list,
    llm_choice: dict | None,
    looks_like_assessment: bool,
) -> dict:
    """Return {opportunity_id, create_company, ask, alias, reason}.

    `llm_choice` is None in AI-off mode. Unsure mail asks the user instead of guessing.
    """
    fuzzy, score = fuzzy_company(company, opportunities)
    if fuzzy is not None:
        return {
            "opportunity_id": fuzzy.id,
            "create_company": None,
            "ask": False,
            "alias": company if company and normalize_company(company) != normalize_company(fuzzy.company) else None,
            "reason": f"Name matched {fuzzy.company} ({score:.2f})",
        }

    if llm_choice:
        choice = (llm_choice.get("choice") or "unsure").lower()
        if choice not in {"new", "unsure"}:
            known = {opp.id for opp in opportunities}
            if choice in known:
                return {
                    "opportunity_id": choice,
                    "create_company": None,
                    "ask": False,
                    "alias": llm_choice.get("alias") or company,
                    "reason": "Model chose an open timeline",
                }
        if choice == "new" and company:
            return {
                "opportunity_id": None,
                "create_company": company,
                "ask": False,
                "alias": None,
                "reason": "Model started a new timeline",
            }

    if thread_id and account_id:
        existing = thread_opportunity(messages, thread_id, account_id)
        if existing:
            return {
                "opportunity_id": existing,
                "create_company": None,
                "ask": False,
                "alias": company,
                "reason": "Same thread is already on a timeline",
            }

    linked = link_domain_match(links, events, opportunities)
    if linked:
        return {
            "opportunity_id": linked,
            "create_company": None,
            "ask": False,
            "alias": company,
            "reason": "Link domain matches an open timeline",
        }

    if looks_like_assessment:
        recent = recent_open_assessment(opportunities, events)
        if recent:
            return {
                "opportunity_id": recent,
                "create_company": None,
                "ask": False,
                "alias": company,
                "reason": "Only one recent timeline is still waiting on an assessment",
            }

    if company:
        return {
            "opportunity_id": None,
            "create_company": None,
            "ask": True,
            "alias": None,
            "reason": "Company is new and no timeline was close enough",
        }
    return {
        "opportunity_id": None,
        "create_company": None,
        "ask": False,
        "alias": None,
        "reason": "No company to attach",
    }
