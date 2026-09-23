import socket

import pytest
from leadsgen.crawl import CrawlError, Page, crawl, extract, normalize_url, public_addresses
from leadsgen.policy import CADENCE_DAYS, email_role


def test_sales_support_and_colocation_are_eligible():
    pages = {
        "/robots.txt": "User-agent: *\nAllow: /",
        "/": "<title>Example Colo</title><p>Colocation space rental</p>"
        '<a href="/contact">Contact</a>',
        "/contact": "<p>Customer service: support@example.com</p>",
    }

    def get(url, domain):
        from urllib.parse import urlsplit

        return Page(url, 200, pages[urlsplit(url).path], "text/html")

    result = crawl(
        {
            "url": "https://example.com/",
            "region": "美国",
            "country": "美国",
            "industry": "机房租赁 / 托管",
            "tier": "1D",
        },
        fetcher=get,
        mail_check=lambda email: "mx_present",
        delay=0,
    )
    a = result["account"]
    assert a["email"] == "support@example.com"
    assert a["stage"] == "ready"
    assert a["tier"] == "1D"
    assert a["model"] == "机房租赁 / 托管商"
    assert a["evidence"]["url"] == "https://example.com/contact"
    assert email_role("sales@example.com") == "sales"
    assert email_role("info@example.com") == "general"
    assert CADENCE_DAYS == (0, 7, 14, 28, 60, 90)


def test_no_public_email_has_no_profile():
    result = crawl(
        {"url": "https://example.com", "region": "美国"},
        fetcher=lambda u, d: Page(u, 200, "<title>No email</title>", "text/html"),
        mail_check=lambda e: "mx_present",
        delay=0,
    )
    assert result["status"] == "skipped"
    assert "account" not in result


def test_robots_blocks_page_fetch():
    seen = []

    def get(u, d):
        seen.append(u)
        return Page(u, 200, "User-agent: *\nDisallow: /", "text/plain")

    crawl({"url": "https://example.com", "region": "美国"}, fetcher=get, delay=0)
    assert seen == ["https://example.com/robots.txt"]


def test_email_not_in_visible_page_or_mailto_is_not_extracted():
    page = Page(
        "https://example.com",
        200,
        '<script>var email="secret@example.com"</script><p>sales@example.com</p>'
        '<a href="mailto:support@example.com">Help</a>',
        "text/html",
    )
    result = extract(page)
    assert {c["email"] for c in result["contacts"]} == {"sales@example.com", "support@example.com"}


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "https://user:password@example.com", "http://example.com:8080"]
)
def test_unsafe_url_rejected(url):
    with pytest.raises(CrawlError):
        normalize_url(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "224.0.0.1"])
def test_private_dns_and_multicast_blocked(monkeypatch, ip):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, (ip, 443))]
    )
    with pytest.raises(CrawlError, match="non_public"):
        public_addresses("example.com", 443)


def test_embedded_vendor_email_does_not_override_company_contact():
    text = "<p>support@vendor.org sales@example.com</p>"
    result = crawl(
        {"url": "https://example.com", "region": "美国"},
        fetcher=lambda u, d: Page(u, 200, text, "text/html"),
        mail_check=lambda e: "mx_present",
        delay=0,
    )
    assert result["account"]["email"] == "sales@example.com"
