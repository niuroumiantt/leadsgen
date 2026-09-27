from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .crawl import CrawlError, crawl
from .policy import CADENCE_DAYS, POLICY_VERSION
from .store import Store, now

log = logging.getLogger(__name__)


class Seed(BaseModel):
    url: str = Field(min_length=4, max_length=2048)
    name: str = Field(default="", max_length=200)
    country: str = Field(default="待核验", max_length=80)
    industry: str = Field(default="待核验", max_length=100)
    tier: Literal["1A", "1B", "1C", "1D", "2A", "2B"] = "1B"


class JobInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    region: Literal["美国", "欧洲", "中国", "东南亚", "其他"]
    seeds: list[Seed] = Field(min_length=1, max_length=30)


class HandoffInput(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


class ContactInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class AssignmentInput(BaseModel):
    recipient: str = Field(min_length=3, max_length=254)
    version: int = Field(ge=0)


class AssignmentDecision(BaseModel):
    version: int = Field(ge=0)


class FollowupInput(BaseModel):
    stage: Literal["待联系", "跟进中", "等待客户", "等待内部", "稍后跟进", "结束"]
    next_step: str = Field(default="", max_length=500)
    due_at: str = Field(default="", max_length=40)


def deliver_one(store: Store, endpoint: str, token: str):
    if not endpoint or not token:
        return
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute(
            "SELECT * FROM handoff WHERE status='queued' AND next_at<=? ORDER BY rowid LIMIT 1",
            (now(),),
        ).fetchone()
        if not r:
            return
        row = dict(r)
        c.execute(
            "UPDATE handoff SET status='delivering',attempts=attempts+1 WHERE id=?", (row["id"],)
        )
    try:
        response = httpx.post(
            endpoint.rstrip("/") + "/v1/prospects/import",
            json=json.loads(row["payload"]),
            headers={"Authorization": "Bearer " + token},
            timeout=15,
            follow_redirects=False,
            trust_env=False,
        )
        response.raise_for_status()
        receipt = response.json()
        if not receipt.get("receipt_id") or receipt.get("external_id") != row["account_id"]:
            raise ValueError("invalid_receipt")
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE handoff SET status='accepted',receipt=?,last_error='' WHERE id=?",
                (json.dumps(receipt), row["id"]),
            )
            account_row = c.execute(
                "SELECT payload FROM account WHERE id=?", (row["account_id"],)
            ).fetchone()
            a = json.loads(account_row[0])
            if a["stage"] == "suppressed":
                c.execute("UPDATE handoff SET status='stop_pending' WHERE id=?", (row["id"],))
            else:
                a["stage"] = "handed"
                a["mail"] = "draft"
            c.execute(
                "UPDATE account SET payload=?,updated_at=? WHERE id=?",
                (json.dumps(a, ensure_ascii=False), now(), a["id"]),
            )
    except (httpx.HTTPError, ValueError, KeyError):
        # Store only bounded generic failures; server response could contain secrets.
        with store.connect() as c:
            next_at = datetime.now(UTC) + timedelta(
                seconds=min(3600, 30 * 2 ** min(row["attempts"], 7))
            )
            c.execute(
                "UPDATE handoff SET status='queued',next_at=?,last_error=? "
                "WHERE id=? AND status='delivering'",
                (next_at.isoformat(), "邮件系统未确认接收，稍后重试", row["id"]),
            )


def sync_mail(store: Store, endpoint: str, token: str):
    if not endpoint or not token:
        return
    with httpx.Client(
        timeout=15,
        trust_env=False,
        follow_redirects=False,
        headers={"Authorization": "Bearer " + token},
    ) as client:
        with store.connect() as c:
            pending = c.execute(
                "SELECT id,receipt FROM handoff WHERE status='stop_pending'"
            ).fetchall()
        for row in pending:
            receipt = json.loads(row["receipt"])
            response = client.post(
                endpoint.rstrip("/") + "/v1/prospects/" + receipt["receipt_id"] + "/stop",
                json={"reason": "paused"},
            )
            response.raise_for_status()
            with store.connect() as c:
                c.execute("UPDATE handoff SET status='stopped' WHERE id=?", (row["id"],))
        cursor = store.mail_cursor()
        response = client.get(
            endpoint.rstrip("/") + "/v1/outreach/events", params={"cursor": cursor}
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("events"), list) or not isinstance(
            payload.get("next_cursor"), int
        ):
            raise ValueError("invalid_mail_events")
        if payload["next_cursor"] < cursor:
            raise ValueError("mail_cursor_regressed")
        store.receive_events(payload)


def sync_confirmed_leads(store: Store, endpoint: str, token: str):
    """Pull Aimail's confirmed facts into the private admin review pipeline."""
    if not endpoint or not token:
        return
    since, after = store.confirmed_lead_cursor()
    response = httpx.get(
        endpoint.rstrip("/") + "/v1/leads",
        params={"since": since, "after": after, "limit": 200},
        headers={"Authorization": "Bearer " + token},
        timeout=15,
        follow_redirects=False,
        trust_env=False,
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("leads")
    next_since = payload.get("next_since")
    next_after = payload.get("next_after")
    if (
        payload.get("version") != "v1"
        or not isinstance(items, list)
        or not isinstance(next_since, str)
        or not isinstance(next_after, int)
        or next_since < since
        or (next_since == since and next_after < after)
    ):
        raise ValueError("invalid_confirmed_mail_leads")
    store.import_confirmed_leads(items, next_since, next_after)


def sync_followup_access(store: Store, endpoint: str, token: str):
    if not endpoint or not token:
        return
    for item in store.pending_followup_grants():
        attempts = item["attempts"] + 1
        try:
            response = httpx.post(
                endpoint.rstrip("/") + "/v1/followups/access",
                json={
                    "external_id": item["account_id"],
                    "thread_id": int(item["thread_id"]),
                    "recipient": item["recipient"],
                },
                headers={"Authorization": "Bearer " + token},
                timeout=15,
                follow_redirects=False,
                trust_env=False,
            )
            response.raise_for_status()
            receipt = response.json()
            if (
                receipt.get("external_id") != item["account_id"]
                or receipt.get("thread_id") != item["thread_id"]
                or receipt.get("owner", "").casefold() != item["recipient"].casefold()
            ):
                raise ValueError("invalid_followup_access_receipt")
            store.mark_followup_grant(item["account_id"], success=True, attempts=attempts)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            store.mark_followup_grant(
                item["account_id"],
                success=False,
                attempts=attempts,
                error="Aimail 尚未确认邮件线程权限，系统稍后重试",
            )


def worker(store: Store, stop: threading.Event, endpoint: str, token: str):
    while not stop.is_set():
        candidate = None
        for sync in (sync_mail, sync_confirmed_leads, sync_followup_access):
            try:
                sync(store, endpoint, token)
            except Exception:
                log.warning("Mail integration step temporarily unavailable")
        try:
            deliver_one(store, endpoint, token)
        except Exception:
            log.warning("Mail delivery step temporarily unavailable")
        try:
            candidate = store.claim()
            if candidate:
                try:
                    result = crawl(json.loads(candidate["seed"]))
                except CrawlError as exc:
                    result = {"status": "failed", "reason": str(exc)}
                except Exception:
                    log.exception("Crawler failed for candidate id %s", candidate["id"])
                    result = {"status": "failed", "reason": "crawl_failed"}
                store.finish(candidate, result)
        except Exception:
            log.exception("Crawl queue temporarily unavailable")
        stop.wait(1 if candidate else 5)


def create_app(
    path: Path,
    *,
    web_dist: Path | None = None,
    proxy_key: str = "",
    allowed_hosts: list[str] | None = None,
    worker_enabled: bool = True,
    endpoint: str = "",
    integration_token: str = "",
    admin_users: tuple[str, ...] = (),
    sales_users: tuple[str, ...] = (),
) -> FastAPI:
    store = Store(path)
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app):
        thread = None
        if worker_enabled:
            store.recover()
            thread = threading.Thread(
                target=worker, args=(store, stop, endpoint, integration_token), daemon=True
            )
            thread.start()
        yield
        stop.set()
        if thread:
            await asyncio.to_thread(thread.join, 2)

    app = FastAPI(title="leadsgen", version="0.2.0", lifespan=lifespan)
    app.state.store = store
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=allowed_hosts or ["127.0.0.1", "localhost", "testserver"],
    )
    admin_users = frozenset(value.strip().casefold() for value in admin_users if value.strip())
    sales_users = frozenset(value.strip().casefold() for value in sales_users if value.strip())

    @app.middleware("http")
    async def authentication(request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        if proxy_key:
            if (
                not secrets.compare_digest(
                    request.headers.get("x-leadsgen-proxy-key", ""), proxy_key
                )
                or not request.headers.get("x-oa-user")
                or not request.headers.get("x-oa-email")
            ):
                return JSONResponse({"detail": "请通过已登录的公司门户访问"}, status_code=401)
            email = request.headers["x-oa-email"].strip().casefold()
            if "@" not in email or len(email) > 254:
                return JSONResponse({"detail": "登录身份邮箱无效"}, status_code=401)
            request.state.identity = email
            request.state.actor = email
            if email in admin_users:
                request.state.role = "admin"
            elif email in sales_users:
                request.state.role = "sales"
            else:
                return JSONResponse({"detail": "当前账号尚未获准使用 leadsgen"}, status_code=403)
        else:
            if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
                return JSONResponse({"detail": "本地模式仅允许本机访问"}, status_code=403)
            request.state.actor = "local-operator"
            request.state.identity = "local-operator"
            request.state.role = "admin"
        if request.method in {"POST", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            expected = {
                "http://" + request.headers.get("host", ""),
                "https://" + request.headers.get("host", ""),
            }
            if origin and origin not in expected:
                return JSONResponse({"detail": "不接受跨站写入"}, status_code=403)
            if request.headers.get("x-leadsgen-client") != "web-v1":
                return JSONResponse({"detail": "缺少请求校验头"}, status_code=403)
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 65536:
                return JSONResponse({"detail": "请求过大"}, status_code=413)
            if len(await request.body()) > 65536:
                return JSONResponse({"detail": "请求过大"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def health():
        return {"ok": True, "version": "0.2.0"}

    @app.get("/api/state")
    def state(request: Request):
        is_admin = request.state.role == "admin"
        return {
            "mode": "live",
            "identity": request.state.identity,
            "role": request.state.role,
            "members": sorted(sales_users) if is_admin else [],
            "accounts": store.accounts()
            if is_admin
            else store.assigned_accounts(request.state.identity),
            "jobs": store.jobs() if is_admin else [],
            "handoffs": store.handoffs() if is_admin else [],
            "cadenceDays": CADENCE_DAYS,
            "policyVersion": POLICY_VERSION,
            "mailConnected": bool(endpoint and integration_token),
        }

    @app.post("/api/accounts/{account_id}/assignment")
    def assign_account(account_id: str, body: AssignmentInput, request: Request):
        if request.state.role != "admin":
            raise HTTPException(403, "只有线索管理员可以分配线索")
        recipient = body.recipient.strip().casefold()
        if recipient not in sales_users:
            raise HTTPException(422, "接收人不是已登记的销售员工")
        try:
            return store.assign(account_id, recipient, request.state.identity, body.version)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/accounts/{account_id}/assignment/accept")
    def accept_account(account_id: str, body: AssignmentDecision, request: Request):
        if request.state.role != "sales":
            raise HTTPException(403, "只有被分配的销售员工可以接手")
        try:
            return store.accept_assignment(account_id, request.state.identity, body.version)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/accounts/{account_id}/assignment/decline")
    def decline_account(account_id: str, body: AssignmentDecision, request: Request):
        if request.state.role != "sales":
            raise HTTPException(403, "只有被分配的销售员工可以退回")
        try:
            return store.decline_assignment(account_id, request.state.identity, body.version)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/accounts/{account_id}/followup")
    def update_account_followup(account_id: str, body: FollowupInput, request: Request):
        if request.state.role != "sales":
            raise HTTPException(403, "跟进状态只能由当前负责人更新")
        try:
            return store.update_followup(
                account_id, request.state.identity, body.stage, body.next_step, body.due_at
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/jobs", status_code=201)
    def create_job(body: JobInput, request: Request):
        if request.state.role != "admin":
            raise HTTPException(403, "只有线索管理员可以创建采集任务")
        try:
            job_id = store.create_job(
                body.name, body.region, [s.model_dump() for s in body.seeds], request.state.actor
            )
        except CrawlError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": job_id}

    @app.post("/api/handoffs")
    def handoff(body: HandoffInput, request: Request):
        if request.state.role != "admin":
            raise HTTPException(403, "只有线索管理员可以提交线索")
        try:
            return store.enqueue(body.ids, request.state.actor)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/accounts/{account_id}/suppress")
    def suppress(account_id: str, request: Request):
        if request.state.role != "admin":
            raise HTTPException(403, "只有线索管理员可以排除客户")
        try:
            store.suppress(account_id, request.state.actor)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/accounts/{account_id}/contact")
    def contact(account_id: str, body: ContactInput, request: Request):
        if request.state.role != "admin":
            raise HTTPException(403, "只有线索管理员可以核验联系邮箱")
        try:
            store.select_contact(account_id, body.email, request.state.actor)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @app.get("/{path:path}")
    def frontend(path: str):
        if (
            path.startswith(("api/", "v1/"))
            or not web_dist
            or not (web_dist / "index.html").is_file()
        ):
            raise HTTPException(404)
        return FileResponse(web_dist / "index.html")

    return app


def main():
    import uvicorn

    os.umask(0o077)
    host = os.environ.get("LEADSGEN_HOST", "127.0.0.1")
    proxy_key = os.environ.get("LEADSGEN_PROXY_KEY", "")
    if host not in {"127.0.0.1", "::1"} and len(proxy_key) < 32:
        raise SystemExit("公开监听需要至少 32 字符的代理认证密钥")
    path = Path(
        os.environ.get("LEADSGEN_DB", str(Path.home() / ".local/share/leadsgen/leadsgen.sqlite3"))
    )
    app = create_app(
        path,
        web_dist=Path(os.environ.get("LEADSGEN_WEB_DIST", "dist")),
        proxy_key=proxy_key,
        allowed_hosts=os.environ.get("LEADSGEN_ALLOWED_HOSTS", "127.0.0.1,localhost").split(","),
        endpoint=os.environ.get("LEADSGEN_MAIL_URL", ""),
        integration_token=os.environ.get("LEADSGEN_MAIL_TOKEN", ""),
        admin_users=tuple(os.environ.get("LEADSGEN_ADMIN_USERS", "").split(",")),
        sales_users=tuple(os.environ.get("LEADSGEN_SALES_USERS", "").split(",")),
    )
    uvicorn.run(app, host=host, port=int(os.environ.get("LEADSGEN_PORT", "8910")))


if __name__ == "__main__":
    main()
