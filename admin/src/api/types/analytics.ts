/** 运营看板与按日扣费、用量统计的接口契约。 */
export type AdminUsageBucket = {
  key: string;
  calls: number;
  charge_fen: number;
};

export type AdminDailyUsage = {
  date: string;
  calls: number;
  charge_fen: number;
};

export type AdminTopUser = {
  user_id: number;
  email?: string | null;
  calls: number;
  charge_fen: number;
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
  usage_by_capability?: AdminUsageBucket[];
  usage_by_domain?: AdminUsageBucket[];
  daily_usage?: AdminDailyUsage[];
  top_users_by_charge?: AdminTopUser[];
};

export type AdminFinanceDailyRow = {
  date: string;
  charge_fen: number;
  calls: number;
  tokens: number;
};

export type AdminFinanceDailyTotals = {
  charge_fen: number;
  calls: number;
  tokens: number;
};

export type AdminFinanceDaily = {
  days: number;
  totals: AdminFinanceDailyTotals;
  series: AdminFinanceDailyRow[];
};
