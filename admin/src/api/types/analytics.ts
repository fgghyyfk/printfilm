/** 运营看板、官方用量对账与按日财务统计的接口契约。 */
export type AdminUsageBucket = {
  key: string;
  calls: number;
  charge_fen: number;
  cost_fen?: number;
};

export type AdminDailyUsage = {
  date: string;
  calls: number;
  charge_fen: number;
  cost_fen?: number;
};

export type AdminTopUser = {
  user_id: number;
  email?: string | null;
  calls: number;
  charge_fen: number;
  cost_fen?: number;
};

export type AdminStats = {
  user_count: number;
  order_paid_total_fen: number;
  order_paid_today_fen: number;
  project_status_counts: Record<string, number>;
  drama_project_count?: number;
  usage_calls_today?: number;
  usage_calls_month?: number;
  usage_calls_total?: number;
  usage_charge_today_fen?: number;
  usage_charge_month_fen?: number;
  usage_charge_total_fen?: number;
  usage_cost_today_fen?: number;
  usage_cost_month_fen?: number;
  usage_cost_total_fen?: number;
  usage_by_capability?: AdminUsageBucket[];
  usage_by_domain?: AdminUsageBucket[];
  daily_usage?: AdminDailyUsage[];
  top_users_by_charge?: AdminTopUser[];
};

export type AdminUpstreamUsageDay = {
  date: string;
  local_cost_fen: number;
  local_tokens: number;
  official_tokens: number;
  official_cost_fen: number;
  delta_fen: number;
  delta_pct?: number | null;
};

export type AdminUpstreamUsage = {
  configured: boolean;
  days: number;
  last_sync_at?: string | null;
  series: AdminUpstreamUsageDay[];
};

export type AdminUpstreamUsageSync = {
  configured: boolean;
  synced: number;
  skipped: number;
  last_sync_at?: string | null;
};

export type AdminFinanceDailyRow = {
  date: string;
  charge_fen: number;
  cost_fen: number;
  tokens: number;
  actual_cost_fen: number;
  profit_fen: number;
  profit_pct?: number | null;
};

export type AdminFinanceDailyTotals = {
  charge_fen: number;
  cost_fen: number;
  tokens: number;
  actual_cost_fen: number;
  profit_fen: number;
  profit_pct?: number | null;
};

export type AdminFinanceDaily = {
  configured: boolean;
  days: number;
  last_sync_at?: string | null;
  totals: AdminFinanceDailyTotals;
  series: AdminFinanceDailyRow[];
};
