"""Bounded search adapters. Search snippets are suggestions, never verified company facts."""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx

from .crawl import CrawlError, normalize_url, registered_domain

COUNTRIES = {
    "US": ("美国", "United States", "美国"),
    "CA": ("加拿大", "Canada", "其他"),
    "GB": ("英国", "United Kingdom", "欧洲"),
    "DE": ("德国", "Germany", "欧洲"),
    "FR": ("法国", "France", "欧洲"),
    "NL": ("荷兰", "Netherlands", "欧洲"),
    "SG": ("新加坡", "Singapore", "东南亚"),
    "AU": ("澳大利亚", "Australia", "其他"),
    "JP": ("日本", "Japan", "其他"),
    "IN": ("印度", "India", "其他"),
}
NON_COMPANY_SITES = frozenset(
    {
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "wikipedia.org",
        "reddit.com",
        "pinterest.com",
        "medium.com",
        "github.com",
        "crunchbase.com",
        "zoominfo.com",
        "apollo.io",
        "alibaba.com",
        "made-in-china.com",
        "amazon.com",
        "ebay.com",
    }
)
ERRORS = {
    "not_configured": "搜索服务尚未接入",
    "auth_failed": "搜索服务凭据或权限无效，计划已暂停",
    "rate_limited": "搜索服务限流，本轮停止请求",
    "unavailable": "搜索服务暂不可用，本轮停止请求",
    "invalid_response": "搜索服务返回的数据格式不完整",
    "daily_budget": "今日搜索请求额度已用完，等待下个 UTC 日",
    "interrupted": "运行被重启中断；已发出的请求不自动重放",
    "paused": "管理员已暂停本轮搜索",
}


class SearchError(Exception):
    pass


@dataclass
class SearchServices:
    keys: dict[str, str] = field(default_factory=dict, repr=False)
    daily_limit: int = 20
    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    def __post_init__(self):
        if not 1 <= self.daily_limit <= 200:
            raise ValueError("搜索服务每日请求上限必须为 1–200")

    def configured(self, provider):
        return provider in {"brave", "tavily"} and bool(self.keys.get(provider, "").strip())

    def public_status(self):
        return [{"id": p, "configured": self.configured(p)} for p in ("brave", "tavily")]

    def search(self, provider, query, country):
        if not self.configured(provider):
            raise SearchError("not_configured")
        if provider == "brave":
            method, url = "GET", "https://api.search.brave.com/res/v1/web/search"
            kwargs = {
                "headers": {
                    "X-Subscription-Token": self.keys[provider],
                    "Accept": "application/json",
                },
                "params": {"q": query, "country": country, "count": 10},
            }
        else:
            method, url = "POST", "https://api.tavily.com/search"
            kwargs = {
                "headers": {"Authorization": "Bearer " + self.keys[provider]},
                "json": {
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 10,
                    "include_answer": False,
                    "include_raw_content": False,
                },
            }
        try:
            # Fixed hosts, no redirects. Preserve the runtime proxy/CA settings.
            with httpx.Client(
                timeout=20, follow_redirects=False, transport=self.transport
            ) as client:
                with client.stream(method, url, **kwargs) as response:
                    if response.status_code in {401, 403}:
                        raise SearchError("auth_failed")
                    if response.status_code == 429:
                        raise SearchError("rate_limited")
                    if response.status_code != 200:
                        raise SearchError("unavailable")
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 512_000:
                            raise SearchError("invalid_response")
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise SearchError("invalid_response")
            if provider == "brave":
                if "web" not in payload and "query" not in payload:
                    raise SearchError("invalid_response")
                rows = payload.get("web", {}).get("results", [])
            else:
                rows = payload["results"]
            if not isinstance(rows, list):
                raise SearchError("invalid_response")
            results = []
            for row in rows[:10]:
                if not isinstance(row, dict) or not isinstance(row.get("url"), str):
                    raise SearchError("invalid_response")
                results.append(
                    {
                        "url": row["url"][:4096],
                        "title": str(row.get("title", ""))[:300],
                        "snippet": str(
                            row.get("description" if provider == "brave" else "content", "")
                        )[:1200],
                    }
                )
            return results
        except (httpx.HTTPError, OSError) as exc:
            raise SearchError("unavailable") from exc
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SearchError("invalid_response") from exc


def site_from_result(raw_url):
    """Classify URLs without fetching them. The crawler still validates and pins public DNS."""
    try:
        url = normalize_url(raw_url)
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        try:
            ipaddress.ip_address(host)
            return "", "", "", "not_company_site"
        except ValueError:
            pass
        if "." not in host or host.endswith((".local", ".internal", ".localhost", ".onion")):
            return "", "", "", "not_public_site"
        domain = registered_domain(url)
        origin = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        # Query strings can contain tracking tokens. Keep the public page path as evidence.
        source = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        return source, origin, domain, "directory_or_social" if domain in NON_COMPANY_SITES else ""
    except (CrawlError, ValueError):
        return "", "", "", "invalid_url"
