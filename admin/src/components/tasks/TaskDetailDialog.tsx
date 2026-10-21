import {
  Children,
  isValidElement,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Ban, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api, type AdminTaskDetail } from "@/api/client";
import { AdminEntityLink } from "@/components/admin/AdminEntityLink";
import { AdminModal } from "@/components/admin/AdminModal";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TaskUpstreamPanel } from "@/components/tasks/TaskUpstreamPanel";
import { TaskEventsPanel } from "@/components/tasks/TaskEventsPanel";
import { useTaskClock } from "@/hooks/useTaskClock";
import { taskDomainLabel, taskStatusLabel, taskTypeLabel } from "@/lib/statusLabels";
import { hasJsonContent, prettyJson } from "@/lib/jsonPreview";
import { cn, fenToYuan } from "@/lib/utils";
import { formatTaskDuration, redactTaskData, redactTaskText, taskBilling, taskDurations, taskUpstream } from "@/lib/taskObservability";

type TaskDetailDialogProps = {
  taskId: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCancelled?: () => void;
};

type DetailTab = "overview" | "upstream" | "billing" | "steps" | "events" | "json";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

const PAYLOAD_FIELD_LABELS: Record<string, string> = {
  prompt: "提示词",
  name: "名称",
  kind: "类型",
  model_id: "模型",
  image_style_id: "风格",
  aspect_ratio: "画幅",
  resolution: "分辨率",
  duration_sec: "时长(秒)",
  duration: "时长",
  force: "强制重跑",
  total: "总数",
  project_id: "科普项目 ID",
  user_id: "用户 ID",
  asset_id: "资产 ID",
  episode_count: "集数",
  phase: "阶段",
  sync: "同步",
  refresh_prompts: "刷新提示词",
  reextract_props: "重抽道具",
};

// 格式化时间为本地字符串
function fmtTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

// 美化 JSON 展示
function fmtJson(value: unknown): string {
  return prettyJson(redactTaskData(value));
}

function money(value: number | null | undefined): string {
  return value == null ? "未记录" : `¥${fenToYuan(value)}`;
}

// 状态 pill 样式
function statusClass(status: string): string {
  if (status === "running" || status === "leased") return "is-run";
  if (status === "succeeded") return "is-done";
  if (status === "failed") return "is-fail";
  if (status === "cancelled" || status === "cancel_requested") return "is-warn";
  return "is-warn";
}

// 仅在有值时渲染一行定义列表项
function DlRow({ label, children }: { label: string; children: ReactNode }) {
  if (children == null || children === "" || children === "—") return null;
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

// DlRow 未渲染时 children 仍是元素描述符；按 props 判断是否会出内容。
function dlRowWillShow(child: ReactNode): boolean {
  if (child == null || child === false) return false;
  if (!isValidElement(child)) return Boolean(child);
  if (child.type !== DlRow) return true;
  const c = (child.props as { children?: ReactNode }).children;
  return c != null && c !== "" && c !== "—";
}

// 有内容才包一层 section，避免「标识」等空壳标题
function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  const visible = Children.toArray(children).filter(dlRowWillShow);
  if (visible.length === 0) return null;
  return (
    <section className="task-detail-section">
      <h4>{title}</h4>
      <dl className="task-detail-dl">{visible}</dl>
    </section>
  );
}

// 从 payload 抽出可读字段摘要（其余仍看下方 JSON）
function payloadSummaryRows(
  payload: Record<string, unknown> | null | undefined,
): Array<{ key: string; label: string; value: string }> {
  if (!payload || typeof payload !== "object") return [];
  const rows: Array<{ key: string; label: string; value: string }> = [];
  for (const [key, label] of Object.entries(PAYLOAD_FIELD_LABELS)) {
    if (!(key in payload)) continue;
    const raw = payload[key];
    if (raw == null || raw === "") continue;
    if (typeof raw === "boolean") {
      rows.push({ key, label, value: raw ? "是" : "否" });
      continue;
    }
    const text = String(raw).trim();
    if (!text) continue;
    rows.push({ key, label, value: text.length > 240 ? `${text.slice(0, 240)}…` : text });
  }
  return rows;
}

// 任务详情弹窗：概览 / 步骤 / 事件 / 原始 JSON
export function TaskDetailDialog({ taskId, open, onOpenChange, onCancelled }: TaskDetailDialogProps) {
  const [task, setTask] = useState<AdminTaskDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<DetailTab>("overview");
  const [cancelling, setCancelling] = useState(false);
  const now = useTaskClock(open && Boolean(task) && !TERMINAL.has(task?.status || ""));
  const duration = task ? taskDurations(task, now) : null;
  const billing = taskBilling(task || {});
  const upstream = useMemo(() => taskUpstream(task || {}), [task]);

  async function loadDetail(id: number) {
    setLoading(true);
    try {
      setTask(await api<AdminTaskDetail>(`/api/admin/tasks/${id}`));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载任务详情失败");
      onOpenChange(false);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!open || !taskId) {
      setTask(null);
      setTab("overview");
      return;
    }
    void loadDetail(taskId);
  }, [open, taskId]);

  useEffect(() => {
    if (!open || !taskId || !task || (TERMINAL.has(task.status) && task.billing_status !== "frozen")) return;
    let cancelled = false;
    let inFlight = false;
    const timer = window.setInterval(async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const current = await api<AdminTaskDetail>(`/api/admin/tasks/${taskId}`);
        if (!cancelled) setTask(current);
      } catch {
        // A transient refresh failure keeps the last verified snapshot visible.
      } finally {
        inFlight = false;
      }
    }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [open, taskId, task?.status, task?.billing_status]);

  const canCancel = useMemo(() => {
    if (!task) return false;
    return task.cancelable && !TERMINAL.has(task.status);
  }, [task]);

  const waitingActivation = useMemo(() => {
    if (!task) return false;
    return task.status === "pending" && task.next_action_at === null;
  }, [task]);

  const payloadRows = useMemo(() => payloadSummaryRows(redactTaskData(task?.payload) as Record<string, unknown> | null), [task?.payload]);

  async function handleCancel() {
    if (!task || !canCancel) return;
    if (!window.confirm(`确定取消任务 #${task.id}？`)) return;
    setCancelling(true);
    try {
      const updated = await api<AdminTaskDetail>(`/api/admin/tasks/${task.id}/cancel`, { method: "POST" });
      setTask(updated);
      toast.success("已提交取消请求");
      onCancelled?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "取消失败");
    } finally {
      setCancelling(false);
    }
  }

  const title = task ? `任务 #${task.id} · ${taskDomainLabel(task.domain)}` : "任务详情";

  return (
    <AdminModal
      open={open}
      onOpenChange={onOpenChange}
      size="full"
      className="task-detail-modal max-h-[90vh]"
      bodyClassName="p-0 overflow-hidden"
      title={title}
      subtitle={
        task ? (
          <span className="font-mono text-xs">
            {taskTypeLabel(task.task_type)}
            {task.user_email ? ` · ${task.user_email}` : ""}
          </span>
        ) : undefined
      }
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
          {taskId ? (
            <Button variant="outline" disabled={loading} onClick={() => void loadDetail(taskId)}>
              <RefreshCw className={cn("mr-2 h-4 w-4", loading && "animate-spin")} />
              刷新
            </Button>
          ) : null}
          {canCancel ? (
            <Button variant="destructive" disabled={cancelling} onClick={() => void handleCancel()}>
              {cancelling ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Ban className="mr-2 h-4 w-4" />}
              取消任务
            </Button>
          ) : null}
        </>
      }
    >
      {loading && !task ? (
        <div className="task-detail-loading">
          <Loader2 className="h-6 w-6 animate-spin text-[#67c23a]" />
          <span>加载任务详情…</span>
        </div>
      ) : task ? (
        <Tabs className="task-detail-body" value={tab} onValueChange={(value) => setTab(value as DetailTab)}>
          <TabsList className="task-detail-tabs !h-auto !justify-start !rounded-none !bg-white !p-0">
            {(
              [
                ["overview", "概览"],
                ["upstream", "上游请求"],
                ["billing", `计费 (${task.usage_lines?.length ?? 0})`],
                ["steps", `步骤 (${task.steps?.length ?? 0})`],
                ["events", `事件 (${task.events_total ?? task.events?.length ?? 0})`],
                ["json", "任务 JSON（脱敏）"],
              ] as const
            ).map(([key, label]) => (
              <TabsTrigger
                key={key}
                value={key}
                className={cn("task-detail-tab", tab === key && "is-active")}
              >
                {label}
              </TabsTrigger>
            ))}
          </TabsList>

          <TabsContent value={tab} className="task-detail-panel !mt-0">
            {tab === "overview" ? (
              <div className="task-detail-grid">
                <section className="task-detail-section">
                  <h4>状态</h4>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`admin-status-pill ${statusClass(task.status)}`}>
                      {waitingActivation ? "待激活" : taskStatusLabel(task.status)}
                    </span>
                    <span className="text-sm text-[#606266]">进度 {task.progress_percent}%</span>
                  </div>
                  {waitingActivation ? (
                    <p className="task-detail-meta">任务尚未激活，等待前置条件或可用并发空位。</p>
                  ) : null}
                  {task.current_step_key ? (
                    <p className="task-detail-meta">
                      当前步骤 {task.current_step_key}
                      {task.current_step_status ? ` · ${task.current_step_status}` : ""}
                    </p>
                  ) : null}
                  {task.error_code || task.error_message ? (
                    <pre className="task-detail-error">
                      {redactTaskText([task.error_code, task.error_message].filter(Boolean).join(" · "))}
                    </pre>
                  ) : null}
                </section>

                <section className="task-detail-section">
                  <h4>执行耗时 {duration?.live ? <span className="ml-2 text-xs font-normal text-[var(--admin-muted)]">实时计时</span> : null}</h4>
                  <dl className="task-detail-dl tabular-nums">
                    <div><dt>开始前等待</dt><dd>{formatTaskDuration(duration?.waitingMs)}</dd></div>
                    <div><dt>运行</dt><dd>{duration?.runningMs == null && !task.started_at && duration?.live ? "尚未开始" : formatTaskDuration(duration?.runningMs)}</dd></div>
                    <div><dt>总耗时</dt><dd className="font-semibold">{formatTaskDuration(duration?.totalMs)}</dd></div>
                  </dl>
                  <p className="task-detail-meta">运行包含准备、上游生成与结果写回；单次 HTTP 耗时见“上游请求”。</p>
                </section>

                <section className="task-detail-section">
                  <h4>最近上游记录</h4>
                  <Badge variant={upstream.latest?.phase === "failed" ? "destructive" : upstream.records.length ? "info" : "secondary"}>{upstream.phase}</Badge>
                  <dl className="task-detail-dl mt-3">
                    <div><dt>{upstream.modelFromRequest ? "实际模型" : "任务选择模型"}</dt><dd>{upstream.model || "未记录"}</dd></div>
                    <div><dt>Provider</dt><dd>{upstream.provider || "未记录"}</dd></div>
                    <div><dt>上游任务 ID</dt><dd className="break-all font-mono text-xs">{upstream.providerTaskId || "未记录"}</dd></div>
                  </dl>
                  <Button variant="outline" size="sm" className="mt-3" onClick={() => setTab("upstream")}>查看请求与响应</Button>
                </section>

                <section className="task-detail-section">
                  <h4>计费与结算</h4>
                  <Badge variant={billing.settled ? "success" : "warning"}>{billing.label}</Badge>
                  <dl className="task-detail-dl mt-3">
                    <div><dt>预估 / 当前冻结</dt><dd>{money(billing.estimateFen)} / {money(billing.frozenFen)}</dd></div>
                    <div><dt>实扣</dt><dd>{billing.settled || (billing.chargedFen ?? 0) > 0 ? money(billing.chargedFen) : "尚未结算"}</dd></div>
                    <div><dt>退还</dt><dd>{billing.settled || (billing.refundedFen ?? 0) > 0 ? money(billing.refundedFen) : "尚未结算"}</dd></div>
                  </dl>
                  <Button variant="outline" size="sm" className="mt-3" onClick={() => setTab("billing")}>查看用量与结算</Button>
                </section>

                <DetailSection title="调度">
                  <DlRow label="优先级">{task.priority}</DlRow>
                  <DlRow label="scheduled_at">{task.scheduled_at ? fmtTime(task.scheduled_at) : null}</DlRow>
                  <DlRow label="next_action_at">{task.next_action_at ? fmtTime(task.next_action_at) : null}</DlRow>
                  <DlRow label="租约到期">{task.lease_until ? fmtTime(task.lease_until) : null}</DlRow>
                  <DlRow label="provider_task_id">
                    {task.provider_task_id ? (
                      <span className="font-mono text-xs break-all">{redactTaskText(task.provider_task_id)}</span>
                    ) : null}
                  </DlRow>
                </DetailSection>

                <DetailSection title="关联实体">
                  <DlRow label="用户">
                    <AdminEntityLink kind="user" id={task.requested_by} label={task.user_email ?? undefined} />
                  </DlRow>
                  <DlRow label="漫剧项目">
                    {task.drama_project_id ? <AdminEntityLink kind="drama" id={task.drama_project_id} /> : null}
                  </DlRow>
                  <DlRow label="资产">
                    {task.asset_id ? <AdminEntityLink kind="drama_asset" id={task.asset_id} /> : null}
                  </DlRow>
                  <DlRow label="剧本">{task.script_id ? `#${task.script_id}` : null}</DlRow>
                  <DlRow label="分集">{task.episode_id ? `#${task.episode_id}` : null}</DlRow>
                  <DlRow label="分镜">{task.fragment_id ? `#${task.fragment_id}` : null}</DlRow>
                  <DlRow label="科普项目">
                    {task.project_id ? <AdminEntityLink kind="project" id={task.project_id} /> : null}
                  </DlRow>
                  <DlRow label="镜头">{task.shot_id ? `#${task.shot_id}` : null}</DlRow>
                </DetailSection>

                <DetailSection title="标识">
                  <DlRow label="dedupe_key">
                    {task.dedupe_key ? <span className="font-mono text-xs break-all">{task.dedupe_key}</span> : null}
                  </DlRow>
                  <DlRow label="batch_key">
                    {task.batch_key ? <span className="font-mono text-xs break-all">{task.batch_key}</span> : null}
                  </DlRow>
                  <DlRow label="client_request_id">
                    {task.client_request_id ? (
                      <span className="font-mono text-xs break-all">{task.client_request_id}</span>
                    ) : null}
                  </DlRow>
                </DetailSection>

                <section className="task-detail-section task-detail-section--full">
                  <h4>时间线</h4>
                  <dl className="task-detail-dl task-detail-dl--inline">
                    <div>
                      <dt>创建</dt>
                      <dd>{fmtTime(task.created_at)}</dd>
                    </div>
                    <div>
                      <dt>开始</dt>
                      <dd>{fmtTime(task.started_at)}</dd>
                    </div>
                    <div>
                      <dt>结束</dt>
                      <dd>{fmtTime(task.finished_at)}</dd>
                    </div>
                    <div>
                      <dt>更新</dt>
                      <dd>{fmtTime(task.updated_at)}</dd>
                    </div>
                  </dl>
                </section>

                <section className="task-detail-section task-detail-section--full">
                  <h4>任务参数（入队 payload）</h4>
                  <p className="mb-3 text-xs text-[var(--admin-muted)]">这是内部调度参数；实际发往上游的请求请查看“上游请求”。</p>
                  {payloadRows.length > 0 ? (
                    <dl className="task-detail-dl task-detail-dl--payload mb-3">
                      {payloadRows.map((row) => (
                        <div key={row.key}>
                          <dt>{row.label}</dt>
                          <dd className={row.key === "prompt" ? "whitespace-pre-wrap break-words" : "break-all"}>
                            {row.value}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  ) : null}
                  {hasJsonContent(task.payload) ? (
                    <pre className="task-detail-json">{fmtJson(task.payload)}</pre>
                  ) : (
                    <p className="task-detail-meta">未记录任务参数</p>
                  )}
                </section>

                <section className="task-detail-section task-detail-section--full">
                  <h4>结果</h4>
                  {hasJsonContent(task.result_payload) ? (
                    <pre className="task-detail-json">{fmtJson(task.result_payload)}</pre>
                  ) : (
                    <p className="task-detail-meta">暂无结果（未完成或未回写 result_payload）</p>
                  )}
                </section>
              </div>
            ) : null}

            {tab === "upstream" ? <TaskUpstreamPanel key={task.id} task={task} /> : null}

            {tab === "billing" ? (
              <div className="task-detail-grid">
                <section className="task-detail-section">
                  <h4>结算摘要</h4>
                  <dl className="task-detail-dl">
                    <div>
                      <dt>计费状态</dt>
                      <dd><Badge variant={billing.settled ? "success" : "warning"}>{billing.label}</Badge></dd>
                    </div>
                    <div>
                      <dt>预估</dt>
                      <dd>{money(billing.estimateFen)}</dd>
                    </div>
                    <div>
                      <dt>当前冻结</dt>
                      <dd>{money(billing.frozenFen)}</dd>
                    </div>
                    <div>
                      <dt>实扣</dt>
                      <dd>{billing.settled || (billing.chargedFen ?? 0) > 0 ? money(billing.chargedFen) : "尚未结算"}</dd>
                    </div>
                    <div>
                      <dt>退回</dt>
                      <dd>{billing.settled || (billing.refundedFen ?? 0) > 0 ? money(billing.refundedFen) : "尚未结算"}</dd>
                    </div>
                  </dl>
                </section>

                <section className="task-detail-section task-detail-section--full">
                  <h4>用量明细</h4>
                  <p className="mb-3 text-xs text-[var(--admin-muted)]">用量记录表示调用计费依据；钱包实际扣款与退还以任务结算摘要为准。</p>
                  <div className="admin-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>时间</th>
                          <th>能力</th>
                          <th>billing_key</th>
                          <th>模型</th>
                          <th>Tokens</th>
                          <th>扣费</th>
                          <th>上游成本</th>
                          <th>计费依据</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(task.usage_lines?.length ?? 0) === 0 ? (
                          <tr>
                            <td colSpan={8} className="text-center text-sm text-[#909399]">
                              暂无用量记录
                            </td>
                          </tr>
                        ) : (
                          task.usage_lines?.map((line) => (
                            <tr key={line.id}>
                              <td className="text-[11px] text-[#909399]">{fmtTime(line.created_at)}</td>
                              <td>{line.capability ?? "—"}</td>
                              <td className="font-mono text-xs">{line.billing_key}</td>
                              <td className="max-w-[120px] truncate text-xs">{line.model || "—"}</td>
                              <td>{line.total_tokens ?? 0}</td>
                              <td>¥{fenToYuan(line.charge_fen ?? 0)}</td>
                              <td>¥{fenToYuan(line.cost_fen ?? 0)}</td>
                              <td>{line.billing_basis_label ?? (line.estimated ? "估算" : "实测")}</td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </section>
              </div>
            ) : null}

            {tab === "steps" ? (
              <div className="admin-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>步骤</th>
                      <th>状态</th>
                      <th>尝试</th>
                      <th>Provider</th>
                      <th>错误</th>
                      <th>时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(task.steps?.length ?? 0) === 0 ? (
                      <tr>
                        <td colSpan={6} className="text-center text-sm text-[#909399]">
                          暂无步骤记录
                        </td>
                      </tr>
                    ) : (
                      task.steps.map((step) => (
                        <tr key={step.id}>
                          <td>
                            <div className="font-mono text-xs">{step.step_key}</div>
                            <div className="text-[11px] text-[#909399]">{step.step_type}</div>
                          </td>
                          <td>
                            <span className={`admin-status-pill ${statusClass(step.status)}`}>
                              {taskStatusLabel(step.status)}
                            </span>
                          </td>
                          <td className="text-xs">{step.attempt_count}</td>
                          <td className="max-w-[140px] truncate font-mono text-[11px]" title={redactTaskText(step.provider_task_id ?? "")}>
                            {redactTaskText(step.provider_name ?? "—")}
                            {step.provider_task_id ? ` · ${redactTaskText(step.provider_task_id)}` : ""}
                          </td>
                          <td className="max-w-[200px] truncate text-[11px] text-[#f56c6c]" title={redactTaskText(step.error_message ?? "")}>
                            {redactTaskText(step.error_message ?? "—")}
                          </td>
                          <td className="text-[11px] text-[#909399]">
                            <div>始 {fmtTime(step.started_at)}</div>
                            <div>终 {fmtTime(step.finished_at)}</div>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            ) : null}

            {tab === "events" ? <TaskEventsPanel key={task.id} task={task} /> : null}

            {tab === "json" ? (
              <div className="space-y-4">
                <div>
                  <h4 className="task-detail-json-title">payload</h4>
                  <pre className="task-detail-json">{fmtJson(task.payload)}</pre>
                </div>
                <div>
                  <h4 className="task-detail-json-title">result_payload</h4>
                  <pre className="task-detail-json">{fmtJson(task.result_payload)}</pre>
                </div>
                <div>
                  <h4 className="task-detail-json-title">targets</h4>
                  <pre className="task-detail-json">{fmtJson(task.targets)}</pre>
                </div>
              </div>
            ) : null}
          </TabsContent>
        </Tabs>
      ) : null}
    </AdminModal>
  );
}
