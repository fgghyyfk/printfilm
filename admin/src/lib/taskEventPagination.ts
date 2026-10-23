// 构建带服务端分页上限与可选类型筛选的事件查询路径。
export function taskEventPagePath(taskId: number, page: number, pageSize: number, eventTypePrefix?: string): string {
  const currentPage = Number.isFinite(page) ? Math.max(1, Math.trunc(page)) : 1;
  const currentSize = Number.isFinite(pageSize) ? Math.min(100, Math.max(1, Math.trunc(pageSize))) : 50;
  const query = new URLSearchParams({ page: String(currentPage), page_size: String(currentSize) });
  if (eventTypePrefix) query.set("event_type_prefix", eventTypePrefix);
  return `/api/admin/tasks/${taskId}/events?${query.toString()}`;
}
