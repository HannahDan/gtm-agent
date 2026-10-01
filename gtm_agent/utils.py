import re
from urllib.parse import urlparse

_MULTI_LABEL_SUFFIXES = ("ac.uk", "co.uk", "org.uk", "nhs.uk", "edu.au", "com.au", "ac.nz")

_NAME_NOISE = re.compile(
    r"\b(the|of|and|at|program|programs|fellowship|fellowships|hematology|oncology|"
    r"medical|hem|onc|division|department|dept)\b"
)


def normalize_name(name: str) -> str:
    s = name.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = _NAME_NOISE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def registrable_domain(url_or_host: str | None) -> str | None:
    if not url_or_host:
        return None
    host = urlparse(url_or_host).hostname if "//" in url_or_host else url_or_host
    if not host:
        return None
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def email_domain_matches(email: str, domain: str | None) -> bool:
    """True if the email's host is the institution domain or a subdomain of it."""
    if not domain:
        return False
    host = email.rsplit("@", 1)[-1].lower()
    return host == domain or host.endswith("." + domain)
