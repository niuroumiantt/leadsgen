import { useMemo, useState } from "react";
import type { Key } from "react";
import {
  App as AntApp,
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Layout,
  Menu,
  Modal,
  Select,
  Space,
  Statistic,
  Steps,
  Table,
  Tabs,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import {
  ApartmentOutlined,
  ArrowRightOutlined,
  CheckCircleOutlined,
  CheckOutlined,
  CompassOutlined,
  FileSearchOutlined,
  FilterOutlined,
  GlobalOutlined,
  InfoCircleOutlined,
  LinkOutlined,
  MailOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SettingOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { accounts as seedAccounts, mailInfo, regions, stageInfo } from "./data";
import type { Account } from "./data";

const { Text, Title, Paragraph } = Typography;
type Page = "accounts" | "discovery" | "handoffs" | "rules";
type Recipe = {
  id: number;
  name: string;
  region: string;
  tier: string;
  limit: number;
  status: string;
};
const initialRecipes: Recipe[] = [
  {
    id: 1,
    name: "美国 · Supermicro 云与裸金属",
    region: "美国",
    tier: "Tier 1",
    limit: 30,
    status: "计划示例",
  },
  {
    id: 2,
    name: "欧洲 · GPU 云与基础设施",
    region: "欧洲",
    tier: "Tier 1",
    limit: 30,
    status: "计划示例",
  },
  {
    id: 3,
    name: "中国 · 整机采购与行业集成",
    region: "中国",
    tier: "Tier 1",
    limit: 20,
    status: "计划示例",
  },
];
const pageNames: Record<Page, string> = {
  accounts: "客户档案",
  discovery: "采集计划",
  handoffs: "交接记录",
  rules: "分类规则",
};

function TierTag({ tier }: { tier: string }) {
  return (
    <Tag variant="filled" color={tier.startsWith("1") ? "blue" : "default"}>
      Tier {tier}
    </Tag>
  );
}
function StageTag({ account }: { account: Account }) {
  return (
    <Badge
      status={
        stageInfo[account.stage].color as
          "success" | "warning" | "processing" | "default"
      }
      text={stageInfo[account.stage].label}
    />
  );
}

export default function App() {
  const { message } = AntApp.useApp();
  const [data, setData] = useState(seedAccounts);
  const [page, setPage] = useState<Page>("accounts");
  const [query, setQuery] = useState("");
  const [region, setRegion] = useState<string>();
  const [country, setCountry] = useState<string>();
  const [industry, setIndustry] = useState<string>();
  const [tier, setTier] = useState<string>();
  const [view, setView] = useState("all");
  const [onlySM, setOnlySM] = useState(false);
  const [selected, setSelected] = useState<Key[]>([]);
  const [detail, setDetail] = useState<Account>();
  const [handoffOpen, setHandoffOpen] = useState(false);
  const [recipeOpen, setRecipeOpen] = useState(false);
  const [recipes, setRecipes] = useState(initialRecipes);
  const [form] = Form.useForm();

  const filtered = useMemo(
    () =>
      data
        .filter(
          (a) =>
            (!query ||
              `${a.name} ${a.legal} ${a.domain} ${a.email}`
                .toLowerCase()
                .includes(query.toLowerCase())) &&
            (!region || a.region === region) &&
            (!country || a.country === country) &&
            (!industry || a.industry === industry) &&
            (!tier || a.tier.startsWith(tier)) &&
            (!onlySM || a.supermicro === "使用已证实") &&
            (view === "all" ||
              (view === "tier1" && a.tier.startsWith("1")) ||
              (view === "tier2" && a.tier.startsWith("2")) ||
              a.stage === view),
        )
        .sort(
          (a, b) =>
            a.tier[0].localeCompare(b.tier[0]) ||
            regions.indexOf(a.region) - regions.indexOf(b.region) ||
            a.tier.localeCompare(b.tier) ||
            b.score - a.score,
        ),
    [data, query, region, country, industry, tier, view, onlySM],
  );
  const eligible = data.filter(
    (a) => selected.includes(a.id) && a.stage === "ready",
  );
  const handoffs = data.filter(
    (a) =>
      a.stage === "queued" || a.stage === "handed" || a.stage === "suppressed",
  );
  const reset = () => {
    setQuery("");
    setRegion(undefined);
    setCountry(undefined);
    setIndustry(undefined);
    setTier(undefined);
    setOnlySM(false);
    setSelected([]);
  };
  const selectRegion = (value: string) => {
    setRegion(value);
    setCountry(undefined);
    setPage("accounts");
    setView("all");
    setSelected([]);
  };
  const filterChange = () => setSelected([]);
  const openHandoff = (a?: Account) => {
    if (a) setSelected([a.id]);
    setHandoffOpen(true);
  };

  const columns: TableColumnsType<Account> = [
    {
      title: "公司 / 官网",
      key: "company",
      width: 248,
      render: (_, a) => (
        <div className="company-cell">
          <Avatar
            shape="square"
            className={`company-avatar tint-${a.id.slice(-1)}`}
          >
            {a.name.slice(0, 2).toUpperCase()}
          </Avatar>
          <div>
            <Button
              className="company-link"
              type="link"
              onClick={() => setDetail(a)}
            >
              {a.name}
            </Button>
            <div className="secondary mono small">{a.domain}</div>
          </div>
        </div>
      ),
    },
    {
      title: "国家 / 行业",
      key: "geography",
      width: 185,
      render: (_, a) => (
        <>
          <div className="cell-main">
            {a.country}
            <span className="region-note">
              {a.region !== a.country ? a.region : ""}
            </span>
          </div>
          <div className="secondary small">{a.industry}</div>
        </>
      ),
    },
    {
      title: "客户分层",
      key: "tier",
      width: 116,
      render: (_, a) => (
        <>
          <TierTag tier={a.tier} />
          <div className="secondary small spaced">{a.model}</div>
        </>
      ),
    },
    {
      title: "Supermicro 证据",
      key: "evidence",
      width: 137,
      render: (_, a) => (
        <span
          className={
            a.supermicro === "使用已证实" ? "evidence-yes" : "secondary"
          }
        >
          {a.supermicro === "使用已证实" && <CheckCircleOutlined />}{" "}
          {a.supermicro}
        </span>
      ),
    },
    {
      title: "公开业务邮箱",
      key: "email",
      width: 252,
      render: (_, a) => (
        <>
          <div className="mono email-cell">{a.email}</div>
          <div className="secondary small spaced">{a.department}</div>
        </>
      ),
    },
    {
      title: "档案状态",
      key: "stage",
      width: 115,
      render: (_, a) => <StageTag account={a} />,
    },
    {
      title: "",
      key: "action",
      width: 54,
      render: (_, a) => (
        <Tooltip title="查看档案">
          <Button
            type="text"
            aria-label={`查看 ${a.name}`}
            icon={<ArrowRightOutlined />}
            onClick={() => setDetail(a)}
          />
        </Tooltip>
      ),
    },
  ];

  const accountContent = (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">YOUR NEXT CUSTOMER STARTS HERE</div>
          <Title level={2}>找到对的客户，建立有据可查的档案。</Title>
          <Paragraph className="page-subtitle">
            聚焦数据中心整机与配件买家。发现公开业务邮箱后建档，交给 mail2leads
            开始对话。
          </Paragraph>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setRecipeOpen(true)}
        >
          新建采集计划
        </Button>
      </div>
      <div className="metrics">
        {[
          {
            label: "已建档公司",
            value: data.length,
            note: "均含公开邮箱示例",
            icon: <TeamOutlined />,
          },
          {
            label: "Tier 1 重点客户",
            value: data.filter((a) => a.tier.startsWith("1")).length,
            note: "终端买家与项目方案商",
            icon: <ApartmentOutlined />,
          },
          {
            label: "可交接名单",
            value: data.filter((a) => a.stage === "ready").length,
            note: "基础资料与用途已演示复核",
            icon: <CheckCircleOutlined />,
          },
          {
            label: "已收到回复",
            value: data.filter((a) => a.mail === "replied").length,
            note: "后续跟踪归 mail2leads",
            icon: <MailOutlined />,
          },
        ].map((m) => (
          <Card key={m.label} className="metric-card">
            <div className="metric-label">
              {m.label}
              <span>{m.icon}</span>
            </div>
            <Statistic value={m.value} />
            <div className="secondary small">{m.note}</div>
          </Card>
        ))}
      </div>
      <div className="accounts-panel">
        <div className="panel-tabs">
          <Tabs
            activeKey={view}
            onChange={(v) => {
              setView(v);
              setSelected([]);
            }}
            items={[
              { key: "all", label: "全部客户" },
              { key: "tier1", label: "Tier 1 · 重点账户" },
              { key: "tier2", label: "Tier 2 · 贸易渠道" },
              { key: "review", label: "待核验" },
              { key: "suppressed", label: "已排除" },
            ]}
          />
          <Tag variant="filled">按 Tier → 地区排序</Tag>
        </div>
        <div className="filters">
          <Input
            prefix={<SearchOutlined />}
            allowClear
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              filterChange();
            }}
            placeholder="搜索公司、官网或邮箱"
            className="search-input"
            aria-label="搜索客户"
          />
          <Select
            aria-label="区域筛选"
            placeholder="全部区域"
            value={region}
            allowClear
            options={regions.map((x) => ({ value: x, label: x }))}
            onChange={(v) => {
              setRegion(v);
              setCountry(undefined);
              filterChange();
            }}
          />
          <Select
            aria-label="国家筛选"
            placeholder="国家 / 地区"
            value={country}
            allowClear
            options={[
              ...new Set(
                data
                  .filter((a) => !region || a.region === region)
                  .map((a) => a.country),
              ),
            ].map((x) => ({ value: x, label: x }))}
            onChange={(v) => {
              setCountry(v);
              filterChange();
            }}
          />
          <Select
            aria-label="行业筛选"
            placeholder="全部行业"
            value={industry}
            allowClear
            className="industry-filter"
            options={[...new Set(data.map((a) => a.industry))].map((x) => ({
              value: x,
              label: x,
            }))}
            onChange={(v) => {
              setIndustry(v);
              filterChange();
            }}
          />
          <Button type="text" onClick={reset}>
            重置
          </Button>
        </div>
        <div className="table-toolbar">
          <Space size={18}>
            <span className="secondary">
              <FilterOutlined /> 共 <strong>{filtered.length}</strong> 家公司
            </span>
            <Checkbox
              checked={onlySM}
              onChange={(e) => {
                setOnlySM(e.target.checked);
                filterChange();
              }}
            >
              仅已证实使用 Supermicro
            </Checkbox>
          </Space>
          <Button
            icon={<LinkOutlined />}
            disabled={eligible.length === 0}
            onClick={() => openHandoff()}
          >
            预览交接{eligible.length ? ` (${eligible.length})` : ""}
          </Button>
        </div>
        <Table
          className="account-table"
          rowKey="id"
          columns={columns}
          dataSource={filtered}
          scroll={{ x: 1207 }}
          rowSelection={{
            selectedRowKeys: selected,
            onChange: setSelected,
            getCheckboxProps: (a) => ({
              disabled: a.stage !== "ready",
              "aria-label": `选择 ${a.name}`,
            }),
          }}
          pagination={{
            pageSize: 8,
            showSizeChanger: false,
            showTotal: (total, range) =>
              `${range[0]}–${range[1]} / ${total} 家`,
          }}
          locale={{
            emptyText: (
              <Empty description="没有匹配的客户，试试调整筛选条件。" />
            ),
          }}
        />
      </div>
      <div className="footnote">
        <SafetyCertificateOutlined />{" "}
        每条线索保留来源。未找到公开业务邮箱的网站，不进入客户档案。
        <span>当前全部为合成示例</span>
      </div>
    </>
  );

  const discoveryContent = (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">FOCUSED DISCOVERY</div>
          <Title level={2}>有目的地发现，不无边界地采集。</Title>
          <Paragraph className="page-subtitle">
            按区域、行业与采购场景建立配方，先找联系方式，再做轻量建档。
          </Paragraph>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setRecipeOpen(true)}
        >
          新建采集计划
        </Button>
      </div>
      <Card className="flow-card">
        <Steps
          size="small"
          current={0}
          items={[
            { title: "发现官网", content: "案例 / 目录 / 搜索" },
            { title: "查找业务邮箱", content: "首页 + 联系页优先" },
            { title: "补充基础档案", content: "品牌证据 / 分类 / 去重" },
            { title: "审核后交接", content: "mail2leads 首封草稿" },
          ]}
        />
      </Card>
      <Alert
        type="info"
        showIcon
        title="当前仅展示采集计划设计，尚未运行爬虫。"
        description="建议先从 30 个美国 Tier 1 候选网站开始。无邮箱仅记录跳过原因，不建立完整客户档案。"
      />
      <Card title="采集配方" className="section-card">
        <Table
          rowKey="id"
          pagination={false}
          dataSource={recipes}
          columns={[
            { title: "计划名称", dataIndex: "name" },
            { title: "区域", dataIndex: "region" },
            { title: "目标客户", dataIndex: "tier" },
            {
              title: "候选网站上限",
              dataIndex: "limit",
              render: (v) => `${v} 家`,
            },
            {
              title: "状态",
              dataIndex: "status",
              render: (v) => <Tag>{v}</Tag>,
            },
          ]}
        />
      </Card>
      <div className="two-columns">
        <Card title="先去哪里找">
          <Timeline
            items={[
              {
                title: "Supermicro 官方客户案例",
                content: "发现真实使用方，再进入客户官网找联系入口。",
              },
              {
                title: "行业目录与展商名单",
                content: "发现域名；邮箱与公司身份回官网确认。",
              },
              {
                title: "定向搜索与手工种子",
                content: "国家 × 行业 × 产品，每次查询有预算。",
              },
            ]}
          />
        </Card>
        <Card title="每个网站的预算">
          <Descriptions
            column={1}
            size="small"
            items={[
              {
                key: "pages",
                label: "页面上限",
                children: "默认 8 页，先读首页与联系页",
              },
              { key: "time", label: "时间上限", children: "90 秒 / 公司" },
              {
                key: "parallel",
                label: "同域并发",
                children: "1；遵守站点限制",
              },
              {
                key: "none",
                label: "没有公开业务邮箱",
                children: "跳过，不建档；只保留轻量检查记录",
              },
              {
                key: "ai",
                label: "模型用途",
                children: "有证据的分类与一句话介绍",
              },
            ]}
          />
        </Card>
      </div>
    </>
  );

  const handoffContent = (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">A CLEAN HANDOFF</div>
          <Title level={2}>名单到这里，下一步是对话。</Title>
          <Paragraph className="page-subtitle">
            leadsgen 管理交接记录；首封审核、发送和回复由 mail2leads 负责。
          </Paragraph>
        </div>
        <Tag icon={<InfoCircleOutlined />}>接口待实现</Tag>
      </div>
      <Alert
        showIcon
        type="info"
        title="以下为邮件状态示例，不是真实发送记录。"
        description="新增交接操作只加入本页演示队列，不会连接 mail.storage.cn，也不会创建真实邮件草稿。"
      />
      <Card className="section-card" title="交接与邮件状态">
        <Table
          rowKey="id"
          dataSource={handoffs}
          pagination={false}
          scroll={{ x: 820 }}
          columns={[
            {
              title: "公司",
              render: (_, a: Account) => (
                <Button
                  className="company-link"
                  type="link"
                  onClick={() => setDetail(a)}
                >
                  {a.name}
                </Button>
              ),
            },
            {
              title: "收件地址",
              dataIndex: "email",
              render: (v) => <span className="mono">{v}</span>,
            },
            {
              title: "档案状态",
              render: (_, a: Account) => <StageTag account={a} />,
            },
            {
              title: "邮件系统状态",
              render: (_, a: Account) => (
                <Tag color={mailInfo[a.mail].color}>
                  {mailInfo[a.mail].label}
                </Tag>
              ),
            },
            {
              title: "说明",
              render: (_, a: Account) =>
                a.mail === "none"
                  ? "本地预览 · 尚无接收回执"
                  : a.mail === "accepted"
                    ? "服务器接受不等于送达"
                    : a.mail === "replied"
                      ? "在 mail2leads 继续跟踪"
                      : "停止再次交接",
            },
          ]}
        />
      </Card>
      <div className="two-columns">
        <Card title="怎样避免重复首封">
          <Paragraph>
            同一采购实体与邮箱的首封记录跨批次保留。重新爬取、换模板、重复点击都不能创建第二次首封。
          </Paragraph>
          <Text type="secondary">
            交接有回执，发送有独立账本，状态通过事件回传。
          </Text>
        </Card>
        <Card title="发送结果不确定怎么办">
          <Paragraph>
            连接中断或发出后记录失败时，先按 Message-ID 与已发送记录核对。
          </Paragraph>
          <Text type="secondary">
            显示“发送结果待核对”，不自动当成失败重发。
          </Text>
        </Card>
      </div>
    </>
  );

  const rulesContent = (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">QUALITY BEFORE QUANTITY</div>
          <Title level={2}>分层有依据，优先级看得懂。</Title>
          <Paragraph className="page-subtitle">
            客户价值、经营类型和品牌证据分别管理。以下为第一版拟议规则。
          </Paragraph>
        </div>
        <Tag>设计 v0.1</Tag>
      </div>
      <Card title="区域优先级">
        <div className="region-priority">
          {regions.map((r, i) => (
            <div key={r}>
              <span>{String(i + 1).padStart(2, "0")}</span>
              <strong>{r}</strong>
              {i < 4 && <ArrowRightOutlined />}
            </div>
          ))}
        </div>
        <Text type="secondary">
          默认先处理 Tier
          1，层内按上述地域顺序排列；国家与目标采购实体单独保存。
        </Text>
      </Card>
      <Card title="客户分层" className="section-card">
        <Table
          pagination={false}
          rowKey="tier"
          dataSource={[
            {
              tier: "1A",
              type: "终端使用方",
              definition: "可信资料已证实使用 Supermicro",
            },
            {
              tier: "1B",
              type: "终端使用方",
              definition: "明确有整机采购/运营场景，品牌待核实",
            },
            {
              tier: "1C",
              type: "方案集成商",
              definition: "以整机、项目与服务为主；不冒充最终用户",
            },
            {
              tier: "1D",
              type: "机房租赁 / 托管商",
              definition: "即使仅出租机房也纳入；采购匹配度独立核验",
            },
            {
              tier: "2A",
              type: "整机贸易商",
              definition: "Supermicro 或其他品牌的服务器转售渠道",
            },
            {
              tier: "2B",
              type: "配件贸易商",
              definition: "CPU、HDD、DRAM、SSD、网卡等数据中心配件",
            },
          ]}
          columns={[
            {
              title: "分层",
              dataIndex: "tier",
              render: (v) => <TierTag tier={v} />,
            },
            { title: "经营类型", dataIndex: "type" },
            { title: "定义", dataIndex: "definition" },
          ]}
        />
      </Card>
      <div className="two-columns">
        <Card title="什么样的邮箱进入名单">
          <Paragraph>
            需要公开页面证据及业务用途。优先采购、供应商引入和商务合作；sales
            邮箱标记路由待核验。
          </Paragraph>
          <Paragraph type="secondary">
            语法和邮件域检查不能保证收件箱存在。销售、客服、通用联系邮箱均保留；招聘、隐私、滥用举报和
            noreply 邮箱不作为业务联系入口。
          </Paragraph>
        </Card>
        <Card title="每条事实都能回到来源">
          <Paragraph>
            公司身份、邮箱、品牌与分类保留
            URL、摘录和采集时间。姓名、电话或全名未公开时如实留空。
          </Paragraph>
          <Paragraph type="secondary">
            官网公开不等于营销许可；地区联系政策与退订排除在交接和邮件系统落实。
          </Paragraph>
        </Card>
      </div>
    </>
  );

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
        <div className="nav-label">工作空间</div>
        <Menu
          selectedKeys={[page]}
          onClick={({ key }) => setPage(key as Page)}
          items={[
            { key: "accounts", icon: <TeamOutlined />, label: "客户档案" },
            { key: "discovery", icon: <CompassOutlined />, label: "采集计划" },
            { key: "handoffs", icon: <LinkOutlined />, label: "交接记录" },
            { key: "rules", icon: <SettingOutlined />, label: "分类规则" },
          ]}
        />
        <div className="nav-label region-label">
          目标市场<span>优先顺序</span>
        </div>
        <div className="region-menu">
          {regions.map((r, i) => (
            <button
              key={r}
              className={region === r && page === "accounts" ? "active" : ""}
              onClick={() => selectRegion(r)}
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
          <div>
            <span className="small-status" /> 专注发现与建档
          </div>
          <p>
            从公开信息出发
            <br />
            让每一次联系都有依据
          </p>
          <Divider />
          <Space>
            <Avatar size={25}>G</Avatar>
            <span>Glocal Storage</span>
          </Space>
        </div>
      </Layout.Sider>
      <Layout className="main-layout">
        <Layout.Header className="topbar">
          <Space size={10}>
            <GlobalOutlined />
            <span>Glocal Storage</span>
            <span className="crumb-divider">/</span>
            <span className="secondary">{pageNames[page]}</span>
          </Space>
          <Space size={12}>
            <Tag variant="filled">设计预览 v0.1</Tag>
            <Avatar size={28} className="user-avatar">
              G
            </Avatar>
          </Space>
        </Layout.Header>
        <div className="demo-strip">
          <InfoCircleOutlined /> 合成示例数据 · 未接入爬虫或邮箱 ·
          页面操作仅用于预览，刷新后重置
        </div>
        <nav className="mobile-navigation" aria-label="主导航">
          {(Object.keys(pageNames) as Page[]).map((key) => (
            <Button
              key={key}
              type={page === key ? "primary" : "text"}
              size="small"
              onClick={() => setPage(key)}
            >
              {pageNames[key]}
            </Button>
          ))}
        </nav>
        <Layout.Content className="page-content">
          {page === "accounts"
            ? accountContent
            : page === "discovery"
              ? discoveryContent
              : page === "handoffs"
                ? handoffContent
                : rulesContent}
        </Layout.Content>
      </Layout>

      <Drawer
        title="客户档案 · 合成示例"
        open={!!detail}
        onClose={() => setDetail(undefined)}
        size={640}
        extra={detail && <TierTag tier={detail.tier} />}
      >
        {detail && (
          <>
            <div className="detail-heading">
              <Avatar shape="square" size={50} className="company-avatar">
                {detail.name.slice(0, 2).toUpperCase()}
              </Avatar>
              <div>
                <Title level={3}>{detail.name}</Title>
                <Text type="secondary">{detail.domain}</Text>
              </div>
            </div>
            <Paragraph>{detail.summary}</Paragraph>
            <Space wrap>
              <StageTag account={detail} />
              <Tag>{detail.country}</Tag>
              <Tag>{detail.industry}</Tag>
            </Space>
            <Tabs
              items={[
                {
                  key: "profile",
                  label: "基础档案",
                  children: (
                    <>
                      <Descriptions
                        column={1}
                        bordered
                        size="small"
                        items={[
                          {
                            key: "name",
                            label: "公开公司全名",
                            children: detail.legal,
                          },
                          {
                            key: "type",
                            label: "经营类型",
                            children: detail.model,
                          },
                          {
                            key: "region",
                            label: "目标区域",
                            children: `${detail.region} / ${detail.country}`,
                          },
                          {
                            key: "sm",
                            label: "Supermicro",
                            children: detail.supermicro,
                          },
                          {
                            key: "product",
                            label: "产品场景",
                            children: detail.products.join("、"),
                          },
                          {
                            key: "email",
                            label: "公开业务邮箱",
                            children: <Text copyable>{detail.email}</Text>,
                          },
                          {
                            key: "person",
                            label: "联系人 / 部门",
                            children: `姓名未公开 / ${detail.department}`,
                          },
                          {
                            key: "tel",
                            label: "联系电话",
                            children: <Text type="secondary">未公开</Text>,
                          },
                        ]}
                      />
                      <div className="detail-section">
                        <Title level={5}>为什么属于这个分层</Title>
                        <Paragraph>{detail.reason}</Paragraph>
                      </div>
                      <Alert
                        type="info"
                        showIcon
                        title="公开业务邮箱 ≠ 已验证收件箱"
                        description="生产版本分别显示来源、域名检查、联系用途与发送资格；本页数据均为虚构。"
                      />
                    </>
                  ),
                },
                {
                  key: "sources",
                  label: "来源与证据",
                  children: (
                    <>
                      <Alert
                        type="warning"
                        showIcon
                        title="以下为证据呈现方式示例，不是真实抓取结果。"
                      />
                      <div className="evidence-card">
                        <Space>
                          <FileSearchOutlined />
                          <Text strong>联系页 · 业务邮箱</Text>
                        </Space>
                        <div className="mono secondary spaced">
                          https://{detail.domain}/contact
                        </div>
                        <blockquote>
                          {detail.department}: {detail.email}
                        </blockquote>
                        <Text type="secondary">
                          演示采集时间：2026-09-23 ·
                          正式记录保留抓取时间与内容哈希
                        </Text>
                      </div>
                      <div className="evidence-card">
                        <Space>
                          <FileSearchOutlined />
                          <Text strong>业务与硬件说明</Text>
                        </Space>
                        <Paragraph className="spaced">
                          {detail.reason}
                        </Paragraph>
                        <Tag>演示来源片段</Tag>
                      </div>
                    </>
                  ),
                },
                {
                  key: "history",
                  label: "交接与邮件",
                  children: (
                    <>
                      <Alert
                        showIcon
                        type="info"
                        title={mailInfo[detail.mail].label}
                        description="邮件状态仅为演示；真实状态必须来自 mail2leads 回执。"
                      />
                      <Timeline
                        className="detail-section"
                        items={[
                          {
                            title: "轻量建档",
                            content: "公开邮箱、公司信息与分类依据（示例）",
                          },
                          ...(detail.stage === "handed"
                            ? [
                                {
                                  title: "名单已交接",
                                  content: "真实系统在收到导入回执后确认",
                                },
                              ]
                            : []),
                          ...(detail.mail !== "none"
                            ? [
                                {
                                  title: mailInfo[detail.mail].label,
                                  content: "示例事件，非真实邮件",
                                },
                              ]
                            : []),
                        ]}
                      />
                    </>
                  ),
                },
              ]}
            />
            <Divider />
            <Button
              type="primary"
              block
              icon={<LinkOutlined />}
              disabled={detail.stage !== "ready"}
              onClick={() => {
                openHandoff(detail);
                setDetail(undefined);
              }}
            >
              {detail.stage === "ready"
                ? "预览交接到 mail2leads"
                : detail.stage === "review"
                  ? "资料待核验，暂不交接"
                  : detail.stage === "queued"
                    ? "已在待交接队列，请勿重复添加"
                    : "已处理，避免重复交接"}
            </Button>
          </>
        )}
      </Drawer>

      <Modal
        title="交接预览 · 不会发送邮件"
        open={handoffOpen}
        onCancel={() => setHandoffOpen(false)}
        width={650}
        okText="加入演示交接队列"
        okButtonProps={{ disabled: eligible.length === 0 }}
        onOk={() => {
          const ids = eligible.map((a) => a.id);
          setData((prev) =>
            prev.map((a) =>
              ids.includes(a.id) ? { ...a, stage: "queued" as const } : a,
            ),
          );
          setSelected([]);
          setHandoffOpen(false);
          message.success(
            `已加入 ${ids.length} 家至本地演示队列，未连接邮件系统。`,
          );
          setPage("handoffs");
        }}
      >
        <Alert
          type="info"
          showIcon
          title={`${eligible.length} 家公司 · 每家公司 1 个业务邮箱`}
          description="真实版本接入后，mail2leads 接收名单并准备首封草稿，由你在那里审核并发送。本原型只更新页面演示状态。"
        />
        <div className="handoff-preview">
          {eligible.map((a) => (
            <div key={a.id}>
              <div>
                <Text strong>{a.name}</Text>
                <div className="secondary mono small">{a.email}</div>
              </div>
              <Tag color="success" icon={<CheckOutlined />}>
                可交接
              </Tag>
            </div>
          ))}
        </div>
        <Descriptions
          column={1}
          size="small"
          items={[
            {
              key: "mailbox",
              label: "候选发件账户",
              children: "sales@glocalstorage.com（尚未接通）",
            },
            {
              key: "template",
              label: "首封模板",
              children: "稍后在 mail2leads 准备与确认",
            },
            {
              key: "action",
              label: "此处操作",
              children: "生成名单交接预览，不创建或发送邮件",
            },
            {
              key: "dedupe",
              label: "重复保护",
              children: "已交接、待核验和已排除记录不可重复选择",
            },
          ]}
        />
      </Modal>

      <Drawer
        title="新建采集计划 · 演示"
        open={recipeOpen}
        onClose={() => setRecipeOpen(false)}
        size={460}
        footer={
          <Button type="primary" block onClick={() => form.submit()}>
            保存演示计划
          </Button>
        }
      >
        <Alert
          showIcon
          type="info"
          title="只保存页面内的采集配方，不启动抓取。"
        />
        <Form
          form={form}
          layout="vertical"
          className="detail-section"
          initialValues={{
            name: "美国 · Supermicro 终端用户",
            region: "美国",
            tier: "Tier 1",
            limit: 30,
          }}
          onFinish={(values) => {
            setRecipes((prev) => [
              ...prev,
              { ...values, id: Date.now(), status: "待接入爬虫" },
            ]);
            setRecipeOpen(false);
            setPage("discovery");
            message.success("演示计划已添加，刷新页面后重置。");
          }}
        >
          <Form.Item
            name="name"
            label="计划名称"
            rules={[
              { required: true, whitespace: true, message: "请输入计划名称" },
            ]}
          >
            <Input maxLength={80} />
          </Form.Item>
          <Form.Item
            name="region"
            label="目标区域"
            rules={[{ required: true }]}
          >
            <Select options={regions.map((r) => ({ label: r, value: r }))} />
          </Form.Item>
          <Form.Item name="tier" label="客户分层" rules={[{ required: true }]}>
            <Select
              options={[
                { label: "Tier 1 · 终端与项目买家", value: "Tier 1" },
                { label: "Tier 2 · 贸易渠道", value: "Tier 2" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="limit"
            label="本批候选网站上限"
            rules={[{ required: true, type: "number", min: 1, max: 100 }]}
          >
            <InputNumber min={1} max={100} />
          </Form.Item>
          <Paragraph type="secondary">
            初始建议 30
            个官网，先评估公开业务邮箱命中率及分类准确性，再增加数量。
          </Paragraph>
          <Divider />
          <Title level={5}>默认采集策略</Title>
          <Paragraph>
            首页及联系页优先 · 最多 8 页/网站 · 找到业务邮箱才补齐档案 ·
            无邮箱记录跳过原因。
          </Paragraph>
        </Form>
      </Drawer>
    </Layout>
  );
}
