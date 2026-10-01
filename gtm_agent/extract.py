"""Turn crawled page text into verified contacts.

The LLM proposes people and roles; an email is only kept if the regex pass found it verbatim
(after de-obfuscation) on the same page and it belongs to the institution's email domains.
"""

import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field
from rich.console import Console
from sqlmodel import Session, select

from gtm_agent import llm
from gtm_agent.models import Contact, CrawlLog, Institution, Role, utcnow
from gtm_agent.utils import email_domain_matches

console = Console()

MAX_CHARS_PER_CALL = 14000

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

_AT = r"\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|<\s*at\s*>|\s+at\s+(?=[A-Za-z0-9-]+\s*(?:\[|\(|\{|<)?\s*dot))\s*"
_DOT = r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|<\s*dot\s*>|\s+dot\s+)\s*"
_OBFUSCATED_RE = re.compile(
    rf"([A-Za-z0-9._%+-]+){_AT}([A-Za-z0-9-]+(?:(?:{_DOT}|\.)[A-Za-z0-9-]+)+)",
    re.I,
)


def deobfuscate(text: str) -> str:
    """Rewrite forms like `jdoe [at] med [dot] edu` into `jdoe@med.edu`."""

    def repl(m: re.Match) -> str:
        host = re.sub(_DOT, ".", m.group(2), flags=re.I)
        return f"{m.group(1)}@{host}"

    text = text.replace("&#64;", "@").replace("&commat;", "@")
    return _OBFUSCATED_RE.sub(repl, text)


def find_emails(text: str) -> set[str]:
    found = set()
    for raw in EMAIL_RE.findall(deobfuscate(text)):
        email = raw.strip(".'").lower()
        if not re.search(r"\.(png|jpe?g|gif|svg|webp)$", email):
            found.add(email)
    return found


class ExtractedContact(BaseModel):
    name: str = Field(description="Full name as written on the page; for a shared program mailbox use the program name")
    role: Literal["program_director", "coordinator", "fellow", "other"] = Field(
        description="program_director includes associate/assistant PDs; coordinator includes program managers/administrators and program mailboxes"
    )
    email: Optional[str] = Field(description="Email exactly as it appears on the page, or null if none is shown")
    title: Optional[str] = Field(description="Title or training year, e.g. 'PGY-5 Fellow' or 'Program Director'")
    research_interests: Optional[str] = Field(description="Short phrase of clinical/research interests if listed")


class ContactList(BaseModel):
    contacts: list[ExtractedContact]


SYSTEM_PROMPT = """You extract contacts from hematology/oncology fellowship program web pages.
Return only people (or program mailboxes) who are: the fellowship program director or associate program directors,
the program coordinator/manager, or current hematology/oncology fellows.
Never invent or guess an email address. Only return an email that appears in the page text.
Ignore patients, general hospital contacts, unrelated faculty, and residents in other specialties (mark those as 'other' or omit)."""


def _chunks(text: str, size: int = MAX_CHARS_PER_CALL) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def _allowed_domains(inst: Institution) -> list[str]:
    domains = [inst.domain] if inst.domain else []
    if inst.extra_email_domains:
        domains += [d.strip().lower() for d in re.split(r"[;,\s]+", inst.extra_email_domains) if d.strip()]
    return domains


def _match_email_by_name(name: str, emails: set[str]) -> Optional[str]:
    parts = [p for p in re.split(r"[^a-z]+", name.lower()) if len(p) > 1]
    if not parts:
        return None
    last, first = parts[-1], parts[0]
    candidates = [e for e in emails if last in e.split("@")[0]]
    if len(candidates) > 1:
        candidates = [e for e in candidates if first in e.split("@")[0] or e.split("@")[0].startswith(first[0])]
    return candidates[0] if len(candidates) == 1 else None


def extract_from_text(text: str, institution_name: str) -> ContactList:
    contacts: list[ExtractedContact] = []
    for chunk in _chunks(deobfuscate(text)):
        result = llm.parse(SYSTEM_PROMPT, f"Institution: {institution_name}\n\nPage text:\n{chunk}", ContactList)
        contacts.extend(result.contacts)
    return ContactList(contacts=contacts)


def verify_contacts(
    extracted: ContactList, page_emails: set[str], domains: list[str]
) -> list[tuple[ExtractedContact, str, float]]:
    """Returns (contact, verified_email, confidence) for contacts that pass all checks."""
    verified = []
    for c in extracted.contacts:
        if c.role == "other":
            continue
        email, confidence = None, 0.0
        if c.email and c.email.strip().lower() in page_emails:
            email, confidence = c.email.strip().lower(), 0.9
        else:
            email = _match_email_by_name(c.name, page_emails)
            confidence = 0.6 if email else 0.0
        if not email or not any(email_domain_matches(email, d) for d in domains):
            continue
        verified.append((c, email, confidence))
    return verified


def extract_institution(session: Session, inst: Institution) -> int:
    domains = _allowed_domains(inst)
    if not domains:
        console.print(f"  [yellow]no domain for {inst.name}; skipping[/yellow]")
        return 0
    logs = session.exec(
        select(CrawlLog).where(CrawlLog.institution_id == inst.id, CrawlLog.content_path.is_not(None))
    ).all()
    existing = set(session.exec(select(Contact.email)).all())
    added = 0
    for log in logs:
        path = Path(log.content_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        page_emails = find_emails(text)
        if not page_emails:
            continue
        extracted = extract_from_text(text, inst.name)
        for c, email, confidence in verify_contacts(extracted, page_emails, domains):
            if email in existing:
                continue
            session.add(
                Contact(
                    institution_id=inst.id,
                    name=c.name.strip(),
                    role=Role(c.role),
                    email=email,
                    title=c.title,
                    research_interests=c.research_interests,
                    source_url=log.url,
                    confidence=confidence,
                )
            )
            existing.add(email)
            added += 1
    inst.extracted_at = utcnow()
    session.add(inst)
    session.commit()
    return added


def extract(session: Session, limit: int | None = None, force: bool = False) -> int:
    query = select(Institution).where(Institution.scraped_at.is_not(None))
    if not force:
        query = query.where(Institution.extracted_at.is_(None))
    institutions = session.exec(query).all()
    if limit:
        institutions = institutions[:limit]
    total = 0
    for inst in institutions:
        console.print(f"[bold]Extracting[/bold] {inst.name}")
        n = extract_institution(session, inst)
        console.print(f"  {n} new contact(s)")
        total += n
    return total
