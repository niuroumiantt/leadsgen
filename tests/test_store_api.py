import httpx
from fastapi.testclient import TestClient
from leadsgen.app import create_app, deliver_one
from leadsgen.store import Store


def account():
    return {
        "domain": "example.com",
        "website": "https://example.com",
        "name": "Example",
        "email": "sales@example.com",
        "department": "销售入口",
        "stage": "ready",
        "mail": "none",
        "country": "美国",
        "tier": "1D",
        "evidence": {"url": "https://example.com/contact"},
    }


def seeded(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    store.create_job("test", "美国", [{"url": "https://example.com"}], "tester")
    task = store.claim()
    store.finish(task, {"status": "completed", "account": account()})
    return store, task


def test_restart_dedupe_and_recrawl_preserve_handoff(tmp_path):
    store, task = seeded(tmp_path)
    a = store.accounts()[0]
    assert store.enqueue([a["id"]], "tester")["queued"] == [a["id"]]
    assert store.enqueue([a["id"]], "tester")["skipped"] == [a["id"]]
    store.finish(task, {"status": "completed", "account": account()})
    reopened = Store(tmp_path / "app.sqlite3")
    assert len(reopened.accounts()) == 1
    assert reopened.accounts()[0]["stage"] == "queued"
    assert len(reopened.handoffs()) == 1
    reopened.suppress(a["id"], "tester")
    reopened.finish(task, {"status": "completed", "account": account()})
    assert reopened.accounts()[0]["stage"] == "suppressed"


def test_no_endpoint_never_marks_delivered(tmp_path):
    store, _ = seeded(tmp_path)
    store.enqueue([store.accounts()[0]["id"]], "tester")
    deliver_one(store, "", "")
    assert store.handoffs()[0]["status"] == "queued"


def test_api_persists_jobs_and_requires_same_origin_custom_header(tmp_path):
    app = create_app(tmp_path / "app.sqlite3", worker_enabled=False)
    with TestClient(app) as client:
        payload = {
            "name": "US",
            "region": "美国",
            "seeds": [{"url": "https://example.com", "tier": "1D"}],
        }
        assert client.post("/api/jobs", json=payload).status_code == 403
        assert (
            client.post(
                "/api/jobs",
                json=payload,
                headers={"X-Leadsgen-Client": "web-v1", "Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/jobs", json=payload, headers={"X-Leadsgen-Client": "web-v1"}
            ).status_code
            == 201
        )
        assert client.get("/api/state").json()["jobs"][0]["candidates"][0]["status"] == "queued"
        assert client.get("/api/missing").status_code == 404
        assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 400


def test_proxy_auth_requires_secret_and_identity(tmp_path):
    app = create_app(tmp_path / "app.sqlite3", worker_enabled=False, proxy_key="a" * 40)
    with TestClient(app) as client:
        assert client.get("/api/state", headers={"X-OA-User": "boss"}).status_code == 401
        assert (
            client.get("/api/state", headers={"X-Leadsgen-Proxy-Key": "a" * 40}).status_code == 401
        )
        assert (
            client.get(
                "/api/state", headers={"X-Leadsgen-Proxy-Key": "a" * 40, "X-OA-User": "boss"}
            ).status_code
            == 200
        )


def test_receipt_required_and_suppression_race_is_not_lost(tmp_path, monkeypatch):
    store, _ = seeded(tmp_path)
    aid = store.accounts()[0]["id"]
    store.enqueue([aid], "tester")

    def receiving(url, **kwargs):
        store.suppress(aid, "tester")
        return httpx.Response(
            200,
            json={"receipt_id": "ps_test", "external_id": aid},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", receiving)
    deliver_one(store, "https://mail.example", "test")
    assert store.accounts()[0]["stage"] == "suppressed"
    assert store.handoffs()[0]["status"] == "stop_pending"


def test_event_deduplication_and_terminal_precedence(tmp_path):
    store, _ = seeded(tmp_path)
    aid = store.accounts()[0]["id"]

    def event(i, kind):
        return {"id": i, "external_id": aid, "type": kind, "occurred_at": "2026-09-23"}

    batch = {"events": [event(1, "unsubscribed"), event(2, "smtp_accepted")], "next_cursor": 2}
    store.receive_events(batch)
    store.receive_events(batch)
    assert store.mail_cursor() == 2
    assert store.accounts()[0]["mail"] == "unsubscribed"
    assert store.accounts()[0]["stage"] == "suppressed"
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM mail_event").fetchone()[0] == 2


def test_recrawl_preserves_selected_email_source(tmp_path):
    store, task = seeded(tmp_path)
    a = account()
    a["email"] = "info@example.com"
    a["evidence"] = {"url": "https://example.com/new-contact"}
    store.finish(task, {"status": "completed", "account": a})
    assert store.accounts()[0]["email"] == "sales@example.com"
    assert store.accounts()[0]["evidence"]["url"] == "https://example.com/contact"


def test_external_public_contact_can_be_reviewed_but_handoff_locks_it(tmp_path):
    import pytest

    store, task = seeded(tmp_path)
    a = account()
    a["contacts"] = [
        {
            "email": "owner@external.example",
            "role": "business",
            "mailRoute": "mx_present",
            "url": "https://example.com/contact",
            "observedAt": "2026-09-23",
        }
    ]
    store.finish(task, {"status": "completed", "account": a})
    aid = store.accounts()[0]["id"]
    store.select_contact(aid, "owner@external.example", "owner")
    assert store.accounts()[0]["stage"] == "ready"
    assert store.accounts()[0]["email"] == "owner@external.example"
    store.enqueue([aid], "owner")
    with pytest.raises(ValueError):
        store.select_contact(aid, "owner@external.example", "owner")
