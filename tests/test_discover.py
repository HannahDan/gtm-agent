from sqlmodel import select

from gtm_agent.discover import InstitutionRecord, dedupe, discover, load_csv, load_html_table
from gtm_agent.models import Institution
from gtm_agent.utils import email_domain_matches, normalize_name, registrable_domain


def test_normalize_and_domain():
    assert normalize_name("The University of Example - Hematology & Oncology Fellowship") == "university example"
    assert registrable_domain("https://www.med.example.edu/hemonc") == "example.edu"
    assert registrable_domain("https://www.ox.ac.uk/x") == "ox.ac.uk"
    assert email_domain_matches("a@med.example.edu", "example.edu")
    assert not email_domain_matches("a@notexample.edu", "example.edu")


def test_load_directory_formats(fixtures):
    csv_records = load_csv(fixtures / "acgme_export.csv", source="directory")
    assert [r.name for r in csv_records][:2] == ["Example University", "Memorial Sloan Kettering Cancer Center"]
    assert csv_records[0].state == "IL"
    html_records = load_html_table(fixtures / "freida_saved.html", source="directory")
    assert html_records[0].name == "Northwind University"
    assert html_records[0].program_url == "https://hemonc.northwind.edu/fellowship"


def test_dedupe_by_name_and_domain():
    records = [
        InstitutionRecord(name="Example University", program_url=None, city="Springfield"),
        InstitutionRecord(name="Example University Hem/Onc Program", program_url="https://www.example.edu/hemonc"),
        InstitutionRecord(name="EU Cancer Center", program_url="https://cancer.example.edu"),
        InstitutionRecord(name="Other Place", program_url="https://other.org"),
    ]
    merged = dedupe(records)
    assert len(merged) == 2
    assert merged[0].program_url == "https://www.example.edu/hemonc"
    assert merged[0].city == "Springfield"


def test_discover_is_idempotent(session, fixtures):
    seed = fixtures.parent.parent / "data" / "seed_institutions.csv"
    created, _ = discover(session, seed, [fixtures / "acgme_export.csv", fixtures / "freida_saved.html"])
    total = len(session.exec(select(Institution)).all())
    assert created == total
    # MSKCC appears in both seed and ACGME export
    assert len([i for i in session.exec(select(Institution)).all() if "sloan" in i.normalized_name]) == 1
    created_again, _ = discover(session, seed, [fixtures / "acgme_export.csv"])
    assert created_again == 0
    dfci = session.exec(select(Institution).where(Institution.name == "Dana-Farber Cancer Institute")).one()
    assert dfci.extra_email_domains == "dfci.harvard.edu;partners.org"
