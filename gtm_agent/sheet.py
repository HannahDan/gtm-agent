"""Local read-only page for the outreach lead sheet."""

import json
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from sqlmodel import Session, select

from gtm_agent.models import Contact, EmailDraft, Institution, Role, Suppression, get_engine, get_session

_ROLE_ORDER = {Role.program_director: 0, Role.coordinator: 1, Role.fellow: 2}

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Outreach lead sheet</title>
<style>
  :root {
    --bg: #f3efe6;
    --paper: #fbf9f4;
    --ink: #1e1a16;
    --muted: #6f675e;
    --line: #e3dcd0;
    --accent: #1d4a3c;
    --accent-soft: #e5efe9;
    --pill-none-bg: #eeeae3; --pill-none-fg: #5e574f;
    --pill-pending-bg: #f4ead2; --pill-pending-fg: #6d4e12;
    --pill-approved-bg: #e4eef8; --pill-approved-fg: #1d3f66;
    --pill-rejected-bg: #f3e6e3; --pill-rejected-fg: #6b3330;
    --pill-sent-bg: #e5efe9; --pill-sent-fg: #1d4a3c;
    --pill-replied-bg: #e7e6f4; --pill-replied-fg: #33315c;
    --pill-suppressed-bg: #f3e4e2; --pill-suppressed-fg: #7a2e2a;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #141210;
      --paper: #211e1b;
      --ink: #f4f0e8;
      --muted: #b3aa9f;
      --line: #3a342c;
      --accent: #9dceba;
      --accent-soft: #24362e;
      --pill-none-bg: #2c2824; --pill-none-fg: #c8bfb4;
      --pill-pending-bg: #3d321c; --pill-pending-fg: #f0d59a;
      --pill-approved-bg: #1c2c3d; --pill-approved-fg: #c5daf0;
      --pill-rejected-bg: #3a2422; --pill-rejected-fg: #f0c8c2;
      --pill-sent-bg: #1c3329; --pill-sent-fg: #c6e6d6;
      --pill-replied-bg: #2a2840; --pill-replied-fg: #d5d3f0;
      --pill-suppressed-bg: #3d2422; --pill-suppressed-fg: #f0c2bc;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font: 15px/1.45 "Segoe UI", system-ui, sans-serif;
  }
  main { max-width: 1180px; margin: 0 auto; padding: 28px 24px 64px; }
  header { margin-bottom: 20px; }
  .kicker {
    margin: 0 0 6px;
    color: var(--accent);
    font-size: 12px;
    font-weight: 650;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  h1 {
    margin: 0;
    font-family: Palatino, "Palatino Linotype", Georgia, serif;
    font-size: 34px;
    font-weight: 600;
    letter-spacing: -0.02em;
  }
  .lede { margin: 8px 0 0; color: var(--muted); max-width: 62ch; }
  .stats { display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0; }
  .stat {
    background: var(--paper);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 8px 12px;
    min-width: 92px;
  }
  .stat b { display: block; font-size: 18px; font-variant-numeric: tabular-nums; }
  .stat span { color: var(--muted); font-size: 12px; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
  input, select, button {
    font: inherit;
    color: var(--ink);
    background: var(--paper);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 8px 10px;
  }
  input[type="search"] { min-width: min(320px, 100%); }
  button { cursor: pointer; }
  button.name-btn {
    display: block;
    width: 100%;
    padding: 0;
    border: 0;
    background: transparent;
    font-weight: 650;
    text-align: left;
  }
  .count { color: var(--muted); font-size: 13px; margin-left: auto; }
  .layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; align-items: start; }
  .layout.open { grid-template-columns: minmax(0, 1fr) 340px; }
  .table-wrap, .detail, .gaps {
    background: var(--paper);
    border: 1px solid var(--line);
    border-radius: 10px;
  }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th {
    font-size: 11px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--muted);
    font-weight: 650;
  }
  tbody tr { cursor: pointer; }
  tbody tr:hover, tbody tr.selected { background: var(--accent-soft); }
  td.empty { cursor: default; color: var(--muted); padding: 28px 12px; }
  .name { font-weight: 650; }
  .sub { display: block; color: var(--muted); font-size: 12px; font-weight: 450; }
  a { color: var(--accent); }
  .email, .email a { white-space: nowrap; }
  .pill, .badge {
    display: inline-block;
    border-radius: 999px;
    padding: 2px 8px;
    font-size: 12px;
    font-weight: 650;
    white-space: nowrap;
  }
  .pill.none { background: var(--pill-none-bg); color: var(--pill-none-fg); }
  .pill.pending { background: var(--pill-pending-bg); color: var(--pill-pending-fg); }
  .pill.approved { background: var(--pill-approved-bg); color: var(--pill-approved-fg); }
  .pill.rejected { background: var(--pill-rejected-bg); color: var(--pill-rejected-fg); }
  .pill.sent { background: var(--pill-sent-bg); color: var(--pill-sent-fg); }
  .pill.replied { background: var(--pill-replied-bg); color: var(--pill-replied-fg); }
  .badge { margin-top: 4px; background: var(--pill-suppressed-bg); color: var(--pill-suppressed-fg); }
  td.status { width: 1%; white-space: nowrap; }
  .detail { position: sticky; top: 16px; max-height: calc(100vh - 32px); overflow: auto; padding: 16px; }
  .detail-head { display: flex; justify-content: space-between; gap: 12px; align-items: start; }
  .detail h2 { margin: 0; font-size: 22px; font-family: Palatino, Georgia, serif; }
  .meta { margin: 4px 0 12px; color: var(--muted); }
  dl { margin: 0 0 12px; display: grid; grid-template-columns: 96px 1fr; gap: 6px 10px; }
  dt { color: var(--muted); }
  dd { margin: 0; }
  .body {
    margin: 0;
    white-space: pre-wrap;
    font: inherit;
    background: var(--bg);
    border-radius: 8px;
    padding: 12px;
  }
  .gaps { margin-top: 28px; padding: 16px 16px 8px; }
  .gaps h2 { margin: 0 0 4px; font-size: 18px; font-family: Palatino, Georgia, serif; }
  .gaps p { margin: 0 0 8px; color: var(--muted); }
  .gaps table { min-width: 0; }
  .gaps tbody tr { cursor: default; }
  .gaps tbody tr:hover { background: transparent; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  @media (max-width: 960px) {
    .layout.open { grid-template-columns: 1fr; }
    .detail { position: static; max-height: none; }
    .count { margin-left: 0; }
  }
</style>
</head>
<body>
<main>
  <header>
    <p class="kicker">GTM agent</p>
    <h1>Outreach lead sheet</h1>
    <p class="lede">Contacts from the local pipeline, with the latest draft for each person. Refresh after you draft or send. This page only reads the database.</p>
  </header>
  <section class="stats" id="stats" aria-label="Pipeline counts"></section>
  <div class="toolbar">
    <input id="q" type="search" placeholder="Search name, institution, email" aria-label="Search leads">
    <select id="role" aria-label="Filter by role">
      <option value="">All roles</option>
      <option value="program_director">Program director</option>
      <option value="coordinator">Coordinator</option>
      <option value="fellow">Fellow</option>
    </select>
    <select id="status" aria-label="Filter by draft status">
      <option value="">All statuses</option>
      <option value="none">No draft</option>
      <option value="pending">Pending</option>
      <option value="approved">Approved</option>
      <option value="rejected">Rejected</option>
      <option value="sent">Sent</option>
      <option value="replied">Replied</option>
      <option value="suppressed">Suppressed</option>
    </select>
    <span class="count" id="count" aria-live="polite"></span>
  </div>
  <div class="layout" id="layout">
    <div class="table-wrap">
      <table aria-label="Outreach leads">
        <thead>
          <tr>
            <th>Name</th>
            <th>Institution</th>
            <th>Role</th>
            <th>Status</th>
            <th>Email</th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <aside class="detail" id="detail" hidden tabindex="-1" aria-labelledby="d-name">
      <div class="detail-head">
        <h2 id="d-name"></h2>
        <button type="button" id="close">Close</button>
      </div>
      <p class="meta" id="d-meta"></p>
      <dl id="d-facts"></dl>
      <pre class="body" id="d-body"></pre>
    </aside>
  </div>
  <section class="gaps" id="gaps" hidden>
    <h2>Institutions with no contacts yet</h2>
    <p>These are in the database, but scrape and extract have not produced a person to email.</p>
    <table aria-label="Institutions with no contacts">
      <thead>
        <tr><th>Institution</th><th>Location</th><th>Website</th><th>Scraped</th><th>Extracted</th></tr>
      </thead>
      <tbody id="gap-rows"></tbody>
    </table>
  </section>
</main>
<script id="sheet-data" type="application/json">__SHEET_DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("sheet-data").textContent);
const ROLE_LABEL = {
  program_director: "Program director",
  coordinator: "Coordinator",
  fellow: "Fellow",
};
const STATUS_LABEL = {
  none: "No draft",
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
  sent: "Sent",
  replied: "Replied",
};

const qEl = document.getElementById("q");
const roleEl = document.getElementById("role");
const statusEl = document.getElementById("status");
const rowsEl = document.getElementById("rows");
const countEl = document.getElementById("count");
const layoutEl = document.getElementById("layout");
const detailEl = document.getElementById("detail");
let selectedEmail = null;

function statusKey(lead) {
  return lead.draft_status || "none";
}

function locationOf(row) {
  return [row.city, row.state].filter(Boolean).join(", ");
}

function safeHttp(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.href;
  } catch (err) {
    return null;
  }
  return null;
}

function hostLabel(url) {
  try {
    return new URL(url).host.replace(/^www\\./, "");
  } catch (err) {
    return url;
  }
}

function link(url, label) {
  const href = safeHttp(url);
  if (!href) return document.createTextNode(label || url || "");
  const a = document.createElement("a");
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.textContent = label || href.replace(/^https?:\\/\\//, "");
  a.addEventListener("click", (event) => event.stopPropagation());
  return a;
}

function matches(lead) {
  const role = roleEl.value;
  const status = statusEl.value;
  const q = qEl.value.trim().toLowerCase();
  if (role && lead.role !== role) return false;
  if (status === "suppressed") {
    if (!lead.suppressed) return false;
  } else if (status && statusKey(lead) !== status) {
    return false;
  }
  if (!q) return true;
  const haystack = [lead.institution, lead.name, lead.email, lead.title, lead.subject, lead.research_interests]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(q);
}

function renderStats() {
  const leads = DATA.leads;
  const counts = [
    ["Contacts", leads.length],
    ["No draft", leads.filter((lead) => !lead.draft_status).length],
    ["Pending", leads.filter((lead) => lead.draft_status === "pending").length],
    ["Approved", leads.filter((lead) => lead.draft_status === "approved").length],
    ["Sent", leads.filter((lead) => lead.draft_status === "sent").length],
    ["Replied", leads.filter((lead) => lead.draft_status === "replied").length],
    ["Suppressed", leads.filter((lead) => lead.suppressed).length],
  ];
  const host = document.getElementById("stats");
  host.replaceChildren();
  for (const [label, value] of counts) {
    const card = document.createElement("div");
    card.className = "stat";
    const number = document.createElement("b");
    number.textContent = String(value);
    const caption = document.createElement("span");
    caption.textContent = label;
    card.append(number, caption);
    host.append(card);
  }
}

function renderTable() {
  const visible = DATA.leads.filter(matches);
  rowsEl.replaceChildren();
  countEl.textContent = "Showing " + visible.length + " of " + DATA.leads.length;
  if (!visible.length) {
    const tr = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = "empty";
    cell.textContent = DATA.leads.length
      ? "No leads match these filters."
      : "No contacts yet. Run discover, scrape, extract, and draft to fill this sheet.";
    tr.append(cell);
    rowsEl.append(tr);
    return;
  }
  for (const lead of visible) {
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    tr.dataset.email = lead.email;
    if (lead.email === selectedEmail) tr.className = "selected";
    const open = () => openDetail(lead);
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", (event) => {
      if (event.target !== tr) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });

    const name = document.createElement("td");
    const strong = document.createElement("button");
    strong.type = "button";
    strong.className = "name-btn";
    strong.textContent = lead.name;
    strong.addEventListener("click", (event) => {
      event.stopPropagation();
      open();
    });
    name.append(strong);
    if (lead.title) {
      const title = document.createElement("span");
      title.className = "sub";
      title.textContent = lead.title;
      name.append(title);
    }
    if (lead.suppressed) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "Suppressed";
      name.append(badge);
    }

    const email = document.createElement("td");
    email.className = "email";
    const mail = document.createElement("a");
    mail.href = "mailto:" + lead.email;
    mail.textContent = lead.email;
    mail.addEventListener("click", (event) => event.stopPropagation());
    email.append(mail);

    const status = document.createElement("td");
    status.className = "status";
    const key = statusKey(lead);
    const pill = document.createElement("span");
    pill.className = "pill " + key;
    pill.textContent = STATUS_LABEL[key] || key;
    status.append(pill);

    tr.append(
      name,
      textCell(lead.institution),
      textCell(ROLE_LABEL[lead.role] || lead.role),
      status,
      email,
    );
    rowsEl.append(tr);
  }
}

function textCell(value) {
  const cell = document.createElement("td");
  cell.textContent = value || "—";
  return cell;
}

function fact(list, label, valueNode) {
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.append(valueNode);
  list.append(dt, dd);
}

function openDetail(lead) {
  selectedEmail = lead.email;
  detailEl.hidden = false;
  layoutEl.classList.add("open");
  detailEl.scrollIntoView({ block: "nearest" });
  document.getElementById("d-name").textContent = lead.name;
  const place = locationOf(lead);
  document.getElementById("d-meta").textContent = [ROLE_LABEL[lead.role] || lead.role, lead.institution, place]
    .filter(Boolean)
    .join(" · ");
  const facts = document.getElementById("d-facts");
  facts.replaceChildren();
  const mail = document.createElement("a");
  mail.href = "mailto:" + lead.email;
  mail.textContent = lead.email;
  fact(facts, "Email", mail);
  fact(facts, "Confidence", document.createTextNode(Number(lead.confidence).toFixed(2)));
  fact(facts, "Status", document.createTextNode(STATUS_LABEL[statusKey(lead)] || statusKey(lead)));
  if (lead.suppressed) fact(facts, "List", document.createTextNode("Suppressed"));
  if (lead.sent_at) fact(facts, "Sent", document.createTextNode(new Date(lead.sent_at).toLocaleString()));
  if (lead.research_interests) fact(facts, "Interests", document.createTextNode(lead.research_interests));
  if (lead.notes) fact(facts, "Notes", document.createTextNode(lead.notes));
  fact(facts, "Source", link(lead.source_url, "Page where this email was found"));
  if (lead.program_url) fact(facts, "Program", link(lead.program_url, "Program website"));
  const body = document.getElementById("d-body");
  if (lead.subject || lead.body) {
    body.textContent = (lead.subject ? "Subject: " + lead.subject + "\\n\\n" : "") + (lead.body || "");
  } else {
    body.textContent = "No draft yet.";
  }
  renderTable();
  detailEl.focus();
}

function focusLead(email) {
  if (!email) return;
  for (const row of rowsEl.querySelectorAll("tr")) {
    if (row.dataset.email === email) {
      row.focus();
      return;
    }
  }
}

function closeDetail() {
  const email = selectedEmail;
  const active = document.activeElement;
  const returnFocus = Boolean(email) && (
    active === detailEl || detailEl.contains(active) || active === document.body
  );
  selectedEmail = null;
  detailEl.hidden = true;
  layoutEl.classList.remove("open");
  renderTable();
  if (returnFocus) focusLead(email);
}

function renderGaps() {
  const section = document.getElementById("gaps");
  const body = document.getElementById("gap-rows");
  if (!DATA.institutions.length) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  body.replaceChildren();
  for (const inst of DATA.institutions) {
    const tr = document.createElement("tr");
    const site = document.createElement("td");
    site.append(inst.program_url ? link(inst.program_url, hostLabel(inst.program_url)) : document.createTextNode("—"));
    tr.append(
      textCell(inst.name),
      textCell(locationOf(inst)),
      site,
      textCell(inst.scraped ? "Yes" : "Not yet"),
      textCell(inst.extracted ? "Yes" : "Not yet"),
    );
    body.append(tr);
  }
}

qEl.addEventListener("input", renderTable);
roleEl.addEventListener("change", renderTable);
statusEl.addEventListener("change", renderTable);
document.getElementById("close").addEventListener("click", closeDetail);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDetail();
});

renderStats();
renderTable();
renderGaps();
</script>
</body>
</html>
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def sheet_payload(session: Session) -> dict:
    """Contacts with their latest draft, plus institutions that have nobody to email yet."""
    institutions = list(session.exec(select(Institution).order_by(Institution.name)).all())
    pairs = session.exec(
        select(Contact, EmailDraft)
        .join(EmailDraft, EmailDraft.contact_id == Contact.id, isouter=True)
        .order_by(Contact.id, EmailDraft.id)
    ).all()
    suppressed = {row.email.lower() for row in session.exec(select(Suppression)).all()}
    by_id = {inst.id: inst for inst in institutions}

    latest: dict[int, tuple[Contact, EmailDraft | None]] = {}
    for contact, draft in pairs:
        previous = latest.get(contact.id)
        if previous is None:
            latest[contact.id] = (contact, draft)
            continue
        previous_draft = previous[1]
        if draft is not None and (previous_draft is None or (draft.id or 0) > (previous_draft.id or 0)):
            latest[contact.id] = (contact, draft)

    leads = []
    for contact, draft in latest.values():
        inst = by_id.get(contact.institution_id)
        if inst is None:
            continue
        leads.append(
            {
                "institution": inst.name,
                "city": inst.city,
                "state": inst.state,
                "program_url": inst.program_url,
                "notes": inst.notes,
                "name": contact.name,
                "role": contact.role.value,
                "title": contact.title,
                "email": contact.email,
                "confidence": contact.confidence,
                "source_url": contact.source_url,
                "research_interests": contact.research_interests,
                "draft_status": draft.status.value if draft else None,
                "subject": draft.subject if draft else None,
                "body": draft.body if draft else None,
                "sent_at": _iso(draft.sent_at) if draft else None,
                "suppressed": contact.email.lower() in suppressed,
            }
        )
    leads.sort(key=lambda row: (row["institution"].lower(), _ROLE_ORDER.get(Role(row["role"]), 9), row["name"].lower()))

    covered = {contact.institution_id for contact, _draft in latest.values()}
    waiting = []
    for inst in institutions:
        if inst.id in covered:
            continue
        waiting.append(
            {
                "name": inst.name,
                "city": inst.city,
                "state": inst.state,
                "program_url": inst.program_url,
                "scraped": inst.scraped_at is not None,
                "extracted": inst.extracted_at is not None,
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "leads": leads,
        "institutions": waiting,
    }


def render_page(payload: dict) -> str:
    data = (
        json.dumps(payload, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    return _PAGE.replace("__SHEET_DATA__", data)


class SheetHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path != "/":
            self.send_error(404)
            return
        try:
            with get_session() as session:
                body = render_page(sheet_payload(session)).encode("utf-8")
        except Exception as exc:
            message = f"Could not load the lead sheet: {exc}".encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), SheetHandler)


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    get_engine()
    server = make_server(host, port)
    if open_browser:
        webbrowser.open(f"http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
