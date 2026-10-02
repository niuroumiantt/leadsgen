"""Real browser checks against a disposable database; no crawls or mail transport."""

import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from leadsgen.app import create_app
from playwright.sync_api import expect, sync_playwright


@pytest.fixture
def workspace(tmp_path):
    app = create_app(
        tmp_path / "browser.db",
        web_dist=Path(__file__).resolve().parents[1] / "dist",
        worker_enabled=False,
    )
    store = app.state.store
    store.create_job(
        "美国公开官网 · 验收样本",
        "美国",
        [
            {"url": "https://example.com"},
            {"url": "https://other.example"},
            {"url": "https://network.example"},
            {"url": "https://denied.example"},
        ],
        "local-operator",
    )
    ids = []
    for domain, name in (("example.com", "Example Cloud"), ("other.example", "Example Inquiry")):
        task = store.claim()
        store.observe(
            task,
            {"phase": "page_parsed", "url": f"https://{domain}/contact", "pages": 1, "contacts": 1},
        )
        store.finish(
            task,
            {
                "status": "completed",
                "account": {
                    "domain": domain,
                    "website": f"https://{domain}",
                    "name": name,
                    "email": f"sales@{domain}",
                    "department": "销售入口",
                    "stage": "ready",
                    "mail": "none",
                    "country": "美国",
                    "region": "美国",
                    "tier": "1B",
                    "industry": "云服务 / 数据中心",
                    "summary": "公开业务联系入口，采购需求待确认。",
                    "contacts": [],
                    "evidence": {},
                    "supermicro": "待核实",
                    "products": [],
                },
            },
        )
        aid = next(a["id"] for a in store.accounts() if a["domain"] == domain)
        ids.append(aid)
        store.assign(aid, "local-operator", "local-operator", 0)
        store.accept_assignment(aid, "local-operator", 1)
    for reason in ("network_error", "robots_denied"):
        task = store.claim()
        store.finish(task, {"status": "failed", "reason": reason})
    store.worker_heartbeat("collection", "idle")
    store.worker_heartbeat("integration", "idle")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    yield store, ids, f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def test_real_workspace_progress_conflicts_collection_and_mobile(workspace):
    store, ids, url = workspace
    screenshots = os.environ.get("LEADSGEN_SCREENSHOTS")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_PATH"), args=["--no-sandbox"]
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.get_by_role("button", name="详情", exact=True).first.click()
        drawer = page.locator(".pipeline-detail-drawer")
        drawer.get_by_label("业务阶段", exact=True).click()
        page.get_by_title("可联系潜客", exact=True).click()
        drawer.get_by_label("下一步行动", exact=True).fill("确认 H200 数量及交付时间")
        drawer.get_by_label("下次跟进日期", exact=True).fill("2026-10-10")
        drawer.get_by_label("本次进展 / 阶段依据", exact=True).fill("官网联系页已核实")
        drawer.get_by_role("button", name="保存客户进展", exact=True).click()
        expect(drawer.get_by_text("记录客户进展", exact=True)).to_be_visible()
        aid = next(a["id"] for a in store.accounts() if a["workflow"]["version"] == 1)
        drawer.get_by_label("下一步行动", exact=True).fill("未保存的本地计划")
        store.update_followup(aid, "local-operator", "等待客户", "另一页面已更新", "2026-10-11")
        expect(drawer.get_by_text("进展已有新版本，当前填写内容已保留", exact=True)).to_be_visible(
            timeout=10000
        )
        expect(drawer.get_by_label("下一步行动", exact=True)).to_have_value("未保存的本地计划")
        expect(drawer.get_by_role("button", name="保存客户进展", exact=True)).to_be_disabled()
        drawer.get_by_role("button", name="载入最新记录").click()
        expect(drawer.get_by_label("下一步行动", exact=True)).to_have_value("另一页面已更新")
        if screenshots:
            Path(screenshots).mkdir(parents=True, exist_ok=True)
            drawer.get_by_text("客户进展与下一步", exact=True).scroll_into_view_if_needed()
            page.screenshot(path=f"{screenshots}/customer-progress.png")
        drawer.get_by_role("button", name="核实企业关联", exact=True).click()
        page.get_by_label("关联企业来源", exact=True).click()
        page.locator(".ant-select-item-option:visible").first.click()
        page.get_by_label("企业关联依据", exact=True).fill("客户确认官网和询价属于同一企业")
        page.get_by_role("button", name="关联到该企业", exact=True).click()
        expect(page.get_by_text("你可查看的同企业来源", exact=True)).to_be_visible()
        assert len({a["company"]["company_id"] for a in store.accounts()}) == 1
        drawer.get_by_role("button", name="关闭", exact=True).click()
        page.get_by_role("menuitem", name="采集任务", exact=False).click()
        expect(
            page.get_by_text("官网采集队列 · 承接手动名单与发现计划", exact=True)
        ).to_be_visible()
        page.locator(".ant-table-row-expand-icon").first.click()
        network = page.locator("tr.ant-table-row").filter(has_text="https://network.example")
        network.get_by_role("button", name="查看过程").click()
        details = page.locator(".collection-drawer")
        expect(details.get_by_role("button", name="60 秒后重试", exact=True)).to_be_visible()
        details.get_by_role("button", name="60 秒后重试", exact=True).click()
        expect(details.get_by_role("button", name="60 秒后重试", exact=True)).to_have_count(0)
        details.get_by_role("button", name="关闭", exact=True).click()
        blocked = page.locator("tr.ant-table-row").filter(has_text="https://denied.example")
        blocked.get_by_role("button", name="查看过程").click()
        expect(details.get_by_text("网站拒绝读取 robots 规则", exact=True).first).to_be_visible()
        expect(details.get_by_role("button", name="60 秒后重试", exact=True)).to_have_count(0)
        details.get_by_role("button", name="关闭", exact=True).click()
        expect(page.locator(".collection-drawer.ant-drawer-open")).to_have_count(0)
        if screenshots:
            page.screenshot(
                path=f"{screenshots}/collection-desktop.png", full_page=True, animations="disabled"
            )
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.get_by_role("heading", name="采集工作台", exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.get_by_role("button", name="美国公开官网 · 验收样本", exact=False).click()
        expect(page.get_by_role("button", name="查看过程").first).to_be_visible()
        if screenshots:
            page.screenshot(
                path=f"{screenshots}/collection-mobile.png", full_page=True, animations="disabled"
            )
        page.get_by_role("button", name="线索总览", exact=True).click()
        page.get_by_role("button", name="详情", exact=True).first.click()
        expect(page.get_by_label("下一步行动", exact=True)).to_have_count(1)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert errors == []
        browser.close()
