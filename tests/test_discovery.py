"""Discovery correctness under concurrency, restart, budget limits and untrusted search data."""

import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from leadsgen.app import create_app
from leadsgen.discovery import discovery_worker
from leadsgen.search import SearchError, SearchServices, site_from_result
from leadsgen.store import Store
from leadsgen.workflow import VersionConflict


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "discovery.db")


def config(**changes):
    return {
        "name": "美国 GPU 云",
        "country": "US",
        "industry": "GPU 云 / AI 算力",
        "product": "GPU 服务器",
        "objective": "寻找 GPU 云硬件采购机会",
        "provider": "brave",
        "queries": ["GPU cloud provider", "server infrastructure company"],
        "interval_hours": 24,
        "tier": "1B",
        **changes,
    }


def services(**kwargs):
    return SearchServices(
        keys={"brave": "synthetic-search-key", "tavily": "synthetic-tavily-key"}, **kwargs
    )


def queued(store, **changes):
    plan = store.save_plan(config(**changes), "admin")
    result = store.queue_search(plan["id"], str(uuid.uuid4()), 0, "admin", services())
    return plan, result["id"]


def row(url="https://example.org/contact", **changes):
    return {"url": url, "title": "New Company", "snippet": "Public hardware services", **changes}


def finish_one(store, run_id, rows=None):
    q = store.begin_query(run_id, 20)
    assert q
    store.finish_query(q["id"], [row()] if rows is None else rows)
    return q


def test_plan_defaults_paused_and_missing_service_never_reserves_request(store):
    plan = store.save_plan(config(), "admin")
    assert plan["enabled"] == 0 and plan["next_run_at"] is None
    assert store.claim_search() is None
    with pytest.raises(ValueError, match="尚未接入"):
        store.queue_search(plan["id"], str(uuid.uuid4()), 0, "admin", SearchServices())
    with pytest.raises(ValueError, match="接入"):
        store.toggle_plan(plan["id"], True, 0, "admin", SearchServices())
    assert store.discovery_state(services())["used_today"] == 0


def test_manual_submission_is_idempotent_and_concurrent_claims_are_unique(store):
    plan = store.save_plan(config(), "admin")
    request_id = str(uuid.uuid4())
    first = store.queue_search(plan["id"], request_id, 0, "admin", services())
    assert store.queue_search(plan["id"], request_id, 0, "admin", services()) == first
    assert store.queue_search(plan["id"], str(uuid.uuid4()), 0, "admin", services()) == first
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda _: store.claim_search(), range(2)))
    assert sum(r is not None for r in claims) == 1
    finish_one(store, first["id"])
    finish_one(store, first["id"], [])
    store.finish_search(first["id"])
    assert store.queue_search(plan["id"], request_id, 0, "admin", services()) == first
    assert store.claim_search() is None
    assert len(store.search_runs(plan["id"])["items"]) == 1


def test_schedule_catches_up_once_and_snapshot_does_not_follow_plan_edits(store):
    plan = store.save_plan(config(), "admin")
    store.toggle_plan(plan["id"], True, 0, "admin", services())
    with store.connect() as c:
        c.execute("UPDATE discovery_plan SET next_run_at='2000-01-01' WHERE id=?", (plan["id"],))
    run = store.claim_search()
    assert run["kind"] == "schedule"
    assert store.claim_search() is None
    current = store.discovery_state(services())["plans"][0]
    assert datetime.fromisoformat(current["next_run_at"]) > datetime.now(UTC)
    store.save_plan(config(queries=["memory distributor"], country="DE"), "admin", plan["id"], 1)
    q = finish_one(store, run["id"])
    assert q["query"] == "GPU cloud provider United States"
    store.finish_search(run["id"], "auth_failed")
    assert store.discovery_state(services())["plans"][0]["enabled"] == 1
    assert store.search_run(run["id"])["config"]["country"] == "US"


def test_global_budget_atomic_across_plans_and_reset_next_day(store):
    _, one = queued(store)
    _, two = queued(store, name="other")
    store.claim_search()
    store.claim_search()

    def begin(run_id):
        try:
            return store.begin_query(run_id, 1)
        except SearchError as e:
            return str(e)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(begin, [one, two]))
    assert len([r for r in results if isinstance(r, dict)]) == 1
    assert "daily_budget" in results
    assert store.discovery_state(services())["used_today"] == 1
    with store.connect() as c:
        c.execute(
            "UPDATE discovery_query SET requested_at=? WHERE requested_at IS NOT NULL",
            ((datetime.now(UTC) - timedelta(days=1)).isoformat(),),
        )
    assert store.begin_query(two if results[1] == "daily_budget" else one, 1)


def test_pause_and_restart_do_not_reissue_inflight_search_or_apply_late_result(store):
    plan, run_id = queued(store)
    store.claim_search()
    q = store.begin_query(run_id, 20)
    store.toggle_plan(plan["id"], False, 0, "admin", services())
    store.finish_query(q["id"], [row()])
    result = store.search_run(run_id)
    assert result["status"] == "cancelled" and result["results"][0]["decision"] == "excluded"
    assert store.begin_query(run_id, 20) is None
    _, new_id = queued(store)
    store.claim_search()
    new_q = store.begin_query(new_id, 20)
    store.recover_discovery()
    store.finish_query(new_q["id"], [row("https://late.example")])
    assert store.search_run(new_id)["results"] == []
    assert store.search_run(new_id)["status"] == "interrupted"
    assert store.claim_search() is None
    assert store.discovery_state(services())["used_today"] == 2


def test_result_dedup_review_and_crawl_link_are_transactional(store):
    store.create_job("old", "美国", [{"url": "https://existing.example"}], "admin")
    plan, run_id = queued(store)
    store.claim_search()
    q = finish_one(
        store,
        run_id,
        [
            row(),
            row("https://www.example.org/about"),
            row("https://existing.example/about"),
            row("https://linkedin.com/company/example"),
        ],
    )
    data = store.search_run(run_id)
    assert [r["decision"] for r in data["results"]] == [
        "pending",
        "duplicate",
        "duplicate",
        "excluded",
    ]
    approved = data["results"][0]["id"]
    first = store.review_results([approved], "collect", "admin")
    again = store.review_results([approved], "collect", "admin")
    assert first["candidates"] == again["candidates"] and again["jobs"] == []
    with store.connect() as c:
        assert c.execute("SELECT count(*) FROM candidate").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute("UPDATE discovery_result SET source_url='https://changed.example'")
    # Same returned HTTP response can only be committed once.
    store.finish_query(q["id"], [row("https://injected.example")])
    assert len(store.search_run(run_id)["results"]) == 4
    # A different search plan does not create another candidate for the same domain.
    _, other = queued(store, name="other")
    store.claim_search()
    finish_one(store, other)
    assert store.search_run(other)["results"][0]["decision"] == "duplicate"
    with pytest.raises(VersionConflict):
        store.review_results([data["results"][1]["id"]], "collect", "admin")
    candidate = store.claim()
    store.finish(candidate, {"status": "skipped", "reason": "no_public_business_email"})
    candidate = store.claim()
    store.finish(
        candidate,
        {
            "status": "completed",
            "account": {
                "domain": "example.org",
                "name": "New Company",
                "website": "https://example.org",
                "email": "info@example.org",
                "department": "通用联系",
                "stage": "ready",
                "mail": "none",
                "country": "美国",
                "tier": "1B",
                "evidence": {},
            },
        },
    )
    result = store.search_run(run_id)["results"][0]
    assert result["account_id"] == store.accounts()[0]["id"]
    assert result["milestone"] == "unverified"
    assert store.handoffs() == []  # Discovery approval never enrolls an email sequence.


def test_rejection_persists_across_plans_and_stale_batch_is_atomic(store):
    _, run_id = queued(store)
    store.claim_search()
    finish_one(store, run_id, [row(), row("https://second.example")])
    a, b = store.search_run(run_id)["results"]
    store.review_results([a["id"]], "reject", "admin", "没有目标产品业务")
    with pytest.raises(VersionConflict):
        store.review_results([b["id"], a["id"]], "collect", "admin")
    assert store.jobs() == []
    assert store.search_run(run_id)["results"][0]["reason"] == "没有目标产品业务"
    _, other = queued(store)
    store.claim_search()
    finish_one(store, other)
    duplicate = store.search_run(other)["results"][0]
    assert duplicate["decision"] == "duplicate"
    assert duplicate["canonical_run_id"] == run_id
    assert duplicate["canonical_decision"] == "rejected"
    assert duplicate["canonical_reason"] == "没有目标产品业务"


def test_auth_failure_pauses_and_repeated_temporary_failure_backs_off(store):
    plan = store.save_plan(config(), "admin")
    store.toggle_plan(plan["id"], True, 0, "admin", services())
    for count in range(1, 4):
        run = store.claim_search()
        store.begin_query(run["id"], 20)
        store.finish_search(run["id"], "unavailable")
        current = store.discovery_state(services())["plans"][0]
        assert current["failures"] == count
        assert current["enabled"] == (0 if count == 3 else 1)
        if count < 3:
            assert datetime.fromisoformat(current["next_run_at"]) > datetime.now(UTC)
            with store.connect() as c:
                c.execute("UPDATE discovery_plan SET next_run_at=?", (f"2000-01-0{count}",))
    store.toggle_plan(plan["id"], True, 1, "admin", services())
    run = store.claim_search()
    store.finish_search(run["id"], "auth_failed")
    assert store.discovery_state(services())["plans"][0]["enabled"] == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://169.254.169.254/",
        "file:///etc/passwd",
        "https://user:secret@company.com",
        "https://host.internal",
        "javascript:alert(1)",
    ],
)
def test_unsafe_search_result_never_enters_queue(url):
    assert site_from_result(url)[3]


def test_provider_contract_and_bounded_errors_hide_credentials():
    calls = []

    def transport(request):
        calls.append(request)
        if request.url.host == "api.search.brave.com":
            assert request.headers["X-Subscription-Token"] == "synthetic-search-key"
            assert request.url.params["count"] == "10"
            return httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {"url": "https://a.example", "title": "A", "description": "hardware"}
                        ]
                    }
                },
            )
        assert request.headers["Authorization"] == "Bearer synthetic-tavily-key"
        assert json.loads(request.content)["search_depth"] == "basic"
        return httpx.Response(
            200, json={"results": [{"url": "https://b.example", "title": "B", "content": "cloud"}]}
        )

    service = services(transport=httpx.MockTransport(transport))
    assert service.search("brave", "GPU United States", "US")[0]["snippet"] == "hardware"
    assert service.search("tavily", "GPU United States", "US")[0]["snippet"] == "cloud"
    assert len(calls) == 2
    for status, code in (
        (401, "auth_failed"),
        (403, "auth_failed"),
        (429, "rate_limited"),
        (500, "unavailable"),
        (302, "unavailable"),
    ):
        service = services(
            transport=httpx.MockTransport(
                lambda request, code=status: httpx.Response(code, text="provider-private-token")
            )
        )
        with pytest.raises(SearchError) as error:
            service.search("brave", "GPU", "US")
        assert str(error.value) == code
    service = services(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"error": "private-token"})
        )
    )
    with pytest.raises(SearchError, match="invalid_response"):
        service.search("tavily", "GPU", "US")


def test_discovery_worker_consumes_mock_provider_and_leaves_review_pending(store):
    _, run_id = queued(store, queries=["GPU cloud provider"])
    service = services(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "url": "https://realistic.example",
                                "title": "Company",
                                "description": "GPU cloud",
                            }
                        ]
                    }
                },
            )
        )
    )
    stop = threading.Event()
    original = store.finish_search

    def finish(*args, **kwargs):
        original(*args, **kwargs)
        stop.set()

    store.finish_search = finish
    t = threading.Thread(target=discovery_worker, args=(store, stop, service))
    t.start()
    t.join(5)
    if t.is_alive():
        stop.set()
        t.join(5)
        pytest.fail("Discovery worker did not complete bounded run")
    run = store.search_run(run_id)
    assert run["status"] == "succeeded" and run["results"][0]["decision"] == "pending"
    assert store.jobs() == []


def test_admin_routes_validation_and_conflicting_versions(tmp_path):
    app = create_app(
        tmp_path / "api.db",
        worker_enabled=False,
        proxy_key="test-key",
        admin_users=("admin@example.com",),
        sales_users=("sales@example.com",),
        search_services=services(),
    )
    with TestClient(app) as client:
        client.headers.update(
            {
                "X-Leadsgen-Proxy-Key": "test-key",
                "X-OA-User": "test",
                "X-OA-Email": "sales@example.com",
                "X-Leadsgen-Client": "web-v1",
            }
        )
        assert client.get("/api/discovery").status_code == 403
        assert client.post("/api/discovery/plans", json=config()).status_code == 403
        client.headers["X-OA-Email"] = "admin@example.com"
        del client.headers["X-Leadsgen-Client"]
        assert client.post("/api/discovery/plans", json=config()).status_code == 403
        client.headers["X-Leadsgen-Client"] = "web-v1"
        assert (
            client.post(
                "/api/discovery/plans", json=config(), headers={"Origin": "https://foreign.example"}
            ).status_code
            == 403
        )
        plan = client.post("/api/discovery/plans", json=config()).json()
        url = f"/api/discovery/plans/{plan['id']}"
        assert client.post(url, json={**config(), "version": 0}).status_code == 200
        assert client.post(url, json={**config(), "version": 0}).status_code == 409
        assert (
            client.post("/api/discovery/plans", json=config(queries=["one", "one"])).status_code
            == 422
        )
        assert (
            client.post("/api/discovery/plans", json=config(country="INVALID")).status_code == 422
        )
        assert (
            client.post(
                "/api/discovery/plans", json=config(queries=[str(i) * 5 for i in range(6)])
            ).status_code
            == 422
        )
        result = client.post(url + "/runs", json={"version": 1, "request_id": str(uuid.uuid4())})
        assert result.status_code == 201
        run_id = result.json()["id"]
        assert client.get(f"/api/discovery/runs/{run_id}").status_code == 200
        assert "synthetic-search-key" not in client.get("/api/discovery").text
        client.headers["X-OA-Email"] = "sales@example.com"
        assert client.get(url + "/runs").status_code == 403
        assert client.get(f"/api/discovery/runs/{run_id}").status_code == 403
        assert (
            client.post(url + "/schedule", json={"version": 1, "enabled": True}).status_code == 403
        )
        assert (
            client.post("/api/discovery/review", json={"ids": [1], "action": "collect"}).status_code
            == 403
        )


def test_repeated_migration_preserves_existing_business_tables_and_stays_idle(store):
    store.create_job("legacy", "美国", [{"url": "https://example.org"}], "admin")
    with store.connect() as c:
        tables = [
            r[0]
            for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not r[0].startswith(("discovery_", "sqlite_"))
        ]
        before = {t: [tuple(r) for r in c.execute(f'SELECT * FROM "{t}"')] for t in tables}
    for _ in range(2):
        restarted = Store(store.path)
        assert restarted.claim_search() is None
        with restarted.connect() as c:
            after = {t: [tuple(r) for r in c.execute(f'SELECT * FROM "{t}"')] for t in tables}
            assert after == before
            assert c.execute("SELECT count(*) FROM discovery_site").fetchone()[0] == 1
            for table in ("plan", "run", "query", "result"):
                assert c.execute(f"SELECT count(*) FROM discovery_{table}").fetchone()[0] == 0


def test_daily_budget_defers_enabled_plan_without_failure_or_request(store):
    _, used_run = queued(store)
    store.claim_search()
    store.begin_query(used_run, 1)
    plan = store.save_plan(config(name="waiting"), "admin")
    store.toggle_plan(plan["id"], True, 0, "admin", services())
    run = store.claim_search()
    with pytest.raises(SearchError, match="daily_budget"):
        store.begin_query(run["id"], 1)
    store.finish_search(run["id"], "daily_budget")
    with store.connect() as c:
        p = store.get_plan(c, plan["id"])
        assert p["enabled"] == 1 and p["failures"] == 0
        due = datetime.fromisoformat(p["next_run_at"])
        assert due.date() == (datetime.now(UTC) + timedelta(days=1)).date()
        assert due.hour == due.minute == due.second == 0
    assert store.search_run(run["id"])["status"] == "deferred"
    assert store.discovery_state(services())["used_today"] == 1


def test_manual_candidate_created_between_search_and_review_is_not_queued_twice(store):
    _, run_id = queued(store)
    store.claim_search()
    finish_one(store, run_id)
    rid = store.search_run(run_id)["results"][0]["id"]
    store.create_job("manual", "美国", [{"url": "https://www.example.org/about"}], "admin")
    result = store.review_results([rid], "collect", "admin")
    assert result["jobs"] == []
    assert len(store.jobs()) == 1
    assert store.search_run(run_id)["results"][0]["decision"] == "duplicate"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"x" * 512001),
        httpx.Response(200, json={"web": None}),
        httpx.Response(200, json={"web": {"results": [{}]}}),
        httpx.Response(200, json={"results": "wrong-type"}),
        httpx.Response(302, headers={"Location": "https://untrusted.example"}),
    ],
)
def test_provider_rejects_oversize_malformed_or_redirected_responses(response):
    calls = []

    def request(req):
        calls.append(req)
        return response

    adapter = services(transport=httpx.MockTransport(request))
    with pytest.raises(SearchError):
        adapter.search("brave", "GPU cloud", "US")
    assert len(calls) == 1
