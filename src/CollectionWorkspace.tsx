import { useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Collapse,
  Grid,
  Drawer,
  Empty,
  Space,
  Table,
  Tag,
  Timeline,
  Typography,
} from "antd";
import { api } from "./api";
import { displayTime } from "./live-types";
import type { CollectionStatus, Job, LiveAccount } from "./live-types";

const { Text, Paragraph } = Typography;
type Candidate = Job["candidates"][number];
type Observation = {
  at: string;
  phase: string;
  url?: string;
  status?: number;
  reason?: string;
  pages?: number;
  contacts?: number;
  delaySeconds?: number;
  actor?: string;
  nextAt?: string;
};
type Attempt = {
  id: number;
  number: number;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  reason: string;
  pages: number;
  legacy: number;
  result_action: string;
  account_id: string | null;
  observations: Observation[];
};
const labels: Record<string, string> = {
  queued: "等待采集",
  running: "正在采集",
  completed: "采集成功",
  skipped: "未建档",
  failed: "采集失败",
  interrupted: "重启中断",
};
const reasons: Record<string, string> = {
  network_or_tls_error: "网络或证书连接失败（旧记录）",
  network_error: "网络连接失败",
  dns_error: "域名解析失败",
  request_timeout: "请求超时",
  tls_error: "网站证书校验失败，请先核实站点",
  robots_unavailable: "robots 服务暂不可用",
  robots_unavailable_or_denied: "robots 不可用或拒绝访问（旧记录，需核实）",
  robots_denied: "网站拒绝读取 robots 规则",
  robots_rate_limited: "网站限制访问频率，请暂停",
  robots_disallowed: "网站规则不允许采集这些页面",
  crawl_delay_exceeds_budget: "网站要求的访问间隔超过本轮上限",
  access_denied: "网站拒绝访问",
  rate_limited: "网站限制访问频率，请暂停",
  page_too_large: "页面超过 2 MB 上限",
  origin_redirect_requires_new_seed: "官网跳转到另一入口，请核验新网址后建任务",
  cross_site_redirect: "跳转至其他站点，需核验新网址",
  no_public_business_email: "本轮未找到公开业务邮箱",
  no_mail_route: "已找到地址，但未确认邮件路由",
  non_public_destination: "目标不是公开网站地址",
  crawl_failed: "采集异常，请检查服务日志",
  resumed_after_restart: "服务重启；旧尝试保留，重新排队",
  invalid_url: "网址格式无效",
  port_not_allowed: "网址端口不支持",
};
const retryable = new Set([
  "network_or_tls_error",
  "network_error",
  "dns_error",
  "request_timeout",
  "robots_unavailable",
]);
const phases: Record<string, string> = {
  robots_requested: "读取网站规则",
  robots_received: "网站规则响应",
  page_requested: "访问页面",
  page_received: "页面响应",
  request_failed: "请求失败",
  policy_checked: "访问规则已检查",
  page_parsed: "页面提取完成",
  page_skipped: "规则禁止访问，已跳过",
  retry_requested: "管理员安排重试",
};
const reasonText = (reason: string) => reasons[reason] ?? reason;
function statusTag(status: string) {
  return (
    <Tag
      color={
        status === "failed"
          ? "red"
          : status === "completed"
            ? "green"
            : status === "running"
              ? "processing"
              : "default"
      }
    >
      {labels[status] ?? status}
    </Tag>
  );
}

export default function CollectionWorkspace({
  jobs,
  status,
  accounts,
  refresh,
  onNewJob,
  onAccount,
}: {
  jobs: Job[];
  status: CollectionStatus | null;
  accounts: LiveAccount[];
  refresh: () => Promise<void>;
  onNewJob: (url: string) => void;
  onAccount: (a: LiveAccount) => void;
}) {
  const { message } = App.useApp();
  const screens = Grid.useBreakpoint();
  const [selected, setSelected] = useState<number>();
  const candidate = jobs
    .flatMap((j) => j.candidates)
    .find((c) => c.id === selected);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (selected === undefined) return;
    let active = true;
    setAttempts([]);
    setError("");
    const read = async () => {
      try {
        const result = await api<{ attempts: Attempt[] }>(
          `/api/candidates/${selected}/attempts`,
        );
        if (active) {
          setAttempts(result.attempts);
          setError("");
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      }
    };
    void read();
    const timer = setInterval(() => void read(), 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [selected]);
  const retry = async (c: Candidate) => {
    setBusy(true);
    try {
      await api(`/api/candidates/${c.id}/retry`, {
        attempt_id: c.lastAttempt?.id,
      });
      await refresh();
      message.success("已安排重试，60 秒后可执行；原失败记录保留。");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const counts = status?.counts ?? {};
  return (
    <>
      <Alert
        showIcon
        type="info"
        title="当前按官网名单采集 · 尚未启用定时搜索"
        description={`每批 1–30 个官网；同站请求至少间隔 ${status?.siteDelaySeconds ?? 3} 秒，并遵守网站要求。后台每 ${status?.idlePollSeconds ?? 5} 秒检查队列，这不代表每 5 秒搜索新客户。`}
      />
      <div className="collection-metrics">
        {[
          ["候选官网", jobs.reduce((n, j) => n + j.candidates.length, 0)],
          ["成功建档 / 更新", counts.completed ?? 0],
          ["失败待检查", counts.failed ?? 0],
          ["等待 / 运行", (counts.queued ?? 0) + (counts.running ?? 0)],
        ].map(([label, value]) => (
          <Card key={label}>
            <Text type="secondary">{label}</Text>
            <strong>{value}</strong>
          </Card>
        ))}
      </div>
      <Card className="section-card" title="运行状态与频率">
        <Space wrap>
          {["collection", "integration"].map((name) => {
            const w = status?.workers.find((w) => w.name === name);
            const stale = !w || Date.now() - Date.parse(w.seen_at) > 180000;
            return (
              <div className="worker-status" key={name}>
                <Text strong>
                  {name === "collection" ? "官网采集" : "邮件同步"}
                </Text>{" "}
                <Tag color={stale || w?.error ? "warning" : "green"}>
                  {stale
                    ? "心跳待确认"
                    : w?.error
                      ? "运行异常"
                      : w?.phase === "running"
                        ? "处理中"
                        : "在线"}
                </Tag>
                <div className="secondary small">
                  最近心跳：{displayTime(w?.seen_at)}
                </div>
                {w?.error && <Text type="danger">{w.error}</Text>}
              </div>
            );
          })}
        </Space>
        <Paragraph type="secondary" className="spaced">
          候选最近变化：{displayTime(status?.lastCandidateUpdate)}
          。这一时间反映任务变化，与服务是否在线不同。
        </Paragraph>
        <Text type="secondary">
          本版记录的成功尝试：新增 {status?.outcomes.created ?? 0}{" "}
          次，更新已有档案 {status?.outcomes.updated ?? 0}{" "}
          次。旧任务只有结果快照，无法还原新增比例和逐页过程。
        </Text>
      </Card>
      <Card title="采集批次" className="section-card">
        {!screens.md ? (
          <Collapse
            items={jobs.map((j) => ({
              key: j.id,
              label: (
                <div>
                  <Text strong>{j.name}</Text>
                  <div className="secondary small">
                    {j.region} · {j.candidates.length} 个官网 ·{" "}
                    {j.candidates.filter((c) => c.status === "failed").length}{" "}
                    个失败
                  </div>
                </div>
              ),
              children: j.candidates.map((c) => (
                <div key={c.id} className="candidate-mobile">
                  <Paragraph strong className="break-anywhere">
                    {c.url}
                  </Paragraph>
                  <Space>
                    {statusTag(c.status)}
                    <Text type="secondary">{c.pages} 页</Text>
                  </Space>
                  {c.reason && (
                    <Paragraph className="spaced">
                      {reasonText(c.reason)}
                    </Paragraph>
                  )}
                  {c.nextAt && (
                    <Paragraph type="secondary">
                      重试时间：{displayTime(c.nextAt)}
                    </Paragraph>
                  )}
                  <Button type="link" onClick={() => setSelected(c.id)}>
                    查看过程
                  </Button>
                </div>
              )),
            }))}
          />
        ) : (
          <Table<Job>
            rowKey="id"
            dataSource={jobs}
            scroll={{ x: 680 }}
            pagination={{ pageSize: 10 }}
            columns={[
              {
                title: "任务 / 市场",
                render: (_, j) => (
                  <>
                    <Text strong>{j.name}</Text>
                    <div className="secondary">
                      {j.region} · {displayTime(j.created_at)}
                    </div>
                  </>
                ),
              },
              { title: "候选官网", render: (_, j) => j.candidates.length },
              {
                title: "成功",
                render: (_, j) =>
                  j.candidates.filter((c) => c.status === "completed").length,
              },
              {
                title: "失败",
                render: (_, j) =>
                  j.candidates.filter((c) => c.status === "failed").length,
              },
              {
                title: "未建档",
                render: (_, j) =>
                  j.candidates.filter((c) => c.status === "skipped").length,
              },
            ]}
            expandable={{
              expandedRowRender: (j) => (
                <Table<Candidate>
                  rowKey="id"
                  size="small"
                  pagination={false}
                  dataSource={j.candidates}
                  scroll={{ x: 720 }}
                  columns={[
                    {
                      title: "官网",
                      dataIndex: "url",
                      render: (url) => (
                        <Text className="break-anywhere">{url}</Text>
                      ),
                    },
                    {
                      title: "状态",
                      render: (_, c) => (
                        <>
                          {statusTag(c.status)}
                          {c.nextAt && (
                            <div className="secondary">
                              重试时间：{displayTime(c.nextAt)}
                            </div>
                          )}
                        </>
                      ),
                    },
                    { title: "已读页数", dataIndex: "pages" },
                    {
                      title: "结果 / 原因",
                      render: (_, c) =>
                        c.reason
                          ? reasonText(c.reason)
                          : c.lastAttempt?.result_action === "created"
                            ? "新增来源档案"
                            : c.lastAttempt?.result_action === "updated"
                              ? "更新已有档案"
                              : c.status === "completed"
                                ? "已建档（旧记录）"
                                : "等待结果",
                    },
                    {
                      title: "过程",
                      render: (_, c) => (
                        <Button type="link" onClick={() => setSelected(c.id)}>
                          查看过程
                        </Button>
                      ),
                    },
                  ]}
                />
              ),
            }}
          />
        )}
      </Card>
      <Drawer
        open={selected !== undefined}
        onClose={() => setSelected(undefined)}
        size={680}
        styles={{ wrapper: { maxWidth: "100vw" } }}
        title="采集过程"
        className="collection-drawer"
      >
        {candidate && (
          <>
            <Paragraph className="break-anywhere" strong>
              {candidate.url}
            </Paragraph>
            <Space wrap>
              {statusTag(candidate.status)}
              <Text>已读 {candidate.pages} 页</Text>
            </Space>
            {candidate.reason && (
              <Alert
                className="section-card"
                type={candidate.status === "failed" ? "warning" : "info"}
                title={reasonText(candidate.reason)}
                description={
                  <Text type="secondary">记录代码：{candidate.reason}</Text>
                }
              />
            )}
            {candidate.status === "failed" && (
              <Space wrap className="spaced">
                {retryable.has(candidate.reason) &&
                  (candidate.lastAttempt?.number ?? 0) < 3 && (
                    <Button
                      type="primary"
                      loading={busy}
                      onClick={() => void retry(candidate)}
                    >
                      60 秒后重试
                    </Button>
                  )}
                <Button
                  onClick={() => {
                    setSelected(undefined);
                    onNewJob(candidate.url);
                  }}
                >
                  核验网址并建新任务
                </Button>
              </Space>
            )}
            <Paragraph type="secondary" className="spaced">
              网络暂时失败可由管理员安排重试；最多三次正式尝试。访问限制、证书、跳转或页面大小问题需先核验，系统不会自动绕过限制。
            </Paragraph>
          </>
        )}
        {error && <Alert type="error" title={error} />}
        {!attempts.length && (
          <Empty description="暂无执行记录；任务可能仍在等待" />
        )}
        {attempts.map((a) => (
          <Card
            key={a.id}
            className="section-card"
            title={a.legacy ? "历史结果快照" : `第 ${a.number} 次尝试`}
            extra={statusTag(a.status)}
          >
            <Paragraph type="secondary">
              {a.legacy
                ? "旧版本未记录开始时间与逐页过程。"
                : `开始：${displayTime(a.started_at)}`}
              <br />
              结束：{displayTime(a.finished_at)} · 已读 {a.pages} 页
            </Paragraph>
            {a.reason && <Paragraph>{reasonText(a.reason)}</Paragraph>}
            {a.account_id && accounts.find((v) => v.id === a.account_id) && (
              <Button
                type="link"
                onClick={() =>
                  onAccount(accounts.find((v) => v.id === a.account_id)!)
                }
              >
                查看关联客户
              </Button>
            )}
            <Timeline
              items={a.observations.map((o, index) => ({
                key: index,
                children: (
                  <div>
                    <Text strong>{phases[o.phase] ?? o.phase}</Text>
                    <div className="secondary small">
                      {displayTime(o.at)}
                      {o.status ? ` · HTTP ${o.status}` : ""}
                    </div>
                    {o.url && <div className="break-anywhere">{o.url}</div>}
                    {o.reason && <div>{reasonText(o.reason)}</div>}
                    {o.pages !== undefined && (
                      <div>
                        累计 {o.pages} 页 · 累计 {o.contacts ?? 0} 个公开地址
                      </div>
                    )}
                    {o.delaySeconds !== undefined && (
                      <div>同站间隔 {o.delaySeconds} 秒</div>
                    )}
                    {o.actor && (
                      <div>
                        {o.actor} · 计划 {displayTime(o.nextAt)}
                      </div>
                    )}
                  </div>
                ),
              }))}
            />
          </Card>
        ))}
      </Drawer>
    </>
  );
}
