import csv
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlmodel import func, select

from gtm_agent.config import ROOT
from gtm_agent.models import Contact, DraftStatus, EmailDraft, Institution, Role, Suppression, get_session

app = typer.Typer(help="GTM email agent for recruiting oncology fellows into user studies.", no_args_is_help=True)
console = Console()


@app.command()
def discover(
    seed: Path = typer.Option(ROOT / "data" / "seed_institutions.csv", help="Seed CSV of institutions"),
    directory: List[Path] = typer.Option([], "--directory", "-d", help="Exported ACGME/FREIDA list (CSV or saved HTML); repeatable"),
):
    """Load institutions from the seed CSV and program directory exports."""
    from gtm_agent.discover import discover as run

    for path in directory:
        if not path.exists():
            raise typer.BadParameter(f"{path} does not exist")
    with get_session() as session:
        created, updated = run(session, seed, directory)
    console.print(f"Institutions: {created} added, {updated} updated")


@app.command()
def scrape(
    limit: Optional[int] = typer.Option(None, help="Max institutions to crawl this run"),
    force: bool = typer.Option(False, help="Re-crawl institutions already scraped"),
    browser: bool = typer.Option(True, help="Use Playwright fallback for JS-rendered pages"),
):
    """Crawl program websites for people/fellows/contact pages."""
    from gtm_agent.scrape import scrape as run

    with get_session() as session:
        pages = run(session, limit=limit, force=force, use_browser=browser)
    console.print(f"Saved {pages} page(s)")


@app.command()
def extract(
    limit: Optional[int] = typer.Option(None, help="Max institutions to process"),
    force: bool = typer.Option(False, help="Re-extract institutions already processed"),
):
    """Extract and verify contacts from crawled pages with the LLM."""
    from gtm_agent.extract import extract as run

    with get_session() as session:
        n = run(session, limit=limit, force=force)
    console.print(f"Added {n} contact(s)")


@app.command()
def draft(
    limit: Optional[int] = typer.Option(None, help="Max drafts to generate"),
    role: List[Role] = typer.Option([], help="Only draft for these roles; repeatable"),
    min_confidence: float = typer.Option(0.6, help="Skip contacts below this confidence"),
):
    """Generate personalized email drafts for contacts without one."""
    from gtm_agent.draft import draft as run

    with get_session() as session:
        n = run(session, limit=limit, roles=role or None, min_confidence=min_confidence)
    console.print(f"Generated {n} draft(s)")


@app.command()
def review():
    """Approve, edit, or reject pending drafts."""
    from gtm_agent.review import review as run

    with get_session() as session:
        counts = run(session)
    console.print(f"Approved {counts['approved']}, rejected {counts['rejected']}, skipped {counts['skipped']}")


@app.command()
def send(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print messages instead of sending"),
    limit: Optional[int] = typer.Option(None, help="Send at most this many (still bounded by DAILY_SEND_CAP)"),
):
    """Send approved drafts through Gmail."""
    from gtm_agent.send import send as run

    with get_session() as session:
        n = run(session, dry_run=dry_run, limit=limit)
    console.print(f"{'Would send' if dry_run else 'Sent'} {n} email(s)")


@app.command("check-replies")
def check_replies(lookback_days: int = typer.Option(30, help="Only check threads sent within this many days")):
    """Mark replied threads and suppress opt-out replies."""
    from gtm_agent.send import check_replies as run

    with get_session() as session:
        replied, opted_out = run(session, lookback_days=lookback_days)
    console.print(f"{replied} new reply(ies), {opted_out} opt-out(s)")


@app.command()
def suppress(email: str, reason: str = typer.Option("manual")):
    """Add an email to the suppression list and cancel its pending drafts."""
    from gtm_agent.send import suppress as run

    with get_session() as session:
        added = run(session, email, reason)
    console.print(f"{email} {'suppressed' if added else 'was already suppressed'}")


@app.command()
def status():
    """Show pipeline counts."""
    with get_session() as session:
        def count(model, *where):
            return session.exec(select(func.count()).select_from(model).where(*where)).one()

        table = Table(title="Pipeline status")
        table.add_column("Stage")
        table.add_column("Count", justify="right")
        table.add_row("Institutions", str(count(Institution)))
        table.add_row("  scraped", str(count(Institution, Institution.scraped_at.is_not(None))))
        table.add_row("  extracted", str(count(Institution, Institution.extracted_at.is_not(None))))
        for r in Role:
            table.add_row(f"Contacts: {r.value}", str(count(Contact, Contact.role == r)))
        for s in DraftStatus:
            table.add_row(f"Drafts: {s.value}", str(count(EmailDraft, EmailDraft.status == s)))
        table.add_row("Suppressed", str(count(Suppression)))
    console.print(table)


@app.command()
def export(output: Path = typer.Option(ROOT / "exports" / "contacts.csv", help="CSV output path")):
    """Export contacts with their latest draft status to CSV."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with get_session() as session:
        rows = session.exec(
            select(Contact, Institution, EmailDraft)
            .join(Institution, Contact.institution_id == Institution.id)
            .join(EmailDraft, EmailDraft.contact_id == Contact.id, isouter=True)
            .order_by(Institution.name, Contact.role)
        ).all()
        with open(output, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["institution", "name", "role", "title", "email", "confidence", "source_url", "draft_status", "subject", "sent_at"])
            for c, inst, d in rows:
                writer.writerow([
                    inst.name, c.name, c.role.value, c.title or "", c.email, c.confidence, c.source_url,
                    d.status.value if d else "", d.subject if d else "", d.sent_at.isoformat() if d and d.sent_at else "",
                ])
    console.print(f"Wrote {len(rows)} row(s) to {output}")


if __name__ == "__main__":
    app()
