import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/page";

/** 团队列表统一呈现加载、错误重试和空结果。 */
export function TeamQueryFeedback({ loading, error, empty, onRetry }: { loading: boolean; error: string; empty: boolean; onRetry: () => void }) {
  if (loading) return <div role="status" className="p-10 text-center text-sm text-[var(--admin-muted)]">正在加载…</div>;
  if (error) return <div role="alert" className="flex flex-wrap items-center justify-center gap-3 p-8 text-sm text-red-700"><span>{error}</span><Button type="button" size="sm" variant="outline" onClick={onRetry}>重新加载</Button></div>;
  if (empty) return <EmptyState title="暂无匹配记录" description="可调整搜索、状态或日期条件后重试" />;
  return null;
}
