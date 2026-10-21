import type { AdminTaskEvent, AdminTaskRow } from "../api/types/tasks";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
const SENSITIVE_KEY = /(?:authorization|cookie|apikey|accesstoken|refreshtoken|securitytoken|password|passwd|secret|signature|credential|privatekey)|^(?:(?:api|x)?token|sig|sign|auth|sessionkey)$/i;

/** Redact credentials and every URL query value before data reaches text or tooltips. */
export function redactTaskText(text: string): string {
  return text
    .replace(/https?:\/\/[^\s"'<>]+/gi, (raw) => {
      try {
        const url = new URL(raw);
        url.username = "";
        url.password = "";
        for (const key of [...url.searchParams.keys()]) url.searchParams.set(key, "[REDACTED]");
        if (url.hash) url.hash = "#REDACTED";
        return url.toString();
      } catch {
        return "[URL REDACTED]";
      }
    })
    .replace(/(["'](?:authorization|api[-_]?key|access[-_]?token|refresh[-_]?token|token|secret|signature|password)["']\s*:\s*["'])[^"']*/gi, "$1[REDACTED]")
    .replace(/\b(Bearer|Basic)\s+[A-Za-z0-9._~+\/-]+=*/gi, "$1 [REDACTED]")
    .replace(/\b((?:authorization|cookie|api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|secret|signature|password)\s*[=:]\s*)[^\s,;"'}]+/gi, "$1[REDACTED]");
}

export function redactTaskData(value: unknown, depth = 0): unknown {
  if (depth > 12) return "[TRUNCATED]";
  if (typeof value === "string") {
    const safe = redactTaskText(value);
    return safe.length > 32_000 ? `${safe.slice(0, 32_000)}…[TRUNCATED]` : safe;
  }
  if (Array.isArray(value)) return value.slice(0, 100).map((item) => redactTaskData(item, depth + 1));
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).slice(0, 150).map(([key, item]) => {
    const normalized = key.replace(/[^a-z0-9]/gi, "");
    const sensitive = SENSITIVE_KEY.test(normalized) || (typeof item === "string" && /token$/i.test(normalized)) || /^(?:xamz|xoss).*(?:signature|credential|securitytoken)$/i.test(normalized);
    return [key, sensitive ? "[REDACTED]" : redactTaskData(item, depth + 1)];
  }));
}

export function formatTaskDuration(milliseconds: number | null | undefined): string {
  if (milliseconds == null || !Number.isFinite(milliseconds)) return "未记录";
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  if (seconds < 60) return `${seconds}秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}分${seconds % 60}秒`;
  return `${Math.floor(seconds / 3600)}小时${Math.floor((seconds % 3600) / 60)}分`;
}

function timestamp(value: string | null | undefined): number | null {
  if (!value) return null;
  const result = Date.parse(value);
  return Number.isFinite(result) ? result : null;
}

export function taskDurations(task: Pick<AdminTaskRow, "status" | "created_at" | "started_at" | "finished_at">, now = Date.now()) {
  const created = timestamp(task.created_at);
  const started = timestamp(task.started_at);
  const end = timestamp(task.finished_at) ?? (TERMINAL.has(task.status) ? null : now);
  const difference = (a: number | null, b: number | null) => a == null || b == null ? null : Math.max(0, a - b);
  return {
    waitingMs: difference(started ?? end, created),
    runningMs: difference(end, started),
    totalMs: difference(end, created),
    live: !TERMINAL.has(task.status),
  };
}

export function taskBilling(task: Pick<AdminTaskRow, "billing_status" | "billing_estimate_fen" | "billing_charged_fen" | "billing_refunded_fen">) {
  const status = task.billing_status;
  const labels: Record<string, string> = { none: "未预扣 · 尚未结算", frozen: "已冻结 · 尚未结算", settled: "已结算", skipped: "已跳过预扣 · 尚未结算" };
  return {
    label: status ? labels[status] || status : "计费状态未记录",
    settled: status === "settled",
    estimateFen: task.billing_estimate_fen,
    frozenFen: status === "frozen" ? task.billing_estimate_fen : status ? 0 : undefined,
    chargedFen: task.billing_charged_fen,
    refundedFen: task.billing_refunded_fen,
  };
}

export type UpstreamPhase = "started" | "accepted" | "failed" | "poll";
export type UpstreamRecord = {
  id: number;
  phase: UpstreamPhase;
  createdAt?: string | null;
  requestId?: string;
  method?: string;
  endpoint?: string;
  model?: string;
  provider?: string;
  providerTaskId?: string;
  elapsedMs?: number;
  httpStatus?: number;
  upstreamStatus?: string;
  request?: unknown;
  response?: unknown;
  error?: unknown;
};

function textField(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? redactTaskText(value.trim()) : undefined;
}

export function upstreamRecords(events: AdminTaskEvent[] | undefined): UpstreamRecord[] {
  return (events || []).flatMap((event) => {
    const match = event.event_type.match(/^upstream\.request\.(started|accepted|failed|poll)$/);
    if (!match) return [];
    const payload = event.payload || {};
    const response = payload.response && typeof payload.response === "object" ? payload.response as Record<string, unknown> : {};
    const body = response.body && typeof response.body === "object" ? response.body as Record<string, unknown> : {};
    const data = body.data && typeof body.data === "object" ? body.data as Record<string, unknown> : {};
    const statusCode = payload.http_status ?? payload.status_code ?? response.status_code;
    return [{
      id: event.id,
      phase: match[1] as UpstreamPhase,
      createdAt: event.created_at,
      requestId: textField(payload.request_id),
      method: textField(payload.method),
      endpoint: textField(payload.endpoint),
      model: textField(payload.model),
      provider: textField(payload.provider),
      providerTaskId: textField(payload.provider_task_id),
      elapsedMs: typeof payload.elapsed_ms === "number" ? payload.elapsed_ms : undefined,
      httpStatus: typeof statusCode === "number" ? statusCode : undefined,
      upstreamStatus: textField(payload.upstream_status) || textField(body.status) || textField(data.status),
      request: payload.request == null ? undefined : redactTaskData(payload.request),
      response: payload.response == null ? undefined : redactTaskData(payload.response),
      error: payload.error == null ? undefined : redactTaskData(payload.error),
    }];
  }).sort((a, b) => a.id - b.id);
}

export const UPSTREAM_PHASE_LABELS: Record<UpstreamPhase, string> = {
  started: "开始请求", accepted: "上游响应", failed: "请求失败", poll: "查询结果",
};

export function upstreamStateLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "上游排队", queued: "上游排队", submitted: "上游排队", waiting: "上游排队",
    running: "上游生成中", processing: "上游生成中", generating: "上游生成中", in_progress: "上游生成中",
    succeeded: "上游完成", success: "上游完成", completed: "上游完成", done: "上游完成", finished: "上游完成",
    failed: "上游失败", error: "上游失败", failure: "上游失败", cancelled: "上游已取消", canceled: "上游已取消",
  };
  return labels[status.toLowerCase()] || `上游状态：${redactTaskText(status)}`;
}

export function groupUpstreamRecords(records: UpstreamRecord[]) {
  const grouped = new Map<string, UpstreamRecord[]>();
  for (const record of records) {
    const key = record.requestId || `event-${record.id}`;
    grouped.set(key, [...(grouped.get(key) || []), record]);
  }
  return [...grouped.entries()].map(([key, items]) => {
    const latest = items.at(-1)!;
    const reverse = [...items].reverse();
    return {
      key, records: items, latest,
      requestId: latest.requestId,
      method: reverse.find((item) => item.method)?.method,
      endpoint: reverse.find((item) => item.endpoint)?.endpoint,
      model: reverse.find((item) => item.model)?.model,
      provider: reverse.find((item) => item.provider)?.provider,
      providerTaskId: reverse.find((item) => item.providerTaskId)?.providerTaskId,
      request: reverse.find((item) => item.request !== undefined)?.request,
      response: reverse.find((item) => item.response !== undefined)?.response,
      error: reverse.find((item) => item.error !== undefined)?.error,
      elapsedMs: reverse.find((item) => item.elapsedMs !== undefined)?.elapsedMs,
      httpStatus: reverse.find((item) => item.httpStatus !== undefined)?.httpStatus,
    };
  }).reverse();
}

export function taskUpstream(task: Pick<AdminTaskRow, "events" | "steps" | "payload" | "provider_task_id">) {
  const records = upstreamRecords(task.events);
  const latest = records.at(-1);
  const modelRecord = [...records].reverse().find((record) => record.model);
  const providerRecord = [...records].reverse().find((record) => record.provider);
  const taskIdRecord = [...records].reverse().find((record) => record.providerTaskId);
  const providerStep = [...(task.steps || [])].reverse().find((step) => step.provider_name);
  const taskIdStep = [...(task.steps || [])].reverse().find((step) => step.provider_task_id);
  const providerTaskId = taskIdRecord?.providerTaskId || textField(task.provider_task_id) || textField(taskIdStep?.provider_task_id);
  return {
    records,
    latest,
    model: modelRecord?.model || textField(task.payload?.model_id) || textField(task.payload?.model),
    modelFromRequest: Boolean(modelRecord),
    provider: providerRecord?.provider || textField(providerStep?.provider_name),
    providerTaskId,
    phase: latest?.upstreamStatus ? upstreamStateLabel(latest.upstreamStatus) : latest ? UPSTREAM_PHASE_LABELS[latest.phase] : providerTaskId ? "已有上游任务 · HTTP未记录" : "尚无上游请求记录",
  };
}
