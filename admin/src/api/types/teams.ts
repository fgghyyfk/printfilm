import type { PageResult } from "./common";

/** 团队列表与详情共用的只读快照；金额单位为分。 */
export type AdminTeam = {
  id: number; name: string; description: string; color: string;
  owner_user_id: number; owner_nickname: string; owner_email: string;
  member_count: number; project_count: number; balance_fen: number; frozen_fen: number;
  monthly_limit_fen: number | null; month_reserved_fen: number; billing_source: "owner_personal";
  month_spent_fen: number; total_spent_fen: number; period: string; created_at: string;
};

/** 当前或历史成员关系与指定月份的预算使用情况。 */
export type AdminTeamMember = {
  user_id: number; nickname: string; email: string; role: "owner" | "admin" | "member";
  active: boolean; monthly_limit_fen: number | null; spent_fen: number; reserved_fen: number; joined_at: string;
};
export type AdminTeamMembers = PageResult<AdminTeamMember> & { period: string };

/** 团队消费明细，待结算金额不计入已结算合计。 */
export type AdminTeamUsage = {
  id: number; user_id: number; user_nickname: string; user_email: string;
  project_id: number | null; project_title: string | null; task_run_id: number | null;
  billing_key: string; billing_label: string; model: string; total_tokens: number;
  charge_fen: number; settled: boolean; created_at: string;
};
export type AdminTeamUsagePage = PageResult<AdminTeamUsage> & {
  summary: { record_count: number; settled_charge_fen: number };
};

/** 团队钱包流水，调账操作人独立于团队所有者。 */
export type AdminTeamWalletLedger = {
  id: number; actor_user_id: number; operator_nickname: string; operator_email: string;
  delta_fen: number; balance_after_fen: number; kind: string; ref_type: string; ref_id: string;
  note: string; created_at: string;
};
