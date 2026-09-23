# leadsgen v0.2：真实采集与邮件交接候选版

日期：2026-09-23。范围：网站种子导入 → 有界采集 → 公开邮箱证据 → SQLite 建档 →
mail2leads 待审核序列 → 邮件状态拉取。公开上线与客户实际发送须以部署验收结果为准。

## 已确认的邮箱架构

用户确认 AI 邮箱网页为 `mail.glocalstorage.cn`，部署在大陆的应用/OA 系统。
实际邮箱为网易企业邮箱的 `larry@glocalstorage.com` 与 `sales@glocalstorage.com`。
2026-09-23 DNS 检查：`glocalstorage.com` 的 MX 指向 `hzmx01.mxmail.netease.com` 和
`hzmx02.mxmail.netease.com`。网页域名与收发邮箱域名是不同层次，不存在必须同域或同机的要求。
应用通过网易 SMTP/IMAP 收发，不改现有 MX，也不把应用服务器当邮件投递服务器。
两个账号必须分别保存凭据、邮箱身份、邮件线程与状态；本版开发信仅选一个已核验账号启用。

## 本轮业务修订

- Tier 1D 纳入仅提供机房租赁/托管的公司；不虚构其整机采购需求。
- sales、客服、通用邮箱均可首封联系；用途标签和客户匹配度分别保留。
- 首封及第 7、14、28、60、90 天共六封，由 mail2leads 执行。
- 已回复、退订、退信、人工暂停与发送不明都会停止；不再以邮箱数量衡量质量。

## 已实现

- React + Ant Design 真实工作空间；HTTP 默认读取数据库，`?demo=1` 才是虚构原型。
- 单批最多 30 个官网、robots 检查、每站有界页面与间隔、DNS/IP 固定连接防 SSRF。
- 仅解析公开页面/mailto，不猜邮箱，不绕登录、验证码或禁止采集规则。未找到邮箱不建档。
- 邮箱来源、网页哈希、时间、短片段及邮件域检查；MX 不等于收件箱已验证。
- 公司域名去重；重新采集保留交接、停止状态和选定邮箱证据。
- 国家、行业和分层初始取自任务标签，明确标示待核验。法人全名、个人姓名未公开则留空。
- 持久交接 outbox、失败退避、幂等回执、邮件事件游标与重复事件保护。
- mail2leads 独立候选分支增加导入、六封批准页、固定时间表、发送账本与停发。
- leadsgen 后端默认仅允许本机；公网监听要求可信代理密钥和登录身份。

## 仍不冒充完成的能力

- 自动全网发现搜索、PDF/浏览器渲染采集、模型分类和深度公司研究尚未接入。
- 当前需导入官网种子。没找到邮箱只表示本轮有界扫描没找到，不断言全站绝对没有。
- 跨多个发件邮箱的统一去重、自动解冻/重新投放、送达保证都不在本版。
- 具体首封和五封后续正文、发件身份、地区发送政策须在 mail2leads 确认后才可启用。
- 生产服务已有配置需先核验，不能把邮箱网页域名直接当 SMTP 主机。

## 本地运行

需要 Python 3.12+、uv、Node 22.12+。源码 `~/code/leadsgen`。

```sh
npm ci
npm run build
uv sync --locked
uv run python -m leadsgen.app
```

打开 http://127.0.0.1:8910。数据库默认 `~/.local/share/leadsgen/leadsgen.sqlite3`。
开发 UI `npm run dev` 使用 Vite 代理连接本机 8910。
邮箱接线：`LEADSGEN_MAIL_URL` 为受控 mail2leads API 基地址，`LEADSGEN_MAIL_TOKEN` 为专用导入令牌。
不配置时真实采集照常工作，交接仅保留队列且明确显示未连接。

## 发布保护

Docker 镜像仅包含 Python 程序与已构建前端，不打包真实客户库、令牌或浏览器状态。
运行端口只能先绑定服务器 loopback；完成域名、TLS、OA forward-auth、代理密钥注入之后才能公开。
SQLite 必须持久化挂载，运行非 root、单 worker，并在接线前备份原邮件库。
升级 mail2leads 需完整 `tools/precommit.sh` 通过；不可覆盖本机正在运行的主工作区。

接口详细契约见 [handoff-v2.md](handoff-v2.md)。
