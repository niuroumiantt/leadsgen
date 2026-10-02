# leadsgen

为 Glocal Storage 汇总主动发现和 sales@ 客户来信，完成初筛、建档、分配及早期跟进。

leadsgen 是轻量获客 CRM：负责线索来源、初筛、企业档案、分配、pipeline 状态和统计。
Aimail 保管邮件原文、附件、线程、AI 阅读结果和员工个人发件身份；员工只获得分给自己的客户记录与邮件线程。

可记录报价与订单引用和业务阶段；报价文件、订单履约和售后由相应业务系统处理。

## 当前版本

**v0.2 已有真实采集、SQLite 建档、Ant Design 工作空间和持久邮件交接。**
HTTP 访问默认真实数据；`?demo=1` 或离线 HTML 为 `.example` 合成示例，不发信。
主动发现只使用公开来源；sales@ 只接收询盘。未经逐封明确批准，不会自动发送客户邮件。

- [采集过程、企业档案与客户进展](docs/customer-progress-2026-10-02.md)
- [第一版系统设计](docs/design-v1.md)
- [开发信接线协议](docs/handoff-v2.md)
- [跨仓库销售工作流与联合检查](https://github.com/niuroumiantt/infra/blob/main/docs/sales-workflow.md)
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

## 发布状态

2026-09-23 首次公开采集上线；2026-10-01 四仓销售工作流已分批上线并由 M5 验收。
详见 [四仓发布记录](https://github.com/niuroumiantt/infra/blob/main/docs/handoff/sales-workflow-release-2026-10-01.md)。

本次新增采集过程、企业关联与客户进展时间线。生产运行版本和升级验收以
[本批发布记录](https://github.com/niuroumiantt/infra/blob/main/docs/handoff/leadsgen-progress-release-2026-10-02.md) 为准。
目前仍需输入官网种子；自动搜索发现、定时搜索尚未接入。
