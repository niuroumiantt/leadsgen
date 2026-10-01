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


@pytest.mark.parametrize("projected", [False, True])
def test_confirmed_profile_refresh_preserves_assignment_and_followup(tmp_path, projected):
    store = Store(tmp_path / "app.sqlite3")
    if projected:
        store.import_mail_handoffs(
            [
                dict(
                    thread_id=991,
                    version=2,
                    owner="isaac@example.com",
                    pending="",
                    subject="RFQ",
                    summary="Reviewed handoff summary",
                    history=[],
                    updated_at="2026-09-27T00:00:00Z",
                )
            ]
        )
    lead = dict(
        version="lead@1",
        id="42",
        company="Example Buyer",
        contact="Alex",
        email="alex@example.net",
        wants="20 modules",
        quantity="20",
        region="美国",
        source=dict(mailbox="sales@glocalstorage.com", thread_id="991"),
    )
    store.import_confirmed_leads([lead], "2026-09-27T00:00:00Z", 42)
    account_id = store.accounts()[0]["id"]
    if not projected:
        store.assign(account_id, "isaac@example.com", "admin", 0)
        store.accept_assignment(account_id, "isaac@example.com", 1)
    store.update_followup(
        account_id, "isaac@example.com", "等待客户", "Confirm quote", "2026-10-08"
    )
    before = store.accounts()[0]
    with store.connect() as conn:
        notices_before = [dict(row) for row in conn.execute("SELECT * FROM assignment_notice")]
    lead.update(company="Example Buyer Ltd", contact="Alex Li", wants="40 modules", quantity="40")
    assert store.import_confirmed_leads([lead], "2026-09-28T00:00:00Z", 42) == 0
    after = store.accounts()[0]
    assert len(store.accounts()) == 1
    assert after["id"] == account_id
    assert after["name"] == "Example Buyer Ltd"
    assert after["aimailContact"] == "Alex Li"
    assert after["aimailQuantity"] == "40"
    assert after["aimailLeadId"] == "42"
    assert after["summary"] == ("Reviewed handoff summary" if projected else "40 modules")
    assert after["assignment"] == before["assignment"]
    assert after["followup"] == before["followup"]
    with store.connect() as conn:
        assert [
            dict(row) for row in conn.execute("SELECT * FROM assignment_notice")
        ] == notices_before


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


def test_assignment_history_return_reason_cancel_and_stale_decision(tmp_path):
    store, _ = seeded(tmp_path)
    aid = store.accounts()[0]["id"]
    store.assign(aid, "isaac@example.com", "admin@example.com", 0)
    store.decline_assignment(aid, "isaac@example.com", 1, "产品不在我的业务范围")
    result = store.accounts()[0]["assignment"]
    assert result["status"] == "returned"
    assert result["history"][0]["reason"] == "产品不在我的业务范围"
    store.assign(aid, "isaac@example.com", "admin@example.com", 2)
    store.cancel_assignment(aid, "admin@example.com", 3)
    with pytest.raises(PermissionError):
        store.accept_assignment(aid, "isaac@example.com", 3)
    assert not store.assigned_accounts("isaac@example.com")
    store.assign(aid, "isaac@example.com", "admin@example.com", 4)
    store.accept_assignment(aid, "isaac@example.com", 5)
    with pytest.raises(ValueError):
        store.update_followup(aid, "isaac@example.com", "跟进中", "电话联系", "tomorrow")
    store.update_followup(aid, "isaac@example.com", "跟进中", "电话联系", "2026-10-01")
    assert store.accounts()[0]["followup"]["next_step"] == "电话联系"


def test_mail_handoff_projection_preserves_owner_and_blocks_double_assignment(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    item = dict(
        thread_id=232,
        version=1,
        owner="larry@example.com",
        pending="isaac@example.com",
        subject="Customer inquiry",
        email="buyer@example.com",
        summary="20 servers",
        updated_at="2026-09-27T10:00:00Z",
        history=[],
    )
    store.import_mail_handoffs([item])
    store.import_mail_handoffs([item])
    assert len(store.accounts()) == 1
    item["mailbox"] = "larry@example.com"
    store.import_mail_handoffs([item])
    assert store.accounts()[0]["sourceMailbox"] == "larry@example.com"
    assert len(store.assigned_accounts("isaac@example.com")) == 1
    assert not store.assigned_accounts("other@example.com")
    with pytest.raises(ValueError):
        store.accept_assignment("handoff_232", "isaac@example.com", 1)
    item.update(
        version=2, owner="isaac@example.com", pending="", summary="40 servers; quote due Friday"
    )
    store.import_mail_handoffs([item])
    assert store.accounts()[0]["assignment"]["status"] == "accepted"
    assert store.accounts()[0]["summary"] == "40 servers; quote due Friday"
    assert not store.assigned_accounts("larry@example.com")
    item.update(
        version=1, owner="larry@example.com", pending="isaac@example.com", summary="20 servers"
    )
    store.import_mail_handoffs([item])
    assert store.accounts()[0]["assignment"]["owner"] == "isaac@example.com"
    assert store.accounts()[0]["summary"] == "40 servers; quote due Friday"


def test_notification_outbox_uses_snapshot_and_cancels_withdrawn_assignment(tmp_path, monkeypatch):
    from leadsgen.app import send_assignment_notices

    store, _ = seeded(tmp_path)
    aid = store.accounts()[0]["id"]
    store.assign(aid, "isaac@example.com", "admin@example.com", 0)
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(
            200, request=httpx.Request("POST", url), json={"id": f"{aid}:1", "state": "sent"}
        )

    monkeypatch.setattr("leadsgen.app.httpx.post", post)
    send_assignment_notices(store, "http://mail.test", "test")
    send_assignment_notices(store, "http://mail.test", "test")
    assert len(calls) == 1
    assert calls[0]["recipient"] == "isaac@example.com"
    assert store.accounts()[0]["assignment"]["notification"]["state"] == "sent"
    store.cancel_assignment(aid, "admin@example.com", 1)
    store.assign(aid, "isaac@example.com", "admin@example.com", 2)
    store.cancel_assignment(aid, "admin@example.com", 3)
    send_assignment_notices(store, "http://mail.test", "test")
    assert len(calls) == 1
    assert store.accounts()[0]["assignment"]["notification"]["state"] == "cancelled"


def test_new_mail_offer_queues_once_but_historical_offer_never_sends(tmp_path):
    import json

    store = Store(tmp_path / "app.sqlite3")
    item = dict(
        thread_id=91,
        version=1,
        owner="larry@example.com",
        pending="isaac@example.com",
        subject="RFQ",
        email="buyer@example.com",
        summary="GPU servers",
        updated_at="2026-09-27T10:00:00Z",
        history=[
            dict(
                actor="larry@example.com",
                action="offer",
                version=1,
                at="2026-09-27T10:00:00Z",
                recipient="isaac@example.com",
            )
        ],
    )
    store.import_mail_handoffs([item])
    with store.connect() as c:
        assert c.execute("SELECT count(*) FROM assignment_notice").fetchone()[0] == 0
    item["version"] = 3
    item["history"].append(
        dict(
            actor="larry@example.com",
            action="offer",
            version=3,
            at=item["updated_at"],
            notify=True,
            recipient="isaac@example.com",
        )
    )
    store.import_mail_handoffs([item])
    store.import_mail_handoffs([item])
    with store.connect() as c:
        rows = c.execute("SELECT payload FROM assignment_notice").fetchall()
        assert len(rows) == 1
        payload = json.loads(rows[0][0])
        assert payload["thread_id"] == 91
        assert payload["account_id"] == "handoff_91"
        assert payload["recipient"] == "isaac@example.com"
    item.update(version=4, pending="")
    item["history"].append(
        dict(
            actor="isaac@example.com",
            action="decline",
            version=4,
            at=item["updated_at"],
            reason="不负责该产品",
        )
    )
    store.import_mail_handoffs([item])
    assert store.accounts()[0]["assignment"]["status"] == "returned"
    assert store.accounts()[0]["assignment"]["history"][0]["reason"] == "不负责该产品"


def test_projected_decision_checks_identity_and_refreshes_source(tmp_path, monkeypatch):
    store = Store(tmp_path / "app.sqlite3")
    item = dict(
        thread_id=232,
        version=1,
        owner="larry@example.com",
        pending="isaac@example.com",
        subject="Customer inquiry",
        updated_at="2026-09-27T10:00:00Z",
        history=[],
    )
    store.import_mail_handoffs([item])
    app = create_app(
        tmp_path / "app.sqlite3",
        worker_enabled=False,
        proxy_key="p" * 40,
        endpoint="http://mail.test",
        integration_token="test",
        admin_users=("larry@example.com",),
        sales_users=("isaac@example.com", "other@example.com"),
    )
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        item.update(version=2, owner="isaac@example.com", pending="")
        return httpx.Response(200, request=httpx.Request("POST", url), json=item)

    def get(url, **kwargs):
        return httpx.Response(
            200, request=httpx.Request("GET", url), json={"version": "followups@1", "items": [item]}
        )

    monkeypatch.setattr("leadsgen.app.httpx.post", post)
    monkeypatch.setattr("leadsgen.app.httpx.get", get)
    headers = {
        "X-Leadsgen-Proxy-Key": "p" * 40,
        "X-OA-User": "other",
        "X-OA-Email": "other@example.com",
        "X-Leadsgen-Client": "web-v1",
    }
    with TestClient(app) as client:
        assert client.post(
            "/api/accounts/handoff_232/assignment/accept", headers=headers, json={"version": 1}
        ).status_code in (403, 409)
        assert calls == []
        headers.update({"X-OA-User": "isaac", "X-OA-Email": "isaac@example.com"})
        assert (
            client.post(
                "/api/accounts/handoff_232/assignment/cancel", headers=headers, json={"version": 1}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/accounts/handoff_232/assignment/accept", headers=headers, json={"version": 1}
            ).status_code
            == 200
        )
        assert calls == [dict(actor="isaac@example.com", version=1, action="accept", reason="")]
        assert store.accounts()[0]["assignment"]["status"] == "accepted"
        assert (
            client.post(
                "/api/accounts/handoff_232/assignment/accept", headers=headers, json={"version": 1}
            ).status_code
            == 403
        )
        assert len(calls) == 1


def test_confirmed_feed_skips_personal_mail_and_advances_mixed_page_cursor(tmp_path, monkeypatch):
    from leadsgen.app import sync_confirmed_leads

    store = Store(tmp_path / "app.sqlite3")
    items = [
        {
            "version": "lead@1",
            "id": "1",
            "source": {"mailbox": "larry@glocalstorage.com", "thread_id": "101"},
        },
        {
            "version": "lead@1",
            "id": "2",
            "source": {"mailbox": "sales@glocalstorage.com", "thread_id": "102"},
        },
    ]
    payload = {
        "version": "v1",
        "leads": items,
        "next_since": "2026-09-27T10:00:00Z",
        "next_after": 2,
    }
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **kw: httpx.Response(
            200, json=payload, request=httpx.Request("GET", "https://mail.test/v1/leads")
        ),
    )
    sync_confirmed_leads(store, "https://mail.test", "test-token")
    assert [a["aimailThreadId"] for a in store.accounts()] == ["102"]
    assert store.confirmed_lead_cursor() == (payload["next_since"], 2)
    payload.update(leads=[items[0]], next_after=3)
    sync_confirmed_leads(store, "https://mail.test", "test-token")
    assert len(store.accounts()) == 1
    assert store.confirmed_lead_cursor() == (payload["next_since"], 3)
    payload.update(leads=[{"source": None}], next_after=4)
    with pytest.raises(ValueError, match="invalid_confirmed_mail_leads"):
        sync_confirmed_leads(store, "https://mail.test", "test-token")
    assert store.confirmed_lead_cursor() == (payload["next_since"], 3)
