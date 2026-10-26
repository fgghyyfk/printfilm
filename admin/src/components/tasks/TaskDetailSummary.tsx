import { Copy } from "lucide-react";
import { toast } from "sonner";
import type { AdminTaskDetail } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { formatTaskDuration, taskBilling, taskDurations, taskStatusTone, taskUpstream } from "@/lib/taskObservability";
import { taskStatusLabel } from "@/lib/statusLabels";
import { cn, fenToYuan } from "@/lib/utils";

type TaskDetailSummaryProps = {
  task: AdminTaskDetail;
  now: number;
  onJump: (tab: "upstream" | "billing") => void;
};

function money(value: number | null | undefined): string {
  return value == null ? "—" : `¥${fenToYuan(value)}`;
}

// 可复制的等宽文本，长 ID 截断显示、完整值放 title
export function CopyText({ value, className }: { value: string; className?: string }) {
  async function copy(e: { stopPropagation: () => void }) {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
      toast.success("已复制");
    } catch {
      toast.error("复制失败，请手动选择");
    }
  }
  return (
    <span className={cn("task-copy", className)} title={value}>
      <span className="truncate">{value}</span>
      <button type="button" aria-label="复制" onClick={(e) => void copy(e)}>
        <Copy className="h-3 w-3" />
      </button>
    </span>
  );
}

// 详情弹窗顶部关键指标条：切换标签页时始终可见，概览不再重复这些信息
export function TaskDetailSummary({ task, now, onJump }: TaskDetailSummaryProps) {
  const tone = taskStatusTone(task.status);
  const duration = taskDurations(task, now);
  const billing = taskBilling(task);
  const upstream = taskUpstream(task);
  const waitingActivation = task.status === "pending" && task.next_action_at === null;
  const charged = billing.settled || (billing.chargedFen ?? 0) > 0;
  return (
    <div className={cn("task-summary", `is-${tone}`)}>
      <div className="task-summary-item">
        <span className="task-summary-label">状态</span>
        <div className="flex items-center gap-2">
          <span className={`admin-status-pill is-${tone}`}>{waitingActivation ? "待激活" : taskStatusLabel(task.status)}</span>
          <span className="text-xs tabular-nums text-[var(--admin-muted)]">{task.progress_percent}%</span>
        </div>
        <div className="task-progress"><span style={{ width: `${task.progress_percent}%` }} /></div>
        <span className="task-summary-sub font-mono">{task.current_step_key || "步骤未记录"}{task.current_step_status ? ` · ${task.current_step_status}` : ""}</span>
        {task.execution_mode === "direct" ? <span className="task-summary-sub">?? LLM ? ????</span> : task.queue_type ? <span className="task-summary-sub">{{ text: "文字", image: "图片", video: "视频" }[task.queue_type]}队列{task.provider_status ? ` · 上游 ${task.provider_status}` : ""}</span> : null}
      </div>

      <div className="task-summary-item">
        <span className="task-summary-label">{upstream.latest?.phase === "simulated" ? "模拟模型" : upstream.modelFromRequest ? "实际模型" : "任务选择模型"}</span>
        <span className="task-summary-value break-all">{upstream.model || "未记录"}</span>
        <span className="task-summary-sub">Provider · {upstream.provider || "未记录"}</span>
      </div>

      <div className="task-summary-item">
        <span className="task-summary-label">上游<button type="button" className="task-summary-jump" onClick={() => onJump("upstream")}>查看请求</button></span>
        <Badge variant={upstream.latest?.phase === "failed" ? "destructive" : upstream.records.length ? "info" : "secondary"}>{upstream.phase}</Badge>
        {upstream.providerTaskId ? <CopyText value={upstream.providerTaskId} className="task-summary-sub" /> : <span className="task-summary-sub">上游任务 ID 未记录</span>}
      </div>

      <div className="task-summary-item">
        <span className="task-summary-label">耗时{duration.live ? <span className="task-live-dot" title="计时中" /> : null}</span>
        <span className="task-summary-value tabular-nums">{formatTaskDuration(duration.totalMs)}</span>
        <span className="task-summary-sub tabular-nums">
          等待 {formatTaskDuration(duration.waitingMs)} · 运行 {duration.runningMs == null && !task.started_at && duration.live ? "未开始" : formatTaskDuration(duration.runningMs)}
        </span>
      </div>

      <div className="task-summary-item">
        <span className="task-summary-label">计费 · {billing.settled ? "已结算" : "未结算"}<button type="button" className="task-summary-jump" onClick={() => onJump("billing")}>明细</button></span>
        <span className="task-summary-value tabular-nums">{charged ? money(billing.chargedFen) : (billing.frozenFen ?? 0) > 0 ? `冻结 ${money(billing.frozenFen)}` : "未扣费"}</span>
        <span className="task-summary-sub tabular-nums">
          预估 {money(billing.estimateFen)}{charged ? ` · 退还 ${money(billing.refundedFen)}` : ""}
        </span>
      </div>
    </div>
  );
}
