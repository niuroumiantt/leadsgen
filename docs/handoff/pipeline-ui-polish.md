# 线索工作台界面优化

2026-09-27，配套 Aimail `codex/followup-ui-polish`。

使用既有 Ant Design，参考 [工作台原则](https://ant.design/docs/spec/research-workbench/) 与 [客户记录布局](https://knowledge.hubspot.com/records/work-with-records)。优先展示客户、负责人、接手状态、下一步和计划日期。统计区与常用筛选收紧；电脑保留表格，窄屏采用卡片。详情按摘要、客户信息、来源、分配、历史分区。

生产此前的交接流程已由用户确认可用。这次只优化展示与筛选，角色隔离、通知幂等、接受/退回的版本校验保持既有实现。主动开发的窄屏交接名单选择仍保留。

验证：构建通过；Python 31 passed、1 skipped；Chrome/Playwright 用虚构数据检查 1440×1050、834×1112、390×844，以及详情抽屉、长地址换行和页面溢出。未发送测试邮件。

发布：构建合并后的完整 server/dist，备份现有数据库，更新 Leadsgen 镜像标签。无需重置同步游标或迁移数据；检查管理员/员工可见记录与生产发布前一致。
