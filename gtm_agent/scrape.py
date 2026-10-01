"""Polite crawler for fellowship program websites.

Starts at each institution's program URL and follows same-domain links that look like
people/fellows/contact pages, up to a configured depth and page cap. Cleaned page text is
saved under data/pages/<institution_id>/ and every fetch is recorded in CrawlLog.
"""

import hashlib
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urldefrag, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from sqlmodel import Session, select
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from gtm_agent.config import Settings, get_settings
from gtm_agent.models import CrawlLog, Institution, utcnow
from gtm_agent.utils import registrable_domain

console = Console()

LINK_PATTERN = re.compile(
    r"faculty|people|fellows?|current[-_ ]?fellows|our[-_ ]?fellows|contact|leadership|"
    r"directory|team|staff|program[-_ ]?director|meet[-_ ]?the|trainees?",
    re.I,
)
SKIP_EXTENSIONS = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|mp4|mp3|zip|docx?|xlsx?|pptx?|ics)$", re.I)
MIN_TEXT_CHARS = 300


def clean_html(html: str) -> str:
    """Visible text with mailto addresses preserved inline."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        tag.decompose()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            a.replace_with(f"{a.get_text(' ', strip=True)} <{addr}>")
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{2,}", "\n", text)


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url, _ = urldefrag(urljoin(base_url, href))
        if urlparse(url).scheme not in ("http", "https"):
            continue
        links.append((url, a.get_text(" ", strip=True)))
    return links


def is_candidate_link(url: str, anchor_text: str, domains: set[str]) -> bool:
    if SKIP_EXTENSIONS.search(urlparse(url).path):
        return False
    if domains and registrable_domain(url) not in domains:
        return False
    return bool(LINK_PATTERN.search(urlparse(url).path) or LINK_PATTERN.search(anchor_text))


@dataclass
class PoliteFetcher:
    settings: Settings
    client: httpx.Client = field(init=False)
    _robots: dict[str, RobotFileParser | None] = field(default_factory=dict)
    _last_fetch: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        self.client = httpx.Client(
            headers={"User-Agent": self.settings.user_agent, "Accept": "text/html,application/xhtml+xml"},
            follow_redirects=True,
            timeout=20.0,
        )

    def close(self):
        self.client.close()

    def _robots_for(self, url: str) -> RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            rp = RobotFileParser()
            try:
                resp = self.client.get(f"{origin}/robots.txt")
                if resp.status_code >= 400:
                    rp = None  # no robots.txt: everything allowed
                else:
                    rp.parse(resp.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[origin] = rp
        return self._robots[origin]

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return rp is None or rp.can_fetch(self.settings.user_agent, url)

    def _wait(self, url: str):
        host = urlparse(url).netloc
        rp = self._robots_for(url)
        delay = self.settings.crawl_delay_seconds
        if rp is not None:
            delay = max(delay, float(rp.crawl_delay(self.settings.user_agent) or 0))
        elapsed = time.monotonic() - self._last_fetch.get(host, 0.0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_fetch[host] = time.monotonic()

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, max=20),
        reraise=True,
    )
    def fetch(self, url: str) -> httpx.Response:
        self._wait(url)
        return self.client.get(url)


def render_with_playwright(url: str, user_agent: str) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=user_agent)
            page.goto(url, wait_until="networkidle", timeout=30000)
            html = page.content()
            browser.close()
            return html
    except Exception as exc:  # playwright raises many error types
        console.print(f"  [yellow]playwright failed for {url}: {exc}[/yellow]")
        return None


def _save_text(pages_dir: Path, institution_id: int, url: str, text: str) -> str:
    folder = pages_dir / str(institution_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.txt"
    path.write_text(f"URL: {url}\n\n{text}", encoding="utf-8")
    return str(path)


def crawl_institution(
    session: Session, fetcher: PoliteFetcher, inst: Institution, use_browser: bool = True, force: bool = False
) -> int:
    settings = fetcher.settings
    if force:
        for old in session.exec(select(CrawlLog).where(CrawlLog.institution_id == inst.id)).all():
            session.delete(old)
        session.commit()
    seen = set(session.exec(select(CrawlLog.url)).all())
    domains = {d for d in (inst.domain,) if d}
    queue: deque[tuple[str, int]] = deque([(inst.program_url, 0)])
    queued = {inst.program_url}
    saved = 0
    fetched = 0

    while queue and fetched < settings.max_pages_per_site:
        url, depth = queue.popleft()
        if url in seen:
            continue
        if not fetcher.allowed(url):
            console.print(f"  [dim]robots.txt disallows {url}[/dim]")
            continue

        log = CrawlLog(institution_id=inst.id, url=url, depth=depth)
        try:
            resp = fetcher.fetch(url)
            fetched += 1
            log.status_code = resp.status_code
            ctype = resp.headers.get("content-type", "")
            if resp.status_code >= 400 or "html" not in ctype:
                log.error = f"skipped: status={resp.status_code} type={ctype}"
            else:
                html = resp.text
                text = clean_html(html)
                if len(text) < MIN_TEXT_CHARS and use_browser:
                    rendered = render_with_playwright(url, settings.user_agent)
                    if rendered:
                        html, text = rendered, clean_html(rendered)
                log.content_path = _save_text(settings.pages_dir, inst.id, url, text)
                saved += 1
                if depth == 0 and (final_domain := registrable_domain(str(resp.url))):
                    domains.add(final_domain)
                if depth < settings.crawl_max_depth:
                    for link, anchor in extract_links(html, str(resp.url)):
                        if link not in queued and link not in seen and is_candidate_link(link, anchor, domains):
                            queued.add(link)
                            queue.append((link, depth + 1))
        except httpx.HTTPError as exc:
            log.error = f"{type(exc).__name__}: {exc}"

        session.add(log)
        session.commit()
        seen.add(url)

    inst.scraped_at = utcnow()
    session.add(inst)
    session.commit()
    return saved


def scrape(session: Session, limit: int | None = None, force: bool = False, use_browser: bool = True) -> int:
    settings = get_settings()
    query = select(Institution).where(Institution.program_url.is_not(None))
    if not force:
        query = query.where(Institution.scraped_at.is_(None))
    institutions = session.exec(query).all()[:limit] if limit else session.exec(query).all()

    fetcher = PoliteFetcher(settings)
    total = 0
    try:
        for inst in institutions:
            console.print(f"[bold]Crawling[/bold] {inst.name} ({inst.program_url})")
            pages = crawl_institution(session, fetcher, inst, use_browser=use_browser, force=force)
            console.print(f"  saved {pages} page(s)")
            total += pages
    finally:
        fetcher.close()
    return total
