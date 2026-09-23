# v0.2 交接接口

leadsgen 只发现、采集、建档、交接和镜像邮件状态，不持有 SMTP 密码，也不执行跟进。
mail2leads 候选分支提供下列接口；导入令牌与人工发送授权完全分开。

## 导入与回执

`POST /v1/prospects/import`，`Authorization: Bearer` 专用 `OUTREACH_IMPORT_TOKEN`。
JSON 必含 `schema_version=1`、`external_id`、`idempotency_key`、`company`、`website`、
`email`、`country`、`tier`、`source.url`、`policy_version`、`cadence_days=[0,7,14,28,60,90]`。
source 同时保留采集时间、网页哈希、原文短片段、邮箱角色和邮件域检查。

回执：`receipt_id`、`external_id`、`status=imported`。它只证明导入，不代表发送。
同一邮箱/公司幂等键/外部编号各自唯一；完全相同的请求返回原回执，冲突返回 409。
当前去重范围为配置的一个 mail2leads mailbox；跨邮箱组织级去重未实现，不应多邮箱并行投放。

## 人工确认与执行

mail2leads 的 `/outreach` 页面显示名单、六封完整内容和停止状态。
人工确认使用现有登录身份、同站校验头以及一次性令牌；机器令牌不能批准。
批准绑定发件地址、收件邮箱与六封完整正文哈希，之后不能修改或重新启用。同一公司的六封不需要逐封再次点击。
实际发送还要求 `OUTREACH_ENABLED=1`、完整发件地址、成功完成最新 INBOX 同步。
默认关闭发送，默认每日 20 次尝试仅为初始保护上限，不代表送达率或政策保证。

首封 SMTP 接受时间是 Day 0。后续为 +7/+14/+28/+60/+90 天，不是相邻累计间隔。
长时间停机时跳过过期节点，一次只执行当前最新到期节点，避免集中补发。
每次 SMTP 前提交原始 MIME、Message-ID 和发送尝试；SMTP 结果不明或重启遗留尝试均进入
`delivery_unknown`，不自动重发。SMTP 接受不等于最终送达。

任何同地址来信或引用本序列 Message-ID 的回复都会停止，包括自动回复。
标准 DSN 失败报告停止；延迟报告也先暂停。退订回复因此先停发，人工可正式登记退订。
所有停止状态都不能由再次导入清除。本版无自动恢复或重发按钮。

## 状态拉取与停发

`GET /v1/outreach/events?cursor=0` 返回 `events` 和整数 `next_cursor`，每页最多 200 条。
事件含 `id`、`external_id`、`sequence_id`、`type`、`occurred_at`、`detail`。
leadsgen 事务内幂等写入事件及游标，不用页面动作猜测已发送。

`POST /v1/prospects/{receipt_id}/stop`，JSON `reason=paused` 或 `unsubscribed`。
leadsgen 的排除操作保留本地禁止联系，并等待邮件系统停发回执；失败显示待确认而非已停发。
独立邮箱人工停止可用 `/api/prospects/{id}/stop`。

## 安全部署

机器接口只接收专用令牌，不能将现有 OA cookie 或 SMTP 密码用作集成令牌。
公开用户界面需要 OA 登录及可信代理；机器接口通过受控私有网络访问，不能为了接入而绕过整个站点登录。
mail2leads 的开发信人工接口另需可信代理注入 `X-Outreach-Approval-Key`，值为独立的
`OUTREACH_APPROVAL_PROXY_KEY`；不能与导入令牌共用，否则导入方可能伪造人工身份。
一套数据库仅运行一个采集 worker、一个邮件 poller。真实库与凭据不进入 Git。
