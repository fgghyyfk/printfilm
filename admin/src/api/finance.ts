import { api } from "./client";
import type { PageResult } from "./types/common";
import type { AdminLedger, AdminOrder, AdminUsageEventListRes } from "./types/finance";
import { DEFAULT_PAGE_SIZE } from "@/lib/pagination";

/** 财务列表共享分页条件；嵌入用户详情时可使用更小的 pageSize。 */
type ListFilters = { userId?: number | null; pageSize?: number };
type OrderFilters = ListFilters & { status?: string; outTradeNo?: string };
type LedgerFilters = ListFilters & { kind?: string; refType?: string; dateFrom?: string; dateTo?: string };
type UsageFilters = ListFilters & {
  taskRunId?: string;
  domain?: string;
  billingKey?: string;
  capability?: string;
  model?: string;
  billingBasis?: string;
  dateFrom?: string;
  dateTo?: string;
};

/** 为财务列表构造服务端分页与用户条件。 */
function listParams(page: number, filters: ListFilters): URLSearchParams {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(filters.pageSize ?? DEFAULT_PAGE_SIZE),
  });
  if (filters.userId) params.set("user_id", String(filters.userId));
  return params;
}

/** 按上海日历日设置时间边界，避免无时区时间被当成 UTC。 */
function setDateRange(params: URLSearchParams, from?: string, to?: string): void {
  if (from?.trim()) params.set("created_from", `${from.trim()}T00:00:00+08:00`);
  if (to?.trim()) params.set("created_to", `${to.trim()}T23:59:59+08:00`);
}

/** 查询充值订单；商户单号筛选也供跨页深链定位使用。 */
export function listAdminOrders(page = 1, filters: OrderFilters = {}): Promise<PageResult<AdminOrder>> {
  const params = listParams(page, filters);
  if (filters.status) params.set("status", filters.status);
  if (filters.outTradeNo) params.set("out_trade_no", filters.outTradeNo);
  return api(`/api/admin/orders?${params}`);
}

/** 查询钱包流水，支持调账记录与用户详情复用同一接口映射。 */
export function listAdminLedger(page = 1, filters: LedgerFilters = {}): Promise<PageResult<AdminLedger>> {
  const params = listParams(page, filters);
  if (filters.kind) params.set("kind", filters.kind);
  if (filters.refType) params.set("ref_type", filters.refType);
  setDateRange(params, filters.dateFrom, filters.dateTo);
  return api(`/api/admin/ledger?${params}`);
}

/** 查询 AI 用量明细，集中维护后端筛选参数与日期边界。 */
export function listAdminUsageEvents(page = 1, filters: UsageFilters = {}): Promise<AdminUsageEventListRes> {
  const params = listParams(page, filters);
  if (filters.taskRunId) params.set("task_run_id", filters.taskRunId);
  if (filters.domain) params.set("domain", filters.domain);
  if (filters.billingKey) params.set("billing_key", filters.billingKey);
  if (filters.capability) params.set("capability", filters.capability);
  if (filters.model) params.set("model", filters.model);
  if (filters.billingBasis) params.set("billing_basis", filters.billingBasis);
  setDateRange(params, filters.dateFrom, filters.dateTo);
  return api(`/api/admin/usage-events?${params}`);
}
