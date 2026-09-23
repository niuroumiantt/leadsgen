# leadsgen

为 Glocal Storage 发现有公开业务邮箱的服务器及配件潜在客户，轻量建档，并向 mail2leads 交接。

本仓库负责：发现网站、定向采集、证据提取、客户分类、去重建档、交接名单和显示邮件系统回传状态。

邮件发送、回复阅读、销售跟进、报价及订单属于其他系统。

## 当前版本

**v0.2 已有真实采集、SQLite 建档、Ant Design 工作空间和持久邮件交接。**
HTTP 访问默认真实数据；`?demo=1` 或离线 HTML 为 `.example` 合成示例，不发信。
纯机房租赁商、sales、客服与通用邮箱均纳入。首封与 +7/+14/+28/+60/+90 天跟进属于 mail2leads。

- [第一版系统设计](docs/design-v1.md)
- [当前接线协议](docs/handoff-v2.md)
- [v0.2 运行与发布说明](docs/release-v0.2.md)
- 前端：React / TypeScript / Ant Design / Vite

```sh
npm install
npm run dev
npm run build
uv sync --locked
uv run python -m leadsgen.app
```

真实工作空间 `http://127.0.0.1:8910`。开发 UI 在 5198 代理本机后端。
需要 Python 3.12+、uv 和 Node.js 22.12+ 或 24+。

## 数据位置

源码：`~/code/leadsgen`；运行数据：`~/.local/share/leadsgen/`；日志：`~/.local/state/leadsgen/`；凭据：`~/.config/leadsgen/`。
真实客户信息、采集页面、邮箱凭据及导出名单不进入 Git。

## 首个生产里程碑

2026-09-23：采集工作台已部署至 [leads.glocalstorage.cn](https://leads.glocalstorage.cn)，
通过 OA 会话保护，生产库已完成美国两个官网的小样本采集。HTTPS、未登录拒绝、
伪造身份拒绝、容器健康和数据库完整性均已验证。当前机器的 DNS 负缓存尚未刷新，
因此登录后页面的浏览器验收仍待完成；请勿把离线示例当成生产页面。

生产邮件交接尚未接线，现有 mail2leads 服务未升级，没有发送开发信。
发件身份和六封正文确认后，才进入邮件接线与发送验收。
交接令牌仅可导入、读状态和停发，不能批准邮件；未配置时队列持久保留，不显示虚假的发送成功。
