"""Additive migration, concurrency and permissions across the customer lifecycle."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from leadsgen.app import create_app
from leadsgen.store import SCHEMA, Store
from leadsgen.workflow import VersionConflict
from test_store_api import account, seeded


def own(store, aid, actor="seller@example.com"):
    store.assign(aid, actor, "admin", 0)
    store.accept_assignment(aid, actor, 1)


def progress(store, aid, **changes):
    body = dict(version=0, milestone="identified", profile={}, reason="官网来源已核实")
    body.update(changes)
    return store.update_progress(
        aid, "seller@example.com", "跟进中", "确认交付时间", "2026-10-10", **body
    )


def test_legacy_migration_preserves_source_tables_and_never_replays_actions(tmp_path):
    path = tmp_path / "legacy.db"
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute(
        "INSERT INTO account VALUES(?,?,?,?)",
        ("old", "example.com", json.dumps(account()), "2026-09-23"),
    )
    c.execute(
        "INSERT INTO account_assignment "
        "VALUES('old','seller@example.com','',2,'admin','2026-09-23')"
    )
    c.execute(
        "INSERT INTO account_followup "
        "VALUES('old','跟进中','询价','2026-10-10','seller@example.com','2026-09-24')"
    )
    c.execute(
        "INSERT INTO "
        "assignment_event(account_id,actor,action,recipient,version,created_at) "
        "VALUES('old','admin','offered','seller@example.com',1,'2026-09-23')"
    )
    c.execute(
        "INSERT INTO assignment_notice VALUES('notice','old',1,'{}','sent','2026-09-23',1,'')"
    )
    c.execute("INSERT INTO mail_event VALUES('event','old','{\"type\":\"replied\"}','2026-09-24')")
    c.execute("INSERT INTO integration_state VALUES('mail_cursor','18')")
    c.execute("INSERT INTO job VALUES('job','旧批次','美国','2026-09-23')")
    c.execute(
        "INSERT INTO candidate(job_id,url,seed,status,reason,updated_at) "
        "VALUES('job','https://example.com','{}','failed','network_or_tls_error','2026-09-23')"
    )
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    before = {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    c.commit()
    c.close()
    store = Store(path)
    assert store.collection_status()["counts"] == {"failed": 1}
    attempt = store.candidate_history(1)["attempts"][0]
    assert attempt["legacy"] == 1 and attempt["started_at"] is None
    assert attempt["observations"] == []
    initial = store.activities("old", "seller@example.com")
    Store(path)
    assert store.activities("old", "seller@example.com") == initial
    assert {e["kind"] for e in initial["items"]} == {
        "baseline.account",
        "baseline.followup",
        "assignment.offered",
        "mail.event",
    }
    with store.connect() as c:
        assert before == {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t}")] for t in tables}
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_progress_is_atomic_versioned_and_append_only(tmp_path):
    store, _ = seeded(tmp_path)
    aid = store.accounts()[0]["id"]
    own(store, aid)
    progress(store, aid)
    with pytest.raises(VersionConflict):
        progress(store, aid, reason="stale draft")
    with pytest.raises(ValueError, match="需补齐"):
        progress(store, aid, version=1, milestone="qualified")
    current = store.accounts()[0]
    assert current["workflow"]["version"] == 1
    assert current["workflow"]["milestone"] == "identified"
    profile = dict(product="GPU", quantity="5", country="US")
    progress(store, aid, version=1, milestone="qualified", profile=profile)
    with pytest.raises(ValueError, match="报价"):
        progress(store, aid, version=2, milestone="quoted", profile=profile)
    with pytest.raises(ValueError, match="订单"):
        progress(store, aid, version=2, milestone="won", profile=profile)
    events = store.activities(aid, "seller@example.com")["items"]
    changes = [e for e in events if e["kind"] == "progress.updated"]
    assert len(changes) == 2
    assert changes[0]["payload"]["before"]["milestone"] == "identified"
    assert changes[0]["payload"]["after"]["profile"] == profile
    for sql in ("UPDATE account_activity SET actor='changed'", "DELETE FROM account_activity"):
        with pytest.raises(sqlite3.IntegrityError, match="append only"), store.connect() as c:
            c.execute(sql)


def test_same_company_does_not_expand_access_or_change_source_ids(tmp_path):
    store, _ = seeded(tmp_path)
    a = store.accounts()[0]
    own(store, a["id"])
    store.create_job("another", "美国", [{"url": "https://other.example"}], "admin")
    store.finish(
        store.claim(), {"status": "completed", "account": {**account(), "domain": "other.example"}}
    )
    b = next(v for v in store.accounts() if v["id"] != a["id"])
    own(store, b["id"], "other@example.com")
    before = [(v["id"], v["domain"], v["assignment"], v["email"]) for v in store.accounts()]
    store.link_company(a["id"], b["id"], 0, 0, "admin", "客户确认同一企业")
    after = store.accounts()
    assert before == [(v["id"], v["domain"], v["assignment"], v["email"]) for v in after]
    assert len({v["company"]["company_id"] for v in after}) == 1
    assert [v["id"] for v in store.assigned_accounts("seller@example.com")] == [a["id"]]
    with pytest.raises(LookupError):
        store.activities(b["id"], "seller@example.com")
    with pytest.raises(VersionConflict):
        store.link_company(a["id"], None, 0, None, "admin", "stale")
    store.link_company(a["id"], None, 1, None, "admin", "核实为独立法人")
    assert len({v["company"]["company_id"] for v in store.accounts()}) == 2


def test_activity_pagination_and_event_replay(tmp_path):
    store, _ = seeded(tmp_path)
    aid = store.accounts()[0]["id"]
    own(store, aid)
    for i in range(55):
        store.update_followup(aid, "seller@example.com", "跟进中", str(i), "2026-10-10")
    first = store.activities(aid, "seller@example.com")
    second = store.activities(aid, "seller@example.com", before=first["nextBefore"])
    assert len(first["items"]) == 50 and second["items"]
    assert not {e["id"] for e in first["items"]} & {e["id"] for e in second["items"]}
    with store.connect() as c:
        event = dict(c.execute("SELECT * FROM assignment_event LIMIT 1").fetchone())
        before = c.execute("SELECT count(*) FROM account_activity").fetchone()[0]
        store.assignment_activity(c, event)
        assert c.execute("SELECT count(*) FROM account_activity").fetchone()[0] == before


def headers(email):
    return {
        "X-Leadsgen-Proxy-Key": "test-key",
        "X-OA-User": email,
        "X-OA-Email": email,
        "X-Leadsgen-Client": "web-v1",
    }


def test_api_role_boundaries_and_admin_as_actual_owner(tmp_path):
    app = create_app(
        tmp_path / "api.db",
        worker_enabled=False,
        proxy_key="test-key",
        admin_users=("admin@example.com",),
        sales_users=("seller@example.com", "other@example.com", "admin@example.com"),
    )
    store = app.state.store
    store.create_job("test", "美国", [{"url": "https://example.com"}], "admin")
    task = store.claim()
    store.finish(task, {"status": "completed", "account": account()})
    aid = store.accounts()[0]["id"]
    store.assign(aid, "admin@example.com", "admin@example.com", 0)
    body = dict(
        version=0,
        milestone="identified",
        profile={},
        reason="verified",
        stage="跟进中",
        next_step="联系",
        due_at="2026-10-10",
    )
    with TestClient(app) as c:
        c.headers.update(headers("other@example.com"))
        assert c.get(f"/api/accounts/{aid}/activity").status_code == 404
        assert c.post(f"/api/accounts/{aid}/progress", json=body).status_code == 403
        assert c.get(f"/api/candidates/{task['id']}/attempts").status_code == 403
        assert (
            c.post(
                f"/api/candidates/{task['id']}/retry", json={"attempt_id": task["attempt_id"]}
            ).status_code
            == 403
        )
        assert (
            c.post(
                f"/api/accounts/{aid}/company", json={"version": 0, "reason": "test"}
            ).status_code
            == 403
        )
        state = c.get("/api/state").json()
        assert state["accounts"] == [] and state["collection"] is None and state["jobs"] == []
        c.headers.update(headers("admin@example.com"))
        assert c.post(f"/api/accounts/{aid}/progress", json=body).status_code == 403
        assert (
            c.post(f"/api/accounts/{aid}/assignment/accept", json={"version": 1}).status_code == 200
        )
        assert c.post(f"/api/accounts/{aid}/progress", json=body).status_code == 200
        assert c.post(f"/api/accounts/{aid}/progress", json=body).status_code == 409
        assert c.get(f"/api/accounts/{aid}/activity").status_code == 200
