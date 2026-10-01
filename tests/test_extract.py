from gtm_agent.extract import ContactList, ExtractedContact, deobfuscate, find_emails, verify_contacts
from gtm_agent.scrape import clean_html


def _contact(name, role, email=None, title=None):
    return ExtractedContact(name=name, role=role, email=email, title=title, research_interests=None)


def test_deobfuscate_variants():
    assert deobfuscate("rlee [at] example [dot] edu") == "rlee@example.edu"
    assert deobfuscate("mgarcia(at)example.edu") == "mgarcia@example.edu"
    assert deobfuscate("jdoe {at} med {dot} school {dot} edu") == "jdoe@med.school.edu"
    assert deobfuscate("jdoe at med dot edu") == "jdoe@med.edu"
    assert deobfuscate("Meet us at the conference") == "Meet us at the conference"


def test_find_emails_from_fixture(fixtures):
    text = clean_html((fixtures / "fellows_page.html").read_text())
    emails = find_emails(text)
    assert {"jsmith@med.example.edu", "rlee@example.edu", "mgarcia@example.edu", "achen@example.edu"} <= emails
    assert "noise@tracker.com" not in emails  # script content is stripped
    assert not any(e.endswith(".png") for e in emails)


def test_verify_contacts_filters_domain_hallucinations_and_roles(fixtures):
    text = clean_html((fixtures / "fellows_page.html").read_text())
    page_emails = find_emails(text)
    extracted = ContactList(
        contacts=[
            _contact("Jane Smith", "program_director", "jsmith@med.example.edu"),
            _contact("Robert Lee", "program_director", None),  # matched by name
            _contact("Maria Garcia", "coordinator", "mgarcia@example.edu"),
            _contact("Alex Chen", "fellow", "alex.chen@example.edu"),  # hallucinated, falls back to name match
            _contact("Priya Patel", "fellow", "priya.patel@gmail.com"),  # off-domain
            _contact("Switchboard", "other", "info@examplehospital.com"),
            _contact("Ghost Person", "fellow", "ghost@example.edu"),  # not on page
        ]
    )
    result = {c.name: (email, conf) for c, email, conf in verify_contacts(extracted, page_emails, ["example.edu"])}
    assert result["Jane Smith"] == ("jsmith@med.example.edu", 0.9)
    assert result["Robert Lee"] == ("rlee@example.edu", 0.6)
    assert result["Maria Garcia"][0] == "mgarcia@example.edu"
    assert result["Alex Chen"] == ("achen@example.edu", 0.6)
    assert "Priya Patel" not in result
    assert "Switchboard" not in result
    assert "Ghost Person" not in result


def test_contact_list_schema_is_strict_compatible():
    schema = ContactList.model_json_schema()
    item = schema["$defs"]["ExtractedContact"]
    assert set(item["required"]) == {"name", "role", "email", "title", "research_interests"}
