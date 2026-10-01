"""Build the institution list from a seed CSV and exported hem/onc program directories.

Directory input is a file you export or save yourself from the public ACGME program search
(https://apps.acgme.org/ads/Public/Programs/Search, specialty "Hematology and medical oncology")
or FREIDA. Both CSV exports and saved HTML pages containing a results table are supported.
"""

import csv
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from sqlmodel import Session, select

from gtm_agent.models import Institution
from gtm_agent.utils import normalize_name, normalize_url, registrable_domain

_COLUMN_ALIASES = {
    "name": ["institution", "sponsoring institution", "sponsor", "program name", "program", "name"],
    "url": ["program_url", "program url", "website", "url", "program website", "web address"],
    "city": ["city", "program city"],
    "state": ["state", "program state", "st"],
    "notes": ["notes", "note"],
    "email_domains": ["email_domains", "email domains"],
}

_MERGE_FIELDS = ("program_url", "city", "state", "notes", "email_domains")


@dataclass
class InstitutionRecord:
    name: str
    program_url: str | None = None
    city: str | None = None
    state: str | None = None
    notes: str | None = None
    email_domains: str | None = None
    source: str = "seed"


def _pick(row: dict[str, str], field: str) -> str | None:
    lowered = {k.strip().lower(): v for k, v in row.items() if k}
    for alias in _COLUMN_ALIASES[field]:
        value = lowered.get(alias)
        if value and value.strip():
            return value.strip()
    return None


def _rows_to_records(rows: list[dict[str, str]], source: str) -> list[InstitutionRecord]:
    records = []
    for row in rows:
        name = _pick(row, "name")
        if not name:
            continue
        records.append(
            InstitutionRecord(
                name=name,
                program_url=normalize_url(_pick(row, "url")),
                city=_pick(row, "city"),
                state=_pick(row, "state"),
                notes=_pick(row, "notes"),
                email_domains=_pick(row, "email_domains"),
                source=source,
            )
        )
    return records


def load_csv(path: Path, source: str) -> list[InstitutionRecord]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return _rows_to_records(list(csv.DictReader(f)), source)


def load_html_table(path: Path, source: str) -> list[InstitutionRecord]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    rows: list[dict[str, str]] = []
    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
        if not headers:
            continue
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            row = {h: c.get_text(" ", strip=True) for h, c in zip(headers, cells)}
            link = tr.find("a", href=lambda h: h and h.startswith("http"))
            if link and not any(k.lower() in _COLUMN_ALIASES["url"] for k in row):
                row["website"] = link["href"]
            rows.append(row)
    return _rows_to_records(rows, source)


def load_directory(path: Path) -> list[InstitutionRecord]:
    if path.suffix.lower() in {".html", ".htm"}:
        return load_html_table(path, source="directory")
    return load_csv(path, source="directory")


def dedupe(records: list[InstitutionRecord]) -> list[InstitutionRecord]:
    """Merge records sharing a normalized name or program domain; earlier records win, gaps get filled."""
    merged: list[InstitutionRecord] = []
    by_name: dict[str, InstitutionRecord] = {}
    by_domain: dict[str, InstitutionRecord] = {}
    for rec in records:
        key = normalize_name(rec.name)
        domain = registrable_domain(rec.program_url)
        existing = by_name.get(key) or (by_domain.get(domain) if domain else None)
        if existing:
            for attr in _MERGE_FIELDS:
                if not getattr(existing, attr) and getattr(rec, attr):
                    setattr(existing, attr, getattr(rec, attr))
            if not domain:
                domain = registrable_domain(existing.program_url)
        else:
            existing = rec
            merged.append(rec)
        by_name[key] = existing
        if domain:
            by_domain[domain] = existing
    return merged


def upsert_institutions(session: Session, records: list[InstitutionRecord]) -> tuple[int, int]:
    created = updated = 0
    existing = session.exec(select(Institution)).all()
    by_name = {i.normalized_name: i for i in existing}
    by_domain = {i.domain: i for i in existing if i.domain}
    for rec in records:
        key = normalize_name(rec.name)
        domain = registrable_domain(rec.program_url)
        inst = by_name.get(key) or (by_domain.get(domain) if domain else None)
        if inst:
            changed = False
            for attr in _MERGE_FIELDS:
                model_attr = "extra_email_domains" if attr == "email_domains" else attr
                value = getattr(rec, attr)
                if value and not getattr(inst, model_attr):
                    setattr(inst, model_attr, value)
                    changed = True
            if domain and not inst.domain:
                inst.domain = domain
                changed = True
            if changed:
                session.add(inst)
                updated += 1
            continue
        inst = Institution(
            name=rec.name,
            normalized_name=key,
            program_url=rec.program_url,
            domain=domain,
            city=rec.city,
            state=rec.state,
            notes=rec.notes,
            extra_email_domains=rec.email_domains,
            source=rec.source,
        )
        session.add(inst)
        by_name[key] = inst
        if domain:
            by_domain[domain] = inst
        created += 1
    session.commit()
    return created, updated


def discover(session: Session, seed: Path | None, directories: list[Path]) -> tuple[int, int]:
    records: list[InstitutionRecord] = []
    if seed and seed.exists():
        records += load_csv(seed, source="seed")
    for path in directories:
        records += load_directory(path)
    return upsert_institutions(session, dedupe(records))
