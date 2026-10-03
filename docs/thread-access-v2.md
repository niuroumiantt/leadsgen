# 接手后的会话授权 v2

客户工作台负责分配和接受；邮件服务只给已接受的负责人开放这一条共享邮箱会话。
授权使用来源版本，防止 A→B 或 A→B→A 期间迟到的请求及回执覆盖最新分配。

## 接口

向邮件服务发送 `POST /v2/followups/access`，使用现有集成 Bearer 令牌。

```json
{
  "external_id": "mail_42",
  "thread_id": 991,
  "recipient": "owner@example.test",
  "assignment_version": 2
}
```

`assignment_version` 必须是正整数，取本次已经接受的分配事件版本。
尚未接受的转交提议不产生新授权。接口只接受已登记跟进人员和允许交接的共享会话。

成功回执：

```json
{
  "external_id": "mail_42",
  "thread_id": "991",
  "owner": "owner@example.test",
  "version": 1,
  "assignment_version": 2
}
```

`version` 是邮件服务的本地授权版本；`assignment_version` 是客户工作台来源版本，
两者独立。客户端必须同时核对客户编号、会话、负责人及来源版本才确认授权成功。
旧版本或同版本不同负责人的请求返回 409；同版本同负责人的重试幂等。
成功及失败回执均只更新请求对应的队列快照，新分配不受旧回执的确认或退避影响。

## 升级与回滚

1. 先升级 Aimail，提供 v2 接口并保持尚未版本化会话的 v1 兼容。
2. 再升级 Leadsgen。已有授权队列按最后一次对应负责人的接受事件补齐来源版本，
   重新同步一次以建立远端版本保护；重启不重复重置状态，不重放内部通知。
   缺少接受事件且仍有待接受提议时不猜测授权版本，保留未确认队列；需完成正式接手。
3. 验收 A→B、A→B→A、迟到成功/失败回执及旧请求：前任无权访问，最新负责人有权访问，
   当前队列保持正确结果。仓库联合检查由 `infra/scripts/test_sales_workflow.sh` 执行。

新客户端遇到旧邮件服务的 404 时保留队列并重试，不回退到 v1。
会话一旦收到 v2 授权，v1 不能再覆盖它；回滚旧客户端会得到 409，需要恢复兼容的新版。
升级前备份数据库；运行服务升级及回滚按部署仓库的正式发布流程执行。

邮件侧持久化、权限边界和跨邮箱回复规则见 Aimail 的
[ADR 0009](https://github.com/niuroumiantt/aimail/blob/main/docs/adr/0009-versioned-thread-access.md)。
