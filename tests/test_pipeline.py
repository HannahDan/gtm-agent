import pytest
from sqlmodel import select

from gtm_agent import draft as draft_mod
from gtm_agent.models import Contact, DraftStatus, EmailDraft, Institution, Role, Suppression, utcnow
from gtm_agent.review import from_editable, to_editable
from gtm_agent.send import build_message, send, sent_today, suppress


@pytest.fixture
def seeded(session):
    inst = Institution(name="Example University", normalized_name="university example", domain="example.edu")
    session.add(inst)
    session.commit()
    pd = Contact(institution_id=inst.id, name="Jane Smith", role=Role.program_director, email="jsmith@example.edu", source_url="https://x", confidence=0.9)
    fellow = Contact(institution_id=inst.id, name="Alex Chen", role=Role.fellow, email="achen@example.edu", source_url="https://x", confidence=0.6, research_interests="lymphoma")
    session.add_all([pd, fellow])
    session.commit()
    return session, inst, pd, fellow


def test_draft_appends_signature_and_opt_out(seeded, monkeypatch):
    session, _, _, _ = seeded
    prompts = []

    def fake_parse(system, user, schema, temperature=0.2):
        prompts.append((system, user))
        return schema(subject="Quick question about your fellows", body="Dear Dr. Smith,\n\nHello.\n\nThanks,")

    monkeypatch.setattr(draft_mod.llm, "parse", fake_parse)
    assert draft_mod.draft(session) == 2
    assert draft_mod.draft(session) == 0  # idempotent
    drafts = session.exec(select(EmailDraft)).all()
    product = draft_mod.load_product_config()
    for d in drafts:
        assert product.opt_out_line in d.body
        assert product.sender.physical_address in d.body
    assert any("share this study opportunity" in s for s, _ in prompts)
    assert any("lymphoma" in u for _, u in prompts)


def test_editable_roundtrip():
    subject, body = from_editable(to_editable("Hi", "Line 1\nLine 2"))
    assert (subject, body) == ("Hi", "Line 1\nLine 2")
    with pytest.raises(ValueError):
        from_editable("no subject here")


def test_build_message_headers():
    msg = build_message("Jane", "j@example.edu", "Subj", "Body", "Me", "me@co.com")
    assert msg["To"] == "Jane <j@example.edu>"
    assert "unsubscribe" in msg["List-Unsubscribe"]


def test_send_dry_run_and_suppression(seeded):
    session, _, pd, fellow = seeded
    for c in (pd, fellow):
        session.add(EmailDraft(contact_id=c.id, subject="s", body="b", status=DraftStatus.approved, reviewed_at=utcnow()))
    session.commit()

    assert suppress(session, "ACHEN@example.edu") is True
    assert suppress(session, "achen@example.edu") is False
    fellow_draft = session.exec(select(EmailDraft).where(EmailDraft.contact_id == fellow.id)).one()
    assert fellow_draft.status == DraftStatus.rejected

    assert send(session, dry_run=True) == 1
    # dry run must not mark anything as sent
    assert sent_today(session) == 0
    assert session.exec(select(Suppression)).one().email == "achen@example.edu"


def test_daily_cap(seeded, monkeypatch):
    session, _, pd, _ = seeded
    session.add(EmailDraft(contact_id=pd.id, subject="s", body="b", status=DraftStatus.sent, sent_at=utcnow()))
    session.commit()
    assert sent_today(session) == 1
    from gtm_agent import send as send_mod

    monkeypatch.setattr(send_mod.get_settings(), "daily_send_cap", 1)
    assert send(session, dry_run=True) == 0
