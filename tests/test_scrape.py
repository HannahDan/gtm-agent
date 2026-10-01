from gtm_agent.scrape import clean_html, extract_links, is_candidate_link


def test_clean_html_strips_scripts_and_keeps_mailto(fixtures):
    text = clean_html((fixtures / "fellows_page.html").read_text())
    assert "tracking" not in text
    assert "Email <jsmith@med.example.edu>" in text


def test_candidate_links(fixtures):
    html = (fixtures / "fellows_page.html").read_text()
    links = extract_links(html, "https://www.example.edu/education/hemonc/")
    candidates = [u for u, a in links if is_candidate_link(u, a, {"example.edu"})]
    assert "https://www.example.edu/education/hemonc/current-fellows" in candidates
    assert "https://www.example.edu/education/hemonc/leadership" in candidates
    assert not any(u.endswith(".pdf") for u in candidates)
    assert not any("othersite.org" in u for u in candidates)
    assert not any(u.endswith("/news") for u in candidates)
