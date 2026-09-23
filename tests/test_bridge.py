"""Optional cross-repo test: set PYTHONPATH to the candidate mail server source."""

from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from leadsgen.app import deliver_one, sync_mail
from test_store_api import seeded

mail = pytest.importorskip("mail2leads.outreach", reason="mail server candidate not on PYTHONPATH")
from mail2leads.api.app import create_app  # noqa: E402
from mail2leads.store.db import connect  # noqa: E402
from mail2leads.store.repo import ensure_mailbox  # noqa: E402


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
