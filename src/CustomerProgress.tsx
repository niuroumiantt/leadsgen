import { useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Tag,
  Timeline,
  Typography,
} from "antd";
import { api } from "./api";
import { displayTime, milestones } from "./live-types";
import type { LiveAccount, Profile } from "./live-types";

const { Text, Paragraph } = Typography;
type Draft = {
  version: number;
  milestone: string;
  profile: Profile;
  stage: string;
  next_step: string;
  due_at: string;
  reason: string;
};
type Snapshot = {
  milestone?: string;
  followup?: { stage: string; next_step: string; due_at: string };
  profile?: Profile;
  companyId?: string;
};
type Activity = {
  id: number;
  kind: string;
  actor: string;
  occurred_at: string;
  recorded_at: string;
  payload: {
    before?: Snapshot;
    after?: Snapshot;
    reason?: string;
    recipient?: string;
    type?: string;
    state?: string;
    status?: string;
    url?: string;
    result?: string;
    event?: string;
  };
};
type ActivityPage = { items: Activity[]; nextBefore: number | null };
const sequence = [
  "unverified",
  "identified",
  "contacted",
  "engaged",
  "qualified",
  "quoted",
  "negotiating",
  "won",
];
const profileLabels: Record<keyof Profile, string> = {
  product: "产品 / 需求",
  quantity: "数量 / 规模",
  country: "交付国家 / 地区",
  quote_ref: "报价 / 方案引用",
  order_ref: "PO / 合同 / 订单引用",
};
const activityLabels: Record<string, string> = {
  "account.created": "来源档案建立",
  "baseline.account": "纳入进展记录",
  "baseline.followup": "已有跟进计划快照",
  "discovery.completed": "官网采集完成",
  "company.linked": "核实企业关联",
  "company.detached": "取消企业关联",
  "assignment.offered": "发起交接",
  "assignment.accepted": "确认接手",
  "assignment.declined": "退回线索",
  "assignment.cancelled": "撤回交接",
  "mail.event": "邮件状态更新",
  "progress.updated": "记录客户进展",
};
const mailLabels: Record<string, string> = {
  accepted: "已受理",
  sent: "已发送",
  replied: "客户已回复",
  bounced: "退信",
  unsubscribed: "退订",
  paused: "已暂停",
  draft: "草稿",
  completed: "序列完成",
  suppressed: "停止联系",
};
function draftOf(a: LiveAccount): Draft {
  return {
    version: a.workflow.version,
    milestone: a.workflow.milestone,
    profile: { ...a.workflow.profile },
    stage: a.followup?.stage ?? "待联系",
    next_step: a.followup?.next_step ?? "",
    due_at: a.followup?.due_at ?? "",
    reason: "",
  };
}
function snapshotView(s: Snapshot) {
  return (
    <div>
      {s.milestone && <Tag>{milestones[s.milestone] ?? s.milestone}</Tag>}
      {s.followup && (
        <div>
          {s.followup.stage} · {s.followup.next_step || "未填下一步"} ·{" "}
          {s.followup.due_at || "未设日期"}
        </div>
      )}
      {s.companyId && (
        <Text code className="break-anywhere">
          {s.companyId}
        </Text>
      )}
      {s.profile &&
        Object.entries(s.profile)
          .filter(([, v]) => v)
          .map(([k, v]) => (
            <div key={k}>
              {profileLabels[k as keyof Profile]}：{v}
            </div>
          ))}
    </div>
  );
}

export default function CustomerProgress({
  account,
  accounts,
  identity,
  admin,
  refresh,
  onAccount,
}: {
  account: LiveAccount;
  accounts: LiveAccount[];
  identity: string;
  admin: boolean;
  refresh: () => Promise<void>;
  onAccount: (a: LiveAccount) => void;
}) {
  const { message } = App.useApp();
  const [draft, setDraft] = useState(() => draftOf(account));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [activity, setActivity] = useState<ActivityPage>({
    items: [],
    nextBefore: null,
  });
  const [historyError, setHistoryError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [linkOpen, setLinkOpen] = useState(false);
  const [linkVersion, setLinkVersion] = useState(account.company.version);
  const [target, setTarget] = useState<LiveAccount>();
  const [linkReason, setLinkReason] = useState("");
  const owner = account.assignment.owner === identity;
  const stale = draft.version !== account.workflow.version;
  const related = accounts.filter(
    (a) =>
      a.id !== account.id &&
      a.company.company_id === account.company.company_id,
  );
  const qualified = ["qualified", "quoted", "negotiating", "won"].includes(
    draft.milestone,
  );
  const closed = ["won", "lost"].includes(draft.milestone);
  useEffect(() => {
    let active = true;
    setHistoryError("");
    setActivity({ items: [], nextBefore: null });
    api<ActivityPage>(`/api/accounts/${account.id}/activity`)
      .then((result) => {
        if (active) setActivity(result);
      })
      .catch((e) => {
        if (active) setHistoryError(e.message);
      });
    return () => {
      active = false;
    };
  }, [
    account.id,
    account.workflow.version,
    account.assignment.version,
    account.company.version,
    account.mail,
    revision,
  ]);
  const loadMore = async () => {
    if (!activity.nextBefore) return;
    setHistoryLoading(true);
    try {
      const next = await api<ActivityPage>(
        `/api/accounts/${account.id}/activity?before=${activity.nextBefore}`,
      );
      setActivity((old) => ({
        items: [
          ...old.items,
          ...next.items.filter((n) => !old.items.some((o) => o.id === n.id)),
        ],
        nextBefore: next.nextBefore,
      }));
      setHistoryError("");
    } catch (e) {
      setHistoryError((e as Error).message);
    } finally {
      setHistoryLoading(false);
    }
  };
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const result = await api<{ version: number }>(
        `/api/accounts/${account.id}/progress`,
        draft,
      );
      setDraft((d) => ({
        ...d,
        version: result.version,
        stage: closed ? "结束" : d.stage,
        reason: "",
      }));
      await refresh();
      setRevision((r) => r + 1);
      message.success("进展已保存，历史记录保留。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const link = async () => {
    setSaving(true);
    try {
      await api(`/api/accounts/${account.id}/company`, {
        target_id: target?.id ?? null,
        target_version: target?.company.version ?? null,
        version: linkVersion,
        reason: linkReason,
      });
      await refresh();
      setLinkOpen(false);
      message.success("企业关联已更新，原来源和交接权限保留。");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const edit = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));
  return (
    <>
      <Card
        className="section-card"
        title="企业档案"
        extra={
          admin && (
            <Button
              onClick={() => {
                setLinkVersion(account.company.version);
                setTarget(undefined);
                setLinkReason("");
                setLinkOpen(true);
              }}
            >
              核实企业关联
            </Button>
          )
        }
      >
        <div className="identity-row">
          <Text type="secondary">企业编号</Text>
          <Text copyable className="break-anywhere">
            {account.company.company_id}
          </Text>
        </div>
        <div className="identity-row">
          <Text type="secondary">本次来源编号</Text>
          <Text copyable className="break-anywhere">
            {account.id}
          </Text>
        </div>
        <Paragraph type="secondary">
          同一企业可以有多次询价或开发记录。企业编号统一归档；每次机会分别记录阶段、负责人和邮件。
        </Paragraph>
        {related.length > 0 && (
          <div>
            <Text strong>你可查看的同企业来源</Text>
            <Space wrap>
              {related.map((a) => (
                <Button key={a.id} type="link" onClick={() => onAccount(a)}>
                  {a.name} ·{" "}
                  {a.sourceType === "sales_inbound" ? "来信" : "官网"}
                </Button>
              ))}
            </Space>
          </div>
        )}
      </Card>
      <Card
        className="section-card progress-card"
        title="客户进展与下一步"
        extra={<Tag color="blue">{milestones[account.workflow.milestone]}</Tag>}
      >
        <div className="milestone-strip">
          {sequence.map((key, index) => (
            <div
              key={key}
              className={`milestone-item ${key === account.workflow.milestone ? "current" : ""}`}
            >
              <span>{index + 1}</span>
              {milestones[key]}
            </div>
          ))}
        </div>
        <Paragraph type="secondary">
          公开邮箱是联系入口；发信、客户回复、确认需求与成交分别记录。阶段变更由负责人根据沟通证据确认。
        </Paragraph>
        {owner ? (
          <Form
            layout="vertical"
            disabled={saving}
            onFinish={() => void save()}
          >
            {stale && (
              <Alert
                className="section-card"
                type="warning"
                title="进展已有新版本，当前填写内容已保留"
                description="请先核对最新记录，再重新填写；系统不会覆盖其他人的更新。"
                action={
                  <Button
                    onClick={() => {
                      setDraft(draftOf(account));
                      setError("");
                    }}
                  >
                    载入最新记录
                  </Button>
                }
              />
            )}
            {error && (
              <Alert className="section-card" type="error" title={error} />
            )}
            <div className="progress-form-grid">
              <Form.Item label="业务阶段" required>
                <Select
                  aria-label="业务阶段"
                  value={draft.milestone}
                  onChange={(v) => edit("milestone", v)}
                  options={Object.entries(milestones).map(([value, label]) => ({
                    value,
                    label,
                  }))}
                />
              </Form.Item>
              <Form.Item label="当前行动状态" required>
                <Select
                  aria-label="当前行动状态"
                  value={closed ? "结束" : draft.stage}
                  disabled={closed}
                  onChange={(v) => edit("stage", v)}
                  options={[
                    "待联系",
                    "跟进中",
                    "等待客户",
                    "等待内部",
                    "稍后跟进",
                    "结束",
                  ].map((value) => ({ value, label: value }))}
                />
              </Form.Item>
            </div>
            <div className="progress-form-grid">
              {(Object.keys(profileLabels) as (keyof Profile)[])
                .filter((key) =>
                  key === "quote_ref"
                    ? ["quoted", "negotiating", "won"].includes(
                        draft.milestone,
                      ) || !!draft.profile[key]
                    : key === "order_ref"
                      ? draft.milestone === "won" || !!draft.profile[key]
                      : true,
                )
                .map((key) => {
                  const required =
                    key === "quote_ref"
                      ? ["quoted", "negotiating"].includes(draft.milestone)
                      : key === "order_ref"
                        ? draft.milestone === "won"
                        : qualified;
                  return (
                    <Form.Item
                      key={key}
                      label={profileLabels[key]}
                      required={required}
                    >
                      <Input
                        aria-label={profileLabels[key]}
                        value={draft.profile[key] ?? ""}
                        maxLength={
                          key === "product"
                            ? 300
                            : ["quantity", "country"].includes(key)
                              ? 100
                              : 200
                        }
                        onChange={(e) =>
                          edit("profile", {
                            ...draft.profile,
                            [key]: e.target.value,
                          })
                        }
                        placeholder={required ? "当前阶段需补齐" : "可逐步补充"}
                      />
                    </Form.Item>
                  );
                })}
            </div>
            <Form.Item label="下一步行动" required={!closed}>
              <Input.TextArea
                aria-label="下一步行动"
                rows={2}
                value={draft.next_step}
                maxLength={500}
                onChange={(e) => edit("next_step", e.target.value)}
                placeholder="例如：确认 GPU 型号与交付日期，由谁负责"
              />
            </Form.Item>
            <Form.Item label="下次跟进日期" required={!closed}>
              <Input
                aria-label="下次跟进日期"
                type="date"
                value={draft.due_at}
                onChange={(e) => edit("due_at", e.target.value)}
              />
            </Form.Item>
            <Form.Item
              label="本次进展 / 阶段依据"
              required={draft.milestone !== account.workflow.milestone}
            >
              <Input.TextArea
                aria-label="本次进展 / 阶段依据"
                rows={3}
                value={draft.reason}
                maxLength={1000}
                onChange={(e) => edit("reason", e.target.value)}
                placeholder="记录客户确认了什么，并注明邮件日期、主题或报价编号"
              />
            </Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              aria-label="保存客户进展"
              loading={saving}
              disabled={stale}
            >
              保存客户进展
            </Button>
          </Form>
        ) : (
          <>
            <Descriptions
              column={1}
              size="small"
              items={[
                {
                  key: "next",
                  label: "下一步",
                  children: account.followup?.next_step || "尚未安排",
                },
                {
                  key: "due",
                  label: "跟进日期",
                  children: account.followup?.due_at || "尚未安排",
                },
                ...Object.entries(account.workflow.profile)
                  .filter(([, v]) => v)
                  .map(([key, value]) => ({
                    key,
                    label: profileLabels[key as keyof Profile],
                    children: value,
                  })),
              ]}
            />
            <Text type="secondary">由当前负责人记录进展；接手后即可更新。</Text>
          </>
        )}
      </Card>
      <Card
        className="section-card"
        title="进展时间线"
        extra={
          <Button type="text" onClick={() => setRevision((r) => r + 1)}>
            刷新记录
          </Button>
        }
      >
        <Paragraph type="secondary">
          按记录入库顺序展示。每条记录保留操作人、当时内容和发生时间；早期缺失的历史不会补造。
        </Paragraph>
        {historyError && <Alert type="error" title={historyError} />}
        {!activity.items.length && !historyError && (
          <Empty description="暂无进展记录" />
        )}
        <Timeline
          items={activity.items.map((e) => ({
            key: e.id,
            children: (
              <div className="activity-entry">
                <Text strong>{activityLabels[e.kind] ?? e.kind}</Text>
                <div className="secondary small">
                  {displayTime(e.occurred_at)} · {e.actor}
                </div>
                {e.kind === "baseline.account" && (
                  <Paragraph>
                    此时开始保留完整进展历史；已有资料保持原样。
                  </Paragraph>
                )}
                {e.payload.recipient && (
                  <Paragraph>接收人：{e.payload.recipient}</Paragraph>
                )}
                {e.payload.before && (
                  <details>
                    <summary>变更前</summary>
                    {snapshotView(e.payload.before)}
                  </details>
                )}
                {e.payload.after && (
                  <div className="activity-after">
                    {snapshotView(e.payload.after)}
                  </div>
                )}
                {e.payload.reason && (
                  <Paragraph className="spaced">{e.payload.reason}</Paragraph>
                )}
                {e.kind === "mail.event" && (
                  <Paragraph>
                    {mailLabels[
                      e.payload.type ??
                        e.payload.event ??
                        e.payload.state ??
                        e.payload.status ??
                        ""
                    ] ?? "邮件事件已接收"}
                  </Paragraph>
                )}
                {e.payload.url && (
                  <div className="break-anywhere">{e.payload.url}</div>
                )}
                {e.payload.result && (
                  <Tag>
                    {e.payload.result === "created"
                      ? "新增来源档案"
                      : "更新已有来源"}
                  </Tag>
                )}
              </div>
            ),
          }))}
        />
        {activity.nextBefore && (
          <Button loading={historyLoading} onClick={() => void loadMore()}>
            加载更早记录
          </Button>
        )}
      </Card>
      <Modal
        open={linkOpen}
        title="核实同一企业"
        onCancel={() => setLinkOpen(false)}
        onOk={() => void link()}
        okText={target ? "关联到该企业" : "取消本来源关联"}
        okButtonProps={{
          disabled: !linkReason.trim() || (!target && !related.length),
        }}
        confirmLoading={saving}
      >
        <Paragraph>
          核对官网、法律名称或客户确认后，再关联到已有企业。仅更改企业归属；原邮件、负责人、跟进记录与访问权限各自保留。
        </Paragraph>
        <Form layout="vertical">
          <Form.Item label="同一企业的已有来源">
            <Select
              aria-label="关联企业来源"
              allowClear
              showSearch
              optionFilterProp="label"
              style={{ width: "100%" }}
              value={target?.id}
              onChange={(id) => setTarget(accounts.find((a) => a.id === id))}
              placeholder="选择目标；留空可取消当前关联"
              options={accounts
                .filter(
                  (a) =>
                    a.id !== account.id &&
                    a.company.company_id !== account.company.company_id,
                )
                .map((a) => ({
                  value: a.id,
                  label: `${a.name} · ${a.domain}`,
                }))}
            />
          </Form.Item>
          <Form.Item label="核实依据" required>
            <Input.TextArea
              aria-label="企业关联依据"
              value={linkReason}
              onChange={(e) => setLinkReason(e.target.value)}
              maxLength={1000}
              rows={3}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
