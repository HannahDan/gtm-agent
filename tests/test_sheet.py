import threading
import urllib.request

from gtm_agent.models import Contact, DraftStatus, EmailDraft, Institution, Role, Suppression
from gtm_agent.sheet import make_server, render_page, serve, sheet_payload


def _seed(session):
    alpha = Institution(
        name="Alpha Cancer Center",
        normalized_name="alpha cancer center",
        city="Boston",
        state="MA",
        program_url="https://alpha.example.edu/fellowship",
    )
    beta = Institution(name="Beta Hospital", normalized_name="beta hospital", city="Austin", state="TX")
    session.add_all([alpha, beta])
    session.commit()
    jane = Contact(
        institution_id=alpha.id,
        name="Jane Smith",
        role=Role.program_director,
        email="jsmith@alpha.example.edu",
        title="Program Director",
        source_url="https://alpha.example.edu/people",
        confidence=0.9,
    )
    session.add(jane)
    session.commit()
    session.add(EmailDraft(contact_id=jane.id, subject="Older", body="First", status=DraftStatus.pending))
    session.add(EmailDraft(contact_id=jane.id, subject="Newer", body="Second", status=DraftStatus.approved))
    bob = Contact(
        institution_id=alpha.id,
        name="<script>alert(1)</script>",
        role=Role.fellow,
        email="bob@alpha.example.edu",
        source_url="https://alpha.example.edu/fellows",
        confidence=0.6,
    )
    session.add(bob)
    session.commit()
    session.add(Suppression(email="bob@alpha.example.edu", reason="unsubscribe"))
    session.commit()
    return jane


def test_payload_uses_latest_draft_and_lists_institutions_without_contacts(session):
    _seed(session)
    payload = sheet_payload(session)

    by_email = {row["email"]: row for row in payload["leads"]}
    assert list(by_email) == ["jsmith@alpha.example.edu", "bob@alpha.example.edu"]
    assert by_email["jsmith@alpha.example.edu"]["draft_status"] == "approved"
    assert by_email["jsmith@alpha.example.edu"]["subject"] == "Newer"
    assert by_email["jsmith@alpha.example.edu"]["body"] == "Second"
    assert by_email["bob@alpha.example.edu"]["draft_status"] is None
    assert by_email["bob@alpha.example.edu"]["suppressed"] is True
    assert payload["institutions"] == [
        {
            "name": "Beta Hospital",
            "city": "Austin",
            "state": "TX",
            "program_url": None,
            "scraped": False,
            "extracted": False,
        }
    ]


def test_render_page_escapes_contact_markup(session):
    _seed(session)
    html = render_page(sheet_payload(session))
    assert "<script>alert(1)</script>" not in html
    assert "\\u003cscript\\u003ealert(1)\\u003c/script\\u003e" in html
    assert "jsmith@alpha.example.edu" in html


def test_server_serves_the_sheet(session):
    _seed(session)
    server = make_server("127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as response:
            html = response.read().decode()
            assert response.status == 200
            assert "Outreach lead sheet" in html
            assert "jsmith@alpha.example.edu" in html
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/favicon.ico") as response:
            assert response.status == 204
    finally:
        server.shutdown()
        server.server_close()


def test_serve_initializes_database_before_accepting(monkeypatch):
    order = []
    monkeypatch.setattr("gtm_agent.sheet.get_engine", lambda: order.append("init"))

    class _Server:
        def serve_forever(self):
            order.append("accept")
            raise KeyboardInterrupt

        def server_close(self):
            order.append("close")

    monkeypatch.setattr("gtm_agent.sheet.make_server", lambda host, port: _Server())
    serve()
    assert order == ["init", "accept", "close"]
