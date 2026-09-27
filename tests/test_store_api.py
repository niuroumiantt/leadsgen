import httpx
import pytest
from fastapi.testclient import TestClient
from leadsgen.app import create_app, deliver_one, sync_followup_access
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


def test_confirmed_sales_inquiry_import_is_idempotent_and_never_becomes_outreach(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    imported = store.import_confirmed_leads(
        [
            {
                "version": "lead@1",
                "id": "42",
                "status": "quote",
                "company": "Example Buyer",
                "contact": "Alex",
                "email": "alex@example.net",
                "wants": "Asked about memory modules",
                "quantity": "20",
                "region": "美国",
                "confirmed_at": "2026-09-27T00:00:00Z",
                "source": {
                    "mailbox": "sales@glocalstorage.com",
                    "thread_id": "991",
                    "subject": "Inquiry",
                },
            }
        ],
        "2026-09-27T00:00:00Z",
        42,
    )
    assert imported == 1
    account = store.accounts()[0]
    assert account["sourceType"] == "sales_inbound"
    assert account["aimailThreadId"] == "991"
    assert account["email"] == "alex@example.net"
    assert (
        store.import_confirmed_leads(
            [
                {
                    "version": "lead@1",
                    "id": "42",
                    "source": {"mailbox": "sales@glocalstorage.com"},
                }
            ],
            "2026-09-27T00:00:00Z",
            42,
        )
        == 0
    )
    assert len(store.accounts()) == 1
    assert store.enqueue([account["id"]], "admin") == {"queued": [], "skipped": [account["id"]]}
    assert store.assign(account["id"], "isaac@example.com", "admin", 0) == {
        "owner": "",
        "pending": "isaac@example.com",
        "version": 1,
    }
    assert store.accept_assignment(account["id"], "isaac@example.com", 1)["owner"] == (
        "isaac@example.com"
    )
    pending = store.pending_followup_grants()
    assert pending == [
        {
            "account_id": account["id"],
            "recipient": "isaac@example.com",
            "thread_id": "991",
            "attempts": 0,
        }
    ]
    store.mark_followup_grant(account["id"], success=True, attempts=1)
    assert store.accounts()[0]["assignment"]["mail_access_status"] == "granted"


def test_followup_access_sync_posts_only_assigned_thread_and_checks_receipt(tmp_path, monkeypatch):
    store = Store(tmp_path / "app.sqlite3")
    store.import_confirmed_leads(
        [
            {
                "version": "lead@1",
                "id": "42",
                "source": {"mailbox": "sales@glocalstorage.com", "thread_id": "991"},
            }
        ],
        "",
        42,
    )
    account_id = store.accounts()[0]["id"]
    store.assign(account_id, "isaac@example.com", "admin", 0)
    store.accept_assignment(account_id, "isaac@example.com", 1)
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "external_id": account_id,
                "thread_id": "991",
                "owner": "isaac@example.com",
                "version": 1,
            },
        )

    monkeypatch.setattr("leadsgen.app.httpx.post", post)
    sync_followup_access(store, "https://mail.example", "private-token")
    assert len(calls) == 1
    url, request = calls[0]
    assert url == "https://mail.example/v1/followups/access"
    assert request["json"] == {
        "external_id": account_id,
        "thread_id": 991,
        "recipient": "isaac@example.com",
    }
    assert store.pending_followup_grants() == []
    assert store.accounts()[0]["assignment"]["mail_access_status"] == "granted"


def test_confirmed_lead_import_rejects_other_mailboxes(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    with pytest.raises(ValueError, match="invalid_confirmed_mail_lead"):
        store.import_confirmed_leads(
            [{"version": "lead@1", "id": "1", "source": {"mailbox": "isaac@semifly.ai"}}],
            "",
            0,
        )


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
    app = create_app(
        tmp_path / "app.sqlite3",
        worker_enabled=False,
        proxy_key="a" * 40,
        admin_users=("boss@example.com",),
    )
    with TestClient(app) as client:
        assert client.get("/api/state", headers={"X-OA-User": "boss"}).status_code == 401
        assert (
            client.get("/api/state", headers={"X-Leadsgen-Proxy-Key": "a" * 40}).status_code == 401
        )
        assert (
            client.get(
                "/api/state",
                headers={
                    "X-Leadsgen-Proxy-Key": "a" * 40,
                    "X-OA-User": "boss",
                    "X-OA-Email": "boss@example.com",
                },
            ).status_code
            == 200
        )


def test_lead_visibility_assignment_and_followup_are_server_enforced(tmp_path):
    store, _ = seeded(tmp_path)
    account_id = store.accounts()[0]["id"]
    app = create_app(
        tmp_path / "app.sqlite3",
        worker_enabled=False,
        proxy_key="p" * 40,
        admin_users=("larry@example.com",),
        sales_users=("isaac@example.com", "other@example.com"),
    )
    admin = {
        "X-Leadsgen-Proxy-Key": "p" * 40,
        "X-OA-User": "larry",
        "X-OA-Email": "larry@example.com",
        "X-Leadsgen-Client": "web-v1",
    }
    isaac = {
        "X-Leadsgen-Proxy-Key": "p" * 40,
        "X-OA-User": "isaac",
        "X-OA-Email": "isaac@example.com",
        "X-Leadsgen-Client": "web-v1",
    }
    other = {
        "X-Leadsgen-Proxy-Key": "p" * 40,
        "X-OA-User": "other",
        "X-OA-Email": "other@example.com",
        "X-Leadsgen-Client": "web-v1",
    }
    stranger = {
        "X-Leadsgen-Proxy-Key": "p" * 40,
        "X-OA-User": "stranger",
        "X-OA-Email": "stranger@example.com",
    }
    with TestClient(app) as client:
        assert client.get("/api/state", headers=stranger).status_code == 403
        assert client.get("/api/state", headers=isaac).json()["accounts"] == []
        offered = client.post(
            f"/api/accounts/{account_id}/assignment",
            headers=admin,
            json={"recipient": "isaac@example.com", "version": 0},
        )
        assert offered.status_code == 200
        assert (
            client.post(
                f"/api/accounts/{account_id}/assignment",
                headers=isaac,
                json={"recipient": "other@example.com", "version": 1},
            ).status_code
            == 403
        )
        assert [a["id"] for a in client.get("/api/state", headers=isaac).json()["accounts"]] == [
            account_id
        ]
        assert client.get("/api/state", headers=other).json()["accounts"] == []
        assert client.get("/api/state", headers=isaac).json()["jobs"] == []
        accepted = client.post(
            f"/api/accounts/{account_id}/assignment/accept", headers=isaac, json={"version": 1}
        )
        assert accepted.json() == {"owner": "isaac@example.com", "pending": "", "version": 2}
        updated = client.post(
            f"/api/accounts/{account_id}/followup",
            headers=isaac,
            json={"stage": "跟进中", "next_step": "核实需求", "due_at": "2026-10-01"},
        )
        assert updated.status_code == 200
        assert (
            client.post(
                f"/api/accounts/{account_id}/followup",
                headers=other,
                json={"stage": "跟进中"},
            ).status_code
            == 403
        )
        assert client.post(f"/api/accounts/{account_id}/suppress", headers=isaac).status_code == 403


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
