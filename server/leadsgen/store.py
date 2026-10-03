from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .collection import CollectionStore
from .crawl import normalize_url
from .discovery import DiscoveryStore
from .policy import CADENCE_DAYS, POLICY_VERSION, ROLE_NAMES
from .workflow import WorkflowStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
 id TEXT PRIMARY KEY, domain TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_assignment (
 account_id TEXT PRIMARY KEY REFERENCES account(id), owner TEXT NOT NULL DEFAULT '',
 pending TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0,
 updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assignment_notice (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES account(id), version INTEGER NOT NULL,
 payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', next_at TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS assignment_event (
 id INTEGER PRIMARY KEY, account_id TEXT NOT NULL REFERENCES account(id),
 actor TEXT NOT NULL, action TEXT NOT NULL, recipient TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_followup (
 account_id TEXT PRIMARY KEY REFERENCES account(id),
 stage TEXT NOT NULL CHECK(stage IN ('待联系','跟进中','等待客户','等待内部','稍后跟进','结束')),
 next_step TEXT NOT NULL DEFAULT '', due_at TEXT NOT NULL DEFAULT '',
 updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS aimail_lead_map (
 external_id TEXT PRIMARY KEY, account_id TEXT NOT NULL UNIQUE REFERENCES account(id),
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS followup_access_sync (
 account_id TEXT PRIMARY KEY REFERENCES account(id), recipient TEXT NOT NULL,
 thread_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL, next_at TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT '',
 assignment_version INTEGER NOT NULL DEFAULT 0
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store(WorkflowStore, CollectionStore, DiscoveryStore):
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self.migrate_followup_access(conn)
            self.migrate_workflow(conn)
            self.migrate_collection(conn)
            self.migrate_discovery(conn)
        path.chmod(0o600)

    @staticmethod
    def migrate_followup_access(conn):
        """Re-establish versioned remote access once for an existing queue."""
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if "assignment_version" in {
                row["name"] for row in conn.execute("PRAGMA table_info(followup_access_sync)")
            }:
                return
            conn.execute(
                "ALTER TABLE followup_access_sync "
                "ADD COLUMN assignment_version INTEGER NOT NULL DEFAULT 0"
            )
            conn.execute(
                "UPDATE followup_access_sync SET assignment_version=COALESCE("
                "(SELECT MAX(e.version) FROM assignment_event e "
                "WHERE e.account_id=followup_access_sync.account_id AND e.action='accepted' "
                "AND e.recipient=followup_access_sync.recipient),"
                "(SELECT a.version FROM account_assignment a "
                "WHERE a.account_id=followup_access_sync.account_id "
                "AND a.owner=followup_access_sync.recipient AND a.pending=''),0),"
                "state='queued',attempts=0,next_at=?,last_error=''",
                (now(),),
            )

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
            rows = c.execute("SELECT id,payload FROM account ORDER BY updated_at DESC").fetchall()
            result = []
            for row in rows:
                account = json.loads(row["payload"])
                assignment = c.execute(
                    "SELECT owner,pending,version,updated_by,updated_at FROM account_assignment "
                    "WHERE account_id=?",
                    (row["id"],),
                ).fetchone()
                mail_access = c.execute(
                    "SELECT state,last_error FROM followup_access_sync WHERE account_id=?",
                    (row["id"],),
                ).fetchone()
                followup = c.execute(
                    "SELECT stage,next_step,due_at,updated_by,updated_at FROM account_followup "
                    "WHERE account_id=?",
                    (row["id"],),
                ).fetchone()
                account["assignment"] = (
                    dict(assignment)
                    if assignment
                    else {
                        "owner": "",
                        "pending": "",
                        "version": 0,
                        "updated_by": "",
                        "updated_at": "",
                    }
                )
                events = [
                    dict(event)
                    for event in c.execute(
                        "SELECT actor,action,recipient,reason,version,created_at FROM "
                        "assignment_event "
                        "WHERE account_id=? ORDER BY id DESC",
                        (row["id"],),
                    )
                ]
                account["assignment"]["history"] = events
                notice = c.execute(
                    "SELECT state,last_error FROM assignment_notice WHERE account_id=? "
                    "ORDER BY version DESC LIMIT 1",
                    (row["id"],),
                ).fetchone()
                account["assignment"]["notification"] = (
                    dict(notice) if notice else {"state": "not_requested", "last_error": ""}
                )
                account["assignment"]["status"] = (
                    "pending"
                    if account["assignment"]["pending"]
                    else "returned"
                    if events and events[0]["action"] == "declined"
                    else "accepted"
                    if account["assignment"]["owner"]
                    else "unassigned"
                )
                account["followup"] = dict(followup) if followup else None
                account["workflow"] = self.workflow(c, row["id"])
                company = c.execute(
                    "SELECT company_id,version FROM account_company WHERE account_id=?",
                    (row["id"],),
                ).fetchone()
                account["company"] = dict(company) if company else None
                account["assignment"]["mail_access_status"] = (
                    mail_access["state"] if mail_access else "not_required"
                )
                account["assignment"]["mail_access_error"] = (
                    mail_access["last_error"] if mail_access else ""
                )
                result.append(account)
            return result

    def import_mail_handoffs(self, items: list[dict]) -> None:
        """Project existing Aimail handoffs; Aimail remains their decision authority."""
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            for item in items:
                if (
                    type(item.get("thread_id")) is not int
                    or item["thread_id"] < 1
                    or type(item.get("version")) is not int
                    or item["version"] < 1
                    or not isinstance(item.get("owner"), str)
                    or not isinstance(item.get("pending"), str)
                ):
                    raise ValueError("invalid_mail_handoff")
                tid = str(item["thread_id"])
                existing = [
                    r
                    for r in c.execute("SELECT id,payload FROM account")
                    if str(json.loads(r["payload"]).get("aimailThreadId", "")) == tid
                ]
                if not existing:
                    aid = "handoff_" + tid
                    account = dict(
                        id=aid,
                        name=item.get("subject", "邮件交接"),
                        legal="",
                        domain=aid + ".invalid",
                        website="",
                        region="其他",
                        country="待核验",
                        industry="待核验",
                        tier="2B",
                        model="邮件交接",
                        email=item.get("email", ""),
                        department=item.get("mailbox", "邮件") + " 来信",
                        supermicro="待核实",
                        products=[],
                        stage="ready",
                        mail="replied",
                        score=0,
                        summary=item.get("summary", ""),
                        reason="来自已提交的邮件交接，客户信息待核实",
                        contacts=[],
                        evidence=dict(
                            email=item.get("email", ""),
                            role="business",
                            url="",
                            excerpt="原邮件交接",
                            observedAt=item["updated_at"],
                            mailRoute="inbound",
                        ),
                        mailRoute="inbound",
                        observedAt=item["updated_at"],
                        classificationVerified=False,
                        sourceType="sales_inbound",
                        sourceMailbox=item.get("mailbox", ""),
                        aimailThreadId=tid,
                    )
                    c.execute(
                        "INSERT INTO account VALUES(?,?,?,?)",
                        (aid, account["domain"], json.dumps(account), now()),
                    )
                    existing = [{"id": aid, "payload": json.dumps(account)}]
                for row in existing:
                    account = json.loads(row["payload"])
                    if (
                        account.get("assignmentAuthority") != "aimail"
                        and c.execute(
                            "SELECT 1 FROM account_assignment WHERE account_id=?", (row["id"],)
                        ).fetchone()
                    ):
                        # Preserve the Leadsgen assignment behind its access grant.
                        continue
                    previous = c.execute(
                        "SELECT version FROM account_assignment WHERE account_id=?", (row["id"],)
                    ).fetchone()
                    if previous and previous["version"] > item["version"]:
                        continue
                    if item.get("mailbox") and account.get("sourceMailbox") != item["mailbox"]:
                        account["sourceMailbox"] = item["mailbox"]
                        account["department"] = item["mailbox"] + " 来信"
                    # 同版本也修复旧同步器留下的摘要；较旧版本不能覆盖当前快照。
                    account["summary"] = item.get("summary", "")
                    account["assignmentAuthority"] = "aimail"
                    if account != json.loads(row["payload"]):
                        c.execute(
                            "UPDATE account SET payload=?,updated_at=? WHERE id=?",
                            (json.dumps(account), now(), row["id"]),
                        )
                    if previous and previous["version"] == item["version"]:
                        continue
                    c.execute(
                        "INSERT INTO account_assignment VALUES(?,?,?,?,?,?) ON "
                        "CONFLICT(account_id) DO UPDATE SET "
                        "owner=excluded.owner,pending=excluded.pending,version=excluded.version,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                        (
                            row["id"],
                            item["owner"],
                            item["pending"],
                            item["version"],
                            "aimail",
                            item["updated_at"],
                        ),
                    )
                    c.execute("DELETE FROM assignment_event WHERE account_id=?", (row["id"],))
                    for event in item.get("history", []):
                        c.execute(
                            "INSERT INTO "
                            "assignment_event(account_id,actor,action,recipient,"
                            "version,created_at,reason) "
                            "VALUES(?,?,?,?,?,?,?)",
                            (
                                row["id"],
                                event["actor"],
                                {
                                    "offer": "offered",
                                    "accept": "accepted",
                                    "cancel": "cancelled",
                                    "decline": "declined",
                                }.get(event["action"], event["action"]),
                                event["actor"]
                                if event["action"] == "accept"
                                else event.get("recipient", ""),
                                event["version"],
                                event["at"],
                                event.get("reason", ""),
                            ),
                        )

                for row in existing:
                    for event in c.execute(
                        "SELECT * FROM assignment_event WHERE account_id=?", (row["id"],)
                    ).fetchall():
                        self.assignment_activity(c, dict(event))

                # Only new offers explicitly request notification. Historical imports never send.
                offered = next(
                    (
                        e
                        for e in item.get("history", [])
                        if e["version"] == item["version"]
                        and e.get("notify")
                        and e["action"] == "offer"
                    ),
                    None,
                )
                if offered and item["pending"]:
                    for row in existing:
                        record = c.execute(
                            "SELECT payload FROM account WHERE id=?", (row["id"],)
                        ).fetchone()
                        if json.loads(record[0]).get("assignmentAuthority") != "aimail":
                            continue
                        notice = dict(
                            account_id=row["id"],
                            version=item["version"],
                            actor=offered["actor"],
                            recipient=item["pending"],
                            subject=item.get("subject", "邮件交接")[:200],
                            summary=item.get("summary", "")[:4000],
                            thread_id=item["thread_id"],
                        )
                        c.execute(
                            "INSERT OR IGNORE INTO assignment_notice"
                            "(id,account_id,version,payload,next_at) VALUES(?,?,?,?,?)",
                            (
                                f"{row['id']}:{item['version']}",
                                row["id"],
                                item["version"],
                                json.dumps(notice),
                                now(),
                            ),
                        )
                        break  # Several confirmed leads may reference one mail conversation.

    @staticmethod
    def require_local_assignment(c, account_id):
        row = c.execute("SELECT payload FROM account WHERE id=?", (account_id,)).fetchone()
        if row and json.loads(row[0]).get("assignmentAuthority") == "aimail":
            raise ValueError("这条已有邮件交接请从原邮件入口操作，避免重复交接")

    def assigned_accounts(self, identity: str) -> list[dict]:
        return [
            account
            for account in self.accounts()
            if identity in {account["assignment"]["owner"], account["assignment"]["pending"]}
        ]

    def assign(self, account_id: str, recipient: str, actor: str, version: int) -> dict:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self.require_local_assignment(c, account_id)
            if not c.execute("SELECT 1 FROM account WHERE id=?", (account_id,)).fetchone():
                raise LookupError("客户不存在")
            current = c.execute(
                "SELECT owner,pending,version FROM account_assignment WHERE account_id=?",
                (account_id,),
            ).fetchone()
            owner = current["owner"] if current else ""
            pending = current["pending"] if current else ""
            current_version = current["version"] if current else 0
            if current_version != version or pending:
                raise ValueError("线索状态已变化，请刷新后重试")
            c.execute(
                "INSERT INTO account_assignment(account_id,owner,pending,version,"
                "updated_by,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(account_id) DO UPDATE SET pending=excluded.pending,"
                "version=excluded.version,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                (account_id, owner, recipient, current_version + 1, actor, now()),
            )
            self.assignment_event(c, account_id, actor, "offered", recipient, current_version + 1)
            payload = json.loads(
                c.execute("SELECT payload FROM account WHERE id=?", (account_id,)).fetchone()[0]
            )
            notice = dict(
                account_id=account_id,
                version=current_version + 1,
                actor=actor,
                recipient=recipient,
                subject=payload.get("name", "客户线索")[:200],
                summary=(
                    payload.get("summary")
                    or f"客户：{payload.get('name', '待核实')}\n"
                    f"联系方式：{payload.get('email', '待核实')}\n"
                    "具体需求待联系核实。"
                )[:4000],
                thread_id=int(payload["aimailThreadId"]) if payload.get("aimailThreadId") else None,
            )
            c.execute(
                "INSERT INTO assignment_notice(id,account_id,version,payload,next_at) "
                "VALUES(?,?,?,?,?)",
                (
                    f"{account_id}:{current_version + 1}",
                    account_id,
                    current_version + 1,
                    json.dumps(notice),
                    now(),
                ),
            )
            self.audit(c, actor, "assignment.offered", account_id)
            return {"owner": owner, "pending": recipient, "version": current_version + 1}

    def accept_assignment(self, account_id: str, actor: str, version: int) -> dict:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self.require_local_assignment(c, account_id)
            current = c.execute(
                "SELECT owner,pending,version FROM account_assignment WHERE account_id=?",
                (account_id,),
            ).fetchone()
            if not current:
                raise LookupError("没有待接手的线索")
            if current["pending"] != actor:
                raise PermissionError("这条线索没有分配给你")
            if current["version"] != version:
                raise ValueError("线索状态已变化，请刷新后重试")
            new_version = version + 1
            c.execute(
                "UPDATE account_assignment SET owner=?,pending='',version=?,"
                "updated_by=?,updated_at=? "
                "WHERE account_id=?",
                (actor, new_version, actor, now(), account_id),
            )
            c.execute(
                "INSERT INTO account_followup(account_id,stage,updated_by,updated_at)"
                " VALUES(?,?,?,?) "
                "ON CONFLICT(account_id) DO NOTHING",
                (account_id, "待联系", actor, now()),
            )
            account_row = c.execute(
                "SELECT payload FROM account WHERE id=?", (account_id,)
            ).fetchone()
            account = json.loads(account_row[0])
            if account.get("sourceType") == "sales_inbound" and account.get("aimailThreadId"):
                c.execute(
                    "INSERT INTO followup_access_sync "
                    "(account_id,recipient,thread_id,updated_at,next_at,assignment_version) "
                    "VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(account_id) DO UPDATE SET recipient=excluded.recipient,"
                    "thread_id=excluded.thread_id,state='queued',updated_at=excluded.updated_at,"
                    "next_at=excluded.next_at,assignment_version=excluded.assignment_version,"
                    "attempts=0,"
                    "last_error=''",
                    (account_id, actor, account["aimailThreadId"], now(), now(), new_version),
                )
            self.assignment_event(c, account_id, actor, "accepted", actor, new_version)
            self.audit(c, actor, "assignment.accepted", account_id)
            return {"owner": actor, "pending": "", "version": new_version}

    def decline_assignment(
        self, account_id: str, actor: str, version: int, reason: str = ""
    ) -> dict:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self.require_local_assignment(c, account_id)
            current = c.execute(
                "SELECT owner,pending,version FROM account_assignment WHERE account_id=?",
                (account_id,),
            ).fetchone()
            if not current:
                raise LookupError("没有待接手的线索")
            if current["pending"] != actor:
                raise PermissionError("这条线索没有分配给你")
            if current["version"] != version:
                raise ValueError("线索状态已变化，请刷新后重试")
            new_version = version + 1
            c.execute(
                "UPDATE account_assignment SET pending='',version=?,updated_by=?,updated_at=? "
                "WHERE account_id=?",
                (new_version, actor, now(), account_id),
            )
            self.assignment_event(c, account_id, actor, "declined", actor, new_version, reason)
            self.audit(c, actor, "assignment.declined", account_id)
            return {"owner": current["owner"], "pending": "", "version": new_version}

    @staticmethod
    def assignment_event(c, account_id, actor, action, recipient, version, reason=""):
        c.execute(
            "INSERT INTO "
            "assignment_event(account_id,actor,action,recipient,reason,version,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (account_id, actor, action, recipient, reason.strip(), version, now()),
        )
        WorkflowStore.assignment_activity(
            c,
            {
                "account_id": account_id,
                "actor": actor,
                "action": action,
                "recipient": recipient,
                "version": version,
                "reason": reason.strip(),
                "created_at": now(),
            },
        )

    def cancel_assignment(self, account_id: str, actor: str, version: int) -> dict:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self.require_local_assignment(c, account_id)
            current = c.execute(
                "SELECT * FROM account_assignment WHERE account_id=?", (account_id,)
            ).fetchone()
            if not current or not current["pending"] or current["version"] != version:
                raise ValueError("交接状态已变化，请刷新后重试")
            c.execute(
                "UPDATE account_assignment SET "
                "pending='',version=version+1,updated_by=?,updated_at=? WHERE "
                "account_id=?",
                (actor, now(), account_id),
            )
            self.assignment_event(
                c, account_id, actor, "cancelled", current["pending"], version + 1
            )
            self.audit(c, actor, "assignment.cancelled", account_id)
            return {"owner": current["owner"], "pending": "", "version": version + 1}

    def update_followup(
        self, account_id: str, actor: str, stage: str, next_step: str, due_at: str
    ) -> dict:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            return self.save_followup(c, account_id, actor, stage, next_step, due_at)

    def jobs(self) -> list[dict]:
        with self.connect() as c:
            jobs = [dict(r) for r in c.execute("SELECT * FROM job ORDER BY created_at DESC")]
            for job in jobs:
                job["candidates"] = [
                    dict(r)
                    for r in c.execute(
                        "SELECT id,url,status,reason,pages,updated_at FROM candidate WHERE "
                        "job_id=?",
                        (job["id"],),
                    )
                ]
                for candidate in job["candidates"]:
                    attempt = c.execute(
                        "SELECT id,number,account_id,result_action,legacy FROM crawl_attempt "
                        "WHERE candidate_id=? ORDER BY number DESC LIMIT 1",
                        (candidate["id"],),
                    ).fetchone()
                    candidate["lastAttempt"] = dict(attempt) if attempt else None
                    due = c.execute(
                        "SELECT next_at FROM crawl_retry WHERE candidate_id=?", (candidate["id"],)
                    ).fetchone()
                    candidate["nextAt"] = due["next_at"] if due else None
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
            for candidate in c.execute(
                "SELECT id,url FROM candidate WHERE job_id=?", (job_id,)
            ).fetchall():
                self.register_site(c, candidate["id"], candidate["url"])
            self.audit(c, actor, "job.created", job_id)
        return job_id

    def recover(self):
        # One worker per instance. Only called before starting it, never per request.
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE crawl_attempt SET "
                "status='interrupted',reason='resumed_after_restart',finished_at=? "
                "WHERE status='running'",
                (now(),),
            )
            c.execute(
                "UPDATE candidate SET status='queued',reason='resumed_after_restart' "
                "WHERE status='running'"
            )
            c.execute("UPDATE handoff SET status='queued' WHERE status='delivering'")

    def finish(self, candidate: dict, result: dict):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if not self.active_attempt(c, candidate):
                return  # Duplicate or interrupted completions cannot overwrite a newer attempt.
            account_id, action = None, ""
            if result.get("account"):
                account = result["account"]
                old = c.execute(
                    "SELECT payload FROM account WHERE domain=?", (account["domain"],)
                ).fetchone()
                action = "updated" if old else "created"
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
                account_id = account["id"]
                self.activity(
                    c,
                    account_id,
                    "discovery.completed",
                    "crawler",
                    {
                        "candidateId": candidate["id"],
                        "attemptId": candidate["attempt_id"],
                        "result": action,
                        "url": candidate["url"],
                    },
                    key=f"crawl:{candidate['attempt_id']}",
                )
            self.finish_attempt(c, candidate, result, account_id, action)
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
                if a["stage"] != "ready" or a.get("sourceType") == "sales_inbound":
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

    def confirmed_lead_cursor(self) -> tuple[str, int]:
        with self.connect() as c:
            rows = dict(
                c.execute(
                    "SELECT key,value FROM integration_state "
                    "WHERE key IN ('aimail_lead_since','aimail_lead_after')"
                ).fetchall()
            )
        return rows.get("aimail_lead_since", ""), int(rows.get("aimail_lead_after", "0"))

    def pending_followup_grants(self) -> list[dict]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT account_id,recipient,thread_id,assignment_version,attempts "
                "FROM followup_access_sync "
                "WHERE state='queued' AND next_at<=? ORDER BY updated_at LIMIT 50",
                (now(),),
            ).fetchall()
            return [dict(row) for row in rows]

    def mark_followup_grant(self, grant: dict, *, success: bool, attempts: int, error: str = ""):
        """Acknowledge only the queued request that actually received this response."""
        with self.connect() as c:
            c.execute(
                "UPDATE followup_access_sync "
                "SET state=?,attempts=?,updated_at=?,next_at=?,last_error=? "
                "WHERE account_id=? AND recipient=? AND thread_id=? "
                "AND assignment_version=? AND state='queued'",
                (
                    "granted" if success else "queued",
                    attempts,
                    now(),
                    now()
                    if success
                    else (
                        datetime.now(UTC) + timedelta(seconds=min(3600, 30 * 2 ** min(attempts, 7)))
                    ).isoformat(),
                    "" if success else error,
                    grant["account_id"],
                    grant["recipient"],
                    grant["thread_id"],
                    grant["assignment_version"],
                ),
            )

    def import_confirmed_leads(self, items: list[dict], next_since: str, next_after: int) -> int:
        """Import only Aimail's human-confirmed sales leads; never copy message bodies."""
        imported = 0
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            for item in items:
                source = item.get("source") or {}
                external_id = str(item.get("id", "")).strip()
                if (
                    item.get("version") != "lead@1"
                    or not external_id
                    or source.get("mailbox", "").casefold() != "sales@glocalstorage.com"
                ):
                    raise ValueError("invalid_confirmed_mail_lead")
                exists = c.execute(
                    "SELECT a.id,a.payload FROM account a JOIN aimail_lead_map m "
                    "ON m.account_id=a.id WHERE m.external_id=?",
                    (external_id,),
                ).fetchone()
                tid = str(source.get("thread_id", ""))
                projected = next(
                    (
                        r
                        for r in ([] if exists else c.execute("SELECT id,payload FROM account"))
                        if json.loads(r["payload"]).get("assignmentAuthority") == "aimail"
                        and str(json.loads(r["payload"]).get("aimailThreadId")) == tid
                        and not c.execute(
                            "SELECT 1 FROM aimail_lead_map WHERE account_id=?", (r["id"],)
                        ).fetchone()
                    ),
                    None,
                )
                digest = hashlib.sha256(external_id.encode()).hexdigest()[:24]
                account_id = "mail_" + digest
                thread_id = str(source.get("thread_id", ""))[:80]
                email = str(item.get("email", ""))[:254].strip()
                company = str(item.get("company", ""))[:200].strip() or "待核实公司"
                wants = str(item.get("wants", ""))[:1000].strip()
                contact = str(item.get("contact", ""))[:200].strip()
                region = str(item.get("region", "")).strip()
                if region not in {"美国", "欧洲", "中国", "东南亚", "其他"}:
                    region = "其他"
                account = {
                    "id": account_id,
                    "name": company,
                    "legal": "",
                    "domain": "mail-lead-" + digest + ".invalid",
                    "website": "",
                    "region": region,
                    "country": region if region != "其他" else "待核验",
                    "industry": "待核验",
                    "tier": "2B",
                    "model": "来信询盘",
                    "email": email,
                    "department": "sales@ 来信",
                    "supermicro": "待核实",
                    "products": [],
                    "stage": "ready",
                    "mail": "replied",
                    "score": 0,
                    "summary": wants or "已由 Aimail 用户确认的客户来信；请查看原邮件线程。",
                    "reason": "来源：sales@ 客户来信；邮件原文、附件和线程均留在 Aimail。",
                    "contacts": [],
                    "evidence": {
                        "email": email,
                        "role": "business",
                        "url": "",
                        "excerpt": "客户通过 sales@ 发来询盘；请在 Aimail 查看原线程。",
                        "observedAt": str(item.get("confirmed_at", ""))[:80],
                        "mailRoute": "inbound",
                    },
                    "mailRoute": "inbound",
                    "observedAt": str(item.get("confirmed_at", ""))[:80],
                    "classificationVerified": True,
                    "sourceType": "sales_inbound",
                    "sourceMailbox": "sales@glocalstorage.com",
                    "aimailLeadId": external_id,
                    "aimailThreadId": thread_id,
                    "aimailContact": contact,
                    "aimailQuantity": str(item.get("quantity", ""))[:200],
                }
                target = exists or projected
                if target:
                    current = json.loads(target["payload"])
                    # 只刷新 Aimail 提供的客户资料；本地阶段、分类、负责人和跟进另有权威。
                    fields = {
                        "company": "name",
                        "email": "email",
                        "contact": "aimailContact",
                        "quantity": "aimailQuantity",
                        "wants": "summary",
                    }
                    for source_key, key in fields.items():
                        if source_key not in item:
                            continue
                        if source_key == "wants" and current.get("assignmentAuthority") == "aimail":
                            # 邮件交接中经人审的摘要比初次询盘更完整。
                            continue
                        current[key] = account[key]
                    current["aimailLeadId"] = external_id
                    current["sourceMailbox"] = account["sourceMailbox"]
                    if "email" in item:
                        current.setdefault("evidence", {})["email"] = email
                    if current != json.loads(target["payload"]):
                        c.execute(
                            "UPDATE account SET payload=?,updated_at=? WHERE id=?",
                            (json.dumps(current, ensure_ascii=False), now(), target["id"]),
                        )
                    c.execute(
                        "INSERT INTO aimail_lead_map VALUES(?,?,?) ON CONFLICT(external_id) "
                        "DO UPDATE SET updated_at=excluded.updated_at",
                        (external_id, target["id"], now()),
                    )
                    continue
                c.execute(
                    "INSERT INTO account(id,domain,payload,updated_at) VALUES(?,?,?,?)",
                    (account_id, account["domain"], json.dumps(account, ensure_ascii=False), now()),
                )
                c.execute(
                    "INSERT INTO aimail_lead_map(external_id,account_id,updated_at) VALUES(?,?,?)",
                    (external_id, account_id, now()),
                )
                self.audit(c, "aimail-integration", "lead.imported", account_id)
                imported += 1
            c.executemany(
                "INSERT INTO integration_state(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (("aimail_lead_since", next_since), ("aimail_lead_after", str(next_after))),
            )
        return imported

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
                self.activity(
                    c,
                    e["external_id"],
                    "mail.event",
                    "aimail",
                    e,
                    key="mail:" + str(e["id"]),
                    at=e["occurred_at"],
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
