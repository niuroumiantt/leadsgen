import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Dropdown,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  Layout,
  Menu,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  CompassOutlined,
  LinkOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  TeamOutlined,
  UserOutlined,
  LogoutOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { regions, stageInfo, mailInfo } from "./data";
import type { Account } from "./data";

const { Title, Text, Paragraph } = Typography;
type Contact = {
  email: string;
  role: string;
  url: string;
  excerpt: string;
  observedAt: string;
  mailRoute: string;
};
type LiveAccount = Account & {
  website: string;
  phone: string;
  contacts: Contact[];
  evidence: Contact;
  mailRoute: string;
  observedAt: string;
  classificationVerified: boolean;
  sourceType?: "sales_inbound" | "discovery";
  sourceMailbox?: string;
  aimailLeadId?: string;
  aimailThreadId?: string;
  aimailContact?: string;
  aimailQuantity?: string;
  assignment: {
    owner: string;
    pending: string;
    version: number;
    mail_access_status?: string;
    mail_access_error?: string;
  };
  followup: { stage: string; next_step: string; due_at: string } | null;
};
type Job = {
  id: string;
  name: string;
  region: string;
  created_at: string;
  candidates: {
    id: number;
    url: string;
    status: string;
    reason: string;
    pages: number;
  }[];
};
type State = {
  role: "admin" | "sales";
  identity: string;
  members: string[];
  accounts: LiveAccount[];
  jobs: Job[];
  handoffs: {
    id: string;
    account_id: string;
    status: string;
    last_error: string;
    attempts: number;
  }[];
  mailConnected: boolean;
  cadenceDays: number[];
};

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Leadsgen-Client": "web-v1",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "服务响应异常" }));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : `请求失败 (${response.status})`,
    );
  }
  return response.json();
}

export default function Workspace() {
  const { message, modal } = App.useApp();
  const [state, setState] = useState<State>();
  const [error, setError] = useState("");
  const [page, setPage] = useState("accounts");
  const [query, setQuery] = useState("");
  const [region, setRegion] = useState<string>();
  const [country, setCountry] = useState<string>();
  const [industry, setIndustry] = useState<string>();
  const [tier, setTier] = useState<string>();
  const [selection, setSelection] = useState<React.Key[]>([]);
  const [detail, setDetail] = useState<LiveAccount>();
  const [jobOpen, setJobOpen] = useState(false);
  const [handoffOpen, setHandoffOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [assignee, setAssignee] = useState("");
  const [nextStep, setNextStep] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [followupStage, setFollowupStage] = useState("待联系");
  const [form] = Form.useForm();
  const refresh = useCallback(async () => {
    try {
      setState(await api<State>("/api/state"));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [refresh]);
  const data = state?.accounts ?? [];
  const isAdmin = state?.role === "admin";
  const openDetail = (account: LiveAccount) => {
    setDetail(account);
    setNextStep(account.followup?.next_step ?? "");
    setDueAt(account.followup?.due_at ?? "");
    setFollowupStage(account.followup?.stage ?? "待联系");
  };
  const filtered = data
    .filter(
      (a) =>
        (!query ||
          `${a.name} ${a.domain} ${a.email}`
            .toLowerCase()
            .includes(query.toLowerCase())) &&
        (!region || a.region === region) &&
        (!country || a.country === country) &&
        (!industry || a.industry === industry) &&
        (!tier || a.tier.startsWith(tier)),
    )
    .sort(
      (a, b) =>
        a.tier[0].localeCompare(b.tier[0]) ||
        regions.indexOf(a.region) - regions.indexOf(b.region) ||
        a.tier.localeCompare(b.tier),
    );
  const selected = data.filter(
    (a) => selection.includes(a.id) && a.stage === "ready",
  );
  const selectContact = (email: string) => {
    if (!detail) return;
    modal.confirm({
      title: "确认这是该公司的公开业务联系入口？",
      content: `${email}。用途标签不代表采购身份；交接后仍需在邮件系统确认发送内容。`,
      onOk: async () => {
        try {
          await api(`/api/accounts/${detail.id}/contact`, { email });
          setDetail(undefined);
          await refresh();
          message.success("联系邮箱已确认，可加入交接名单。");
        } catch (e) {
          message.error((e as Error).message);
          throw e;
        }
      },
    });
  };
  const suppressAccount = () => {
    if (!detail) return;
    modal.confirm({
      title: "停止联系这家公司？",
      content:
        "停止新的交接；已交接客户会向邮件系统请求暂停，收到回执后才显示已停止。",
      onOk: async () => {
        try {
          await api(`/api/accounts/${detail.id}/suppress`, {});
          setDetail(undefined);
          await refresh();
          message.success("已保存禁止联系；邮件系统停发状态见交接记录。");
        } catch (e) {
          message.error((e as Error).message);
          throw e;
        }
      },
    });
  };
  const assignAccount = async () => {
    if (!detail || !assignee) return;
    setSaving(true);
    try {
      await api(`/api/accounts/${detail.id}/assignment`, {
        recipient: assignee,
        version: detail.assignment?.version ?? 0,
      });
      setAssignee("");
      setDetail(undefined);
      await refresh();
      message.success("已发送线索接手邀请；员工接受后成为负责人。");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const decideAssignment = async (accept: boolean) => {
    if (!detail) return;
    setSaving(true);
    try {
      await api(
        `/api/accounts/${detail.id}/assignment/${accept ? "accept" : "decline"}`,
        { version: detail.assignment.version },
      );
      setDetail(undefined);
      await refresh();
      message.success(accept ? "已接手这条线索。" : "已退回这条线索。管理员可重新分配。");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const saveFollowup = async () => {
    if (!detail) return;
    setSaving(true);
    try {
      await api(`/api/accounts/${detail.id}/followup`, {
        stage: followupStage,
        next_step: nextStep,
        due_at: dueAt,
      });
      setDetail(undefined);
      await refresh();
      message.success("跟进计划已保存。");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const items = isAdmin
    ? [
        { key: "accounts", icon: <TeamOutlined />, label: "线索初筛" },
        { key: "jobs", icon: <CompassOutlined />, label: "采集任务" },
        { key: "outreach", icon: <LinkOutlined />, label: "交接与联系状态" },
      ]
    : [{ key: "accounts", icon: <TeamOutlined />, label: "我的线索" }];
  const labels: Record<string, string> = {
    queued: "等待采集",
    running: "正在采集",
    completed: "已建档",
    skipped: "已跳过",
    failed: "采集失败",
  };
  const createJob = async (values: Record<string, string>) => {
    const urls = values.urls
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!urls.length || urls.length > 30) {
      message.error("每批请输入 1–30 个官网网址，每行一个。");
      return;
    }
    setSaving(true);
    try {
      await api("/api/jobs", {
        name: values.name,
        region: values.region,
        seeds: urls.map((url) => ({
          url,
          country: values.country || "待核验",
          industry: values.industry || "待核验",
          tier: values.tier,
        })),
      });
      setJobOpen(false);
      setPage("jobs");
      await refresh();
      message.success("采集任务已保存，后台开始处理。");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const handoff = async () => {
    setSaving(true);
    try {
      const result = await api<{ queued: string[]; skipped: string[] }>(
        "/api/handoffs",
        { ids: selected.map((a) => a.id) },
      );
      setSelection([]);
      setHandoffOpen(false);
      await refresh();
      setPage("outreach");
      message.success(`已加入 ${result.queued.length} 家至持久交接队列。`);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Layout className="app-shell">
      <Layout.Sider width={218} theme="light" className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            lg
            <span />
          </div>
          <div>
            <strong>leadsgen</strong>
            <small>GLOCAL STORAGE</small>
          </div>
        </div>
        <div className="nav-label">真实工作空间</div>
        <Menu
          items={items}
          selectedKeys={[page]}
          onClick={({ key }) => setPage(key)}
        />
        {isAdmin && <>
        <div className="nav-label region-label">
          目标市场<span>优先顺序</span>
        </div>
        <div className="region-menu">
          {regions.map((r, i) => (
            <button
              key={r}
              onClick={() => {
                setRegion(r);
                setPage("accounts");
              }}
            >
              <span className="region-index">0{i + 1}</span>
              {r}
              <span className="region-count">
                {data.filter((a) => a.region === r).length}
              </span>
            </button>
          ))}
        </div>
        <div className="sidebar-note">
          公开业务邮箱均可作为入口
          <br />
          采购 · 销售 · 客服 · 通用联系
        </div>
        </>}
      </Layout.Sider>
      <Layout className="main-layout">
        <Layout.Header className="topbar">
          <Space>
            Glocal Storage{" "}
            <Text type="secondary">
              / {items.find((i) => i.key === page)?.label}
            </Text>
          </Space>
          <Space>
            <Tag color="blue">真实数据</Tag>
            <Button
              type="text"
              icon={<ReloadOutlined />}
              onClick={() => void refresh()}
            >
              刷新
            </Button>
            {state?.identity && <Dropdown
              trigger={["click"]}
              menu={{ items: [
                { key: "identity", label: `当前账号：${state.identity}`, disabled: true },
                { type: "divider" },
                { key: "profile", icon: <SettingOutlined />, label: <a href="https://login.glocalstorage.cn/if/user/#/settings">个人资料与账号设置</a> },
                { key: "logout", icon: <LogoutOutlined />, danger: true, label: <a href="/oauth2/sign_out?rd=https%3A%2F%2Flogin.glocalstorage.cn%2Fif%2Fuser%2F">退出当前应用</a> },
              ] }}
            >
              <Button aria-label={`当前账号 ${state.identity}`} icon={<UserOutlined />}>{state.identity}</Button>
            </Dropdown>}
          </Space>
        </Layout.Header>
        <nav className="mobile-navigation" aria-label="主导航">
          {items.map((i) => (
            <Button
              key={i.key}
              type={page === i.key ? "primary" : "text"}
              onClick={() => setPage(i.key)}
            >
              {i.label}
            </Button>
          ))}
        </nav>
        <Layout.Content className="page-content">
          {error && (
            <Alert
              type="error"
              showIcon
              title={error}
              description="数据加载失败；保留最后成功状态，不使用示例数据替代。"
              className="section-card"
            />
          )}
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                PUBLIC CONTACTS. REAL OPPORTUNITIES.
              </div>
              <Title level={2}>
                {page === "accounts"
                  ? isAdmin ? "统一初筛主动发现与客户来信。" : "处理分配给我的客户线索。"
                  : page === "jobs"
                    ? "每一次采集都有结果和出处。"
                    : "首封之后，按计划继续联系。"}
              </Title>
              <Paragraph className="page-subtitle">
                终端用户、机房租赁商、集成商与贸易商均纳入。邮箱用途是标签，不是排除理由。
              </Paragraph>
            </div>
            {isAdmin && <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setJobOpen(true)}
            >
              新建采集任务
            </Button>}
          </div>
          {page === "accounts" && (
            <>
              <div className="metrics">
                {(isAdmin ? [
                  { label: "全部线索", value: data.length },
                  { label: "主动发现", value: data.filter((a) => a.sourceType !== "sales_inbound").length },
                  { label: "sales 来信", value: data.filter((a) => a.sourceType === "sales_inbound").length },
                  {
                    label: "待分配", value: data.filter((a) => !a.assignment?.owner && !a.assignment?.pending).length,
                  },
                ] : [
                  { label: "我的线索", value: data.length },
                  { label: "待接手", value: data.filter((a) => a.assignment.pending === state?.identity).length },
                  { label: "跟进中", value: data.filter((a) => a.followup?.stage === "跟进中").length },
                  { label: "等待客户", value: data.filter((a) => a.followup?.stage === "等待客户").length },
                ]).map((m) => (
                  <Card key={m.label} className="metric-card">
                    <Text type="secondary">{m.label}</Text>
                    <Title level={2}>{m.value}</Title>
                  </Card>
                ))}
              </div>
              <div className="accounts-panel">
                <div className="filters">
                  <Input
                    className="search-input"
                    prefix={<SearchOutlined />}
                    placeholder="搜索公司、官网或邮箱"
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value);
                      setSelection([]);
                    }}
                  />
                  <Select
                    allowClear
                    placeholder="全部区域"
                    value={region}
                    onChange={(v) => {
                      setRegion(v);
                      setSelection([]);
                    }}
                    options={regions.map((r) => ({ label: r, value: r }))}
                  />
                  <Select
                    allowClear
                    placeholder="全部行业"
                    value={industry}
                    onChange={(v) => {
                      setIndustry(v);
                      setSelection([]);
                    }}
                    className="industry-filter"
                    options={[...new Set(data.map((a) => a.industry))].map(
                      (i) => ({ label: i, value: i }),
                    )}
                  />
                  <Select
                    allowClear
                    placeholder="全部国家"
                    value={country}
                    onChange={(v) => {
                      setCountry(v);
                      setSelection([]);
                    }}
                    options={[...new Set(data.map((a) => a.country))].map(
                      (c) => ({ label: c, value: c }),
                    )}
                  />
                  <Select
                    allowClear
                    placeholder="客户分层"
                    value={tier}
                    onChange={(v) => {
                      setTier(v);
                      setSelection([]);
                    }}
                    options={[
                      { label: "Tier 1", value: "1" },
                      { label: "Tier 2", value: "2" },
                    ]}
                  />
                </div>
                <div className="table-toolbar">
                  <Text type="secondary">
                    {filtered.length} 条线索 · {isAdmin ? "管理员全局视图" : "仅本人负责或待本人接手"}
                  </Text>
                  {isAdmin && <Button
                    icon={<LinkOutlined />}
                    disabled={!selected.length || !!error}
                    onClick={() => setHandoffOpen(true)}
                  >
                    交接名单 ({selected.length})
                  </Button>}
                </div>
                <Table
                  rowKey="id"
                  dataSource={filtered}
                  loading={!state && !error}
                  scroll={{ x: 1100 }}
                  pagination={{ pageSize: 12, showSizeChanger: false }}
                  rowSelection={isAdmin ? {
                    selectedRowKeys: selection,
                    onChange: setSelection,
                    getCheckboxProps: (a) => ({
                      disabled: a.stage !== "ready" || a.sourceType === "sales_inbound",
                    }),
                  } : undefined}
                  locale={{
                    emptyText: (
                      <Empty description="尚未建档。添加官网种子，开始第一批真实采集。" />
                    ),
                  }}
                  columns={[
                    {
                      title: "公司 / 官网",
                      width: 250,
                      render: (_, a) => (
                        <>
                          <Button
                            type="link"
                            className="company-link"
                            onClick={() => openDetail(a)}
                          >
                            {a.name}
                          </Button>
                          <div className="secondary mono">{a.sourceType === "sales_inbound" ? "sales@ 客户来信" : a.domain}</div>
                        </>
                      ),
                    },
                    {
                      title: "国家 / 行业",
                      width: 180,
                      render: (_, a) => (
                        <>
                          {a.country}
                          <div className="secondary small">{a.industry}</div>
                        </>
                      ),
                    },
                    {
                      title: "分层",
                      width: 100,
                      render: (_, a) => <Tag color="blue">Tier {a.tier}</Tag>,
                    },
                    {
                      title: "公开邮箱",
                      width: 290,
                      render: (_, a) => (
                        <>
                          <Text className="mono">{a.email || "待核实"}</Text>
                          <div className="secondary small">{a.sourceType === "sales_inbound" ? `客户来信 · ${a.aimailContact || "联系人待核实"}` : a.department}</div>
                        </>
                      ),
                    },
                    {
                      title: "档案状态",
                      width: 100,
                      render: (_, a) => (
                        <Tag color={stageInfo[a.stage].color}>
                          {stageInfo[a.stage].label}
                        </Tag>
                      ),
                    },
                    {
                      title: "负责人 / 阶段",
                      width: 180,
                      render: (_, a) => (
                        <>
                          <div>{a.assignment?.pending ? `待 ${a.assignment.pending} 接手` : a.assignment?.owner || "未分配"}</div>
                          <div className="secondary small">{a.followup?.stage || "初筛 / 待分配"}</div>
                        </>
                      ),
                    },
                    {
                      title: "操作",
                      render: (_, a) => (
                        <Button type="link" onClick={() => openDetail(a)}>
                          详情
                        </Button>
                      ),
                    },
                  ]}
                />
              </div>
            </>
          )}
          {page === "jobs" && (
            <>
              <Alert
                showIcon
                type="info"
                title="有界采集，后台持续运行"
                description="只访问公开官网，读取联系页优先。没有业务邮箱不建档；sales@、info@、support@ 均可保留。每批最多 30 个网站。"
              />
              <Card title="采集批次" className="section-card">
                <Table
                  rowKey="id"
                  dataSource={state?.jobs ?? []}
                  expandable={{
                    expandedRowRender: (j) => (
                      <Table
                        size="small"
                        rowKey="id"
                        pagination={false}
                        dataSource={j.candidates}
                        columns={[
                          { title: "官网", dataIndex: "url" },
                          {
                            title: "状态",
                            dataIndex: "status",
                            render: (v) => <Tag>{labels[v] ?? v}</Tag>,
                          },
                          { title: "已读页数", dataIndex: "pages" },
                          { title: "结果 / 原因", dataIndex: "reason" },
                        ]}
                      />
                    ),
                  }}
                  columns={[
                    { title: "任务", dataIndex: "name" },
                    { title: "区域", dataIndex: "region" },
                    { title: "网站数", render: (_, j) => j.candidates.length },
                    {
                      title: "已完成",
                      render: (_, j) =>
                        j.candidates.filter((c) =>
                          ["completed", "skipped", "failed"].includes(c.status),
                        ).length,
                    },
                    {
                      title: "创建时间",
                      dataIndex: "created_at",
                      render: (v) => new Date(v).toLocaleString(),
                    },
                  ]}
                />
              </Card>
            </>
          )}
          {page === "outreach" && (
            <>
              <Alert
                showIcon
                type={state?.mailConnected ? "info" : "warning"}
                title={
                  state?.mailConnected
                    ? "邮件交接接口已配置，以下以接收回执为准"
                    : "邮件接口尚未配置：名单可以排队，但不会发送"
                }
                description="邮件内容和发件账户确定后，在 mail2leads 启用经确认的序列。leadsgen 不持有 SMTP 凭据。"
              />
              <Card title="跟进节奏" className="section-card">
                <div className="region-priority">
                  {[0, 7, 14, 28, 60, 90].map((day) => (
                    <div key={day}>
                      <span>{day === 0 ? "首封" : "第"}</span>
                      <strong>{day === 0 ? "Day 0" : `${day} 天`}</strong>
                    </div>
                  ))}
                </div>
                <Paragraph>
                  后续日期均从首封成功发出时计算，不是累计间隔。任何回复、退订、硬退信或人工暂停后停止；发送结果不确定时先核对。
                </Paragraph>
                <Tag>模板待确定</Tag>
              </Card>
              <Card title="持久交接记录" className="section-card">
                <Table
                  rowKey="id"
                  dataSource={state?.handoffs ?? []}
                  columns={[
                    {
                      title: "公司",
                      render: (_, h) =>
                        data.find((a) => a.id === h.account_id)?.name ??
                        h.account_id,
                    },
                    {
                      title: "交接状态",
                      dataIndex: "status",
                      render: (v) => (
                        <Tag>
                          {(
                            {
                              queued: "待交接",
                              delivering: "正在交接",
                              accepted: "已接收",
                              cancelled: "已取消",
                              stop_pending: "停发请求待确认",
                              stopped: "已确认暂停",
                            } as Record<string, string>
                          )[v] ?? v}
                        </Tag>
                      ),
                    },
                    { title: "尝试次数", dataIndex: "attempts" },
                    {
                      title: "邮件状态",
                      render: (_, h) => {
                        const a = data.find((a) => a.id === h.account_id);
                        return a
                          ? (mailInfo[a.mail]?.label ?? a.mail)
                          : "未发起";
                      },
                    },
                    { title: "连接提示", dataIndex: "last_error" },
                  ]}
                />
              </Card>
            </>
          )}
        </Layout.Content>
      </Layout>
      <Drawer
        open={!!detail}
        onClose={() => setDetail(undefined)}
        title="客户档案 · 真实采集"
        size={650}
      >
        {detail && (
          <>
            <Title level={3}>{detail.name}</Title>
            <Paragraph>{detail.summary}</Paragraph>
            <Descriptions
              bordered
              size="small"
              column={1}
              items={[
                {
                  key: "full",
                  label: "公司全名",
                  children: detail.legal || "尚未公开或核实",
                },
                {
                  key: "site",
                  label: "官网",
                  children: (
                    <a href={detail.website} target="_blank" rel="noreferrer">
                      {detail.website}
                    </a>
                  ),
                },
                {
                  key: "country",
                  label: "目标国家 / 地区",
                  children: `${detail.country}（采集计划标签）`,
                },
                {
                  key: "tier",
                  label: "分层",
                  children: `Tier ${detail.tier} · 分类待核验`,
                },
                {
                  key: "phone",
                  label: "电话",
                  children: detail.phone || "本轮未提取（可查来源页）",
                },
                { key: "sm", label: "Supermicro", children: detail.supermicro },
              ]}
            />
            <DividerLine />
            <Title level={5}>公开联系方式与来源</Title>
                {detail.sourceType === "sales_inbound" && <Card size="small" className="section-card" title="来信线索来源">
                  <Paragraph>由 Aimail 人工确认并同步；邮件正文、附件和线程仍保存在 Aimail。</Paragraph>
                  {detail.aimailQuantity && <Paragraph>数量信息：{detail.aimailQuantity}</Paragraph>}
                  {detail.assignment?.mail_access_status === "granted" && detail.aimailThreadId ? (
                    <a href={`https://mail.glocalstorage.cn/followups/${detail.aimailThreadId}`} target="_blank" rel="noreferrer">打开 Aimail 中已授权的邮件线程 ↗</a>
                  ) : <Text type="secondary">{detail.assignment?.mail_access_error || "邮件线程权限同步中；完成后会出现安全链接。"}</Text>}
                  <div><Text copyable>邮件线程 ID：{detail.aimailThreadId || "未提供"}</Text></div>
                  <div><Text type="secondary">确认线索 ID：{detail.aimailLeadId}</Text></div>
                </Card>}
                {detail.sourceType !== "sales_inbound" && detail.contacts?.map((c) => (
              <Card size="small" key={c.email} className="section-card">
                <Text copyable>{c.email}</Text>
                <Tag className="spaced">
                  {(
                    {
                      procurement: "采购入口",
                      partnership: "合作入口",
                      sales: "销售入口",
                      support: "客服入口",
                      general: "通用联系",
                      business: "用途待核实",
                    } as Record<string, string>
                  )[c.role] ?? c.role}
                </Tag>
                <Paragraph className="spaced">{c.excerpt}</Paragraph>
                <a href={c.url} target="_blank" rel="noreferrer">
                  查看来源页面
                </a>
                <div className="secondary small spaced">
                  {new Date(c.observedAt).toLocaleString()} · {c.mailRoute}
                </div>
                {isAdmin && ["ready", "review"].includes(detail.stage) && (
                  <Button
                    className="spaced"
                    size="small"
                    onClick={() => selectContact(c.email)}
                    disabled={
                      !["mx_present", "implicit_mx_unverified"].includes(
                        c.mailRoute,
                      )
                    }
                  >
                    {detail.email.toLowerCase() === c.email.toLowerCase() &&
                    detail.stage === "ready"
                      ? "当前联系邮箱 · 重新确认"
                      : "确认并选为联系邮箱"}
                  </Button>
                )}
              </Card>
            ))}
            <Card className="section-card" title={isAdmin ? "负责人分配" : "我的跟进"}>
              {isAdmin ? (
                <>
                  <Paragraph>当前负责人：{detail.assignment?.owner || "未分配"}；待接手：{detail.assignment?.pending || "无"}</Paragraph>
                  <Space.Compact block>
                    <Select
                      aria-label="选择线索接收员工"
                      value={assignee || undefined}
                      placeholder="选择已授权销售员工"
                      onChange={setAssignee}
                      options={(state?.members ?? []).map((member) => ({ label: member, value: member }))}
                    />
                    <Button type="primary" loading={saving} disabled={!assignee || !!detail.assignment?.pending} onClick={() => void assignAccount()}>
                      提交交接
                    </Button>
                  </Space.Compact>
                </>
              ) : detail.assignment?.pending === state?.identity ? (
                <Space>
                  <Button type="primary" loading={saving} onClick={() => void decideAssignment(true)}>接受线索</Button>
                  <Button loading={saving} onClick={() => void decideAssignment(false)}>退回管理员</Button>
                </Space>
              ) : detail.assignment?.owner === state?.identity ? (
                <Space direction="vertical" style={{ width: "100%" }}>
                  <Select
                    aria-label="跟进阶段"
                    value={followupStage}
                    onChange={setFollowupStage}
                    options={["待联系", "跟进中", "等待客户", "等待内部", "稍后跟进", "结束"].map((value) => ({ label: value, value }))}
                  />
                  {detail.followup?.next_step && <Text type="secondary">上次下一步：{detail.followup.next_step}</Text>}
                  {detail.followup?.due_at && <Text type="secondary">计划时间：{detail.followup.due_at}</Text>}
                  <Input value={nextStep} onChange={(e) => setNextStep(e.target.value)} placeholder="下一步要做什么" maxLength={500} />
                  <Input value={dueAt} onChange={(e) => setDueAt(e.target.value)} placeholder="下次跟进时间，例如 2026-10-01" maxLength={40} />
                  <Button type="primary" loading={saving} onClick={() => void saveFollowup()}>保存跟进计划</Button>
                </Space>
              ) : <Text type="secondary">这条线索尚未分配给你。</Text>}
            </Card>
            <Alert
              className="section-card"
              showIcon
              type="info"
              title="邮箱角色不等于实际决策人"
              description="公开销售或客服入口可能由老板、主管接收，或转交对应部门。保留用途标签，但不因此排除。"
            />
            {isAdmin && <Button
              danger
              disabled={detail.stage === "suppressed"}
              onClick={suppressAccount}
            >
              停止联系这家公司
            </Button>}
          </>
        )}
      </Drawer>
      <Drawer
        title="新建真实采集任务"
        open={jobOpen}
        onClose={() => setJobOpen(false)}
        size={520}
        footer={
          <Button
            block
            type="primary"
            loading={saving}
            onClick={() => form.submit()}
          >
            保存并开始采集
          </Button>
        }
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            name: "美国 · 第一批客户",
            region: "美国",
            country: "美国",
            industry: "云服务 / 数据中心",
            tier: "1B",
          }}
          onFinish={createJob}
        >
          <Form.Item
            name="name"
            label="任务名称"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item
            name="region"
            label="目标区域"
            rules={[{ required: true }]}
          >
            <Select options={regions.map((r) => ({ label: r, value: r }))} />
          </Form.Item>
          <Form.Item name="country" label="目标国家 / 地区">
            <Input maxLength={80} />
          </Form.Item>
          <Form.Item name="industry" label="行业标签">
            <Select
              options={[
                "云服务 / 数据中心",
                "机房租赁 / 托管",
                "GPU 云 / AI 算力",
                "金融 / 企业 IT",
                "行业系统集成",
                "服务器渠道",
                "配件贸易",
                "待核验",
              ].map((i) => ({ label: i, value: i }))}
            />
          </Form.Item>
          <Form.Item name="tier" label="初始分层" rules={[{ required: true }]}>
            <Select
              options={["1A", "1B", "1C", "1D", "2A", "2B"].map((t) => ({
                label: `Tier ${t}${t === "1D" ? " · 机房租赁 / 托管" : ""}`,
                value: t,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="urls"
            label="公开官网网址（每行一个，最多 30 个）"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input.TextArea
              rows={8}
              maxLength={60000}
              placeholder="https://company.com/"
            />
          </Form.Item>
          <Text type="secondary">
            国家与行业是任务标签，不冒充网站已证实的事实。新站默认保留原文和来源；模型不会猜测邮箱。
          </Text>
        </Form>
      </Drawer>
      <Modal
        open={handoffOpen}
        onCancel={() => setHandoffOpen(false)}
        title="交接名单到 mail2leads"
        okText="确认加入交接队列"
        confirmLoading={saving}
        onOk={() => void handoff()}
      >
        <Paragraph>
          共 {selected.length}{" "}
          家公司，每家只选一个公开邮箱。此操作不会直接发邮件。
        </Paragraph>
        {selected.map((a) => (
          <Paragraph key={a.id}>
            {a.name}
            <br />
            <Text type="secondary">
              {a.email} · {a.department}
            </Text>
          </Paragraph>
        ))}
        <Alert
          type="info"
          showIcon
          title="默认序列：首封 + 第 7、14、28、60、90 天"
          description="待邮件内容确认后由 mail2leads 激活。回复、退订、硬退信后停止。"
        />
      </Modal>
    </Layout>
  );
}

function DividerLine() {
  return <div style={{ height: 24 }} />;
}
