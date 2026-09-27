# leadsgen

为 Glocal Storage 汇总主动发现和 sales@ 客户来信，完成初筛、建档、分配及早期跟进。

leadsgen 是轻量获客 CRM：负责线索来源、初筛、企业档案、分配、pipeline 状态和统计。
Aimail 保管邮件原文、附件、线程、AI 阅读结果和员工个人发件身份；员工只获得分给自己的客户记录与邮件线程。

报价、订单、合同及售后不在本轮范围内。

## 当前版本

**v0.2 已有真实采集、SQLite 建档、Ant Design 工作空间和持久邮件交接。**
HTTP 访问默认真实数据；`?demo=1` 或离线 HTML 为 `.example` 合成示例，不发信。
主动发现只使用公开来源；sales@ 只接收询盘。未经逐封明确批准，不会自动发送客户邮件。

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

当前 CRM-lite 角色隔离、sales 来信导入、员工接手后单线程授权和跟进状态已在开发分支实现并通过自动化测试；代码尚未部署。
生产邮箱角色边界已调整为 Larry 可看 sales@ 与个人邮箱、Isaac 只看个人邮箱。新 leadsgen 角色名单和完整浏览器验收仍须随应用发布完成。
导入/权限令牌不能批准邮件；未配置时队列持久保留，不显示虚假的发送成功。
# leadsgen

leadsgen is the controlled intake and assignment pipeline for publicly discovered companies and human-confirmed customer inquiries. The first role-separated pipeline slice is described in [docs/lead-pipeline-v1.md](docs/lead-pipeline-v1.md).
