export type PlanConfig = {
  name: string;
  country: string;
  industry: string;
  product: string;
  objective: string;
  provider: "brave" | "tavily";
  queries: string[];
  interval_hours: 0 | 6 | 24 | 168;
  tier: string;
};
export type SearchRun = {
  id: string;
  plan_id: string;
  kind: string;
  config: PlanConfig;
  status: string;
  error: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};
export type DiscoveryPlan = {
  id: string;
  config: PlanConfig;
  enabled: number;
  version: number;
  next_run_at: string | null;
  failures: number;
  last_error: string;
  last_run: SearchRun | null;
};
export type DiscoveryState = {
  worker: { phase: string; seen_at: string; error: string } | null;
  plans: DiscoveryPlan[];
  providers: { id: string; configured: boolean }[];
  daily_limit: number;
  used_today: number;
  resets_at: string;
  pending: number;
  countries: { code: string; name: string }[];
};
export type SearchResult = {
  id: number;
  query_id: number;
  rank: number;
  title: string;
  source_url: string;
  site_url: string;
  domain: string;
  snippet: string;
  decision: string;
  reason: string;
  canonical_id: number | null;
  canonical_run_id: string | null;
  canonical_decision: string | null;
  canonical_reason: string | null;
  candidate_id: number | null;
  reviewed_by: string;
  reviewed_at: string | null;
  crawl_status: string | null;
  account_id: string | null;
  milestone: string | null;
};
export type RunDetails = SearchRun & {
  queries: {
    id: number;
    query: string;
    status: string;
    requested_at: string | null;
    finished_at: string | null;
    error: string;
  }[];
  results: SearchResult[];
};
export const searchErrors: Record<string, string> = {
  not_configured: "搜索服务尚未接入",
  auth_failed: "搜索凭据或权限无效，计划已暂停",
  rate_limited: "搜索服务限流，本轮已停止",
  unavailable: "搜索服务暂不可用",
  invalid_response: "搜索服务返回格式不完整",
  daily_budget: "今日请求额度已用完，等待下个 UTC 日",
  interrupted: "重启中断；已发出的请求不自动重放",
  paused: "管理员已暂停本轮",
};
export const resultReasons: Record<string, string> = {
  invalid_url: "网址格式不支持",
  not_company_site: "不是企业官网地址",
  not_public_site: "不是公开网站地址",
  directory_or_social: "目录、平台或社交网站，需要独立核实官网",
  cancelled_run: "收到结果时本轮已暂停",
  existing_account: "官网已在客户库",
  existing_candidate: "官网已在采集记录中",
  seen_in_discovery: "其他搜索已发现此官网",
};
export const runLabels: Record<string, string> = {
  queued: "等待搜索",
  running: "正在搜索",
  succeeded: "搜索完成",
  partial: "部分完成",
  failed: "搜索失败",
  deferred: "等待额度",
  interrupted: "重启中断",
  cancelled: "已暂停",
  skipped: "未执行",
};
export const frequencies: Record<number, string> = {
  0: "仅手动",
  6: "每 6 小时",
  24: "每天",
  168: "每周",
};
export const templates = [
  {
    name: "GPU 云与数据中心",
    industry: "云服务 / 数据中心",
    product: "GPU 服务器、存储与数据中心配件",
    objective:
      "寻找经营 GPU 云、托管或数据中心服务的企业；核实硬件采购、扩容或替换需求。",
    queries: [
      "GPU cloud provider company contact",
      "data center infrastructure hosting company contact",
    ],
  },
  {
    name: "服务器与配件渠道",
    industry: "服务器渠道",
    product: "服务器整机、内存、SSD 与 GPU 配件",
    objective:
      "寻找服务器经销商、硬件渠道与配件贸易企业，了解常采品牌、型号和合作方式。",
    queries: [
      "enterprise server hardware reseller company contact",
      "server memory SSD distributor company contact",
    ],
  },
  {
    name: "企业 IT 与系统集成",
    industry: "行业系统集成",
    product: "企业服务器、存储和基础设施方案",
    objective:
      "寻找为企业提供 IT 基础设施的集成商，核实项目型采购与长期供应合作机会。",
    queries: [
      "enterprise IT infrastructure systems integrator company contact",
    ],
  },
];
