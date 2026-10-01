"""Interactive human review of pending drafts."""

import os
import shlex
import subprocess
import tempfile

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from sqlmodel import Session, select

from gtm_agent.models import Contact, DraftStatus, EmailDraft, Institution, is_suppressed, utcnow

console = Console()


def to_editable(subject: str, body: str) -> str:
    return f"Subject: {subject}\n\n{body}"


def from_editable(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or not lines[0].lower().startswith("subject:"):
        raise ValueError("First line must start with 'Subject:'")
    subject = lines[0].split(":", 1)[1].strip()
    body = "\n".join(lines[1:]).strip("\n")
    if not subject or not body:
        raise ValueError("Subject and body must both be non-empty")
    return subject, body


def edit_in_editor(subject: str, body: str) -> tuple[str, str]:
    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as f:
        f.write(to_editable(subject, body))
        path = f.name
    try:
        subprocess.run([*shlex.split(editor), path], check=True)
        with open(path) as f:
            return from_editable(f.read())
    finally:
        os.unlink(path)


def _render(draft: EmailDraft, contact: Contact, inst: Institution, index: int, total: int):
    header = (
        f"[bold]{index}/{total}[/bold]  To: {contact.name} <{contact.email}>\n"
        f"Role: {contact.role.value}  |  {inst.name}  |  confidence {contact.confidence:.1f}\n"
        f"Source: {contact.source_url}"
    )
    console.print(Panel(header, title=f"Draft #{draft.id}", expand=False))
    console.print(f"[bold]Subject:[/bold] {draft.subject}\n")
    console.print(draft.body)
    console.print()


def review(session: Session) -> dict[str, int]:
    rows = session.exec(
        select(EmailDraft, Contact, Institution)
        .join(Contact, EmailDraft.contact_id == Contact.id)
        .join(Institution, Contact.institution_id == Institution.id)
        .where(EmailDraft.status == DraftStatus.pending)
        .order_by(EmailDraft.id)
    ).all()
    counts = {"approved": 0, "rejected": 0, "skipped": 0}
    if not rows:
        console.print("No pending drafts.")
        return counts

    for i, (draft, contact, inst) in enumerate(rows, start=1):
        if is_suppressed(session, contact.email):
            draft.status = DraftStatus.rejected
            draft.reviewed_at = utcnow()
            session.add(draft)
            session.commit()
            counts["rejected"] += 1
            continue
        while True:
            _render(draft, contact, inst, i, len(rows))
            choice = Prompt.ask("[a]pprove  [e]dit  [r]eject  [s]kip  [q]uit", choices=["a", "e", "r", "s", "q"], default="s")
            if choice == "e":
                try:
                    draft.subject, draft.body = edit_in_editor(draft.subject, draft.body)
                    session.add(draft)
                    session.commit()
                except (ValueError, subprocess.CalledProcessError) as exc:
                    console.print(f"[red]Edit discarded: {exc}[/red]")
                continue
            break
        if choice == "q":
            break
        if choice == "s":
            counts["skipped"] += 1
            continue
        draft.status = DraftStatus.approved if choice == "a" else DraftStatus.rejected
        draft.reviewed_at = utcnow()
        session.add(draft)
        session.commit()
        counts["approved" if choice == "a" else "rejected"] += 1
    return counts
