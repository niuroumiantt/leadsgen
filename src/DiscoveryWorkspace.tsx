import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Collapse,
  Drawer,
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
import {
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { api } from "./api";
import { displayTime, milestones } from "./live-types";
import {
  frequencies,
  resultReasons,
  runLabels,
  searchErrors,
  templates,
} from "./discovery-types";
import type {
  DiscoveryPlan,
  DiscoveryState,
  PlanConfig,
  RunDetails,
  SearchResult,
  SearchRun,
} from "./discovery-types";
const { Title, Text, Paragraph } = Typography;
const colorFor = (status: string) =>
  status === "succeeded"
    ? "green"
    : ["failed", "interrupted"].includes(status)
      ? "red"
      : status === "running"
        ? "processing"
        : "default";
const errorText = (code: string) => searchErrors[code] ?? code;
const sourceDecision: Record<string, string> = {
  pending: "待核验官网",
  queued: "已转入采集",
  duplicate: "重复来源",
  excluded: "自动排除",
  rejected: "人工排除",
};
const countryNames: Record<string, string> = {
  US: "美国",
  CA: "加拿大",
  GB: "英国",
  DE: "德国",
  FR: "法国",
  NL: "荷兰",
  SG: "新加坡",
  AU: "澳大利亚",
  JP: "日本",
  IN: "印度",
};

export default function DiscoveryWorkspace({
  onCandidate,
  onAccount,
  onCollectionChanged,
}: {
  onCandidate: (id: number) => void;
  onAccount: (id: string) => void;
  onCollectionChanged: () => Promise<void>;
}) {
  const { message } = App.useApp();
  const [state, setState] = useState<DiscoveryState>();
  const [error, setError] = useState("");
  const sequence = useRef(0);
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState(false);
  const [editing, setEditing] = useState<DiscoveryPlan>();
  const [form] = Form.useForm();
  const [history, setHistory] = useState<DiscoveryPlan>();
  const [runs, setRuns] = useState<SearchRun[]>([]);
  const [historyBefore, setHistoryBefore] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState("");
  const historySequence = useRef(0);
  const [runId, setRunId] = useState<string>();
  const [run, setRun] = useState<RunDetails>();
  const [runError, setRunError] = useState("");
  const [selection, setSelection] = useState<number[]>([]);
  const [resultFilter, setResultFilter] = useState("pending");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(async () => {
    const request = ++sequence.current;
    try {
      const next = await api<DiscoveryState>("/api/discovery");
      if (request === sequence.current) {
        setState(next);
        setError("");
      }
    } catch (e) {
      if (request === sequence.current) setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), 5000);
    return () => {
      sequence.current++;
      clearInterval(t);
    };
  }, [refresh]);
  useEffect(() => {
    historySequence.current++;
    if (!history) return;
    let active = true;
    setRuns([]);
    setHistoryError("");
    api<{ items: SearchRun[]; next_before: string | null }>(
      `/api/discovery/plans/${history.id}/runs`,
    )
      .then((v) => {
        if (active) {
          setRuns(v.items);
          setHistoryBefore(v.next_before);
        }
      })
      .catch((e) => {
        if (active) setHistoryError(e.message);
      });
    return () => {
      active = false;
    };
  }, [history, revision]);
  useEffect(() => {
    setSelection([]);
    setRun(undefined);
    setRunError("");
    setResultFilter("pending");
  }, [runId]);
  useEffect(() => {
    if (!runId) return;
    let active = true;
    let request = 0;
    const read = async () => {
      const current = ++request;
      try {
        const data = await api<RunDetails>(`/api/discovery/runs/${runId}`);
        if (active && current === request) {
          setRun(data);
          setRunError("");
          setSelection((ids) =>
            ids.filter((id) =>
              data.results.some((r) => r.id === id && r.decision === "pending"),
            ),
          );
        }
      } catch (e) {
        if (active && current === request) setRunError((e as Error).message);
      }
    };
    void read();
    const t = setInterval(() => void read(), 5000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, [runId, revision]);
  const configured = (provider: string) =>
    !!state?.providers.find((p) => p.id === provider)?.configured;
  const beginEdit = (plan?: DiscoveryPlan) => {
    setEditing(plan);
    form.resetFields();
    const template = templates[0];
    form.setFieldsValue(
      plan
        ? { ...plan.config, query_text: plan.config.queries.join("\n") }
        : {
            ...template,
            name: "美国 · GPU 云与数据中心",
            country: "US",
            provider: state?.providers.find((p) => p.configured)?.id ?? "brave",
            tier: "1B",
            interval_hours: 24,
            query_text: template.queries.join("\n"),
          },
    );
    setEditor(true);
  };
  const save = async (values: PlanConfig & { query_text: string }) => {
    setBusy(true);
    try {
      const { query_text, ...config } = values;
      await api(
        editing ? `/api/discovery/plans/${editing.id}` : "/api/discovery/plans",
        {
          ...config,
          queries: query_text
            .split(/\r?\n/)
            .map((q) => q.trim())
            .filter(Boolean),
          ...(editing ? { version: editing.version } : {}),
        },
      );
      setEditor(false);
      await refresh();
      message.success(
        editing
          ? "计划已保存；正在执行的搜索保留原目标快照。"
          : "计划已保存。可先搜索一次，或启用定时执行。",
      );
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const start = async (plan: DiscoveryPlan) => {
    setBusy(true);
    try {
      const r = await api<{ id: string }>(
        `/api/discovery/plans/${plan.id}/runs`,
        { version: plan.version, request_id: crypto.randomUUID() },
      );
      setRunId(r.id);
      setRevision((v) => v + 1);
      await refresh();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const toggle = async (plan: DiscoveryPlan, enabled: boolean) => {
    setBusy(true);
    try {
      await api(`/api/discovery/plans/${plan.id}/schedule`, {
        version: plan.version,
        enabled,
      });
      await refresh();
      setRevision((v) => v + 1);
      message.success(
        enabled
          ? "定时计划已启用，首轮即将开始。"
          : "已暂停后续搜索；当前在途响应仍会保留记录。",
      );
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const review = async (action: "collect" | "reject") => {
    if (!selection.length) return;
    setBusy(true);
    try {
      const result = await api<{ candidates: number[] }>(
        "/api/discovery/review",
        { ids: selection, action, reason: rejectReason },
      );
      setSelection([]);
      setRejectOpen(false);
      setRejectReason("");
      setRevision((v) => v + 1);
      await refresh();
      await onCollectionChanged();
      message.success(
        action === "collect"
          ? `已关联 ${result.candidates.length} 个采集候选；结果和客户入口会在本页更新。`
          : "已记录排除原因，后续搜索仍按官网去重。",
      );
    } catch (e) {
      message.error((e as Error).message);
      setRevision((v) => v + 1);
    } finally {
      setBusy(false);
    }
  };
  const moreRuns = async () => {
    if (!history || !historyBefore) return;
    const generation = historySequence.current;
    setBusy(true);
    try {
      const next = await api<{
        items: SearchRun[];
        next_before: string | null;
      }>(
        `/api/discovery/plans/${history.id}/runs?before=${encodeURIComponent(historyBefore)}`,
      );
      if (generation !== historySequence.current) return;
      setRuns((old) => [
        ...old,
        ...next.items.filter((n) => !old.some((r) => r.id === n.id)),
      ]);
      setHistoryBefore(next.next_before);
    } catch (e) {
      if (generation === historySequence.current)
        setHistoryError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const openLatest = (plan: DiscoveryPlan) => {
    setHistory(plan);
    setRevision((v) => v + 1);
  };
  const results = run?.results ?? [];
  const pending = results.filter((r) => r.decision === "pending");
  const visible = results.filter(
    (r) => resultFilter === "all" || r.decision === resultFilter,
  );
  const accountIds = new Set(
    results
      .filter((r) => r.decision === "queued" && r.account_id)
      .map((r) => r.account_id),
  );
  const qualifiedIds = new Set(
    results
      .filter(
        (r) =>
          r.decision === "queued" &&
          r.account_id &&
          ["qualified", "quoted", "negotiating", "won"].includes(
            r.milestone ?? "",
          ),
      )
      .map((r) => r.account_id),
  );
  const renderResult = (r: SearchResult) => (
    <article className="discovery-result" key={r.id}>
      <div className="discovery-result-top">
        <Checkbox
          aria-label={`选择官网 ${r.domain || r.title}`}
          disabled={
            r.decision !== "pending" ||
            busy ||
            (!selection.includes(r.id) && selection.length >= 30)
          }
          checked={selection.includes(r.id)}
          onChange={(e) =>
            setSelection((ids) =>
              e.target.checked
                ? [...ids, r.id]
                : ids.filter((id) => id !== r.id),
            )
          }
        />
        <div>
          <Text strong>{r.title || r.domain || "无标题结果"}</Text>
          <div className="secondary small break-anywhere">
            {r.site_url || "无可采集官网"}
          </div>
        </div>
        <Tag
          color={
            r.decision === "pending"
              ? "blue"
              : r.decision === "queued"
                ? "green"
                : "default"
          }
        >
          {sourceDecision[r.decision] ?? r.decision}
        </Tag>
      </div>
      <Paragraph className="discovery-snippet">
        {r.snippet || "搜索服务没有提供摘要"}
      </Paragraph>
      <Paragraph type="secondary" className="small break-anywhere">
        来自查询：{run?.queries.find((q) => q.id === r.query_id)?.query}
      </Paragraph>
      <Space wrap>
        <Text type="secondary">结果 #{r.rank} · 搜索摘要待核实</Text>
        {r.source_url && (
          <a href={r.source_url} target="_blank" rel="noreferrer">
            查看搜索来源 ↗
          </a>
        )}
        {r.reason && (
          <Text type="secondary">{resultReasons[r.reason] ?? r.reason}</Text>
        )}
      </Space>
      {r.canonical_run_id && (
        <div className="spaced">
          <Text type="secondary">
            首次发现记录：{sourceDecision[r.canonical_decision ?? ""]}
            {r.canonical_decision === "rejected" && ` · ${r.canonical_reason}`}
          </Text>{" "}
          {r.canonical_run_id !== runId && (
            <Button
              size="small"
              type="link"
              onClick={() => setRunId(r.canonical_run_id!)}
            >
              查看首次搜索
            </Button>
          )}
        </div>
      )}
      {r.reviewed_by && (
        <div className="secondary small spaced">
          {r.reviewed_by} · {displayTime(r.reviewed_at)}
        </div>
      )}
      {r.candidate_id && (
        <Space wrap className="spaced">
          <Button size="small" onClick={() => onCandidate(r.candidate_id!)}>
            查看官网采集
          </Button>
          {r.crawl_status && (
            <Tag>
              {(
                {
                  queued: "等待采集",
                  running: "正在采集",
                  completed: "已建档",
                  failed: "采集失败",
                  skipped: "本轮未建档",
                } as Record<string, string>
              )[r.crawl_status] ?? r.crawl_status}
            </Tag>
          )}
          {r.account_id && (
            <Button
              size="small"
              type="link"
              onClick={() => onAccount(r.account_id!)}
            >
              查看客户 · {milestones[r.milestone ?? ""] ?? "待核验"}
            </Button>
          )}
        </Space>
      )}
    </article>
  );
  return (
    <>
      {error && <Alert type="error" title={error} className="section-card" />}
      <div className="discovery-intro">
        <div>
          <Text className="eyebrow">DISCOVERY PLANS</Text>
          <Title level={2}>发现计划</Title>
          <Paragraph type="secondary">
            搜索企业官网，核验后提取公开联系方式。每一步保留出处，客户进展沿用同一条记录。
          </Paragraph>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => beginEdit()}
        >
          新建发现计划
        </Button>
      </div>
      {state && !state.providers.some((p) => p.configured) && (
        <Alert
          showIcon
          type="warning"
          title="搜索服务待接入"
          description="可以先保存计划；接入 Brave 或 Tavily 后即可执行。当前不会发起搜索请求。"
        />
      )}
      <div className="collection-metrics discovery-metrics">
        {[
          ["发现计划", state?.plans.length ?? 0],
          ["定时执行", state?.plans.filter((p) => p.enabled).length ?? 0],
          ["待核验官网", state?.pending ?? 0],
          [
            "今日请求",
            `${state?.used_today ?? 0} / ${state?.daily_limit ?? 20}`,
          ],
        ].map(([label, value]) => (
          <Card key={label}>
            <Text type="secondary">{label}</Text>
            <strong>{value}</strong>
          </Card>
        ))}
      </div>
      <div className="discovery-service-row">
        <Space wrap>
          {state?.providers.map((p) => (
            <Tag key={p.id} color={p.configured ? "blue" : "default"}>
              {p.id === "brave" ? "Brave" : "Tavily"} ·{" "}
              {p.configured ? "已配置，结果以调用为准" : "未接入"}
            </Tag>
          ))}
        </Space>
        <Tag
          color={
            state?.worker &&
            Date.now() - Date.parse(state.worker.seen_at) < 60000 &&
            state.worker.phase !== "error"
              ? "green"
              : "orange"
          }
        >
          搜索服务：
          {!state?.worker
            ? "等待心跳"
            : Date.now() - Date.parse(state.worker.seen_at) >= 60000
              ? "心跳超时"
              : state.worker.phase === "running"
                ? "搜索中"
                : state.worker.phase === "error"
                  ? "暂不可用"
                  : "待命"}
        </Tag>
        <Text type="secondary">
          请求预算每日 UTC 00:00 重置 · 本地时间 {displayTime(state?.resets_at)}
        </Text>
        <Button
          type="text"
          icon={<ReloadOutlined />}
          onClick={() => void refresh()}
        >
          刷新计划
        </Button>
      </div>
      {!state?.plans.length ? (
        <Card className="section-card">
          <Empty description="还没有发现计划。先选一个市场和客户类型，保存可重复执行的搜索词。" />
          <div className="discovery-templates">
            {templates.map((t) => (
              <Button
                key={t.name}
                onClick={() => {
                  beginEdit();
                  form.setFieldsValue({
                    ...t,
                    name: `美国 · ${t.name}`,
                    query_text: t.queries.join("\n"),
                  });
                }}
              >
                {t.name}
              </Button>
            ))}
          </div>
        </Card>
      ) : (
        <div className="discovery-plans">
          {state.plans.map((plan) => (
            <Card
              key={plan.id}
              className="discovery-plan"
              title={
                <div>
                  <Tag color={plan.enabled ? "green" : "default"}>
                    {plan.enabled ? "定时已启用" : "定时未启用"}
                  </Tag>
                  <Title level={4}>{plan.config.name}</Title>
                </div>
              }
              extra={
                <Button type="text" onClick={() => beginEdit(plan)}>
                  编辑
                </Button>
              }
            >
              <Space wrap>
                <Tag>
                  {countryNames[plan.config.country] ?? plan.config.country}
                </Tag>
                <Tag>{plan.config.industry}</Tag>
                <Tag>{frequencies[plan.config.interval_hours]}</Tag>
              </Space>
              <Paragraph className="spaced" strong>
                {plan.config.product}
              </Paragraph>
              <Paragraph type="secondary">{plan.config.objective}</Paragraph>
              <div className="discovery-plan-meta">
                <span>
                  每轮 {plan.config.queries.length} 次搜索 · 最多{" "}
                  {plan.config.queries.length * 10} 条结果
                </span>
                <span>
                  下一轮：
                  {plan.enabled ? displayTime(plan.next_run_at) : "未安排"}
                </span>
                <span>
                  最近结果：
                  {plan.last_run
                    ? (runLabels[plan.last_run.status] ?? plan.last_run.status)
                    : "尚未执行"}
                </span>
              </div>
              {plan.last_error && (
                <Alert
                  type="warning"
                  className="spaced"
                  title={errorText(plan.last_error)}
                  description={
                    plan.failures >= 3
                      ? "连续失败三次，已暂停；检查接入后可重新启用。"
                      : undefined
                  }
                />
              )}
              <Space wrap className="discovery-plan-actions">
                <Button
                  type="primary"
                  icon={<SearchOutlined />}
                  loading={busy}
                  disabled={!configured(plan.config.provider)}
                  onClick={() => void start(plan)}
                >
                  搜索一次
                </Button>
                <Button onClick={() => openLatest(plan)}>执行记录</Button>
                {plan.enabled ? (
                  <Button
                    loading={busy}
                    onClick={() => void toggle(plan, false)}
                  >
                    暂停计划
                  </Button>
                ) : (
                  <Button
                    loading={busy}
                    disabled={
                      !configured(plan.config.provider) ||
                      !plan.config.interval_hours
                    }
                    onClick={() => void toggle(plan, true)}
                  >
                    启用定时
                  </Button>
                )}
                {!plan.enabled &&
                  plan.last_run &&
                  ["queued", "running"].includes(plan.last_run.status) && (
                    <Button
                      loading={busy}
                      onClick={() => void toggle(plan, false)}
                    >
                      停止本轮
                    </Button>
                  )}
              </Space>
            </Card>
          ))}
        </div>
      )}
      <Drawer
        open={editor}
        onClose={() => setEditor(false)}
        title={editing ? "编辑发现计划" : "新建发现计划"}
        size={620}
        styles={{ wrapper: { maxWidth: "100vw" } }}
        destroyOnHidden
        footer={
          <Button
            block
            type="primary"
            loading={busy}
            onClick={() => form.submit()}
          >
            保存计划
          </Button>
        }
      >
        <Form form={form} layout="vertical" onFinish={save} disabled={busy}>
          {!editing && (
            <Form.Item label="计划模板">
              <Select
                aria-label="计划模板"
                placeholder="选择后可继续修改"
                options={templates.map((t, i) => ({ value: i, label: t.name }))}
                onChange={(i) => {
                  const t = templates[i];
                  form.setFieldsValue({
                    ...t,
                    name: `${countryNames[form.getFieldValue("country")] ?? "目标市场"} · ${t.name}`,
                    query_text: t.queries.join("\n"),
                  });
                }}
              />
            </Form.Item>
          )}
          <Form.Item
            name="name"
            label="计划名称"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input aria-label="计划名称" maxLength={100} />
          </Form.Item>
          <div className="progress-form-grid">
            <Form.Item
              name="country"
              label="目标国家"
              rules={[{ required: true }]}
            >
              <Select
                aria-label="目标国家"
                options={(
                  state?.countries ??
                  Object.entries(countryNames).map(([code, name]) => ({
                    code,
                    name,
                  }))
                ).map((c) => ({ value: c.code, label: c.name }))}
              />
            </Form.Item>
            <Form.Item
              name="industry"
              label="客户类型"
              rules={[{ required: true, whitespace: true }]}
            >
              <Input aria-label="客户类型" maxLength={100} />
            </Form.Item>
          </div>
          <Form.Item
            name="product"
            label="产品方向"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input aria-label="产品方向" maxLength={150} />
          </Form.Item>
          <Form.Item
            name="objective"
            label="什么样的公司值得跟进"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input.TextArea
              aria-label="什么样的公司值得跟进"
              rows={3}
              maxLength={1000}
            />
          </Form.Item>
          <Form.Item
            name="query_text"
            label="搜索词（每行一次查询，最多 5 行）"
            rules={[{ required: true, whitespace: true }]}
            extra="每条查询会附加目标国家英文名。产品或目标变化时，请同时核对搜索词。"
          >
            <Input.TextArea aria-label="搜索词" rows={5} maxLength={1504} />
          </Form.Item>
          <div className="progress-form-grid">
            <Form.Item
              name="provider"
              label="搜索服务"
              rules={[{ required: true }]}
            >
              <Select
                aria-label="搜索服务"
                options={[
                  { value: "brave", label: "Brave" },
                  { value: "tavily", label: "Tavily" },
                ]}
              />
            </Form.Item>
            <Form.Item
              name="interval_hours"
              label="执行频率"
              rules={[{ required: true }]}
            >
              <Select
                aria-label="执行频率"
                options={Object.entries(frequencies).map(([value, label]) => ({
                  value: Number(value),
                  label,
                }))}
              />
            </Form.Item>
            <Form.Item
              name="tier"
              label="初始客户分层"
              rules={[{ required: true }]}
            >
              <Select
                aria-label="初始客户分层"
                options={["1A", "1B", "1C", "1D", "2A", "2B"].map((value) => ({
                  value,
                  label: `Tier ${value}`,
                }))}
              />
            </Form.Item>
          </div>
          <Alert
            type="info"
            showIcon
            title="新计划保存后不自动运行"
            description="可先搜索一次检查质量，再启用定时。目标国家、类型和分层是计划标签，需结合官网与沟通核实。"
          />
        </Form>
      </Drawer>
      <Drawer
        open={!!history}
        onClose={() => setHistory(undefined)}
        title={`执行记录 · ${history?.config.name ?? ""}`}
        size={620}
        styles={{ wrapper: { maxWidth: "100vw" } }}
      >
        <Button
          icon={<ReloadOutlined />}
          onClick={() => setRevision((v) => v + 1)}
        >
          刷新执行记录
        </Button>
        {historyError && <Alert type="error" title={historyError} />}
        {!runs.length && !historyError && (
          <Empty description="这个计划还没有执行记录" />
        )}
        {runs.map((r) => (
          <Card
            key={r.id}
            size="small"
            className="section-card"
            title={displayTime(r.created_at)}
            extra={
              <Tag color={colorFor(r.status)}>
                {runLabels[r.status] ?? r.status}
              </Tag>
            }
          >
            <Paragraph>
              {r.kind === "manual" ? "手动运行" : "定时运行"} ·{" "}
              {r.config.provider} · {r.config.queries.length} 条查询
            </Paragraph>
            {r.error && (
              <Paragraph type="warning">{errorText(r.error)}</Paragraph>
            )}
            <Button onClick={() => setRunId(r.id)}>查看搜索过程与结果</Button>
          </Card>
        ))}
        {historyBefore && (
          <Button loading={busy} onClick={() => void moreRuns()}>
            加载更早执行
          </Button>
        )}
      </Drawer>
      <Drawer
        open={!!runId}
        onClose={() => setRunId(undefined)}
        title="搜索过程与候选官网"
        size={940}
        styles={{ wrapper: { maxWidth: "100vw" } }}
        className="discovery-run-drawer"
      >
        {runError && <Alert type="error" title={runError} />}
        {run && (
          <>
            <Space wrap>
              <Title level={4} style={{ margin: 0 }}>
                {run.config.name}
              </Title>
              <Tag color={colorFor(run.status)}>
                {runLabels[run.status] ?? run.status}
              </Tag>
            </Space>
            <Paragraph type="secondary" className="spaced">
              {countryNames[run.config.country]} · {run.config.provider} · 开始{" "}
              {displayTime(run.started_at)} · 结束{" "}
              {displayTime(run.finished_at)}
            </Paragraph>
            {run.error && (
              <Alert type="warning" showIcon title={errorText(run.error)} />
            )}
            <div className="discovery-funnel">
              {[
                ["搜索结果", results.length],
                ["待核验", pending.length],
                [
                  "转入采集",
                  results.filter((r) => r.decision === "queued").length,
                ],
                ["已建档", accountIds.size],
                ["确认需求及以后", qualifiedIds.size],
              ].map(([label, value]) => (
                <div key={label}>
                  <strong>{value}</strong>
                  <span>{label}</span>
                </div>
              ))}
            </div>
            <Paragraph type="secondary">
              建档和需求数按本轮转入采集的独立客户统计，阶段以负责人当前记录为准。重复结果不会重复计数。
            </Paragraph>
            <Collapse
              items={[
                {
                  key: "queries",
                  label: `查询过程 · ${run.queries.filter((q) => q.status === "succeeded").length} / ${run.queries.length} 完成`,
                  children: (
                    <Timeline
                      items={run.queries.map((q) => ({
                        key: q.id,
                        children: (
                          <div>
                            <Text strong className="break-anywhere">
                              {q.query}
                            </Text>
                            <div>
                              <Tag color={colorFor(q.status)}>
                                {runLabels[q.status] ?? q.status}
                              </Tag>
                              <Text type="secondary">
                                {displayTime(q.requested_at)}
                              </Text>
                            </div>
                            {q.error && (
                              <Text type="warning">{errorText(q.error)}</Text>
                            )}
                          </div>
                        ),
                      }))}
                    />
                  ),
                },
              ]}
            />
            <div className="discovery-review-toolbar">
              <Select
                aria-label="搜索结果筛选"
                value={resultFilter}
                onChange={(v) => {
                  setResultFilter(v);
                  setSelection([]);
                }}
                options={[
                  { value: "pending", label: `待核验（${pending.length}）` },
                  { value: "queued", label: "已转入采集" },
                  { value: "duplicate", label: "重复来源" },
                  { value: "excluded", label: "自动排除" },
                  { value: "rejected", label: "人工排除" },
                  { value: "all", label: `全部结果（${results.length}）` },
                ]}
              />
              <Space wrap>
                <Button
                  disabled={!pending.length || busy}
                  onClick={() =>
                    setSelection(pending.slice(0, 30).map((r) => r.id))
                  }
                >
                  选择前 30 个待核验官网
                </Button>
                <Button
                  type="primary"
                  disabled={!selection.length}
                  loading={busy}
                  onClick={() => void review("collect")}
                >
                  转入官网采集（{selection.length}）
                </Button>
                <Button
                  disabled={!selection.length || busy}
                  onClick={() => setRejectOpen(true)}
                >
                  排除
                </Button>
              </Space>
            </div>
            <Paragraph type="secondary">
              先打开来源核实公司官网。转入后仅采集公开联系方式，邮件仍按原有审核与发送流程处理。
            </Paragraph>
            {visible.length ? (
              visible.map(renderResult)
            ) : (
              <Empty
                description={
                  run.status === "running" || run.status === "queued"
                    ? "搜索执行中，结果会自动更新"
                    : "此筛选下没有结果"
                }
              />
            )}
          </>
        )}
      </Drawer>
      <Modal
        open={rejectOpen}
        title="排除候选官网"
        onCancel={() => setRejectOpen(false)}
        onOk={() => void review("reject")}
        confirmLoading={busy}
        okButtonProps={{ disabled: !rejectReason.trim() }}
      >
        <Paragraph>原因会保留，后续重复结果仍可追溯。</Paragraph>
        <Input.TextArea
          aria-label="排除原因"
          value={rejectReason}
          maxLength={500}
          rows={3}
          onChange={(e) => setRejectReason(e.target.value)}
        />
      </Modal>
    </>
  );
}
