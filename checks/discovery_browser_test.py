"""Search → review → collection → CRM in Chromium; all search responses are synthetic."""

import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from leadsgen.app import create_app
from leadsgen.discovery import discovery_worker
from leadsgen.search import SearchServices
from playwright.sync_api import expect, sync_playwright


@pytest.fixture
def discovery_workspace(tmp_path):
    calls = []

    def search(request):
        calls.append(request)
        assert request.url.host == "api.search.brave.com"
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "url": "https://example.org/contact",
                            "title": "Example GPU Cloud",
                            "description": "Synthetic GPU company for browser acceptance.",
                        },
                        {
                            "url": "https://www.example.org/about",
                            "title": "Same company, another page",
                        },
                        {
                            "url": "https://linkedin.com/company/example",
                            "title": "Directory listing",
                        },
                    ]
                }
            },
        )

    services = SearchServices(
        keys={"brave": "synthetic-key"}, transport=httpx.MockTransport(search)
    )
    app = create_app(
        tmp_path / "browser.db",
        search_services=services,
        web_dist=Path(__file__).resolve().parents[1] / "dist",
        worker_enabled=False,
    )
    store = app.state.store
    stop = threading.Event()
    worker = threading.Thread(target=discovery_worker, args=(store, stop, services), daemon=True)
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
    worker.start()
    yield store, services, calls, f"http://127.0.0.1:{port}"
    stop.set()
    server.should_exit = True
    worker.join(5)
    thread.join(5)


def test_discovery_review_collection_links_and_mobile(discovery_workspace):
    store, services, calls, url = discovery_workspace
    screenshots = os.environ.get("LEADSGEN_SCREENSHOTS")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_PATH"), args=["--no-sandbox"]
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.get_by_role("menuitem", name="发现计划", exact=False).click()
        page.get_by_role("button", name="新建发现计划", exact=False).click()
        page.get_by_label("计划名称", exact=True).fill("美国 · GPU 云验证计划")
        page.get_by_role("button", name="保存计划", exact=True).click()
        expect(page.get_by_text("美国 · GPU 云验证计划", exact=True)).to_be_visible()
        assert len(calls) == 0
        plan = store.discovery_state(services)["plans"][0]
        assert not plan["enabled"]
        page.get_by_role("button", name="搜索一次", exact=False).click()
        drawer = page.locator(".discovery-run-drawer")
        expect(drawer.get_by_text("Example GPU Cloud", exact=True)).to_be_visible(timeout=15000)
        drawer.get_by_text("查询过程", exact=False).click()
        expect(drawer.get_by_text("查询过程 · 2 / 2 完成", exact=True)).to_be_visible(timeout=15000)
        assert len(calls) == 2
        assert store.jobs() == []
        if screenshots:
            Path(screenshots).mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=f"{screenshots}/discovery-search-desktop.png", animations="disabled"
            )
        drawer.get_by_label("选择官网 example.org", exact=True).check()
        drawer.get_by_role("button", name="转入官网采集（1）", exact=True).click()
        drawer.get_by_label("搜索结果筛选", exact=True).click()
        page.get_by_title("已转入采集", exact=True).click()
        expect(drawer.get_by_role("button", name="查看官网采集", exact=True)).to_be_visible()
        assert len(store.jobs()) == 1
        assert store.handoffs() == []
        # Complete the reviewed candidate with a fixture; never start the website/mail workers.
        candidate = store.claim()
        store.finish(
            candidate,
            {
                "status": "completed",
                "account": {
                    "domain": "example.org",
                    "website": "https://example.org",
                    "name": "Example GPU Cloud",
                    "email": "info@example.org",
                    "department": "通用联系",
                    "stage": "ready",
                    "mail": "none",
                    "country": "美国",
                    "region": "美国",
                    "tier": "1B",
                    "industry": "GPU 云",
                    "summary": "验收合成记录，未抓取网络。",
                    "contacts": [],
                    "evidence": {},
                    "supermicro": "待核实",
                    "products": [],
                },
            },
        )
        expect(drawer.get_by_role("button", name="查看客户", exact=False)).to_be_visible(
            timeout=10000
        )
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if screenshots:
            page.screenshot(
                path=f"{screenshots}/discovery-results-mobile.png", animations="disabled"
            )
        drawer.get_by_role("button", name="查看官网采集", exact=True).click()
        expect(
            page.locator(".collection-drawer").get_by_text("https://example.org/", exact=True).first
        ).to_be_visible()
        page.locator(".collection-drawer").get_by_role("button", name="关闭", exact=True).click()
        page.get_by_role("button", name="发现计划", exact=True).click()
        expect(page.get_by_text("美国 · GPU 云验证计划", exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if screenshots:
            page.screenshot(
                path=f"{screenshots}/discovery-plans-mobile.png",
                full_page=True,
                animations="disabled",
            )
        page.get_by_role("button", name="执行记录", exact=True).click()
        page.get_by_role("button", name="查看搜索过程与结果", exact=True).click()
        drawer.get_by_label("搜索结果筛选", exact=True).click()
        page.get_by_title("已转入采集", exact=True).click()
        drawer.get_by_role("button", name="查看客户", exact=False).click()
        expect(
            page.locator(".pipeline-detail-drawer").get_by_text("客户进展与下一步", exact=True)
        ).to_be_visible()
        page.locator(".pipeline-detail-drawer").get_by_role(
            "button", name="关闭", exact=True
        ).click()
        drawer.get_by_role("button", name="关闭", exact=True).click()
        page.get_by_role("dialog", name="执行记录 · 美国 · GPU 云验证计划").get_by_role(
            "button", name="关闭", exact=True
        ).click()
        page.get_by_role("button", name="编辑", exact=True).click()
        page.get_by_label("计划名称", exact=True).fill("未保存的本地计划")
        store.save_plan({**plan["config"], "name": "另一页面已修改"}, "admin", plan["id"], 0)
        page.get_by_role("button", name="保存计划", exact=True).click()
        expect(page.get_by_text("计划已更新，请刷新后编辑", exact=True)).to_be_visible()
        expect(page.get_by_label("计划名称", exact=True)).to_have_value("未保存的本地计划")
        assert errors == []
        browser.close()
