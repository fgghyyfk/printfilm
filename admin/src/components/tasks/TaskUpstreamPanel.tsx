import { useMemo } from "react";
import type { AdminTaskDetail } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { prettyJson } from "@/lib/jsonPreview";
import { groupUpstreamRecords, redactTaskData, redactTaskText, taskUpstream, upstreamStateLabel, UPSTREAM_PHASE_LABELS } from "@/lib/taskObservability";

function time(value?: string | null) {
  return value ? new Date(value).toLocaleString() : "未记录";
}

function JsonRecord({ title, value }: { title: string; value: unknown }) {
  return (
    <details className="rounded-lg border border-[var(--admin-border)] bg-white p-3">
      <summary className="cursor-pointer text-xs font-semibold text-[var(--admin-text)]">{title}（脱敏）</summary>
      {value === undefined ? <p className="mt-3 text-xs text-[var(--admin-muted)]">未记录</p> : <pre className="task-detail-json mt-3">{prettyJson(redactTaskData(value))}</pre>}
    </details>
  );
}

/** Only dedicated HTTP events are treated as evidence of actual upstream requests. */
export function TaskUpstreamPanel({ task }: { task: AdminTaskDetail }) {
  const summary = useMemo(() => taskUpstream(task), [task]);
  const requests = useMemo(() => groupUpstreamRecords(summary.records), [summary]);
  return (
    <div className="space-y-4">
      <section className="task-detail-section">
        <h4>实际上游请求</h4>
        <p className="text-xs leading-6 text-[var(--admin-muted)]">按请求 ID 关联提交与响应；查询事件可能为采样记录。缺失内容显示“未记录”。任务参数与步骤参数保留在各自页签。</p>
        <dl className="mt-3 grid gap-3 text-xs sm:grid-cols-3">
          <div><dt className="text-[var(--admin-muted)]">最近记录阶段</dt><dd className="mt-1 font-medium">{summary.phase}</dd></div>
          <div><dt className="text-[var(--admin-muted)]">上游任务 ID</dt><dd className="mt-1 break-all font-mono">{summary.providerTaskId || "未记录"}</dd></div>
          <div><dt className="text-[var(--admin-muted)]">请求记录</dt><dd className="mt-1">{requests.length} 组 · {summary.records.length} 个事件</dd></div>
        </dl>
      </section>

      {requests.length === 0 ? (
        <div className="rounded-xl border border-dashed border-[var(--admin-border)] px-5 py-8 text-sm text-[var(--admin-muted)]">未记录上游 HTTP 请求。已有上游任务 ID 或任务已完成，也不能据此还原实际请求体。</div>
      ) : requests.map((request) => (
        <section key={request.key} className="task-detail-section">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={request.latest.phase === "failed" ? "destructive" : request.latest.phase === "started" ? "warning" : "info"}>{UPSTREAM_PHASE_LABELS[request.latest.phase]}</Badge>
              {request.latest.upstreamStatus ? <Badge variant="secondary">{upstreamStateLabel(request.latest.upstreamStatus)}</Badge> : null}
              <span className="font-mono text-xs">{request.requestId || `事件 #${request.latest.id} · 请求 ID 未记录`}</span>
            </div>
            <span className="text-xs text-[var(--admin-muted)]">{time(request.latest.createdAt)}</span>
          </div>
          <div className="mt-3 break-all rounded-lg bg-[var(--admin-bg)] px-3 py-2 font-mono text-xs"><strong>{request.method || "METHOD未记录"}</strong> {request.endpoint || "Endpoint未记录"}</div>
          <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-2 xl:grid-cols-4">
            <div><dt className="text-[var(--admin-muted)]">实际模型 / Provider</dt><dd className="mt-1 break-all">{request.model || "未记录"} / {request.provider || "未记录"}</dd></div>
            <div><dt className="text-[var(--admin-muted)]">上游任务 ID</dt><dd className="mt-1 break-all font-mono">{request.providerTaskId || "未记录"}</dd></div>
            <div><dt className="text-[var(--admin-muted)]">HTTP 状态</dt><dd className="mt-1">{request.httpStatus ?? "未记录"}</dd></div>
            <div><dt className="text-[var(--admin-muted)]">本次 HTTP 耗时</dt><dd className="mt-1 tabular-nums">{request.elapsedMs == null ? "未记录" : `${request.elapsedMs} ms`}</dd></div>
          </dl>
          <ol className="my-4 space-y-2 border-l-2 border-[var(--admin-border)] pl-3">
            {request.records.map((record) => <li key={record.id} className="flex flex-wrap gap-x-3 gap-y-1 text-[11px]"><span>{UPSTREAM_PHASE_LABELS[record.phase]}</span>{record.upstreamStatus ? <span>{upstreamStateLabel(record.upstreamStatus)}</span> : null}<span className="text-[var(--admin-muted)]">{time(record.createdAt)}</span>{record.elapsedMs != null ? <span className="text-[var(--admin-muted)]">{record.elapsedMs} ms</span> : null}</li>)}
          </ol>
          <div className="grid items-start gap-3 xl:grid-cols-2">
            <JsonRecord title="请求" value={request.request} />
            <JsonRecord title="响应" value={request.response} />
          </div>
          {request.error !== undefined ? <div className="mt-3"><JsonRecord title="错误" value={request.error} /></div> : null}
        </section>
      ))}

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
