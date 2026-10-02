"""Attempt evidence, bounded explicit retries and independent mail processing."""

import json
import sqlite3
import threading

import pytest
from leadsgen.app import collection_worker, worker
from leadsgen.crawl import CrawlError, Page, crawl
from leadsgen.store import Store
from leadsgen.workflow import VersionConflict
from test_store_api import account, seeded


def test_attempts_preserve_failure_and_delay_manual_retry(tmp_path):
    store = Store(tmp_path / "crawl.db")
    store.create_job("test", "美国", [{"url": "https://example.com"}], "admin")
    task = store.claim()
    store.observe(
        task,
        {
            "phase": "page_parsed",
            "url": "https://example.com",
            "pages": 2,
            "raw_body": "private",
            "token": "secret",
        },
    )
    store.finish(task, {"status": "failed", "reason": "network_error"})
    assert store.jobs()[0]["candidates"][0]["pages"] == 2
    history = store.candidate_history(task["id"])["attempts"]
    assert history[0]["observations"][0].keys() == {"at", "phase", "url", "pages"}
    store.queue_retry(task["id"], task["attempt_id"], "admin")
    assert store.claim() is None
    with pytest.raises(VersionConflict):
        store.queue_retry(task["id"], task["attempt_id"], "admin")
    for number in (2, 3):
        with store.connect() as c:
            c.execute("UPDATE crawl_retry SET next_at='2000-01-01'")
        task = store.claim()
        assert task["attempt_number"] == number
        store.finish(task, {"status": "failed", "reason": "network_error"})
        if number == 2:
            store.queue_retry(task["id"], task["attempt_id"], "admin")
    with pytest.raises(ValueError, match="三次"):
        store.queue_retry(task["id"], task["attempt_id"], "admin")
    assert len(store.candidate_history(task["id"])["attempts"]) == 3
    with pytest.raises(sqlite3.IntegrityError, match="append only"), store.connect() as c:
        c.execute("DELETE FROM crawl_observation")


@pytest.mark.parametrize(
    "reason",
    [
        "robots_denied",
        "robots_rate_limited",
        "access_denied",
        "tls_error",
        "page_too_large",
        "origin_redirect_requires_new_seed",
        "robots_unavailable_or_denied",
    ],
)
def test_policy_failures_cannot_be_retried_blindly(tmp_path, reason):
    store = Store(tmp_path / "crawl.db")
    store.create_job("test", "美国", [{"url": "https://example.com"}], "admin")
    task = store.claim()
    store.finish(task, {"status": "failed", "reason": reason})
    with pytest.raises(ValueError, match="核验"):
        store.queue_retry(task["id"], task["attempt_id"], "admin")
    assert store.claim() is None


def test_restart_ignores_late_old_completion_and_recrawl_preserves_business_state(tmp_path):
    store, original = seeded(tmp_path)
    a = store.accounts()[0]
    store.enqueue([a["id"]], "admin")
    store.create_job("recrawl", "美国", [{"url": "https://example.com"}], "admin")
    interrupted = store.claim()
    store.recover()
    resumed = store.claim()
    store.finish(interrupted, {"status": "completed", "account": {**account(), "name": "stale"}})
    assert store.accounts()[0]["name"] != "stale"
    store.finish(resumed, {"status": "completed", "account": {**account(), "name": "updated"}})
    current = store.accounts()[0]
    assert current["name"] == "updated" and current["stage"] == "queued"
    assert current["id"] == a["id"] and current["company"] == a["company"]
    assert [r["status"] for r in store.candidate_history(interrupted["id"])["attempts"]] == [
        "completed",
        "interrupted",
    ]
    store.finish(original, {"status": "failed", "reason": "late"})
    assert store.collection_status()["outcomes"] == {"created": 1, "updated": 1}


@pytest.mark.parametrize(
    "path,status,reason",
    [
        ("/robots.txt", 403, "robots_denied"),
        ("/robots.txt", 429, "robots_rate_limited"),
        ("/robots.txt", 503, "robots_unavailable"),
        ("/", 403, "access_denied"),
        ("/", 429, "rate_limited"),
    ],
)
def test_access_errors_are_not_mislabeled_missing_email(path, status, reason):
    observations = []

    def fetch(url, domain):
        code = status if url.endswith(path) else 200
        return Page(url, code, "", "text/html")

    with pytest.raises(CrawlError, match=reason):
        crawl(
            {"url": "https://example.com/", "region": "美国"},
            fetcher=fetch,
            delay=0,
            progress=observations.append,
        )
    assert observations[-1]["status"] == status
    assert "@" not in json.dumps(observations)


def test_mail_loop_runs_while_collection_is_blocked(tmp_path, monkeypatch):
    store = Store(tmp_path / "crawl.db")
    store.create_job("test", "美国", [{"url": "https://example.com"}], "admin")
    entered, release, synced, stop = (threading.Event() for _ in range(4))

    def slow_crawl(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {"status": "skipped", "reason": "no_public_business_email"}

    monkeypatch.setattr("leadsgen.app.crawl", slow_crawl)
    for name in (
        "sync_mail",
        "sync_confirmed_leads",
        "sync_followup_access",
        "sync_mail_handoffs",
        "send_assignment_notices",
        "deliver_one",
    ):
        monkeypatch.setattr("leadsgen.app." + name, lambda *args: synced.set())
    crawler = threading.Thread(target=collection_worker, args=(store, stop))
    mail = threading.Thread(target=worker, args=(store, stop, "", ""))
    crawler.start()
    try:
        assert entered.wait(2)
        mail.start()
        assert synced.wait(2), "mail integration must not wait for a website"
    finally:
        stop.set()
        release.set()
        crawler.join(5)
        if mail.ident:
            mail.join(5)
    assert not crawler.is_alive() and not mail.is_alive()
