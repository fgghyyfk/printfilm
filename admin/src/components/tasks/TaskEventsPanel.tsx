import type { AdminTaskDetail } from "@/api/client";
import { PaginationBar } from "@/components/PaginationBar";
import { Button } from "@/components/ui/button";
import { useTaskEventPage } from "@/hooks/useTaskEventPage";
import { prettyJson } from "@/lib/jsonPreview";
import { redactTaskData, redactTaskText } from "@/lib/taskObservability";

// 分页查看任务的完整历史，避免渲染全部事件。
export function TaskEventsPanel({ task }: { task: AdminTaskDetail }) {
  const refreshToken = `${task.events_total}:${task.updated_at}:${task.status}`;
  const history = useTaskEventPage(task.id, refreshToken);
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-[var(--admin-muted)]">按事件 ID 倒序分页；历史记录保留在数据库，不会随详情截断丢失。</p>
        <Button size="sm" variant="outline" disabled={history.loading} onClick={history.refresh}>刷新事件</Button>
      </div>
      {history.error ? <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700" role="alert">{history.error}<Button className="ml-3" variant="outline" size="sm" onClick={history.refresh}>重试</Button></div> : null}
      {history.loading ? <p className="py-8 text-center text-sm text-[var(--admin-muted)]" role="status">正在加载本页事件…</p> : !history.error ? (
        <div className="task-detail-events">
          {history.data?.items.length ? history.data.items.map((event) => (
            <div key={event.id} className="task-detail-event">
              <div className="task-detail-event-head">
                <span className="font-mono text-xs">#{event.id} · {event.event_type}</span>
                <span className="text-[11px] text-[var(--admin-muted)]">{event.created_at ? new Date(event.created_at).toLocaleString() : "未记录"}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-[var(--admin-muted)]">
                {event.status ? <span>status={event.status}</span> : null}
                {event.phase ? <span>phase={event.phase}</span> : null}
              </div>
              {event.message ? <p className="mt-1 text-sm">{redactTaskText(event.message)}</p> : null}
              {event.payload ? <details className="mt-3"><summary className="cursor-pointer text-xs text-[var(--admin-muted)]">事件数据（脱敏）</summary><pre className="task-detail-json task-detail-json--compact mt-2">{prettyJson(redactTaskData(event.payload))}</pre></details> : null}
            </div>
          )) : <p className="text-sm text-[var(--admin-muted)]">本页暂无事件日志</p>}
        </div>
      ) : null}
      {history.data ? <PaginationBar page={history.page} pageSize={history.pageSize} total={history.data.total} onPageChange={history.setPage} onPageSizeChange={history.changePageSize} pageSizeOptions={[20, 50, 100]} /> : null}
    </div>
  );
}
