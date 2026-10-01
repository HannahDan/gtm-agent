# gtm-agent
GTM for User Research

A CLI agent that finds hematology/oncology fellowship programs, scrapes their public websites for program
director, coordinator, and fellow contacts, drafts personalized emails inviting fellows into a pilot user
study, and sends human-approved emails through Gmail.

```
discover -> scrape -> extract -> draft -> review -> send -> check-replies
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pip install -e '.[browser]' && playwright install chromium   # optional, for JS-rendered sites
cp .env.example .env    # add OPENAI_API_KEY and sender details
```

1. Edit `config/product.yaml` with your product pitch, study format, incentive, scheduling link, signature,
   and physical mailing address (required for CAN-SPAM).
2. Add institutions to `data/seed_institutions.csv` (`institution,program_url,email_domains,notes`).
   `email_domains` is optional and lists extra email domains, separated by semicolons, for institutions
   whose staff emails differ from the website domain (e.g. `hopkinsmedicine.org` site, `jhmi.edu` emails).
3. Optional: export the hem/onc program list from the
   [ACGME public program search](https://apps.acgme.org/ads/Public/Programs/Search)
   (specialty "Hematology and medical oncology") or save a FREIDA results page as HTML.
4. For sending: create a Google Cloud project, enable the Gmail API, create an OAuth client of type
   "Desktop app", and save it as `credentials.json` in the repo root. The first `send` opens a browser to authorize.

## Usage

```bash
gtm-agent discover -d ~/Downloads/acgme_hemonc.csv   # seed CSV is always included
gtm-agent scrape --limit 10
gtm-agent extract
gtm-agent draft --min-confidence 0.6                 # --role fellow to target fellows only
gtm-agent review                                     # approve / edit ($EDITOR) / reject / skip
gtm-agent send --dry-run                             # preview
gtm-agent send                                       # bounded by DAILY_SEND_CAP
gtm-agent check-replies                              # marks replies, suppresses "unsubscribe" replies
gtm-agent suppress someone@example.edu
gtm-agent status
gtm-agent export                                     # exports/contacts.csv
```

Every step is idempotent: re-running skips work already done (use `--force` on `scrape`/`extract` to redo).

## How contacts are verified

- The crawler only fetches pages allowed by `robots.txt`, waits between requests to the same host, and only
  follows same-domain links that look like people/fellows/leadership/contact pages (depth and page caps in `.env`).
- The LLM proposes people and roles, but an email is only saved if it appears on the same page (after
  de-obfuscating forms like `name [at] school [dot] edu`) and belongs to the institution's domain.
  Emails the LLM returns that are not on the page are dropped.
- Contacts store their `source_url` and a confidence score: 0.9 when the email was listed with the person,
  0.6 when it was matched to them by name.

## Compliance

- Nothing is sent without human approval in `review`.
- Each email ends with the sender's physical address and an opt-out line, and carries a `List-Unsubscribe` header.
- The suppression list is checked when drafting, reviewing, and sending.
- Only public, unauthenticated pages are scraped.

## Tests

```bash
pytest
```
