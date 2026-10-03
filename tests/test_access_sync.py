"""迟到的邮件授权回执不能确认或推迟下一次分配。"""

import json
import sqlite3

import httpx
import pytest
from leadsgen.app import sync_followup_access
from leadsgen.store import Store


def assigned_store(tmp_path):
    store = Store(tmp_path / "leads.sqlite3")
    store.import_confirmed_leads(
        [
            {
                "version": "lead@1",
                "id": "42",
                "source": {
                    "mailbox": "sales@glocalstorage.com",
                    "thread_id": "991",
                },
            }
        ],
        "",
        42,
    )
    aid = store.accounts()[0]["id"]
    store.assign(aid, "a@example.test", "admin", 0)
    store.accept_assignment(aid, "a@example.test", 1)
    return store, aid


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize("return_to_a", [False, True])
def test_stale_receipt_cannot_confirm_or_delay_new_assignment(
    tmp_path,
    monkeypatch,
    success,
    return_to_a,
):
    store, aid = assigned_store(tmp_path)

    def post(url, **kwargs):
        store.assign(aid, "b@example.test", "admin", 2)
        store.accept_assignment(aid, "b@example.test", 3)
        if return_to_a:
            store.assign(aid, "a@example.test", "admin", 4)
            store.accept_assignment(aid, "a@example.test", 5)
        return httpx.Response(
            200 if success else 503,
            request=httpx.Request("POST", url),
            json={
                "external_id": aid,
                "thread_id": "991",
                "owner": "a@example.test",
                "version": 1,
                "assignment_version": 2,
            },
        )

    monkeypatch.setattr("leadsgen.app.httpx.post", post)
    sync_followup_access(store, "https://mail.example", "test-token")
    assert store.accounts()[0]["assignment"]["mail_access_status"] == "queued"
    pending = store.pending_followup_grants()
    assert len(pending) == 1  # 旧失败也不能将新请求推迟到退避时间之后。
    assert pending[0]["recipient"] == ("a@example.test" if return_to_a else "b@example.test")
    assert pending[0]["attempts"] == 0
    assert pending[0]["assignment_version"] == (6 if return_to_a else 4)


@pytest.mark.parametrize("version", [None, 1, "2", 2.0, True])
def test_unversioned_or_mismatched_receipt_cannot_mark_access_granted(
    tmp_path,
    monkeypatch,
    version,
):
    store, aid = assigned_store(tmp_path)

    def post(url, **kwargs):
        receipt = {"external_id": aid, "thread_id": "991", "owner": "a@example.test", "version": 1}
        if version is not None:
            receipt["assignment_version"] = version
        return httpx.Response(200, request=httpx.Request("POST", url), json=receipt)

    monkeypatch.setattr("leadsgen.app.httpx.post", post)
    sync_followup_access(store, "https://mail.example", "test-token")
    account = store.accounts()[0]
    assert account["assignment"]["mail_access_status"] == "queued"
    assert account["assignment"]["mail_access_error"]


def test_legacy_queue_migration_requeues_granted_access_with_accepted_version(tmp_path):
    store, aid = assigned_store(tmp_path)
    with store.connect() as conn:
        conn.execute("DROP TABLE followup_access_sync")
        conn.execute(
            "CREATE TABLE followup_access_sync (account_id TEXT PRIMARY KEY, recipient TEXT, "
            "thread_id TEXT, state TEXT, attempts INTEGER, updated_at TEXT, next_at TEXT, "
            "last_error TEXT)"
        )
        conn.execute(
            "INSERT INTO followup_access_sync VALUES(?,?,?,'granted',7,'old','old','')",
            (aid, "a@example.test", "991"),
        )
    # 尚未接受的B提议不能替代A已接受分配的版本。
    store.assign(aid, "b@example.test", "admin", 2)
    migrated = Store(store.path)
    pending = migrated.pending_followup_grants()
    assert pending[0]["assignment_version"] == 2
    assert pending[0]["attempts"] == 0
    assert migrated.accounts()[0]["assignment"]["mail_access_status"] == "queued"
    # 重启不重复重置已有版本的退避与结果。
    migrated.mark_followup_grant(pending[0], success=True, attempts=1)
    restarted = Store(store.path)
    assert restarted.pending_followup_grants() == []
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM account").fetchone()[0] == 1


@pytest.mark.parametrize("receipt", [None, [], {"owner": None}])
def test_malformed_receipt_keeps_access_queued_without_crashing_worker(
    tmp_path,
    monkeypatch,
    receipt,
):
    store, aid = assigned_store(tmp_path)

    def post(url, **kwargs):
        body = receipt
        if isinstance(body, dict):
            body = {"external_id": aid, "thread_id": "991", "assignment_version": 2, **body}
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            content=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    monkeypatch.setattr("leadsgen.app.httpx.post", post)
    sync_followup_access(store, "https://mail.example", "test-token")
    assignment = store.accounts()[0]["assignment"]
    assert assignment["mail_access_status"] == "queued"
    assert assignment["mail_access_error"]


def test_migration_does_not_guess_accepted_version_from_pending_offer(tmp_path):
    store, aid = assigned_store(tmp_path)
    with store.connect() as conn:
        conn.execute("ALTER TABLE followup_access_sync DROP COLUMN assignment_version")
        conn.execute("DELETE FROM assignment_event WHERE action='accepted'")
    store.assign(aid, "b@example.test", "admin", 2)
    migrated = Store(store.path)
    assert migrated.pending_followup_grants()[0]["assignment_version"] == 0
    assert migrated.accounts()[0]["assignment"]["pending"] == "b@example.test"
