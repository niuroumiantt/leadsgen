"""Company references and append-only progress, without changing source account IDs."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

MILESTONES = {
    "unverified": "待核验",
    "identified": "可联系潜客",
    "contacted": "已首次联系",
    "engaged": "已建立沟通",
    "qualified": "已确认需求",
    "quoted": "已报价",
    "negotiating": "商务 / 技术推进",
    "won": "成交 / 转交履约",
    "lost": "已关闭",
    "nurture": "长期培育",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS company (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_company (
 account_id TEXT PRIMARY KEY REFERENCES account(id), company_id TEXT NOT NULL REFERENCES
 company(id),
 version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS account_workflow (
 account_id TEXT PRIMARY KEY REFERENCES account(id), milestone TEXT NOT NULL DEFAULT 'unverified',
 profile TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS account_activity (
 id INTEGER PRIMARY KEY, account_id TEXT NOT NULL REFERENCES account(id), kind TEXT NOT NULL,
 actor TEXT NOT NULL, event_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
 occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS activity_by_account ON account_activity(account_id,id);
CREATE TRIGGER IF NOT EXISTS activity_no_update BEFORE UPDATE ON account_activity
 BEGIN SELECT RAISE(ABORT, 'activity is append only'); END;
CREATE TRIGGER IF NOT EXISTS activity_no_delete BEFORE DELETE ON account_activity
 BEGIN SELECT RAISE(ABORT, 'activity is append only'); END;
CREATE TRIGGER IF NOT EXISTS account_workflow_created AFTER INSERT ON account BEGIN
 INSERT INTO company(id,created_at) VALUES('company_' || NEW.id, NEW.updated_at);
 INSERT INTO account_company(account_id,company_id) VALUES(NEW.id,'company_' || NEW.id);
 INSERT INTO account_workflow(account_id) VALUES(NEW.id);
 INSERT INTO account_activity(account_id,kind,actor,event_key,payload,occurred_at,recorded_at)
 VALUES(NEW.id,'account.created','system','created:' || NEW.id,'{}',NEW.updated_at,NEW.updated_at);
END;
"""


def timestamp():
    return datetime.now(UTC).isoformat()


class VersionConflict(ValueError):
    pass


class WorkflowStore:
    @staticmethod
    def activity(c, account_id, kind, actor, payload, *, key=None, at=None):
        c.execute(
            "INSERT OR IGNORE INTO account_activity "
            "(account_id,kind,actor,event_key,payload,occurred_at,recorded_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                account_id,
                kind,
                actor,
                key or uuid.uuid4().hex,
                json.dumps(payload, ensure_ascii=False),
                at or timestamp(),
                timestamp(),
            ),
        )

    @classmethod
    def migrate_workflow(cls, c):
        c.executescript(SCHEMA)
        c.execute("BEGIN IMMEDIATE")
        for row in c.execute("SELECT id,updated_at FROM account").fetchall():
            # Add references only. Never merge based on email, name, or a synthetic domain.
            c.execute(
                "INSERT OR IGNORE INTO company VALUES(?,?)",
                ("company_" + row["id"], row["updated_at"]),
            )
            c.execute(
                "INSERT OR IGNORE INTO account_company(account_id,company_id) VALUES(?,?)",
                (row["id"], "company_" + row["id"]),
            )
            created = c.execute(
                "INSERT OR IGNORE INTO account_workflow(account_id) VALUES(?)", (row["id"],)
            ).rowcount
            if created:
                cls.activity(
                    c, row["id"], "baseline.account", "migration", {}, key="baseline:" + row["id"]
                )
                followup = c.execute(
                    "SELECT * FROM account_followup WHERE account_id=?", (row["id"],)
                ).fetchone()
                if followup:
                    cls.activity(
                        c,
                        row["id"],
                        "baseline.followup",
                        followup["updated_by"],
                        {
                            "after": {
                                "followup": {
                                    k: followup[k] for k in ("stage", "next_step", "due_at")
                                }
                            }
                        },
                        key="baseline-followup:" + row["id"],
                        at=followup["updated_at"],
                    )
        # Existing evidence is imported once; missing history is never invented.
        for e in c.execute("SELECT * FROM assignment_event").fetchall():
            cls.assignment_activity(c, dict(e))
        for e in c.execute("SELECT * FROM mail_event").fetchall():
            cls.activity(
                c,
                e["account_id"],
                "mail.event",
                "aimail",
                json.loads(e["payload"]),
                key="mail:" + e["id"],
                at=e["created_at"],
            )
        c.commit()

    @classmethod
    def assignment_activity(cls, c, e):
        cls.activity(
            c,
            e["account_id"],
            "assignment." + e["action"],
            e["actor"],
            {"recipient": e["recipient"], "reason": e.get("reason", ""), "version": e["version"]},
            key=f"assignment:{e['account_id']}:{e['version']}:{e['action']}",
            at=e["created_at"],
        )

    @staticmethod
    def workflow(c, account_id):
        row = c.execute(
            "SELECT * FROM account_workflow WHERE account_id=?", (account_id,)
        ).fetchone()
        return (
            {
                "milestone": row["milestone"],
                "profile": json.loads(row["profile"]),
                "version": row["version"],
            }
            if row
            else {"milestone": "unverified", "profile": {}, "version": 0}
        )

    @staticmethod
    def require_visible(c, account_id, identity, admin=False):
        if not c.execute("SELECT 1 FROM account WHERE id=?", (account_id,)).fetchone():
            raise LookupError("客户不存在或未授权")
        if (
            not admin
            and not c.execute(
                "SELECT 1 FROM account_assignment WHERE account_id=? AND (owner=? OR pending=?)",
                (account_id, identity, identity),
            ).fetchone()
        ):
            raise LookupError("客户不存在或未授权")

    def activities(self, account_id, identity, admin=False, before=None):
        with self.connect() as c:
            c.execute("BEGIN")
            self.require_visible(c, account_id, identity, admin)
            rows = c.execute(
                "SELECT * FROM account_activity WHERE account_id=? AND (? IS NULL OR id<?) "
                "ORDER BY id DESC LIMIT 51",
                (account_id, before, before),
            ).fetchall()
            return {
                "items": [{**dict(r), "payload": json.loads(r["payload"])} for r in rows[:50]],
                "nextBefore": rows[49]["id"] if len(rows) > 50 else None,
            }

    def link_company(self, account_id, target_id, version, target_version, actor, reason):
        if not reason.strip():
            raise ValueError("请记录确认同一企业的依据")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self.require_visible(c, account_id, actor, True)
            source = c.execute(
                "SELECT * FROM account_company WHERE account_id=?", (account_id,)
            ).fetchone()
            if source["version"] != version:
                raise VersionConflict("企业关联已变化，请刷新")
            if target_id:
                self.require_visible(c, target_id, actor, True)
                target = c.execute(
                    "SELECT * FROM account_company WHERE account_id=?", (target_id,)
                ).fetchone()
                if target["version"] != target_version:
                    raise VersionConflict("目标企业已变化，请刷新")
                company_id = target["company_id"]
            else:
                size = c.execute(
                    "SELECT count(*) FROM account_company WHERE company_id=?",
                    (source["company_id"],),
                ).fetchone()[0]
                if size == 1:
                    return dict(source)
                company_id = "company_" + uuid.uuid4().hex
                c.execute("INSERT INTO company VALUES(?,?)", (company_id, timestamp()))
            if company_id == source["company_id"]:
                return dict(source)
            c.execute(
                "UPDATE account_company SET company_id=?,version=version+1 WHERE account_id=?",
                (company_id, account_id),
            )
            # Association only: no ownership, mail access, recipients or suppression changes.
            self.activity(
                c,
                account_id,
                "company.linked" if target_id else "company.detached",
                actor,
                {
                    "before": {"companyId": source["company_id"]},
                    "after": {"companyId": company_id},
                    "reason": reason.strip(),
                },
            )
            return {"account_id": account_id, "company_id": company_id, "version": version + 1}

    def update_progress(
        self, account_id, actor, stage, next_step, due_at, *, version, milestone, profile, reason
    ):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            owner = c.execute(
                "SELECT owner FROM account_assignment WHERE account_id=?", (account_id,)
            ).fetchone()
            if not owner or owner["owner"] != actor:
                raise PermissionError("只有当前负责人可以更新跟进状态")
            if milestone not in MILESTONES:
                raise ValueError("业务阶段无效")
            current = self.workflow(c, account_id)
            if milestone != current["milestone"] and not reason.strip():
                raise ValueError("阶段变更需要记录依据；收到邮件不等于确认商机")
            if milestone in {"qualified", "quoted", "negotiating", "won"}:
                if any(
                    not profile.get(key, "").strip() for key in ("product", "quantity", "country")
                ):
                    raise ValueError("确认需求后需补齐产品、数量和交付国家 / 地区")
            if milestone in {"quoted", "negotiating"} and not profile.get("quote_ref", "").strip():
                raise ValueError("报价阶段需要正式报价 / 方案引用")
            if milestone == "won" and not profile.get("order_ref", "").strip():
                raise ValueError("成交需记录 PO / 合同 / 订单引用")
            if milestone not in {"won", "lost"} and (not next_step.strip() or not due_at.strip()):
                raise ValueError("请填写下一步和跟进日期")
            if milestone in {"won", "lost"}:
                stage = "结束"
            elif stage == "结束":
                raise ValueError("结束跟进时请选择成交或已关闭；暂缓请选长期培育")
            return self.save_followup(
                c,
                account_id,
                actor,
                stage,
                next_step,
                due_at,
                version=version,
                milestone=milestone,
                profile=profile,
                reason=reason,
            )

    def save_followup(
        self,
        c,
        account_id,
        actor,
        stage,
        next_step,
        due_at,
        *,
        version=None,
        milestone=None,
        profile=None,
        reason="",
    ):
        allowed = {"待联系", "跟进中", "等待客户", "等待内部", "稍后跟进", "结束"}
        if stage not in allowed:
            raise ValueError("跟进阶段无效")
        if len(next_step) > 500 or len(due_at) > 40:
            raise ValueError("下一步内容过长")
        if due_at.strip():
            try:
                datetime.strptime(due_at.strip(), "%Y-%m-%d")
            except ValueError:
                raise ValueError("下次跟进日期必须为 YYYY-MM-DD") from None
        owner = c.execute(
            "SELECT owner FROM account_assignment WHERE account_id=?", (account_id,)
        ).fetchone()
        if not owner or owner["owner"] != actor:
            raise PermissionError("只有当前负责人可以更新跟进状态")
        current = self.workflow(c, account_id)
        if version is not None and current["version"] != version:
            raise VersionConflict("进展已由其他页面更新，请刷新后重试")
        old = c.execute(
            "SELECT stage,next_step,due_at FROM account_followup WHERE account_id=?", (account_id,)
        ).fetchone()
        after = {"stage": stage, "next_step": next_step.strip(), "due_at": due_at.strip()}
        milestone = milestone or current["milestone"]
        profile = (
            current["profile"] if profile is None else {k: v.strip() for k, v in profile.items()}
        )
        before = {"followup": dict(old) if old else None, **current}
        if (
            old
            and dict(old) == after
            and milestone == current["milestone"]
            and profile == current["profile"]
            and not reason.strip()
        ):
            return {**after, "version": current["version"]}
        c.execute(
            "INSERT INTO account_followup(account_id,stage,next_step,due_at,updated_by,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET stage=excluded.stage, "
            "next_step=excluded.next_step,due_at=excluded.due_at,updated_by=excluded.updated_by,"
            "updated_at=excluded.updated_at",
            (account_id, stage, after["next_step"], after["due_at"], actor, timestamp()),
        )
        c.execute(
            "UPDATE account_workflow SET milestone=?,profile=?,version=version+1 WHERE "
            "account_id=?",
            (milestone, json.dumps(profile, ensure_ascii=False), account_id),
        )
        self.activity(
            c,
            account_id,
            "progress.updated",
            actor,
            {
                "before": before,
                "after": {
                    "followup": after,
                    "milestone": milestone,
                    "profile": profile,
                    "version": current["version"] + 1,
                },
                "reason": reason.strip(),
            },
        )
        self.audit(c, actor, "followup.updated", account_id)
        return {**after, "version": current["version"] + 1}
