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


def crawl_fixture(pages):
    from urllib.parse import urlsplit

    seen = []

    def get(url, domain):
        path = urlsplit(url).path
        seen.append(path)
        return Page(url, 200, pages[path], "text/html")

    result = crawl(
        {"url": "https://example.com/", "region": "美国"},
        fetcher=get,
        mail_check=lambda email: "mx_present",
        delay=0,
    )
    return result, seen


def test_footer_contact_is_read_before_company_pages_consume_the_budget():
    result, seen = crawl_fixture(
        {
            "/robots.txt": "User-agent: *\nAllow: /",
            "/": '<a href="/about">About</a><a href="/company/team">Company team</a>'
            '<a href="/company/news">Company news</a><a href="/contact">Contact</a>',
            "/about": "Company overview",
            "/company/team": "Company team",
            "/company/news": "Company news",
            "/contact": "Public business contact: sales@example.com",
        }
    )
    assert result["status"] == "completed"
    assert result["account"]["email"] == "sales@example.com"
    assert seen == ["/robots.txt", "/", "/contact"]


def test_contact_discovered_on_about_page_precedes_queued_news():
    result, seen = crawl_fixture(
        {
            "/robots.txt": "User-agent: *\nAllow: /",
            "/": '<a href="/about">About</a><a href="/company/news">Company news</a>'
            '<a href="/company/team">Company team</a>',
            "/about": '<a href="/contact">Contact us</a>',
            "/contact": 'Email <a href="mailto:info@example.com">us</a>',
        }
    )
    assert result["account"]["email"] == "info@example.com"
    assert seen == ["/robots.txt", "/", "/about", "/contact"]


def test_supplier_entry_precedes_support_and_company_pages():
    result, seen = crawl_fixture(
        {
            "/robots.txt": "User-agent: *\nAllow: /",
            "/": '<a href="/company">Company</a><a href="/support">Support</a>'
            '<a href="/suppliers">Supplier relations</a>',
            "/suppliers": "purchasing@example.com",
        }
    )
    assert result["account"]["department"] == "采购 / 供应商入口"
    assert seen == ["/robots.txt", "/", "/suppliers"]


def test_prioritized_contact_still_respects_robots_and_no_email_page_limit():
    result, seen = crawl_fixture(
        {
            "/robots.txt": "User-agent: *\nDisallow: /contact",
            "/": '<a href="/contact">Contact</a><a href="/about">About</a>'
            '<a href="/company/team">Company team</a><a href="/company/news">Company news</a>'
            '<a href="/company/fifth">Company fifth</a>',
            "/about": "Overview",
            "/company/team": "Team",
            "/company/news": "News",
        }
    )
    assert result == {"status": "skipped", "reason": "no_public_business_email", "pages": 4}
    assert seen == ["/robots.txt", "/", "/about", "/company/team", "/company/news"]


def test_contact_priority_uses_page_intent_and_normalized_urls():
    from leadsgen.crawl import contact_priority

    assert contact_priority("News", "https://contact.example.com/news?contact=yes") is None
    assert contact_priority("Info", "https://example.com/%E8%81%94%E7%BB%9C") == 0
    result, seen = crawl_fixture(
        {
            "/robots.txt": "User-agent: *\nAllow: /",
            "/": '<a href="https://vendor.org/contact">Contact vendor</a>'
            '<a href="https://support.example.com/contact">Contact subdomain</a>'
            '<a href="javascript:void(0)">Contact</a>'
            '<a href="/contact#one">Contact</a><a href="/contact#two">Contact again</a>'
            '<a href="/about">About</a>',
            "/contact": "Contact form only; no published email",
            "/about": '<a href="/contact#three">Contact</a>',
        }
    )
    assert result["status"] == "skipped"
    assert seen == ["/robots.txt", "/", "/contact", "/about"]


def test_later_contact_label_promotes_an_existing_company_link():
    result, seen = crawl_fixture(
        {
            "/robots.txt": "User-agent: *\nAllow: /",
            "/": '<a href="/about">About</a><a href="/company/news">Company news</a>'
            '<a href="/company/team">Company team</a><a href="/directory">Company</a>'
            '<a href="/directory#contact">Contact us</a>',
            "/directory": "sales@example.com",
        }
    )
    assert result["account"]["email"] == "sales@example.com"
    assert seen == ["/robots.txt", "/", "/directory"]
