# leadsgen → mail2leads 交接协议草案

状态：历史设计稿。当前实现使用更小的扁平载荷和事件拉取，见 [v0.2 接口](handoff-v2.md)，不要按本文件接线。

## 所有权

leadsgen 保存采集档案、来源及交接 outbox。mail2leads 保存邮件草稿、审核、首封账本、实际消息、线程和退订/退信事实。外部潜客属于 prospect，不直接写入人工确认的销售商机表。

## 名单导入

拟议 `POST /v1/prospects/import`，使用限定为导入/读状态的服务身份，**导入不能发送**。身份绑定发件组织，不能信任 body 自报 tenant。接收端验证类型、大小、邮箱、来源 scheme 和字段长度；不自动抓取传入 URL。

```json
{
  "schema_version": "1",
  "handoff_id": "ho_demo_001",
  "idempotency_key": "org_demo:account_demo:first_touch",
  "prospect": {
    "external_id": "lg_demo_001",
    "account_key": "account_demo",
    "company_name": "Northline Compute (fictional)",
    "website": "https://northline.example",
    "country_code": "US",
    "tier": "1A",
    "business_model": "end_user",
    "contact": {
      "name": null,
      "department": "Procurement",
      "email": "procurement@northline.example",
      "phone": null,
      "source_url": "https://northline.example/contact",
      "source_excerpt": "Fictional demonstration only",
      "observed_at": "2026-09-23T02:00:00Z"
    },
    "review": {
      "status": "approved",
      "reviewed_by": "demo-user",
      "policy_version": "demo-only"
    }
  },
  "requested_mailbox_id": "configured-mailbox-id",
  "template_id": null
}
```

地址与身份都是演示值，不可发送。生产邮箱映射由服务端允许列表裁决。

重复幂等键返回原接收回执，载荷发生实质冲突返回 409，不能创建第二封草稿。默认幂等键不含模板版本或批次号，避免换模板导致重复首封。接收端还对组织内的规范化收件邮箱做独立首封去重，并检查同一采购实体的已有待发/已发记录。多邮箱使用同一组织去重范围。

正常回执包含 `receipt_id`、`external_id`、`prospect_id`、`status=imported`、`review_url` 和版本；它仅证明导入，不证明发信。

## 发送前与异常

实际发送动作留在 mail2leads：用户审核具体草稿及收件人，服务端保存审核人、内容哈希、模板版本和适用政策。既有身份/一次性发送令牌应扩展到新草稿，后台导入拿不到发送权限。

mail2leads 必须在外部调用之前提交 outbox、确切 MIME、Message-ID 及 attempt。SMTP 返回拒收要按该收件人落失败，不能仅因 sendmail 没抛异常就计为已发。SMTP 超时或发送后落库失败记 `delivery_unknown`，通过供应商日志或 Sent / Message-ID 对账；没有足够证据就等待核对，不能自动重发。Message-ID 本身并不能让 SMTP 服务端幂等。

## 状态事件

拟议 `POST /v1/integrations/mail2leads/events`，HMAC-SHA256 签名覆盖时间戳和原始 body，校验时间窗口、event_id 和外部账户映射。签名密钥轮换，HTTPS/受控网络传输。不能把客户端发来的邮箱状态直接作为可信事件。

```json
{
  "schema_version": "1",
  "event_id": "evt_demo_001",
  "external_id": "lg_demo_001",
  "receipt_id": "receipt_demo_001",
  "sequence": 3,
  "type": "outreach.smtp_accepted",
  "occurred_at": "2026-09-23T03:00:00Z",
  "mailbox_id": "configured-mailbox-id",
  "thread_id": "thread_demo_001",
  "message_id": "<demo-only@sender.example>"
}
```

事件：`imported / draft_ready / queued / smtp_accepted / delivery_unknown / failed / bounced / replied / unsubscribed`。退信、有效回复、退订分别保留事实时间，不能仅用“最后一条事件”覆盖全部状态；排除状态拥有最高优先级。

接收端事务内去重并写事件，提交后返回 2xx；处理前崩溃可重投，处理后回执丢失不会重复应用。顺序号处理迟到事件，原始事件可审计。拉取接口 `GET /v1/outreach/events?cursor=...` 补齐遗漏事件，使用稳定游标，不只以秒级时间戳分页。

## 最低验收

- 同一客户/地址两次交接，只创建一个待审核首封；换模板和批次不绕过。
- 邮件接口未接通或离线时，leadsgen 显示待交接或失败，不显示已发送。
- SMTP 接受与最终送达区分；模糊传输结果不自动重发。
- 重复/乱序回传不重复计数、不撤销退订。
- 一家公司的多个邮箱默认只选择一个；集团不同实体需经过明确区分。
- 回复在 mail2leads 查看，leadsgen 仅显示状态和链接。
