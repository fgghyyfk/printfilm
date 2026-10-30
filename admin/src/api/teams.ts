import { api } from "./client";

/** 团队查询共用的服务端分页参数。 */
type ListFilters = { page: number; pageSize: number; q: string };

/** 构造分页参数，避免将未填写的筛选传给接口。 */
function listParams(filters: ListFilters): URLSearchParams {
  const params = new URLSearchParams({ page: String(filters.page), page_size: String(filters.pageSize) });
  if (filters.q.trim()) params.set("q", filters.q.trim());
  return params;
}

/** 集中管理团队只读 API 路径，页面只传业务筛选值。 */
export const teamPaths = {
  /** 团队列表搜索与分页。 */
  list(filters: ListFilters): string {
    return `/api/admin/teams?${listParams(filters)}`;
  },
  /** 指定团队的只读概况。 */
  detail(teamId: number): string {
    return `/api/admin/teams/${teamId}`;
  },
  /** 成员身份、状态与消费月份筛选。 */
  members(teamId: number, filters: ListFilters & { role: string; state: string; period: string }): string {
    const params = listParams(filters);
    params.set("state", filters.state);
    if (filters.role) params.set("role", filters.role);
    if (filters.period) params.set("period", filters.period);
    return `/api/admin/teams/${teamId}/members?${params}`;
  },
  /** 团队消费记录与筛选合计。 */
  usage(teamId: number, filters: ListFilters & { memberId: string; settled: string; dateFrom: string; dateTo: string }): string {
    const params = listParams(filters);
    if (filters.memberId) params.set("user_id", filters.memberId);
    if (filters.settled) params.set("settled", filters.settled);
    if (filters.dateFrom) params.set("date_from", filters.dateFrom);
    if (filters.dateTo) params.set("date_to", filters.dateTo);
    return `/api/admin/teams/${teamId}/usage-events?${params}`;
  },
  /** 团队钱包流水及后台调账搜索。 */
  walletLedger(teamId: number, filters: ListFilters & { adjustmentsOnly: boolean }): string {
    const params = listParams(filters);
    params.set("adjustments_only", String(filters.adjustmentsOnly));
    return `/api/admin/teams/${teamId}/wallet-ledger?${params}`;
  },
};

/** 设置团队或有效成员月额度，不更改所有者余额。 */
export function setAdminTeamBudget(teamId: number, monthlyLimitFen: number | null, memberId?: number): Promise<{ monthly_limit_fen: number | null }> {
  const scope = memberId === undefined ? "" : "/members/" + memberId;
  return api("/api/admin/teams/" + teamId + scope + "/budget", { method: "PATCH", body: JSON.stringify({ monthly_limit_fen: monthlyLimitFen }) });
}
