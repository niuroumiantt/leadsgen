from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .crawl import normalize_url
from .policy import CADENCE_DAYS, POLICY_VERSION, ROLE_NAMES

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
 id TEXT PRIMARY KEY, domain TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate (
 id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES job(id), url TEXT NOT NULL,
 seed TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', reason TEXT NOT NULL DEFAULT '',
 pages INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, UNIQUE(job_id,url)
);
CREATE TABLE IF NOT EXISTS handoff (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL UNIQUE REFERENCES account(id),
 payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', receipt TEXT,
 attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mail_event (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES account(id),
 payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
 resource TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS integration_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def accounts(self) -> list[dict]:
        with self.connect() as c:
            return [
                json.loads(r[0])
                for r in c.execute("SELECT payload FROM account ORDER BY updated_at DESC")
            ]

    def jobs(self) -> list[dict]:
        with self.connect() as c:
            jobs = [dict(r) for r in c.execute("SELECT * FROM job ORDER BY created_at DESC")]
            for job in jobs:
                job["candidates"] = [
                    dict(r)
                    for r in c.execute(
                        "SELECT id,url,status,reason,pages FROM candidate WHERE job_id=?",
                        (job["id"],),
                    )
                ]
            return jobs

    def create_job(self, name: str, region: str, seeds: list[dict], actor: str) -> str:
        job_id = "job_" + uuid.uuid4().hex
        normalized = [{**s, "url": normalize_url(s["url"]), "region": region} for s in seeds]
        with self.connect() as c:
            c.execute("INSERT INTO job VALUES(?,?,?,?)", (job_id, name, region, now()))
            for seed in normalized:
                c.execute(
                    "INSERT OR IGNORE INTO candidate(job_id,url,seed,updated_at) VALUES(?,?,?,?)",
                    (job_id, seed["url"], json.dumps(seed, ensure_ascii=False), now()),
                )
            self.audit(c, actor, "job.created", job_id)
        return job_id

    def recover(self):
        # One worker per instance. Only called before starting it, never per request.
        with self.connect() as c:
            c.execute(
                "UPDATE candidate SET status='queued',reason='resumed_after_restart' "
                "WHERE status='running'"
            )
            c.execute("UPDATE handoff SET status='queued' WHERE status='delivering'")

    def claim(self) -> dict | None:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM candidate WHERE status='queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            c.execute(
                "UPDATE candidate SET status='running',updated_at=? WHERE id=?", (now(), row["id"])
            )
            return dict(row)

    def finish(self, candidate: dict, result: dict):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if result.get("account"):
                account = result["account"]
                old = c.execute(
                    "SELECT payload FROM account WHERE domain=?", (account["domain"],)
                ).fetchone()
                if old:
                    existing = json.loads(old[0])
                    # Recrawling never resets outreach, approval or a selected recipient.
                    for field in (
                        "id",
                        "stage",
                        "mail",
                        "email",
                        "department",
                        "classificationVerified",
                        "evidence",
                        "mailRoute",
                        "observedAt",
                        "lastMailEvent",
                    ):
                        account[field] = existing.get(field, account.get(field))
                    previous = {v["email"].casefold(): v for v in existing.get("contacts", [])}
                    previous.update({v["email"].casefold(): v for v in account.get("contacts", [])})
                    account["contacts"] = list(previous.values())
                    if existing.get("classificationVerified"):
                        for field in (
                            "tier",
                            "model",
                            "industry",
                            "country",
                            "region",
                            "reason",
                            "supermicro",
                        ):
                            account[field] = existing[field]
                else:
                    account["id"] = "lg_" + uuid.uuid4().hex
                c.execute(
                    "INSERT INTO account VALUES(?,?,?,?) ON CONFLICT(domain) DO UPDATE SET "
                    "payload=excluded.payload,updated_at=excluded.updated_at",
                    (
                        account["id"],
                        account["domain"],
                        json.dumps(account, ensure_ascii=False),
                        now(),
                    ),
                )
            c.execute(
                "UPDATE candidate SET status=?,reason=?,pages=?,updated_at=? WHERE id=?",
                (
                    result["status"],
                    result.get("reason", ""),
                    result.get("pages", 0),
                    now(),
                    candidate["id"],
                ),
            )

    @staticmethod
    def audit(c, actor: str, action: str, resource: str):
        c.execute(
            "INSERT INTO audit(actor,action,resource,created_at) VALUES(?,?,?,?)",
            (actor, action, resource, now()),
        )

    def enqueue(self, ids: list[str], actor: str) -> dict:
        added, skipped = [], []
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            for account_id in dict.fromkeys(ids):
                row = c.execute("SELECT payload FROM account WHERE id=?", (account_id,)).fetchone()
                if not row:
                    raise ValueError("客户不存在")
                a = json.loads(row[0])
                if a["stage"] != "ready":
                    skipped.append(account_id)
                    continue
                handoff_id = "ho_" + uuid.uuid4().hex
                payload = {
                    "schema_version": "1",
                    "external_id": account_id,
                    "idempotency_key": a["domain"] + ":initial-outreach",
                    "company": a["name"],
                    "website": a["website"],
                    "email": a["email"],
                    "country": a["country"],
                    "tier": a["tier"],
                    "source": a["evidence"],
                    "cadence_days": list(CADENCE_DAYS),
                    "policy_version": POLICY_VERSION,
                }
                c.execute(
                    "INSERT INTO handoff(id,account_id,payload,next_at) VALUES(?,?,?,?)",
                    (handoff_id, account_id, json.dumps(payload, ensure_ascii=False), now()),
                )
                a["stage"] = "queued"
                c.execute(
                    "UPDATE account SET payload=?,updated_at=? WHERE id=?",
                    (json.dumps(a, ensure_ascii=False), now(), account_id),
                )
                self.audit(c, actor, "handoff.queued", account_id)
                added.append(account_id)
        return {"queued": added, "skipped": skipped}

    def handoffs(self) -> list[dict]:
        with self.connect() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT id,account_id,status,receipt,attempts,last_error FROM handoff "
                    "ORDER BY rowid DESC"
                )
            ]

    def suppress(self, account_id: str, actor: str):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT payload FROM account WHERE id=?", (account_id,)).fetchone()
            if not r:
                raise ValueError("客户不存在")
            a = json.loads(r[0])
            a["stage"] = "suppressed"
            c.execute(
                "UPDATE account SET payload=?,updated_at=? WHERE id=?",
                (json.dumps(a, ensure_ascii=False), now(), account_id),
            )
            c.execute(
                "UPDATE handoff SET status='cancelled' WHERE account_id=? AND status='queued'",
                (account_id,),
            )
            c.execute(
                "UPDATE handoff SET status='stop_pending' WHERE account_id=? AND status='accepted'",
                (account_id,),
            )
            self.audit(c, actor, "account.suppressed", account_id)

    def mail_cursor(self) -> int:
        with self.connect() as c:
            row = c.execute(
                "SELECT value FROM integration_state WHERE key='mail_cursor'"
            ).fetchone()
            return int(row[0]) if row else 0

    def select_contact(self, account_id: str, email: str, actor: str):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT payload FROM account WHERE id=?", (account_id,)).fetchone()
            if not row:
                raise ValueError("客户不存在")
            a = json.loads(row[0])
            if a["stage"] not in {"ready", "review"}:
                raise ValueError("已交接或停止的客户不能更换收件人")
            contact = next(
                (v for v in a.get("contacts", []) if v["email"].casefold() == email.casefold()),
                None,
            )
            if not contact or contact.get("mailRoute") not in {
                "mx_present",
                "implicit_mx_unverified",
            }:
                raise ValueError("只能选有公开来源且具备邮件路由的地址")
            a.update(
                email=contact["email"],
                evidence=contact,
                department=ROLE_NAMES[contact["role"]],
                stage="ready",
                mailRoute=contact["mailRoute"],
                observedAt=contact["observedAt"],
            )
            c.execute(
                "UPDATE account SET payload=?,updated_at=? WHERE id=?",
                (json.dumps(a, ensure_ascii=False), now(), account_id),
            )
            self.audit(c, actor, "contact.reviewed", account_id)

    def receive_events(self, payload: dict):
        mapping = {
            "approved": "draft",
            "sending": "draft",
            "smtp_accepted": "accepted",
            "replied": "replied",
            "unsubscribed": "unsubscribed",
            "bounced": "bounced",
            "delivery_unknown": "unknown",
            "paused": "paused",
        }
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            for e in payload["events"]:
                row = c.execute(
                    "SELECT payload FROM account WHERE id=?", (e["external_id"],)
                ).fetchone()
                if not row:
                    continue
                exists = c.execute(
                    "SELECT 1 FROM mail_event WHERE id=?", (str(e["id"]),)
                ).fetchone()
                if exists:
                    continue
                c.execute(
                    "INSERT INTO mail_event VALUES(?,?,?,?)",
                    (
                        str(e["id"]),
                        e["external_id"],
                        json.dumps(e),
                        e["occurred_at"],
                    ),
                )
                a = json.loads(row[0])
                if a["mail"] not in {"unsubscribed", "bounced", "replied"}:
                    a["mail"] = mapping.get(e["type"], a["mail"])
                if e["type"] == "unsubscribed":
                    a["mail"] = "unsubscribed"
                if e["type"] in {"unsubscribed", "bounced"}:
                    a["stage"] = "suppressed"
                a["lastMailEvent"] = e
                c.execute(
                    "UPDATE account SET payload=?,updated_at=? WHERE id=?",
                    (
                        json.dumps(a, ensure_ascii=False),
                        now(),
                        a["id"],
                    ),
                )
            c.execute(
                "INSERT INTO integration_state VALUES('mail_cursor',?) ON CONFLICT(key) "
                "DO UPDATE SET value=excluded.value",
                (str(payload["next_cursor"]),),
            )
