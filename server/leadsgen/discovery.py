"""Durable discovery plans, one-run-per-plan scheduling, and a reviewed website queue."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from .crawl import CrawlError, registered_domain
from .search import COUNTRIES, ERRORS, SearchError, site_from_result
from .workflow import VersionConflict

SCHEMA = """
CREATE TABLE IF NOT EXISTS discovery_plan (
 id TEXT PRIMARY KEY, config TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
 version INTEGER NOT NULL DEFAULT 0, next_run_at TEXT, failures INTEGER NOT NULL DEFAULT 0,
 last_error TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discovery_run (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES discovery_plan(id), kind TEXT NOT NULL,
 scheduled_for TEXT NOT NULL, config TEXT NOT NULL, plan_version INTEGER NOT NULL, status TEXT
 NOT NULL,
 created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT NOT NULL DEFAULT '',
 UNIQUE(plan_id,kind,scheduled_for)
);
CREATE UNIQUE INDEX IF NOT EXISTS discovery_one_active ON discovery_run(plan_id)
 WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS discovery_query (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES discovery_run(id), ordinal INTEGER NOT
 NULL,
 query TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', requested_at TEXT, finished_at TEXT,
 error TEXT NOT NULL DEFAULT '', UNIQUE(run_id,ordinal)
);
CREATE INDEX IF NOT EXISTS discovery_daily_queries ON discovery_query(requested_at);
CREATE TABLE IF NOT EXISTS discovery_result (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES discovery_run(id),
 query_id INTEGER NOT NULL REFERENCES discovery_query(id), rank INTEGER NOT NULL,
 title TEXT NOT NULL, source_url TEXT NOT NULL, site_url TEXT NOT NULL, domain TEXT NOT NULL,
 snippet TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
 canonical_id INTEGER REFERENCES discovery_result(id), candidate_id INTEGER REFERENCES
 candidate(id),
 reviewed_by TEXT NOT NULL DEFAULT '', reviewed_at TEXT, created_at TEXT NOT NULL,
 UNIQUE(query_id,rank)
);
CREATE TABLE IF NOT EXISTS discovery_site (
 domain TEXT PRIMARY KEY, result_id INTEGER REFERENCES discovery_result(id),
 candidate_id INTEGER REFERENCES candidate(id)
);
CREATE INDEX IF NOT EXISTS discovery_results_run ON discovery_result(run_id,id);
CREATE INDEX IF NOT EXISTS discovery_runs_plan ON discovery_run(plan_id,created_at);
CREATE TRIGGER IF NOT EXISTS discovery_evidence_immutable
 BEFORE UPDATE OF run_id,query_id,rank,title,source_url,site_url,domain,snippet,created_at
 ON discovery_result BEGIN SELECT RAISE(ABORT,'search evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS discovery_evidence_no_delete BEFORE DELETE ON discovery_result
 BEGIN SELECT RAISE(ABORT,'search evidence is immutable'); END;
"""


def now():
    return datetime.now(UTC).isoformat()


def next_day():
    return (
        datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    ).isoformat()


def snapshot(row):
    return {**dict(row), "config": json.loads(row["config"])}


class DiscoveryStore:
    @staticmethod
    def register_site(c, candidate_id, url):
        try:
            domain = registered_domain(url)
        except (CrawlError, ValueError):
            return
        c.execute(
            "INSERT INTO discovery_site(domain,candidate_id) VALUES(?,?) "
            "ON CONFLICT(domain) DO UPDATE SET "
            "candidate_id=coalesce(discovery_site.candidate_id,excluded.candidate_id)",
            (domain, candidate_id),
        )

    @classmethod
    def migrate_discovery(cls, c):
        c.executescript(SCHEMA)
        c.execute("BEGIN IMMEDIATE")
        for candidate in c.execute("SELECT id,url FROM candidate ORDER BY id").fetchall():
            cls.register_site(c, candidate["id"], candidate["url"])
        c.commit()

    def save_plan(self, config, actor, plan_id=None, version=None):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            at = now()
            if plan_id:
                row = self.get_plan(c, plan_id)
                if row["version"] != version:
                    raise VersionConflict("计划已更新，请刷新后编辑")
                # Editing creates a new version; an active run retains its immutable snapshot.
                c.execute(
                    "UPDATE discovery_plan SET config=?,version=version+1,updated_at=?,"
                    "next_run_at=?,enabled=?,failures=0,last_error='' WHERE id=?",
                    (
                        json.dumps(config, ensure_ascii=False),
                        at,
                        (datetime.now(UTC) + timedelta(hours=config["interval_hours"])).isoformat()
                        if row["enabled"] and config["interval_hours"]
                        else None,
                        int(bool(row["enabled"] and config["interval_hours"])),
                        plan_id,
                    ),
                )
            else:
                if c.execute("SELECT count(*) FROM discovery_plan").fetchone()[0] >= 100:
                    raise ValueError("最多保留 100 个发现计划")
                plan_id = "plan_" + uuid.uuid4().hex
                c.execute(
                    "INSERT INTO discovery_plan(id,config,created_by,created_at,updated_at) "
                    "VALUES(?,?,?,?,?)",
                    (plan_id, json.dumps(config, ensure_ascii=False), actor, at, at),
                )
            self.audit(c, actor, "discovery.plan_saved", plan_id)
            return snapshot(self.get_plan(c, plan_id))

    @staticmethod
    def get_plan(c, plan_id):
        row = c.execute("SELECT * FROM discovery_plan WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise LookupError("发现计划不存在")
        return row

    def toggle_plan(self, plan_id, enabled, version, actor, services):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = self.get_plan(c, plan_id)
            if row["version"] != version:
                raise VersionConflict("计划状态已变化，请刷新")
            config = json.loads(row["config"])
            if enabled and (
                not config["interval_hours"] or not services.configured(config["provider"])
            ):
                raise ValueError("启用定时计划需要设置频率并接入搜索服务")
            c.execute(
                "UPDATE discovery_plan SET enabled=?,next_run_at=?,version=version+1,updated_at=?,"
                "failures=0,last_error='' WHERE id=?",
                (int(enabled), now() if enabled else None, now(), plan_id),
            )
            if not enabled:
                self.cancel_runs(c, plan_id, "paused")
            self.audit(c, actor, "discovery.enabled" if enabled else "discovery.paused", plan_id)
            return snapshot(self.get_plan(c, plan_id))

    @staticmethod
    def cancel_runs(c, plan_id, reason):
        at = now()
        c.execute(
            "UPDATE discovery_query SET status='cancelled',finished_at=?,error=? WHERE "
            "status='queued' "
            "AND run_id IN (SELECT id FROM discovery_run WHERE plan_id=? AND status IN "
            "('queued','running'))",
            (at, reason, plan_id),
        )
        c.execute(
            "UPDATE discovery_run SET status='cancelled',error=?,finished_at=? "
            "WHERE plan_id=? AND status IN ('queued','running')",
            (reason, at, plan_id),
        )

    @staticmethod
    def insert_run(c, row, kind, slot, run_id):
        config = json.loads(row["config"])
        c.execute(
            "INSERT INTO "
            "discovery_run(id,plan_id,kind,scheduled_for,config,plan_version,status,created_at) "
            "VALUES(?,?,?,?,?,?,'queued',?)",
            (run_id, row["id"], kind, slot, row["config"], row["version"], now()),
        )
        for i, terms in enumerate(config["queries"]):
            query = f"{terms} {COUNTRIES[config['country']][1]}"
            c.execute(
                "INSERT INTO discovery_query(run_id,ordinal,query) VALUES(?,?,?)",
                (run_id, i, query),
            )

    def queue_search(self, plan_id, request_id, version, actor, services):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            run_id = "search_" + request_id
            existing = c.execute("SELECT * FROM discovery_run WHERE id=?", (run_id,)).fetchone()
            if existing:
                if existing["plan_id"] != plan_id:
                    raise VersionConflict("请求标识已经用于另一计划")
                return {"id": run_id}
            row = self.get_plan(c, plan_id)
            if row["version"] != version:
                raise VersionConflict("计划已变化，请刷新")
            if not services.configured(json.loads(row["config"])["provider"]):
                raise ValueError(ERRORS["not_configured"])
            active = c.execute(
                "SELECT id FROM discovery_run WHERE plan_id=? AND status IN ('queued','running')",
                (plan_id,),
            ).fetchone()
            if active:
                return {"id": active["id"]}
            self.insert_run(c, row, "manual", request_id, run_id)
            self.audit(c, actor, "discovery.run_requested", run_id)
            return {"id": run_id}

    def claim_search(self):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            at = now()
            row = c.execute(
                "SELECT * FROM discovery_plan p WHERE enabled=1 AND next_run_at<=? "
                "AND NOT EXISTS(SELECT 1 FROM discovery_run r WHERE r.plan_id=p.id AND "
                "r.status IN ('queued','running')) "
                "ORDER BY next_run_at LIMIT 1",
                (at,),
            ).fetchone()
            if row:
                config = json.loads(row["config"])
                self.insert_run(
                    c, row, "schedule", row["next_run_at"], "search_" + uuid.uuid4().hex
                )
                # Resume once, never replay every missed interval after downtime.
                c.execute(
                    "UPDATE discovery_plan SET next_run_at=? WHERE id=?",
                    (
                        (datetime.now(UTC) + timedelta(hours=config["interval_hours"])).isoformat(),
                        row["id"],
                    ),
                )
            run = c.execute(
                "SELECT * FROM discovery_run WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not run:
                return None
            c.execute(
                "UPDATE discovery_run SET status='running',started_at=? WHERE id=?", (at, run["id"])
            )
            return snapshot(run)

    def begin_query(self, run_id, limit):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            run = c.execute("SELECT status FROM discovery_run WHERE id=?", (run_id,)).fetchone()
            if not run or run["status"] != "running":
                return None
            row = c.execute(
                "SELECT * FROM discovery_query WHERE run_id=? AND status='queued' ORDER BY "
                "ordinal LIMIT 1",
                (run_id,),
            ).fetchone()
            if not row:
                return None
            used = c.execute(
                "SELECT count(*) FROM discovery_query WHERE requested_at>=?",
                (datetime.now(UTC).date().isoformat(),),
            ).fetchone()[0]
            if used >= limit:
                raise SearchError("daily_budget")
            c.execute(
                "UPDATE discovery_query SET status='running',requested_at=? WHERE id=?",
                (now(), row["id"]),
            )
            return dict(row)

    def finish_query(self, query_id, results, error=""):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            q = c.execute("SELECT * FROM discovery_query WHERE id=?", (query_id,)).fetchone()
            if not q or q["status"] != "running":
                return  # A duplicate/late response cannot overwrite recorded evidence.
            run = c.execute("SELECT * FROM discovery_run WHERE id=?", (q["run_id"],)).fetchone()
            cancelled = run["status"] != "running"
            for rank, result in enumerate(results[:10], 1):
                source, site, domain, reason = site_from_result(result["url"])
                decision, canonical, candidate_id = "pending", None, None
                if reason:
                    decision = "excluded"
                elif cancelled:
                    decision, reason = "excluded", "cancelled_run"
                elif c.execute("SELECT 1 FROM account WHERE domain=?", (domain,)).fetchone():
                    decision, reason = "duplicate", "existing_account"
                else:
                    old = c.execute(
                        "SELECT * FROM discovery_site WHERE domain=?", (domain,)
                    ).fetchone()
                    if old:
                        decision, reason = (
                            "duplicate",
                            "existing_candidate" if old["candidate_id"] else "seen_in_discovery",
                        )
                        canonical, candidate_id = old["result_id"], old["candidate_id"]
                result_id = c.execute(
                    "INSERT INTO "
                    "discovery_result(run_id,query_id,rank,title,source_url,site_url,domain,"
                    "snippet,decision,reason,canonical_id,candidate_id,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        q["run_id"],
                        query_id,
                        rank,
                        result["title"],
                        source,
                        site,
                        domain,
                        result["snippet"],
                        decision,
                        reason,
                        canonical,
                        candidate_id,
                        now(),
                    ),
                ).lastrowid
                if decision == "pending":
                    c.execute(
                        "INSERT INTO discovery_site(domain,result_id) VALUES(?,?)",
                        (domain, result_id),
                    )
            c.execute(
                "UPDATE discovery_query SET status=?,error=?,finished_at=? WHERE id=?",
                ("failed" if error else "succeeded", error, now(), query_id),
            )

    def finish_search(self, run_id, error=""):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            run = c.execute("SELECT * FROM discovery_run WHERE id=?", (run_id,)).fetchone()
            if not run or run["status"] != "running":
                return
            if error:
                c.execute(
                    "UPDATE discovery_query SET status='failed',error=?,finished_at=? WHERE "
                    "run_id=? AND status='running'",
                    (error, now(), run_id),
                )
            done = c.execute(
                "SELECT count(*) FROM discovery_query WHERE run_id=? AND status='succeeded'",
                (run_id,),
            ).fetchone()[0]
            status = (
                "succeeded"
                if not error
                else "partial"
                if done
                else "deferred"
                if error == "daily_budget"
                else "failed"
            )
            c.execute(
                "UPDATE discovery_query SET status='skipped',error=?,finished_at=? WHERE "
                "run_id=? AND status='queued'",
                (error, now(), run_id),
            )
            c.execute(
                "UPDATE discovery_run SET status=?,error=?,finished_at=? WHERE id=?",
                (status, error, now(), run_id),
            )
            plan = self.get_plan(c, run["plan_id"])
            # Do not change a newer edited/paused plan based on an old run snapshot.
            if plan["version"] != run["plan_version"]:
                return
            failures = plan["failures"] + 1 if error and error != "daily_budget" else 0
            enabled = bool(
                plan["enabled"] and error not in {"auth_failed", "not_configured"} and failures < 3
            )
            due = plan["next_run_at"] if enabled else None
            if enabled and error:
                due = (
                    next_day()
                    if error == "daily_budget"
                    else (
                        datetime.now(UTC) + timedelta(minutes=15 * 2 ** min(failures - 1, 4))
                    ).isoformat()
                )
            c.execute(
                "UPDATE discovery_plan SET failures=?,last_error=?,enabled=?,next_run_at=? "
                "WHERE id=?",
                (failures, error, int(enabled), due, plan["id"]),
            )

    def recover_discovery(self):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            at = now()
            c.execute(
                "UPDATE discovery_query SET "
                "status='interrupted',error='interrupted',finished_at=? WHERE status='running'",
                (at,),
            )
            c.execute(
                "UPDATE discovery_query SET "
                "status='skipped',error='interrupted',finished_at=? WHERE status='queued' AND "
                "run_id IN (SELECT id FROM discovery_run WHERE status='running')",
                (at,),
            )
            c.execute(
                "UPDATE discovery_run SET "
                "status='interrupted',error='interrupted',finished_at=? WHERE status='running'",
                (at,),
            )

    def review_results(self, result_ids, action, actor, reason=""):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if action == "reject" and not reason.strip():
                raise ValueError("排除网站需要填写原因")
            rows = [
                c.execute("SELECT * FROM discovery_result WHERE id=?", (rid,)).fetchone()
                for rid in dict.fromkeys(result_ids)
            ]
            if any(row is None for row in rows):
                raise LookupError("搜索结果不存在")
            # All-or-nothing for stale selections; already queued approvals are idempotent.
            if any(
                row["decision"] != "pending"
                and not (action == "collect" and row["decision"] == "queued")
                for row in rows
            ):
                raise VersionConflict("候选状态已变化，请刷新后重新选择")
            jobs, linked = {}, []
            for row in rows:
                if row["decision"] == "queued":
                    linked.append(row["candidate_id"])
                    continue
                if action == "reject":
                    c.execute(
                        "UPDATE discovery_result SET "
                        "decision='rejected',reason=?,reviewed_by=?,reviewed_at=? WHERE id=?",
                        (reason.strip(), actor, now(), row["id"]),
                    )
                    continue
                registry = c.execute(
                    "SELECT * FROM discovery_site WHERE domain=?", (row["domain"],)
                ).fetchone()
                if (registry and registry["candidate_id"]) or c.execute(
                    "SELECT 1 FROM account WHERE domain=?", (row["domain"],)
                ).fetchone():
                    c.execute(
                        "UPDATE discovery_result SET "
                        "decision='duplicate',reason='existing_candidate',reviewed_by=?,"
                        "reviewed_at=? WHERE id=?",
                        (actor, now(), row["id"]),
                    )
                    continue
                config = json.loads(
                    c.execute(
                        "SELECT config FROM discovery_run WHERE id=?", (row["run_id"],)
                    ).fetchone()[0]
                )
                if row["run_id"] not in jobs:
                    job_id = "job_" + uuid.uuid4().hex
                    jobs[row["run_id"]] = job_id
                    c.execute(
                        "INSERT INTO job VALUES(?,?,?,?)",
                        (
                            job_id,
                            "发现计划 · " + config["name"],
                            COUNTRIES[config["country"]][2],
                            now(),
                        ),
                    )
                seed = {
                    "url": row["site_url"],
                    "region": COUNTRIES[config["country"]][2],
                    "country": COUNTRIES[config["country"]][0],
                    "industry": config["industry"],
                    "tier": config["tier"],
                }
                candidate_id = c.execute(
                    "INSERT INTO candidate(job_id,url,seed,updated_at) VALUES(?,?,?,?)",
                    (
                        jobs[row["run_id"]],
                        row["site_url"],
                        json.dumps(seed, ensure_ascii=False),
                        now(),
                    ),
                ).lastrowid
                self.register_site(c, candidate_id, row["site_url"])
                c.execute(
                    "UPDATE discovery_result SET "
                    "decision='queued',candidate_id=?,reviewed_by=?,reviewed_at=? WHERE id=?",
                    (candidate_id, actor, now(), row["id"]),
                )
                linked.append(candidate_id)
            self.audit(
                c, actor, "discovery.results_" + action, json.dumps(list(dict.fromkeys(result_ids)))
            )
            return {"jobs": list(jobs.values()), "candidates": linked}

    def discovery_state(self, services):
        with self.connect() as c:
            c.execute("BEGIN")
            worker = c.execute("SELECT * FROM worker_status WHERE name='discovery'").fetchone()
            plans = [
                snapshot(r)
                for r in c.execute("SELECT * FROM discovery_plan ORDER BY created_at DESC")
            ]
            for plan in plans:
                last = c.execute(
                    "SELECT * FROM discovery_run WHERE plan_id=? ORDER BY created_at DESC LIMIT 1",
                    (plan["id"],),
                ).fetchone()
                plan["last_run"] = snapshot(last) if last else None
            return {
                "worker": dict(worker) if worker else None,
                "plans": plans,
                "providers": services.public_status(),
                "daily_limit": services.daily_limit,
                "used_today": c.execute(
                    "SELECT count(*) FROM discovery_query WHERE requested_at>=?",
                    (datetime.now(UTC).date().isoformat(),),
                ).fetchone()[0],
                "resets_at": next_day(),
                "pending": c.execute(
                    "SELECT count(*) FROM discovery_result WHERE decision='pending'"
                ).fetchone()[0],
                "countries": [{"code": k, "name": v[0]} for k, v in COUNTRIES.items()],
            }

    def search_runs(self, plan_id, before=None):
        with self.connect() as c:
            self.get_plan(c, plan_id)
            rows = c.execute(
                "SELECT * FROM discovery_run WHERE plan_id=? AND (? IS NULL OR created_at<?) "
                "ORDER BY created_at DESC LIMIT 21",
                (plan_id, before, before),
            ).fetchall()
            return {
                "items": [snapshot(r) for r in rows[:20]],
                "next_before": rows[19]["created_at"] if len(rows) > 20 else None,
            }

    def search_run(self, run_id):
        with self.connect() as c:
            c.execute("BEGIN")
            run = c.execute("SELECT * FROM discovery_run WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise LookupError("搜索执行不存在")
            results = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM discovery_result WHERE run_id=? ORDER BY id", (run_id,)
                )
            ]
            for r in results:
                r["crawl_status"], r["account_id"], r["milestone"] = None, None, None
                r["canonical_run_id"], r["canonical_decision"], r["canonical_reason"] = (
                    None,
                    None,
                    None,
                )
                if r["canonical_id"]:
                    original = c.execute(
                        "SELECT run_id,decision,reason FROM discovery_result WHERE id=?",
                        (r["canonical_id"],),
                    ).fetchone()
                    if original:
                        r["canonical_run_id"], r["canonical_decision"], r["canonical_reason"] = (
                            original
                        )
                if r["decision"] == "duplicate" and not r["candidate_id"]:
                    site = c.execute(
                        "SELECT candidate_id FROM discovery_site WHERE domain=?", (r["domain"],)
                    ).fetchone()
                    r["candidate_id"] = site[0] if site else None
                if r["candidate_id"]:
                    status = c.execute(
                        "SELECT status FROM candidate WHERE id=?", (r["candidate_id"],)
                    ).fetchone()
                    attempt = c.execute(
                        "SELECT account_id FROM crawl_attempt WHERE candidate_id=? AND "
                        "account_id IS NOT NULL ORDER BY number DESC LIMIT 1",
                        (r["candidate_id"],),
                    ).fetchone()
                    r["crawl_status"] = status[0] if status else None
                    if attempt:
                        r["account_id"] = attempt[0]
                        r["milestone"] = self.workflow(c, attempt[0])["milestone"]
            return {
                **snapshot(run),
                "queries": [
                    dict(r)
                    for r in c.execute(
                        "SELECT * FROM discovery_query WHERE run_id=? ORDER BY ordinal", (run_id,)
                    )
                ],
                "results": results,
            }


def discovery_worker(store, stop, services):
    while not stop.is_set():
        run = None
        try:
            run = store.claim_search()
            store.worker_heartbeat("discovery", "running" if run else "idle")
            if run:
                error = ""
                if not services.configured(run["config"]["provider"]):
                    error = "not_configured"
                else:
                    while not stop.is_set():
                        try:
                            query = store.begin_query(run["id"], services.daily_limit)
                        except SearchError as exc:
                            error = str(exc)
                            break
                        if not query:
                            break
                        try:
                            results = services.search(
                                run["config"]["provider"], query["query"], run["config"]["country"]
                            )
                            store.finish_query(query["id"], results)
                        except SearchError as exc:
                            error = str(exc)
                            store.finish_query(query["id"], [], error)
                            break
                        store.worker_heartbeat("discovery", "running")
                        if stop.wait(1):
                            break
                if not stop.is_set():
                    store.finish_search(run["id"], error)
        except Exception:
            # Never log provider response bodies, headers, or credential values.
            if run:
                store.finish_search(run["id"], "unavailable")
            store.worker_heartbeat("discovery", "error", error="发现任务暂不可用")
        stop.wait(5)
