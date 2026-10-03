"""跨仓库合同；infra/scripts/test_sales_workflow.sh 强制执行，缺依赖直接失败。"""

import os
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import httpx
import pytest
from fastapi.testclient import TestClient
from leadsgen.app import (
    create_app as create_leads_app,
)
from leadsgen.app import (
    deliver_one,
    send_assignment_notices,
    sync_confirmed_leads,
    sync_followup_access,
    sync_mail,
    sync_mail_handoffs,
)
from leadsgen.store import Store
from test_store_api import seeded

if os.environ.get("AIMAIL_INTEGRATION_REQUIRED") == "1":
    from aimail import outreach as mail
else:
    mail = pytest.importorskip("aimail.outreach", reason="Run infra/scripts/test_sales_workflow.sh")
from aimail.api.app import create_app  # noqa: E402
from aimail.ingest.run import store_raw  # noqa: E402
from aimail.store import followup, leads  # noqa: E402
from aimail.store.db import connect  # noqa: E402
from aimail.store.repo import ensure_mailbox  # noqa: E402


def test_import_approve_simulated_send_and_return_events(tmp_path, monkeypatch):
    store, _ = seeded(tmp_path)
    aid = store.accounts()[0]["id"]
    conn = connect(tmp_path / "mail.sqlite3")
    mailbox = ensure_mailbox(conn, "supplier@fictional.example")
    app = create_app(conn, mailbox, outreach_import_token="bridge-test-only")
    with TestClient(app) as client:

        def post(url, **kwargs):
            return client.post(httpx.URL(url).path, json=kwargs["json"], headers=kwargs["headers"])

        monkeypatch.setattr(httpx, "post", post)
        store.enqueue([aid], "test-operator")
        deliver_one(store, "https://mail.test", "bridge-test-only")
        assert store.accounts()[0]["stage"] == "handed"
        assert store.accounts()[0]["mail"] == "draft"
        sid = mail.listing(conn, mailbox)[0]["id"]
        mail.approve(
            conn,
            mailbox,
            sid,
            "test-operator",
            [{"day": d, "subject": "Test only", "body": "Never transmitted"} for d in mail.CADENCE],
            True,
        )

        class NoNetwork:
            def deliver(self, sender, recipients, raw):
                return "ok"

        assert mail.tick(
            conn,
            mailbox,
            sender="supplier@fictional.example",
            sender_name="Test",
            transport=NoNetwork(),
            enabled=True,
            now=datetime.now(UTC),
        )

        class Adapter:
            def __init__(self, **kwargs):
                self.headers = kwargs["headers"]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def get(self, url, **kwargs):
                return client.get(httpx.URL(url).path, headers=self.headers, **kwargs)

            def post(self, url, **kwargs):
                return client.post(httpx.URL(url).path, headers=self.headers, **kwargs)

        monkeypatch.setattr(httpx, "Client", Adapter)
        sync_mail(store, "https://mail.test", "bridge-test-only")
        assert store.accounts()[0]["mail"] == "accepted"
        store.suppress(aid, "test-operator")
        sync_mail(store, "https://mail.test", "bridge-test-only")
        assert mail.get(conn, mailbox, sid)["state"] == "paused"
        assert store.handoffs()[0]["status"] == "stopped"
    conn.close()


@pytest.mark.parametrize("old_request_first", [True, False], ids=["late-receipt", "late-request"])
def test_reassigned_owner_keeps_access_when_old_request_or_receipt_arrives_late(
    tmp_path,
    monkeypatch,
    old_request_first,
):
    token = "bridge-test-only"
    owners = ("a@example.test", "b@example.test")
    conn = connect(tmp_path / "mail.sqlite3")
    mailbox = ensure_mailbox(conn, "sales@glocalstorage.com")
    incoming = EmailMessage()
    incoming["From"], incoming["To"] = "buyer@example.test", "sales@glocalstorage.com"
    incoming["Subject"], incoming["Message-ID"] = "RFQ", "<race@example.test>"
    incoming.set_content("Please quote 48 units.")
    pk, _ = store_raw(conn, mailbox, incoming.as_bytes(), "in", datetime.now(UTC))
    tid = conn.execute("SELECT thread_id FROM message WHERE id=?", (pk,)).fetchone()[0]
    store = Store(tmp_path / "leads.sqlite3")
    store.import_confirmed_leads(
        [
            {
                "version": "lead@1",
                "id": "42",
                "source": {
                    "mailbox": "sales@glocalstorage.com",
                    "thread_id": str(tid),
                },
            }
        ],
        "",
        42,
    )
    aid = store.accounts()[0]["id"]
    store.assign(aid, owners[0], "admin", 0)
    store.accept_assignment(aid, owners[0], 1)
    app = create_app(
        conn,
        mailbox,
        require_oa_auth=True,
        followup_members=owners,
        outreach_import_token=token,
    )
    with TestClient(app) as client:
        calls = []

        def post(url, **kwargs):
            body = kwargs["json"]
            calls.append(body["assignment_version"])
            if body["assignment_version"] == 4:
                return client.post(httpx.URL(url).path, json=body, headers=kwargs["headers"])
            assert body["assignment_version"] == 2
            if old_request_first:
                delayed = client.post(httpx.URL(url).path, json=body, headers=kwargs["headers"])
                assert delayed.status_code == 200
            store.assign(aid, owners[1], "admin", 2)
            store.accept_assignment(aid, owners[1], 3)
            sync_followup_access(store, "https://mail.test", token)
            if not old_request_first:
                delayed = client.post(httpx.URL(url).path, json=body, headers=kwargs["headers"])
                assert delayed.status_code == 409
            return delayed

        monkeypatch.setattr(httpx, "post", post)
        sync_followup_access(store, "https://mail.test", token)
        assert calls == [2, 4]
        assert followup.get(conn, tid)["owner"] == owners[1]
        assert store.accounts()[0]["assignment"]["mail_access_status"] == "granted"
        assert store.pending_followup_grants() == []
        for owner, status in [(owners[0], 403), (owners[1], 200)]:
            assert (
                client.get(
                    f"/api/followups/{tid}",
                    headers={"X-OA-User": owner, "X-OA-Email": owner},
                ).status_code
                == status
            )
    conn.close()


@pytest.mark.parametrize("projected", [False, True], ids=["leadsgen-owner", "aimail-owner"])
def test_confirm_assign_notify_accept_and_thread_access(tmp_path, monkeypatch, projected):
    """两个真实 API + 临时数据库，仅替换 HTTP 传输和 SMTP，不伪造回执。"""
    admin, sales = "larry@glocalstorage.com", "isaac@example.test"
    token = "bridge-test-only"
    conn = connect(tmp_path / "mail.sqlite3")
    personal = ensure_mailbox(conn, admin)
    shared = ensure_mailbox(conn, "sales@glocalstorage.com")
    ensure_mailbox(conn, sales)
    thread_ids = []
    for mailbox, address in ((shared, "sales@glocalstorage.com"), (personal, admin)):
        message = EmailMessage()
        message["From"], message["To"] = "buyer@example.test", address
        message["Subject"] = "RFQ 20 modules"
        message["Message-ID"] = f"<inquiry-{mailbox}@example.test>"
        message.set_content("Please quote 20 modules. Original conversation.")
        pk, _ = store_raw(conn, mailbox, message.as_bytes(), "in", datetime.now(UTC))
        tid = conn.execute("SELECT thread_id FROM message WHERE id=?", (pk,)).fetchone()[0]
        thread_ids.append(tid)
        suggestion = leads.insert_suggestion(
            conn,
            pk,
            tid,
            "test-model",
            "lead@1",
            datetime.now(UTC).isoformat(),
            dict(company="Example Buyer", contact="Alex", wants="20 modules", quantity="20"),
        )
        leads.confirm(conn, suggestion, admin)
    sent = []

    class NoNetwork:
        def deliver(self, sender, recipients, raw):
            sent.append((sender, recipients, raw))
            return "ok"

    app = create_app(
        conn,
        personal,
        shared_mailbox_id=shared,
        require_oa_auth=True,
        mailbox_access={admin: (admin, "sales@glocalstorage.com"), sales: (sales,)},
        followup_members=(admin, sales),
        outreach_import_token=token,
        api_tokens={"leadsgen": token},
        sender=admin,
        transport=NoNetwork(),
    )
    store = Store(tmp_path / "leads.sqlite3")
    with TestClient(app) as client:

        def get(url, **kwargs):
            return client.get(
                httpx.URL(url).path, headers=kwargs["headers"], params=kwargs.get("params")
            )

        def post(url, **kwargs):
            return client.post(httpx.URL(url).path, headers=kwargs["headers"], json=kwargs["json"])

        monkeypatch.setattr(httpx, "get", get)
        monkeypatch.setattr(httpx, "post", post)
        if projected:
            followup.transfer(
                conn,
                thread_ids[0],
                admin,
                sales,
                0,
                {"needs": "Reviewed 20 modules"},
                "",
                notify=True,
            )
            sync_mail_handoffs(store, "https://mail.test", token)
        sync_confirmed_leads(store, "https://mail.test", token)
        sync_confirmed_leads(store, "https://mail.test", token)
        accounts = store.accounts()
        assert len(accounts) == 1  # 个人邮箱的确认线索不进入共享客户库。
        account_id = accounts[0]["id"]
        assert accounts[0]["name"] == "Example Buyer"
        assert accounts[0]["aimailThreadId"] == str(thread_ids[0])
        if not projected:
            store.assign(account_id, sales, admin, 0)
        send_assignment_notices(store, "https://mail.test", token)
        send_assignment_notices(store, "https://mail.test", token)
        assert len(sent) == 1
        assert sent[0][:2] == (admin, [sales])
        notification = BytesParser(policy=policy.default).parsebytes(sent[0][2])
        originals = list(notification.iter_attachments())
        assert len(originals) == 1 and originals[0].get_filename().endswith(".eml")
        assert b"Original conversation" in originals[0].as_bytes()
        headers = {
            "X-OA-User": sales,
            "X-OA-Email": sales,
            "X-Leadsgen-Proxy-Key": "test-proxy",
            "X-Leadsgen-Client": "web-v1",
        }
        with TestClient(
            create_leads_app(
                tmp_path / "leads.sqlite3",
                worker_enabled=False,
                proxy_key="test-proxy",
                admin_users=(admin,),
                sales_users=(sales,),
                endpoint="https://mail.test",
                integration_token=token,
            )
        ) as crm:
            path = f"/api/accounts/{account_id}/assignment/accept"
            outsider = {**headers, "X-OA-Email": "outsider@example.test"}
            assert crm.post(path, headers=outsider, json={"version": 1}).status_code == 403
            accepted = crm.post(path, headers=headers, json={"version": 1})
            assert accepted.status_code == 200, accepted.text
            # 已接手后不再具有「待接手人」身份，不能重放同一决定。
            assert crm.post(path, headers=headers, json={"version": 1}).status_code == 403
        sync_followup_access(store, "https://mail.test", token)
        sync_mail_handoffs(store, "https://mail.test", token)
        assert store.accounts()[0]["assignment"]["owner"] == sales
        assert followup.get(conn, thread_ids[0])["owner"] == sales
        if not projected:
            rejected = client.post(
                f"/api/followups/{thread_ids[0]}/offer",
                headers=headers,
                json={"recipient": admin, "version": 1, "note": "Transfer"},
            )
            assert rejected.status_code == 409
            assert followup.get(conn, thread_ids[0])["owner"] == sales
        assert client.get(f"/api/followups/{thread_ids[0]}", headers=headers).status_code == 200
        assert client.get(f"/api/threads/{thread_ids[0]}", headers=headers).status_code == 404
        assert client.get(f"/api/followups/{thread_ids[1]}", headers=headers).status_code != 200
        assert (
            client.get("/v1/followups", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        send_assignment_notices(store, "https://mail.test", token)
        assert len(sent) == 1
    conn.close()
