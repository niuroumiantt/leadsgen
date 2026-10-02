"""Bounded public-web fetcher with DNS pinning, robots checks and source evidence."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import certifi
import dns.resolver
import tldextract
import urllib3
from bs4 import BeautifulSoup

from .policy import ROLE_NAMES, contact_order, email_role

AGENT = "GlocalLeadsgen/0.2 (+https://www.glocalstorage.com/)"
MAX_BYTES = 2_000_000
DOMAIN = tldextract.TLDExtract(suffix_list_urls=())
EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}"
)
CONTACT_WORDS = ("contact", "about", "company", "support", "sales", "联系我们", "关于我们")


class CrawlError(Exception):
    """Safe public status; no response bodies or credentials in errors."""


def normalize_url(url: str) -> str:
    if len(url) > 2048 or any(ord(c) < 32 for c in url):
        raise CrawlError("invalid_url")
    p = urlsplit(url if "://" in url else "https://" + url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise CrawlError("invalid_url")
    try:
        host = p.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if p.port not in {None, 80 if p.scheme == "http" else 443}:
            raise CrawlError("port_not_allowed")
    except (UnicodeError, ValueError) as exc:
        raise CrawlError("invalid_url") from exc
    return urlunsplit((p.scheme, host, p.path or "/", p.query, ""))


def registered_domain(url: str) -> str:
    host = urlsplit(normalize_url(url)).hostname or ""
    return DOMAIN(host).top_domain_under_public_suffix or host


def public_addresses(host: str, port: int) -> list[str]:
    try:
        addresses = sorted(
            {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        )
    except OSError as exc:
        raise CrawlError("dns_error") from exc
    if not addresses or any(
        not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast
        for ip in addresses
    ):
        raise CrawlError("non_public_destination")
    return addresses


@dataclass
class Page:
    url: str
    status: int
    text: str
    content_type: str


def fetch(url: str, domain: str, *, redirects: int = 3) -> Page:
    """Connect to the validated IP, retaining the original host for TLS and HTTP."""
    url = normalize_url(url)
    if registered_domain(url) != domain:
        raise CrawlError("cross_site_redirect")
    p = urlsplit(url)
    host = p.hostname or ""
    port = 443 if p.scheme == "https" else 80
    ip = public_addresses(host, port)[0]
    options = {"host": ip, "port": port, "timeout": urllib3.Timeout(connect=8, read=8)}
    pool = (
        urllib3.HTTPSConnectionPool(
            **options,
            server_hostname=host,
            assert_hostname=host,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(),
        )
        if p.scheme == "https"
        else urllib3.HTTPConnectionPool(**options)
    )
    response = None
    try:
        response = pool.urlopen(
            "GET",
            urlunsplit(("", "", p.path, p.query, "")),
            headers={"Host": host, "User-Agent": AGENT, "Accept-Encoding": "identity"},
            redirect=False,
            retries=False,
            preload_content=False,
            assert_same_host=False,
        )
        if response.status in {301, 302, 303, 307, 308}:
            if redirects <= 0:
                raise CrawlError("too_many_redirects")
            target = urljoin(url, response.headers.get("Location", ""))
            response.close()
            return fetch(target, domain, redirects=redirects - 1)
        if response.headers.get("Content-Encoding", "identity") not in {"identity", ""}:
            raise CrawlError("unsupported_encoding")
        raw = response.read(MAX_BYTES + 1, decode_content=False)
        if len(raw) > MAX_BYTES:
            raise CrawlError("page_too_large")
        content_type = response.headers.get("Content-Type", "").lower()
        if not any(t in content_type for t in ("html", "text/plain")):
            raise CrawlError("unsupported_content_type")
        return Page(url, response.status, raw.decode("utf-8", errors="replace"), content_type)
    except urllib3.exceptions.SSLError as exc:
        raise CrawlError("tls_error") from exc
    except urllib3.exceptions.TimeoutError as exc:
        raise CrawlError("request_timeout") from exc
    except (urllib3.exceptions.HTTPError, OSError) as exc:
        raise CrawlError("network_error") from exc
    finally:
        if response:
            response.close()
        pool.close()


def extract(page: Page) -> dict:
    soup = BeautifulSoup(page.text, "html.parser")
    name = legal = phone = ""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            raw = json.loads(script.string or "{}")
            nodes = raw if isinstance(raw, list) else [raw]
            for node in list(nodes):
                if isinstance(node, dict) and isinstance(node.get("@graph"), list):
                    nodes.extend(node["@graph"])
            for node in nodes:
                if not isinstance(node, dict) or node.get("@type") not in {
                    "Organization",
                    "Corporation",
                    "LocalBusiness",
                }:
                    continue
                name = str(node.get("name") or "")[:200]
                legal = str(node.get("legalName") or "")[:250]
                phone = str(node.get("telephone") or "")[:80]
        except (ValueError, TypeError):
            pass
    if not name:
        og = soup.find("meta", property="og:site_name")
        name = str(og.get("content", ""))[:200] if og else ""
    if not name and soup.title:
        name = soup.title.get_text(" ", strip=True)[:200]
    links = [
        (a.get_text(" ", strip=True), str(a.get("href", ""))) for a in soup.find_all("a", href=True)
    ]
    if not phone:
        phone = next(
            (unquote(href[4:])[:80] for _, href in links if href.lower().startswith("tel:")), ""
        )
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    normalized = re.sub(r"\s*\[at\]\s*|\s*\(at\)\s*", "@", text, flags=re.I)
    normalized = re.sub(r"\s*\[dot\]\s*|\s*\(dot\)\s*", ".", normalized, flags=re.I)
    mailtos = [
        unquote(href[7:].split("?")[0]) for _, href in links if href.lower().startswith("mailto:")
    ]
    emails = {
        e.casefold() for e in set(EMAIL.findall(normalized)) | set(EMAIL.findall(" ".join(mailtos)))
    }
    evidence = []
    for email in sorted(emails, key=contact_order):
        if len(email) > 254 or email_role(email) == "excluded":
            continue
        pos = normalized.casefold().find(email)
        excerpt = (
            normalized[max(0, pos - 90) : pos + len(email) + 100]
            if pos >= 0
            else f"公开 mailto 链接：{email}"
        )
        evidence.append(
            {
                "email": email,
                "role": email_role(email),
                "url": page.url,
                "excerpt": excerpt,
                "observedAt": datetime.now(UTC).isoformat(),
                "sha256": hashlib.sha256(page.text.encode()).hexdigest(),
            }
        )
    description = soup.find("meta", attrs={"name": "description"})
    summary = str(description.get("content", ""))[:400] if description else text[:240]
    return {
        "name": name,
        "legal": legal,
        "phone": phone,
        "contacts": evidence,
        "links": links,
        "summary": summary,
        "mentionsSupermicro": "supermicro" in text.lower(),
    }


def check_mail_domain(email: str) -> str:
    host = email.rsplit("@", 1)[1]
    try:
        records = dns.resolver.resolve(host, "MX", lifetime=5)
        return "null_mx" if any(str(r.exchange) == "." for r in records) else "mx_present"
    except dns.resolver.NoAnswer:
        try:
            public_addresses(host, 25)
            return "implicit_mx_unverified"
        except (OSError, CrawlError):
            return "no_mail_route"
    except dns.resolver.NXDOMAIN:
        return "no_mail_route"
    except (dns.exception.DNSException, OSError):
        return "check_unavailable"


def crawl(
    seed: dict, *, fetcher=fetch, mail_check=check_mail_domain, delay: float = 3, progress=None
) -> dict:
    def emit(phase, **values):
        if progress:
            progress({"phase": phase, **values})

    def read(url, domain, phase):
        emit(phase + "_requested", url=url)
        try:
            page = fetcher(url, domain)
        except CrawlError as exc:
            emit("request_failed", url=url, reason=str(exc))
            raise
        emit(phase + "_received", url=page.url, status=page.status)
        return page

    start = time.monotonic()
    url = normalize_url(seed["url"])
    domain = registered_domain(url)
    origin = urlunsplit((*urlsplit(url)[:2], "", "", ""))
    robots = read(origin + "/robots.txt", domain, "robots")
    if robots.status in {401, 403}:
        raise CrawlError("robots_denied")
    if robots.status == 429:
        raise CrawlError("robots_rate_limited")
    if robots.status >= 500:
        raise CrawlError("robots_unavailable")
    parser = RobotFileParser()
    parser.parse(robots.text.splitlines() if robots.status == 200 else [])
    canonical_origin = urlunsplit((*urlsplit(robots.url)[:2], "", "", ""))
    if canonical_origin != origin:
        url = canonical_origin + urlsplit(url).path
        origin = canonical_origin
    spacing = max(delay, parser.crawl_delay(AGENT) or 0)
    if spacing > 30:
        raise CrawlError("crawl_delay_exceeds_budget")
    emit("policy_checked", url=origin, delaySeconds=spacing)
    queue, visited, pages, contacts = [url], set(), [], {}
    disallowed = 0
    while queue and len(visited) < 8 and time.monotonic() - start < 90:
        target = normalize_url(queue.pop(0))
        if target in visited or registered_domain(target) != domain:
            continue
        if urlsplit(target).netloc != urlsplit(origin).netloc:
            # Only the validated origin is followed; no unvalidated subdomain robots policy.
            continue
        visited.add(target)
        if not parser.can_fetch(AGENT, target):
            disallowed += 1
            emit("page_skipped", url=target, reason="robots_disallowed")
            continue
        if spacing:
            time.sleep(spacing)
        page = read(target, domain, "page")
        if page.status in {401, 403, 429}:
            raise CrawlError("rate_limited" if page.status == 429 else "access_denied")
        if page.status != 200:
            continue
        # Redirects within the same registered domain still need origin-specific robots rules.
        if urlsplit(page.url).netloc != urlsplit(origin).netloc:
            raise CrawlError("origin_redirect_requires_new_seed")
        parsed = extract(page)
        pages.append(parsed)
        for c in parsed["contacts"]:
            contacts.setdefault(c["email"].lower(), c)
        emit("page_parsed", url=page.url, pages=len(pages), contacts=len(contacts))
        for label, href in parsed["links"]:
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            if any(word in (label + " " + href).lower() for word in CONTACT_WORDS):
                candidate = urljoin(page.url, href)
                try:
                    if registered_domain(candidate) == domain and candidate not in queue:
                        queue.append(candidate)
                except CrawlError:
                    pass
        if contacts and len(pages) >= 2:
            break
        if not contacts and len(pages) >= 4:
            break
    if not contacts:
        return {
            "status": "skipped",
            "reason": "robots_disallowed"
            if disallowed == len(visited)
            else "no_public_business_email",
            "pages": len(pages),
        }
    choices = sorted(contacts.values(), key=lambda c: contact_order(c["email"]))
    for contact in choices[:8]:
        contact["mailRoute"] = mail_check(contact["email"])
        contact["sameCompanyDomain"] = (
            registered_domain("https://" + contact["email"].rsplit("@", 1)[1]) == domain
        )
    acceptable = [c for c in choices[:8] if c["mailRoute"] not in {"null_mx", "no_mail_route"}]
    if not acceptable:
        return {"status": "skipped", "reason": "no_mail_route", "pages": len(pages)}
    acceptable.sort(key=lambda c: (not c["sameCompanyDomain"], contact_order(c["email"])))
    primary = acceptable[0]
    page = pages[0]
    tier = seed.get("tier", "1B")
    industry = seed.get("industry", "待核验")
    model = (
        "机房租赁 / 托管商"
        if industry == "机房租赁 / 托管"
        else ("贸易渠道" if tier.startswith("2") else "经营类型待核验")
    )
    account = {
        "name": seed.get("name") or page["name"] or domain,
        "legal": next((p["legal"] for p in pages if p["legal"]), ""),
        "phone": next((p["phone"] for p in pages if p["phone"]), ""),
        "domain": domain,
        "website": url,
        "region": seed["region"],
        "country": seed.get("country") or "待核验",
        "industry": industry,
        "tier": tier,
        "model": model,
        "email": primary["email"],
        "department": ROLE_NAMES[primary["role"]],
        "supermicro": "待核实",
        "products": [],
        "stage": "ready"
        if primary["mailRoute"] == "mx_present" and primary["sameCompanyDomain"]
        else "review",
        "mail": "none",
        "score": 0,
        "summary": page["summary"],
        "reason": "行业与分层来自采集计划；品牌使用情况和采购需求尚未核实。",
        "contacts": acceptable,
        "evidence": primary,
        "mailRoute": primary["mailRoute"],
        "observedAt": primary["observedAt"],
        "classificationVerified": False,
    }
    return {"status": "completed", "account": account, "pages": len(pages)}
