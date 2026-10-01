from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Session, SQLModel, create_engine, select

from gtm_agent.config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, Enum):
    program_director = "program_director"
    coordinator = "coordinator"
    fellow = "fellow"


class DraftStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    sent = "sent"
    replied = "replied"


class Institution(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    normalized_name: str = Field(index=True, unique=True)
    program_url: Optional[str] = None
    domain: Optional[str] = Field(default=None, index=True)
    extra_email_domains: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    source: str = "seed"
    notes: Optional[str] = None
    scraped_at: Optional[datetime] = None
    extracted_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class Contact(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("email", name="uq_contact_email"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    institution_id: int = Field(foreign_key="institution.id", index=True)
    name: str
    role: Role
    email: str = Field(index=True)
    title: Optional[str] = None
    research_interests: Optional[str] = None
    source_url: str
    confidence: float = 0.5
    created_at: datetime = Field(default_factory=utcnow)


class EmailDraft(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    contact_id: int = Field(foreign_key="contact.id", index=True)
    subject: str
    body: str
    status: DraftStatus = Field(default=DraftStatus.pending, index=True)
    gmail_message_id: Optional[str] = None
    gmail_thread_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    reviewed_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    replied_at: Optional[datetime] = None


class CrawlLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    institution_id: int = Field(foreign_key="institution.id", index=True)
    url: str = Field(index=True, unique=True)
    status_code: Optional[int] = None
    depth: int = 0
    content_path: Optional[str] = None
    error: Optional[str] = None
    fetched_at: datetime = Field(default_factory=utcnow)


class Suppression(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    reason: str = "unsubscribe"
    created_at: datetime = Field(default_factory=utcnow)


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = get_settings().database_url
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url)
        SQLModel.metadata.create_all(_engine)
    return _engine


def get_session() -> Session:
    return Session(get_engine())


def is_suppressed(session: Session, email: str) -> bool:
    return session.exec(select(Suppression).where(Suppression.email == email.lower())).first() is not None
