import type { Account } from "./data";
type Contact = {
  email: string;
  role: string;
  url: string;
  excerpt: string;
  observedAt: string;
  mailRoute: string;
};
export type LiveAccount = Account & {
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
  assignmentAuthority?: "aimail";
  aimailThreadId?: string;
  aimailContact?: string;
  aimailQuantity?: string;
  assignment: {
    owner: string;
    pending: string;
    version: number;
    status?: string;
    notification?: { state: string; last_error: string };
    updated_at?: string;
    history?: {
      actor: string;
      action: string;
      recipient: string;
      reason: string;
      version: number;
      created_at: string;
    }[];
    mail_access_status?: string;
    mail_access_error?: string;
  };
  company: { company_id: string; version: number };
  workflow: { milestone: string; profile: Profile; version: number };
  followup: { stage: string; next_step: string; due_at: string } | null;
};
export type Job = {
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
    updated_at: string;
    nextAt: string | null;
    lastAttempt: {
      id: number;
      number: number;
      account_id: string | null;
      result_action: string;
      legacy: number;
    } | null;
  }[];
};
export type State = {
  role: "admin" | "sales";
  identity: string;
  members: string[];
  accounts: LiveAccount[];
  jobs: Job[];
  collection: CollectionStatus | null;
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

export type Profile = {
  product?: string;
  quantity?: string;
  country?: string;
  quote_ref?: string;
  order_ref?: string;
};
export type CollectionStatus = {
  workers: {
    name: string;
    phase: string;
    seen_at: string;
    candidate_id: number | null;
    error: string;
  }[];
  counts: Record<string, number>;
  outcomes: Record<string, number>;
  lastCandidateUpdate: string | null;
  discoverySchedule: string;
  siteDelaySeconds: number;
  idlePollSeconds: number;
  companyRecords: number;
};
export const milestones: Record<string, string> = {
  unverified: "待核验",
  identified: "可联系潜客",
  contacted: "已首次联系",
  engaged: "已建立沟通",
  qualified: "已确认需求",
  quoted: "已报价",
  negotiating: "商务 / 技术推进",
  won: "成交 / 转交履约",
  lost: "已关闭",
  nurture: "长期培育",
};
export const displayTime = (value?: string | null) =>
  value ? new Date(value).toLocaleString() : "暂无记录";
