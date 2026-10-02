"""Durable crawl attempts and operator-requested retries; never discover or send implicitly."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from .workflow import VersionConflict

RETRYABLE = {
    "network_or_tls_error",
    "network_error",
    "dns_error",
    "request_timeout",
    "robots_unavailable",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS crawl_attempt (
 id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL REFERENCES candidate(id),
 number INTEGER NOT NULL, started_at TEXT, finished_at TEXT, status TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '', pages INTEGER NOT NULL DEFAULT 0,
 account_id TEXT REFERENCES account(id), result_action TEXT NOT NULL DEFAULT '',
 legacy INTEGER NOT NULL DEFAULT 0, UNIQUE(candidate_id,number)
);
CREATE TABLE IF NOT EXISTS crawl_observation (
 id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL REFERENCES crawl_attempt(id),
 at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS observation_no_update BEFORE UPDATE ON crawl_observation
 BEGIN SELECT RAISE(ABORT, 'observations are append only'); END;
CREATE TRIGGER IF NOT EXISTS observation_no_delete BEFORE DELETE ON crawl_observation
 BEGIN SELECT RAISE(ABORT, 'observations are append only'); END;
CREATE TABLE IF NOT EXISTS crawl_retry (
 candidate_id INTEGER PRIMARY KEY REFERENCES candidate(id), next_at TEXT NOT NULL,
 actor TEXT NOT NULL, requested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_status (
 name TEXT PRIMARY KEY, phase TEXT NOT NULL, seen_at TEXT NOT NULL, candidate_id INTEGER,
 error TEXT NOT NULL DEFAULT ''
);
"""


def timestamp():
    return datetime.now(UTC).isoformat()


class CollectionStore:
    @staticmethod
    def migrate_collection(c):
        c.executescript(SCHEMA)
        c.execute("BEGIN IMMEDIATE")
        # Old runs have only a final snapshot, never fabricate start times or page logs.
        c.execute(
            "INSERT INTO crawl_attempt(candidate_id,number,finished_at,status,reason,pages,legacy) "
            "SELECT id,0,updated_at,status,reason,pages,1 FROM candidate WHERE status!='queued' "
            "AND NOT EXISTS(SELECT 1 FROM crawl_attempt a WHERE a.candidate_id=candidate.id)"
        )
        c.commit()

    def worker_heartbeat(self, name, phase, candidate_id=None, error=""):
        with self.connect() as c:
            c.execute(
                "INSERT INTO worker_status VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "phase=excluded.phase,seen_at=excluded.seen_at,candidate_id=excluded.candidate_id,"
                "error=excluded.error",
                (name, phase, timestamp(), candidate_id, error),
            )

    def collection_status(self):
        with self.connect() as c:
            c.execute("BEGIN")
            workers = [dict(r) for r in c.execute("SELECT * FROM worker_status")]
            counts = dict(
                c.execute("SELECT status,count(*) FROM candidate GROUP BY status").fetchall()
            )
            outcomes = dict(
                c.execute(
                    "SELECT result_action,count(*) FROM crawl_attempt "
                    "WHERE legacy=0 AND status='completed' GROUP BY result_action"
                ).fetchall()
            )
            last = c.execute("SELECT max(updated_at) FROM candidate").fetchone()[0]
            return {
                "workers": workers,
                "counts": counts,
                "outcomes": outcomes,
                "lastCandidateUpdate": last,
                "discoverySchedule": "manual",
                "siteDelaySeconds": 3,
                "idlePollSeconds": 5,
                "companyRecords": c.execute(
                    "SELECT count(DISTINCT company_id) FROM account_company"
                ).fetchone()[0],
            }

    def claim(self):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT c.* FROM candidate c LEFT JOIN crawl_retry r ON r.candidate_id=c.id "
                "WHERE c.status='queued' AND (r.next_at IS NULL OR r.next_at<=?) ORDER BY "
                "c.id LIMIT 1",
                (timestamp(),),
            ).fetchone()
            if not row:
                return None
            at = timestamp()
            number = c.execute(
                "SELECT coalesce(max(number),0)+1 FROM crawl_attempt WHERE candidate_id=?",
                (row["id"],),
            ).fetchone()[0]
            attempt = c.execute(
                "INSERT INTO crawl_attempt(candidate_id,number,started_at,status) "
                "VALUES(?,?,?,'running')",
                (row["id"], number, at),
            ).lastrowid
            c.execute(
                "UPDATE candidate SET status='running',updated_at=? WHERE id=?", (at, row["id"])
            )
            c.execute("DELETE FROM crawl_retry WHERE candidate_id=?", (row["id"],))
            return {**dict(row), "attempt_id": attempt, "attempt_number": number}

    def observe(self, candidate, observation):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute(
                "SELECT 1 FROM crawl_attempt WHERE id=? AND candidate_id=? AND status='running'",
                (candidate["attempt_id"], candidate["id"]),
            ).fetchone():
                return
            count = c.execute(
                "SELECT count(*) FROM crawl_observation WHERE attempt_id=?",
                (candidate["attempt_id"],),
            ).fetchone()[0]
            if count >= 100:
                return
            safe = {
                key: observation[key]
                for key in ("phase", "url", "status", "reason", "pages", "contacts", "delaySeconds")
                if key in observation
            }
            c.execute(
                "INSERT INTO crawl_observation(attempt_id,at,payload) VALUES(?,?,?)",
                (candidate["attempt_id"], timestamp(), json.dumps(safe, ensure_ascii=False)),
            )

    @staticmethod
    def active_attempt(c, candidate):
        return c.execute(
            "SELECT * FROM crawl_attempt WHERE id=? AND candidate_id=? AND status='running'",
            (candidate.get("attempt_id"), candidate["id"]),
        ).fetchone()

    @staticmethod
    def finish_attempt(c, candidate, result, account_id, action):
        pages = max(
            result.get("pages", 0),
            c.execute(
                "SELECT coalesce(max(json_extract(payload,'$.pages')),0) FROM "
                "crawl_observation WHERE attempt_id=?",
                (candidate["attempt_id"],),
            ).fetchone()[0],
        )
        result["pages"] = pages
        c.execute(
            "UPDATE crawl_attempt SET "
            "status=?,reason=?,pages=?,finished_at=?,account_id=?,result_action=? "
            "WHERE id=? AND status='running'",
            (
                result["status"],
                result.get("reason", ""),
                pages,
                timestamp(),
                account_id,
                action,
                candidate["attempt_id"],
            ),
        )

    def queue_retry(self, candidate_id, attempt_id, actor):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM candidate WHERE id=?", (candidate_id,)).fetchone()
            last = c.execute(
                "SELECT * FROM crawl_attempt WHERE candidate_id=? ORDER BY number DESC LIMIT 1",
                (candidate_id,),
            ).fetchone()
            if not row or not last:
                raise LookupError("采集候选不存在")
            if last["id"] != attempt_id or row["status"] != "failed":
                raise VersionConflict("采集状态已变化，请刷新")
            if row["reason"] not in RETRYABLE:
                raise ValueError("此类失败需要先核验站点或新网址，不支持直接重试")
            if last["number"] >= 3:
                raise ValueError("已达到三次尝试上限，请先核实站点")
            at = timestamp()
            due = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()
            c.execute(
                "INSERT INTO crawl_retry VALUES(?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET "
                "next_at=excluded.next_at,actor=excluded.actor,requested_at=excluded.requested_at",
                (candidate_id, due, actor, at),
            )
            c.execute(
                "UPDATE candidate SET status='queued',updated_at=? WHERE id=?", (at, candidate_id)
            )
            c.execute(
                "INSERT INTO crawl_observation(attempt_id,at,payload) VALUES(?,?,?)",
                (
                    attempt_id,
                    at,
                    json.dumps({"phase": "retry_requested", "actor": actor, "nextAt": due}),
                ),
            )
            self.audit(c, actor, "candidate.retry_requested", str(candidate_id))
            return {"nextAt": due}

    def candidate_history(self, candidate_id):
        with self.connect() as c:
            c.execute("BEGIN")
            if not c.execute("SELECT 1 FROM candidate WHERE id=?", (candidate_id,)).fetchone():
                raise LookupError("采集候选不存在")
            attempts = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM crawl_attempt WHERE candidate_id=? ORDER BY number DESC",
                    (candidate_id,),
                )
            ]
            for attempt in attempts:
                attempt["observations"] = [
                    {"at": r["at"], **json.loads(r["payload"])}
                    for r in c.execute(
                        "SELECT * FROM crawl_observation WHERE attempt_id=? ORDER BY id",
                        (attempt["id"],),
                    )
                ]
            return {"attempts": attempts}
