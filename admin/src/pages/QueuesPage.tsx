import { Select } from "@/components/ui/select";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Ban, Layers, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api, type AdminTaskListRes, type AdminTaskRow, type AdminTaskStats } from "@/api/client";
import { AdminSearchInput } from "@/components/admin/AdminSearchInput";
import { AdminUserSearchSelect } from "@/components/admin/AdminUserSearchSelect";
import { StatCard } from "@/components/admin/StatCard";
import { PageSection } from "@/components/admin/PageSection";
import { TaskDetailDialog } from "@/components/tasks/TaskDetailDialog";
import { TaskListRow } from "@/components/tasks/TaskListRow";
import { PageHeader, Toolbar } from "@/components/ui/page";
import { PaginationBar } from "@/components/PaginationBar";
import { useAdminDetailQuery } from "@/hooks/useAdminDetailQuery";
import { useTaskClock } from "@/hooks/useTaskClock";
import { canCancelTask, taskBilling, taskUpstream } from "@/lib/taskObservability";
import { cn } from "@/lib/utils";
import { DEFAULT_PAGE_SIZE } from "@/lib/pagination";
import { taskDomainLabel, taskTypeLabel } from "@/lib/statusLabels";

const REFRESH_MS = 15000;
const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

function formatElapsed(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "暂无记录";
  const value = Math.max(0, Math.floor(seconds));
  if (value < 60) return `${value}秒`;
  if (value < 3600) return `${Math.floor(value / 60)}分${value % 60}秒`;
  return `${Math.floor(value / 3600)}小时${Math.floor((value % 3600) / 60)}分`;
}

// 统一任务平台监控页
export function QueuesPage() {
  /*
   * stats 平台聚合统计
   * data 分页任务列表
   * loading 首次加载
   * refreshing 刷新中
   * page / pageSize 分页
   * q 搜索关键词
   * domain / status 筛选
   * viewTab 快捷视图
   * cancelLoading 取消中的任务 id
   */
  const [stats, setStats] = useState<AdminTaskStats | null>(null);
  const [data, setData] = useState<AdminTaskListRes | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [q, setQ] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [domain, setDomain] = useState("");
  const [status, setStatus] = useState("");
  const [filterUserId, setFilterUserId] = useState<number | null>(null);
  const [taskType, setTaskType] = useState("");
  const [viewTab, setViewTab] = useState<"all" | "active">("all");
  const [cancelLoading, setCancelLoading] = useState<number | null>(null);
  const apiOnlyNode = stats?.scheduler === "disabled";
  const taskDetail = useAdminDetailQuery("task");
  const taskViews = useMemo(() => (data?.items || []).map((task) => ({ task, upstream: taskUpstream(task), billing: taskBilling(task) })), [data]);
  const now = useTaskClock(data?.items.some((task) => !TERMINAL_STATUSES.has(task.status)) ?? false);

  const loadStats = useCallback(async () => {
    const res = await api<AdminTaskStats>("/api/admin/tasks/stats");
    setStats(res);
  }, []);

  const loadTasks = useCallback(
    async (silent = false) => {
      if (!silent) setRefreshing(true);
      try {
        const params = new URLSearchParams({
          page: String(page),
          page_size: String(pageSize),
        });
        if (domain) params.set("domain", domain);
        if (status) params.set("status", status);
        if (filterUserId) params.set("user_id", String(filterUserId));
        if (taskType.trim()) params.set("task_type", taskType.trim());
        if (q.trim()) params.set("q", q.trim());
        if (viewTab === "active" && !status) params.set("active_only", "true");
        const res = await api<AdminTaskListRes>(`/api/admin/tasks?${params}`);
        setData(res);
      } catch (err) {
        if (!silent) {
          toast.error(err instanceof Error ? err.message : "加载任务失败");
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [domain, filterUserId, page, pageSize, q, status, taskType, viewTab],
  );

  const refreshAll = useCallback(
    async (silent = false) => {
      if (!silent) setRefreshing(true);
      try {
        await Promise.all([loadStats(), loadTasks(true)]);
      } catch (err) {
        if (!silent) toast.error(err instanceof Error ? err.message : "刷新失败");
      } finally {
        setRefreshing(false);
        setLoading(false);
      }
    },
    [loadStats, loadTasks],
  );

  useEffect(() => {
    void refreshAll();
  }, [page, pageSize, domain, status, q, viewTab, filterUserId, taskType, refreshAll]);

  useEffect(() => {
    const timer = window.setInterval(() => void refreshAll(true), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [refreshAll]);

  function openDetail(taskId: number) {
    taskDetail.open(taskId);
  }

  const handleCancel = useCallback(
    async (task: AdminTaskRow) => {
      if (!canCancelTask(task)) return;
      if (!window.confirm(`确定取消任务 #${task.id}（${taskTypeLabel(task.task_type)}）？`)) return;
      setCancelLoading(task.id);
      try {
        await api(`/api/admin/tasks/${task.id}/cancel`, { method: "POST" });
        toast.success("已提交取消请求");
        await refreshAll(true);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "取消失败");
      } finally {
        setCancelLoading(null);
      }
    },
    [refreshAll],
  );

  const domainOptions = useMemo(() => {
    const fromStats = stats?.domains.map((d) => d.domain) ?? [];
    return Array.from(new Set(fromStats));
  }, [stats]);

  return (
    <div className="admin-page">
      <PageHeader title="任务中心"
        description={`统一任务平台 · 自动刷新 ${REFRESH_MS / 1000}s · ${apiOnlyNode ? "本节点仅 API / 队列由执行节点处理" : `本节点并发 ${stats?.scheduler_running_jobs ?? 0}/${stats?.max_concurrency ?? 0}`}`}
        actions={
          <button
            type="button"
            className="admin-btn admin-btn-secondary"
            onClick={() => void refreshAll()}
            disabled={refreshing}
          >
            {refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            刷新
          </button>
        }
      />

      {loading && !stats ? (
        <div className="admin-panel flex items-center justify-center py-16 text-[var(--admin-muted)]">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          加载中…
        </div>
      ) : (
        <>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="待处理"
              value={stats?.pending_count ?? 0}
              hint={`待调度 ${stats?.pending_ready_count ?? "—"} · 待激活 ${stats?.pending_deferred_count ?? "—"} · 延时 ${stats?.pending_delayed_count ?? "—"}`}
              icon={Layers}
              tone="warn"
            />
            <StatCard
              label="进行中"
              value={stats?.active_count ?? 0}
              hint={`执行 ${stats?.running_count ?? 0} · 轮询 ${stats?.awaiting_poll_count ?? 0} · 取消中 ${stats?.cancel_requested_count ?? 0}`}
              icon={Activity}
              tone="info"
            />
            <StatCard
              label="本节点执行槽位"
              value={
                apiOnlyNode ? <span className="text-lg">仅 API</span> : <>
                  {stats?.scheduler_running_jobs ?? 0}
                  <span className="text-lg text-[var(--admin-muted)]"> / {stats?.max_concurrency ?? 0}</span>
                </>
              }
              hint={apiOnlyNode ? "队列由执行节点处理" : `全站已租约 ${stats?.leased_count ?? 0}`}
              icon={RefreshCw}
            />
            <StatCard
              label="终态统计"
              icon={Ban}
              tone="success"
              value={
                <span className="admin-terminal-stats">
                  <span><small>成功</small><strong>{stats?.succeeded_count ?? 0}</strong></span>
                  <span><small>失败</small><strong>{stats?.failed_count ?? 0}</strong></span>
                  <span><small>取消</small><strong>{stats?.cancelled_count ?? 0}</strong></span>
                </span>
              }
              hint={`更新 ${stats?.fetched_at ? new Date(stats.fetched_at).toLocaleTimeString() : "—"}`}
            />
          </div>

          <div className="admin-panel mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3 text-xs text-[var(--admin-muted)]">
            <span className={cn("admin-status-pill", stats?.runtime_healthy === undefined ? "" : stats.runtime_healthy ? "is-done" : "is-fail")}>
              {apiOnlyNode ? "本节点仅 API" : `运行时${stats?.runtime_healthy === undefined ? "状态未知" : stats.runtime_healthy ? "正常" : "异常"}`}
            </span>
            {apiOnlyNode ? <span>本节点仅 API / 队列由执行节点处理</span> : <>
              <span>调度器 {stats?.scheduler ?? "—"} · 轮询器 {stats?.poller ?? "—"} · 看门狗 {stats?.watchdog ?? "—"}</span>
              <span>最近成功调度：{stats?.scheduler_success_age_sec == null ? "暂无记录" : `${formatElapsed(stats.scheduler_success_age_sec)}前`}</span>
              <span>连续失败：{stats?.scheduler_consecutive_failures ?? "—"}</span>
            </>}
            <span>最老到期待调度已等待：{formatElapsed(stats?.oldest_ready_wait_sec)}</span>
            <span>待激活表示前置条件或并发空位尚未满足；延时表示尚未到调度时间。</span>
          </div>

          {stats?.queues?.length ? <PageSection title="调度队列" bodyClassName="!pt-0">
            <div className="grid gap-3 md:grid-cols-3">{stats.queues.map((queue) => <div key={queue.name} className="admin-panel px-4 py-3">
              <div className="text-sm font-medium">{queue.label}队列 <span className="font-mono text-xs text-[var(--admin-muted)]">{queue.name}</span></div>
              <div className="mt-2 text-xs text-[var(--admin-muted)]">排队 {queue.pending} · 在途 {queue.active} · 并发 {queue.max_concurrency}</div>
              {queue.awaiting_poll ? <div className="mt-1 text-xs text-[var(--admin-muted)]">等待上游 {queue.awaiting_poll}</div> : null}
            </div>)}</div>
          </PageSection> : null}
          {(stats?.domains.length ?? 0) > 0 ? (
            <PageSection title="各领域任务" bodyClassName="!pt-0">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                {stats?.domains.map((d) => (
                  <div key={d.domain} className="admin-domain-card">
                    <div className="flex items-center justify-between gap-2">
                      <div>
                        <div className="text-sm font-medium">{taskDomainLabel(d.domain)}</div>
                        <div className="mt-0.5 font-mono text-xs text-[var(--admin-muted)]">{d.domain}</div>
                      </div>
                      <span
                        className={cn(
                          "admin-status-pill",
                          d.pending + d.active > 0 ? "is-warn" : "is-done",
                        )}
                      >
                        {d.pending + d.active}
                      </span>
                    </div>
                    <div className="mt-2 text-xs text-[var(--admin-muted)]">
                      待处理 {d.pending} · 进行 {d.active} · 成功 {d.succeeded}
                    </div>
                  </div>
                ))}
              </div>
            </PageSection>
          ) : null}

          <PageSection
            title="任务列表"
            actions={
              <div className="flex flex-wrap gap-2">
                {(
                  [
                    ["active", `未结束 (${stats?.pending_count ?? 0}+${stats?.active_count ?? 0})`],
                    ["all", "全部"],
                  ] as const
                ).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    className={cn("admin-tab !min-h-[30px] !px-3 !text-xs", viewTab === key && "is-active")}
                    onClick={() => {
                      setViewTab(key);
                      setPage(1);
                      if (key === "active") setStatus("");
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            }
            bodyClassName="space-y-4 !pt-0"
          >
            <Toolbar>
              <Select
                className="admin-select"
                value={domain}
                onChange={(e) => {
                  setDomain(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">全部领域</option>
                {domainOptions.map((d) => (
                  <option key={d} value={d}>
                    {taskDomainLabel(d)}
                  </option>
                ))}
              </Select>
              <Select
                className="admin-select"
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value);
                  setPage(1);
                  if (e.target.value) setViewTab("all");
                }}
              >
                <option value="">全部状态</option>
                {Object.entries({
                  pending: "待处理",
                  leased: "已租约",
                  running: "执行中",
                  awaiting_poll: "等待轮询",
                  cancel_requested: "取消中",
                  succeeded: "已成功",
                  failed: "失败",
                  cancelled: "已取消",
                }).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
              <AdminUserSearchSelect
                value={filterUserId}
                onChange={(id) => {
                  setFilterUserId(id);
                  setPage(1);
                }}
              />
              <input
                className="admin-input"
                placeholder="任务类型"
                value={taskType}
                onChange={(e) => {
                  setTaskType(e.target.value);
                  setPage(1);
                }}
              />
              <AdminSearchInput
                value={searchInput}
                onChange={setSearchInput}
                placeholder="任务类型 / 用户邮箱 / dedupe_key"
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    setQ(searchInput);
                    setPage(1);
                  }
                }}
              />
              <button
                type="button"
                className="admin-btn admin-btn-primary admin-filter-action"
                onClick={() => {
                  setQ(searchInput);
                  setPage(1);
                }}
              >
                查询
              </button>
            </Toolbar>

            <div className="admin-table-wrap">
              <table className="task-table">
                <thead>
                  <tr>
                    <th>任务 / 模型</th>
                    <th>状态</th>
                    <th>上游</th>
                    <th>计费</th>
                    <th>耗时</th>
                    <th>用户 / 关联</th>
                    <th className="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.items.length ?? 0) === 0 ? (
                    <tr>
                      <td colSpan={7}>
                        <div className="admin-empty !py-10">
                          <div className="admin-empty-title">暂无任务</div>
                          <div className="admin-empty-desc">切换到「全部」查看历史任务</div>
                        </div>
                      </td>
                    </tr>
                  ) : (
                    taskViews.map(({ task, upstream, billing }) => (
                      <TaskListRow
                        key={task.id}
                        task={task}
                        upstream={upstream}
                        billing={billing}
                        now={now}
                        cancelling={cancelLoading === task.id}
                        onOpen={openDetail}
                        onCancel={(row) => void handleCancel(row)}
                      />
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {data?.meta ? (
              <PaginationBar
                page={page}
                pageSize={pageSize}
                total={data.meta.total}
                onPageChange={setPage}
                onPageSizeChange={(size) => {
                  setPageSize(size);
                  setPage(1);
                }}
              />
            ) : null}
          </PageSection>
        </>
      )}

      <TaskDetailDialog
        taskId={taskDetail.id}
        open={taskDetail.isOpen}
        onOpenChange={(open) => {
          if (!open) taskDetail.close();
        }}
        onCancelled={() => void refreshAll(true)}
      />
    </div>
  );
}
