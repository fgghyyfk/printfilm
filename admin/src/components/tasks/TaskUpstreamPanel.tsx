import { useMemo } from "react";
import type { AdminTaskDetail } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PaginationBar } from "@/components/PaginationBar";
import { useTaskEventPage } from "@/hooks/useTaskEventPage";
import { prettyJson } from "@/lib/jsonPreview";
import { CopyText } from "@/components/tasks/TaskDetailSummary";
import { groupUpstreamRecords, redactTaskData, redactTaskText, shortEndpoint, taskUpstream, upstreamRecords, upstreamStateLabel, UPSTREAM_PHASE_LABELS } from "@/lib/taskObservability";
import { cn } from "@/lib/utils";

function time(value?: string | null) {
  return value ? new Date(value).toLocaleString() : "未记录";
}

function JsonRecord({ title, value, paginated = false }: { title: string; value: unknown; paginated?: boolean }) {
  return (
    <details className="task-json-toggle">
      <summary>{title}<span>脱敏</span></summary>
      {value === undefined ? <p className="mt-3 text-xs text-[var(--admin-muted)]">{paginated ? "本页未包含，可翻页查看历史记录" : "未记录"}</p> : <pre className="task-detail-json mt-3">{prettyJson(redactTaskData(value))}</pre>}
    </details>
  );
}

/** Only dedicated HTTP events are treated as evidence of actual upstream requests. */
export function TaskUpstreamPanel({ task }: { task: AdminTaskDetail }) {
  const summary = useMemo(() => taskUpstream(task), [task]);
  const refreshToken = `${task.upstream_events_total}:${task.updated_at}:${task.status}`;
  const history = useTaskEventPage(task.id, refreshToken, "upstream.request.");
  const pageRecords = useMemo(() => upstreamRecords(history.data?.items), [history.data]);
  const requests = useMemo(() => groupUpstreamRecords(pageRecords), [pageRecords]);
  return (
    <div className="space-y-3">
      <div className="task-upstream-toolbar">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          <span><span className="text-[var(--admin-muted)]">最近阶段</span> <strong>{summary.phase}</strong></span>
          <span className="text-[var(--admin-muted)]">本页 {pageRecords.length} 条 · 历史 {history.data?.total ?? task.upstream_events_total ?? "—"} 条</span>
        </div>
        <Button variant="outline" size="sm" disabled={history.loading} onClick={history.refresh}>刷新上游记录</Button>
      </div>
      <p className="text-[11px] leading-5 text-[var(--admin-muted)]">按事件 ID 倒序分页，本页内按请求 ID 关联提交与响应；同一请求可能跨页，查询事件可能为采样记录。</p>
      {history.error ? <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700" role="alert">{history.error}<Button className="ml-3" variant="outline" size="sm" onClick={history.refresh}>重试</Button></div> : null}

      {history.loading ? <p className="py-8 text-center text-sm text-[var(--admin-muted)]" role="status">正在加载本页上游事件…</p> : history.error ? null : requests.length === 0 ? (
        <div className="rounded-xl border border-dashed border-[var(--admin-border)] px-5 py-8 text-sm text-[var(--admin-muted)]">本页未记录上游 HTTP 请求。已有上游任务 ID 或任务已完成，也不能据此还原实际请求体。</div>
      ) : requests.map((request) => {
        const failed = request.latest.phase === "failed" || (request.httpStatus ?? 0) >= 400;
        return (
          <section key={request.key} className={cn("task-upstream-card", failed && "is-fail")}>
            <header className="task-upstream-head">
              <Badge variant={failed ? "destructive" : request.latest.phase === "started" ? "warning" : "info"}>{UPSTREAM_PHASE_LABELS[request.latest.phase]}</Badge>
              {request.latest.upstreamStatus ? <Badge variant="secondary">{upstreamStateLabel(request.latest.upstreamStatus)}</Badge> : null}
              <code className="task-upstream-endpoint" title={request.endpoint}>
                <strong>{request.method || "METHOD?"}</strong> {request.endpoint ? shortEndpoint(request.endpoint) : "Endpoint未记录"}
              </code>
              <span className="ml-auto flex items-center gap-3 text-xs tabular-nums">
                <span className={cn("task-http-chip", failed && "is-fail")}>{request.httpStatus ?? "—"}</span>
                <span>{request.elapsedMs == null ? "—" : `${Math.round(request.elapsedMs)} ms`}</span>
                <span className="text-[var(--admin-muted)]">{time(request.latest.createdAt)}</span>
              </span>
            </header>
            <div className="task-upstream-meta">
              <span><span className="text-[var(--admin-muted)]">模型</span> {request.model || "未记录"} / {request.provider || "未记录"}</span>
              <span className="flex min-w-0 items-center gap-1"><span className="text-[var(--admin-muted)]">上游任务</span> {request.providerTaskId ? <CopyText value={request.providerTaskId} /> : "未记录"}</span>
              <span className="flex min-w-0 items-center gap-1"><span className="text-[var(--admin-muted)]">请求 ID</span> {request.requestId ? <CopyText value={request.requestId} /> : `事件 #${request.latest.id}`}</span>
            </div>
            {request.records.length > 1 ? (
              <ol className="task-upstream-steps">
                {request.records.map((record) => (
                  <li key={record.id}>
                    <span className="font-medium">{UPSTREAM_PHASE_LABELS[record.phase]}</span>
                    {record.upstreamStatus ? <span>{upstreamStateLabel(record.upstreamStatus)}</span> : null}
                    <span className="text-[var(--admin-muted)]">{record.createdAt ? new Date(record.createdAt).toLocaleTimeString() : "—"}</span>
                    {record.elapsedMs != null ? <span className="text-[var(--admin-muted)]">{Math.round(record.elapsedMs)} ms</span> : null}
                  </li>
                ))}
              </ol>
            ) : null}
            <div className="grid items-start gap-2 xl:grid-cols-2">
              <JsonRecord title="请求" value={request.request} paginated />
              <JsonRecord title="响应" value={request.response} paginated />
            </div>
            {request.error !== undefined ? <div className="mt-2"><JsonRecord title="错误" value={request.error} paginated /></div> : null}
          </section>
        );
      })}

      {history.data ? <PaginationBar page={history.page} pageSize={history.pageSize} total={history.data.total} onPageChange={history.setPage} onPageSizeChange={history.changePageSize} pageSizeOptions={[20, 50, 100]} /> : null}

      {(task.steps || []).some((step) => step.input_payload || step.output_payload) ? (
        <details className="task-detail-section">
          <summary className="cursor-pointer text-sm font-semibold">已有步骤数据（内部执行记录）</summary>
          <p className="my-3 text-xs text-[var(--admin-muted)]">以下是步骤输入与结果，不代表实际发往上游的 HTTP 请求体。</p>
          <div className="space-y-4">{task.steps.filter((step) => step.input_payload || step.output_payload).map((step) => <section key={step.id}><h5 className="mb-2 text-xs font-semibold">{step.step_key}{step.provider_name ? ` · ${redactTaskText(step.provider_name)}` : ""}</h5><div className="grid items-start gap-3 xl:grid-cols-2"><JsonRecord title="步骤输入" value={step.input_payload ?? undefined} /><JsonRecord title="步骤结果" value={step.output_payload ?? undefined} /></div></section>)}</div>
        </details>
      ) : null}
    </div>
  );
}
