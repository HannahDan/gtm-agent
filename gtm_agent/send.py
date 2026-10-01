"""Send approved drafts through the Gmail API and track replies / opt-outs."""

import base64
import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from rich.console import Console
from sqlmodel import Session, func, select

from gtm_agent.config import get_settings, load_product_config
from gtm_agent.models import Contact, DraftStatus, EmailDraft, Suppression, is_suppressed, utcnow

console = Console()

SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"]
OPT_OUT_RE = re.compile(r"\b(unsubscribe|remove me|opt[- ]?out|stop emailing|do not contact)\b", re.I)


def gmail_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    settings = get_settings()
    creds = None
    if settings.gmail_token_file.exists():
        creds = Credentials.from_authorized_user_file(str(settings.gmail_token_file), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not settings.gmail_credentials_file.exists():
                raise FileNotFoundError(
                    f"Gmail OAuth client file not found at {settings.gmail_credentials_file}. "
                    "Create a Desktop OAuth client in Google Cloud Console and download it there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(settings.gmail_credentials_file), SCOPES)
            creds = flow.run_local_server(port=0)
        settings.gmail_token_file.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def sender_identity() -> tuple[str, str]:
    settings = get_settings()
    product = load_product_config()
    return (settings.sender_name or product.sender.name, settings.sender_email or product.sender.email)


def build_message(to_name: str, to_email: str, subject: str, body: str, from_name: str, from_email: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = f"{to_name} <{to_email}>"
    msg["Subject"] = subject
    msg["List-Unsubscribe"] = f"<mailto:{from_email}?subject=unsubscribe>"
    msg.set_content(body)
    return msg


def sent_today(session: Session) -> int:
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    since = local_midnight.astimezone(timezone.utc)
    return session.exec(select(func.count()).select_from(EmailDraft).where(EmailDraft.sent_at >= since)).one()


def send(session: Session, dry_run: bool = False, limit: int | None = None) -> int:
    settings = get_settings()
    remaining = settings.daily_send_cap - sent_today(session)
    if limit is not None:
        remaining = min(remaining, limit)
    if remaining <= 0:
        console.print(f"[yellow]Daily cap of {settings.daily_send_cap} reached; nothing sent.[/yellow]")
        return 0

    rows = session.exec(
        select(EmailDraft, Contact)
        .join(Contact, EmailDraft.contact_id == Contact.id)
        .where(EmailDraft.status == DraftStatus.approved)
        .order_by(EmailDraft.reviewed_at)
    ).all()
    already_sent = set(
        session.exec(
            select(EmailDraft.contact_id).where(EmailDraft.status.in_([DraftStatus.sent, DraftStatus.replied]))
        ).all()
    )
    from_name, from_email = sender_identity()
    service = None if dry_run else gmail_service()

    count = 0
    for draft, contact in rows:
        if count >= remaining:
            break
        if contact.id in already_sent:
            continue
        if is_suppressed(session, contact.email):
            draft.status = DraftStatus.rejected
            session.add(draft)
            session.commit()
            console.print(f"[dim]Suppressed, skipping {contact.email}[/dim]")
            continue
        msg = build_message(contact.name, contact.email, draft.subject, draft.body, from_name, from_email)
        if dry_run:
            console.rule(f"[dry-run] draft #{draft.id}")
            console.print(msg.as_string())
            count += 1
            continue
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        draft.status = DraftStatus.sent
        draft.gmail_message_id = result.get("id")
        draft.gmail_thread_id = result.get("threadId")
        draft.sent_at = utcnow()
        session.add(draft)
        session.commit()
        already_sent.add(contact.id)
        console.print(f"Sent to {contact.email} (thread {draft.gmail_thread_id})")
        count += 1
    return count


def _header(message: dict, name: str) -> str:
    for h in message.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def check_replies(session: Session, lookback_days: int = 30) -> tuple[int, int]:
    """Mark threads with an inbound message as replied; suppress senders who ask to opt out."""
    service = gmail_service()
    _, from_email = sender_identity()
    cutoff = utcnow() - timedelta(days=lookback_days)
    rows = session.exec(
        select(EmailDraft, Contact)
        .join(Contact, EmailDraft.contact_id == Contact.id)
        .where(EmailDraft.status == DraftStatus.sent, EmailDraft.gmail_thread_id.is_not(None), EmailDraft.sent_at >= cutoff)
    ).all()
    replied = opted_out = 0
    for draft, contact in rows:
        thread = (
            service.users()
            .threads()
            .get(userId="me", id=draft.gmail_thread_id, format="metadata", metadataHeaders=["From"])
            .execute()
        )
        inbound = [m for m in thread.get("messages", []) if from_email.lower() not in _header(m, "From").lower()]
        if not inbound:
            continue
        draft.status = DraftStatus.replied
        draft.replied_at = utcnow()
        session.add(draft)
        replied += 1
        if any(OPT_OUT_RE.search(m.get("snippet", "")) for m in inbound) and not is_suppressed(session, contact.email):
            session.add(Suppression(email=contact.email.lower(), reason="reply opt-out"))
            opted_out += 1
        session.commit()
    return replied, opted_out


def suppress(session: Session, email: str, reason: str = "manual") -> bool:
    email = email.strip().lower()
    if is_suppressed(session, email):
        return False
    session.add(Suppression(email=email, reason=reason))
    for draft in session.exec(
        select(EmailDraft)
        .join(Contact, EmailDraft.contact_id == Contact.id)
        .where(Contact.email == email, EmailDraft.status.in_([DraftStatus.pending, DraftStatus.approved]))
    ).all():
        draft.status = DraftStatus.rejected
        session.add(draft)
    session.commit()
    return True
