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


def worker(store: Store, stop: threading.Event, endpoint: str, token: str):
    while not stop.is_set():
        candidate = None
        try:
            sync_mail(store, endpoint, token)
            deliver_one(store, endpoint, token)
        except Exception:
            log.warning("Mail integration temporarily unavailable")
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

    @app.middleware("http")
    async def authentication(request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        if proxy_key:
            if not secrets.compare_digest(
                request.headers.get("x-leadsgen-proxy-key", ""), proxy_key
            ) or not request.headers.get("x-oa-user"):
                return JSONResponse({"detail": "请通过已登录的公司门户访问"}, status_code=401)
            request.state.actor = request.headers["x-oa-user"][:200]
        else:
            if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
                return JSONResponse({"detail": "本地模式仅允许本机访问"}, status_code=403)
            request.state.actor = "local-operator"
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
    def state():
        return {
            "mode": "live",
            "accounts": store.accounts(),
            "jobs": store.jobs(),
            "handoffs": store.handoffs(),
            "cadenceDays": CADENCE_DAYS,
            "policyVersion": POLICY_VERSION,
            "mailConnected": bool(endpoint and integration_token),
        }

    @app.post("/api/jobs", status_code=201)
    def create_job(body: JobInput, request: Request):
        try:
            job_id = store.create_job(
                body.name, body.region, [s.model_dump() for s in body.seeds], request.state.actor
            )
        except CrawlError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": job_id}

    @app.post("/api/handoffs")
    def handoff(body: HandoffInput, request: Request):
        try:
            return store.enqueue(body.ids, request.state.actor)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/accounts/{account_id}/suppress")
    def suppress(account_id: str, request: Request):
        try:
            store.suppress(account_id, request.state.actor)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/accounts/{account_id}/contact")
    def contact(account_id: str, body: ContactInput, request: Request):
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
    )
    uvicorn.run(app, host=host, port=int(os.environ.get("LEADSGEN_PORT", "8910")))


if __name__ == "__main__":
    main()
