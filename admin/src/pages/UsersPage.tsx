import { useEffect, useId, useRef, useState } from "react";
import { ArrowRight, Loader2, Wallet } from "lucide-react";
import { toast } from "sonner";
import { api, type AdminStats, type AdminUserRow, type PageMeta } from "@/api/client";
import { AdminField } from "@/components/admin/AdminField";
import { AdminFilterBar } from "@/components/admin/AdminFilterBar";
import { AdminListStats } from "@/components/admin/AdminListStats";
import { AdminModal } from "@/components/admin/AdminModal";
import { AdminSearchInput } from "@/components/admin/AdminSearchInput";
import { UserDetailDrawer } from "@/components/admin/UserDetailDrawer";
import { PaginationBar } from "@/components/PaginationBar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EmptyState, PageHeader } from "@/components/ui/page";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAdminDetailQuery } from "@/hooks/useAdminDetailQuery";
import { formatAccountId } from "@/lib/admin-account";
import { DEFAULT_PAGE_SIZE } from "@/lib/pagination";
import { fenToYuan } from "@/lib/utils";
import { yuanInputToFen } from "@/lib/moneyInput";
import { Switch } from "@/components/ui/switch";

type ListRes = { items: AdminUserRow[]; meta: PageMeta };

// 用户管理：搜索、筛选、只读明细与编辑
export function UsersPage() {
  const [q, setQ] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [data, setData] = useState<ListRes | null>(null);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<AdminUserRow | null>(null);
  const [detailUser, setDetailUser] = useState<AdminUserRow | null>(null);
  const [form, setForm] = useState({
    role: "user",
    /** increase=增加 / decrease=减少 */
    balance_dir: "increase" as "increase" | "decrease",
    /** 调账金额（元），空表示不改余额 */
    balance_amount_yuan: "",
    balance_note: "",
  });
  const [saving, setSaving] = useState(false);
  const [adjustBalance, setAdjustBalance] = useState(false);
  const [amountTouched, setAmountTouched] = useState(false);
  const [submitAttempted, setSubmitAttempted] = useState(false);
  const [saveError, setSaveError] = useState("");
  const pendingSave = useRef(false);
  const editFormId = useId();
  const adjustmentToggleId = useId();
  const userDetail = useAdminDetailQuery("user");

  const amountFen = yuanInputToFen(form.balance_amount_yuan);
  const balanceAfterFen = editing && adjustBalance && amountFen !== null
    ? editing.balance_fen + (form.balance_dir === "decrease" ? -amountFen : amountFen)
    : editing?.balance_fen ?? 0;
  const amountError = !adjustBalance ? "" : !form.balance_amount_yuan.trim()
    ? "请输入需要调整的金额"
    : amountFen === null ? "请输入有效金额，最多保留两位小数"
    : amountFen === 0 ? "调账金额须大于 0 元"
    : !Number.isSafeInteger(balanceAfterFen) ? "调整后金额过大，请减少调账金额"
    : balanceAfterFen < 0 ? "减少金额不能超过当前余额"
    : "";
  const visibleAmountError = amountTouched || submitAttempted ? amountError : "";
  const roleChanged = editing !== null && form.role !== editing.role;
  const hasChanges = roleChanged || adjustBalance;

  async function load(nextPage = page, nextQ = q, nextSize = pageSize) {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(nextPage),
        page_size: String(nextSize),
      });
      if (nextQ.trim()) params.set("q", nextQ.trim());
      if (roleFilter) params.set("role", roleFilter);
      const res = await api<ListRes>(`/api/admin/users?${params}`);
      setData(res);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize]);

  useEffect(() => {
    void api<AdminStats>("/api/admin/stats?days=7")
      .then(setStats)
      .catch((err) => {
        setStats(null);
        toast.error(err instanceof Error ? err.message : "统计加载失败");
      });
  }, []);

  useEffect(() => {
    if (!userDetail.id || !data?.items) return;
    const hit = data.items.find((u) => u.id === userDetail.id);
    if (hit) setDetailUser(hit);
  }, [userDetail.id, data?.items]);

  function openEdit(user: AdminUserRow) {
    if (pendingSave.current) return;
    setEditing(user);
    setAdjustBalance(false);
    setAmountTouched(false);
    setSubmitAttempted(false);
    setSaveError("");
    setForm({
      role: user.role || "user",
      balance_dir: "increase",
      balance_amount_yuan: "",
      balance_note: "",
    });
  }

  async function saveEdit() {
    if (!editing || pendingSave.current || !hasChanges) return;
    setSubmitAttempted(true);
    setSaveError("");
    if (amountError) return;
    pendingSave.current = true;
    setSaving(true);
    try {
      const payload: {
        role: string;
        balance_delta_fen?: number;
        balance_note?: string;
      } = { role: form.role };

      if (adjustBalance && amountFen !== null && amountFen > 0) {
        payload.balance_delta_fen = form.balance_dir === "decrease" ? -amountFen : amountFen;
        payload.balance_note = form.balance_note.trim() || undefined;
      }

      await api(`/api/admin/users/${editing.id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      toast.success(payload.balance_delta_fen ? "已调账并保存" : "已保存");
      setEditing(null);
      await load();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "保存失败，请重试");
    } finally {
      pendingSave.current = false;
      setSaving(false);
    }
  }

  function applyFilters() {
    setPage(1);
    void load(1, q, pageSize);
  }

  return (
    <div className="admin-list-page">
      <PageHeader title="用户管理" description="搜索用户，调整角色与余额" />

      <AdminFilterBar>
        <AdminSearchInput
          value={q}
          onChange={setQ}
          placeholder="搜索邮箱 / 昵称 / 账号 ID"
          onKeyDown={(e) => {
            if (e.key === "Enter") applyFilters();
          }}
        />
        <Select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
          <option value="">全部角色</option>
          <option value="user">普通用户</option>
          <option value="admin">管理员</option>
        </Select>
        <Button size="sm" className="admin-filter-action" onClick={applyFilters} disabled={loading}>
          {loading ? "加载中…" : "搜索"}
        </Button>
      </AdminFilterBar>

      <AdminListStats
        items={[
          { label: "总用户数", value: stats?.user_count ?? (loading ? "…" : "—") },
          {
            label: "本月调用",
            value: stats != null ? (stats.usage_calls_month ?? 0) : loading ? "…" : "—",
            hint: stats ? `今日 ${stats.usage_calls_today ?? 0} 次` : undefined,
          },
          {
            label: "累计调用",
            value: stats != null ? (stats.usage_calls_total ?? 0) : loading ? "…" : "—",
          },
        ]}
      />

      <div className="space-y-3">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>账号 ID</TableHead>
              <TableHead>邮箱</TableHead>
              <TableHead>昵称</TableHead>
              <TableHead>手机</TableHead>
              <TableHead>余额</TableHead>
              <TableHead>冻结</TableHead>
              <TableHead>角色</TableHead>
              <TableHead>注册时间</TableHead>
              <TableHead className="w-[140px]">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(data?.items ?? []).map((u) => (
              <TableRow key={u.id}>
                <TableCell className="font-mono text-xs text-[#909399]">{formatAccountId(u.id)}</TableCell>
                <TableCell className="font-medium">{u.email}</TableCell>
                <TableCell>{u.nickname || "—"}</TableCell>
                <TableCell className="text-xs">{u.phone || "—"}</TableCell>
                <TableCell className="tabular-nums">¥{fenToYuan(u.balance_fen)}</TableCell>
                <TableCell className="tabular-nums">¥{fenToYuan(u.frozen_fen)}</TableCell>
                <TableCell>
                  <Badge variant={u.role === "admin" ? "success" : "secondary"}>{u.role === "admin" ? "管理员" : "普通用户"}</Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {u.created_at ? new Date(u.created_at).toLocaleString() : "—"}
                </TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setDetailUser(u);
                        userDetail.open(u.id);
                      }}
                    >
                      查看
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => openEdit(u)}>
                      编辑
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
            {!loading && (data?.items.length ?? 0) === 0 && (
              <TableRow>
                <TableCell colSpan={9} className="p-0">
                  <EmptyState title="暂无用户" description="试试换个关键词搜索" />
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>

        {data && (
          <PaginationBar
            page={data.meta.page}
            pageSize={pageSize}
            total={data.meta.total}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(1);
            }}
          />
        )}
      </div>

      <UserDetailDrawer
        userId={userDetail.id}
        open={userDetail.isOpen}
        onOpenChange={(open) => {
          if (!open) userDetail.close();
        }}
        initialUser={detailUser}
      />

      <AdminModal
        open={!!editing}
        onOpenChange={(open) => { if (!open && !pendingSave.current) setEditing(null); }}
        size="lg"
        title="编辑用户"
        subtitle={editing?.email}
        footer={
          <>
            <Button type="button" variant="outline" disabled={saving} onClick={() => setEditing(null)}>取消</Button>
            <Button type="submit" form={editFormId} disabled={saving || !hasChanges} aria-busy={saving}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
              {saving ? "正在保存…" : adjustBalance ? "确认调账并保存" : "保存权限修改"}
            </Button>
          </>
        }
      >
        <form id={editFormId} noValidate onSubmit={(event) => { event.preventDefault(); void saveEdit(); }} className="space-y-6">
          <div className="admin-form-summary grid gap-4 rounded-xl border border-[var(--admin-border)] bg-[#f7f9f4] p-4 sm:grid-cols-2">
            <div className="admin-form-summary-item"><div className="text-xs text-[var(--admin-muted)]">当前账户余额</div><div className="mt-1.5 text-2xl font-semibold tabular-nums text-[var(--admin-text)]">¥{fenToYuan(editing?.balance_fen ?? 0)}</div></div>
            <div className="admin-form-summary-item"><div className="text-xs text-[var(--admin-muted)]">已冻结金额</div><div className="mt-1.5 text-lg font-medium tabular-nums text-[var(--admin-text)]">¥{fenToYuan(editing?.frozen_fen ?? 0)}</div><p className="mt-1 text-xs leading-relaxed text-[var(--admin-muted)]">调账不会修改任务已冻结的金额</p></div>
          </div>
          <fieldset disabled={saving} className="admin-form-section min-w-0 space-y-4">
            <legend className="admin-form-section-title mb-1 text-sm font-semibold text-[var(--admin-text)]">账号权限</legend>
            <AdminField label="账号角色" required hint="管理员可进入管理端，查看和管理全部业务数据。">
              <Select value={form.role} onChange={(e) => { setSaveError(""); setForm((f) => ({ ...f, role: e.target.value })); }} disabled={saving}>
                <option value="user">普通用户</option><option value="admin">管理员</option>
              </Select>
            </AdminField>
          </fieldset>
          <section className="admin-form-section space-y-4 border-t border-[var(--admin-border)] pt-5">
            <div className="flex items-start justify-between gap-4">
              <div><label htmlFor={adjustmentToggleId} className="text-sm font-semibold text-[var(--admin-text)]">调整账户余额</label><p className="mt-1 text-xs leading-relaxed text-[var(--admin-muted)]">按需开启；仅修改角色时，账户余额保持不变。</p></div>
              <Switch id={adjustmentToggleId} checked={adjustBalance} disabled={saving} onCheckedChange={(checked) => { setAdjustBalance(checked); setSaveError(""); }} />
            </div>
            {adjustBalance ? <fieldset disabled={saving} className="min-w-0 space-y-4">
              <div className="admin-form-grid admin-form-grid--2">
                <AdminField label="调整方向" required>
                  <Select value={form.balance_dir} disabled={saving} onChange={(e) => { setSaveError(""); setForm((f) => ({ ...f, balance_dir: e.target.value === "decrease" ? "decrease" : "increase" })); }}>
                    <option value="increase">增加余额</option><option value="decrease">减少余额</option>
                  </Select>
                </AdminField>
                <AdminField label="调整金额（元）" required hint="大于 0，最多两位小数。" error={visibleAmountError}>
                  <Input inputMode="decimal" value={form.balance_amount_yuan} disabled={saving} placeholder="例如 100.00" onBlur={() => setAmountTouched(true)} onChange={(e) => { setSaveError(""); setForm((f) => ({ ...f, balance_amount_yuan: e.target.value })); }} />
                </AdminField>
              </div>
              <AdminField label="调账备注" optional hint="备注会写入账户流水，便于后续核对。">
                <Input value={form.balance_note} disabled={saving} onChange={(e) => setForm((f) => ({ ...f, balance_note: e.target.value }))} placeholder="例如：活动奖励补发、退款扣回" />
              </AdminField>
              {!amountError && amountFen !== null ? <div className="admin-form-preview rounded-xl border border-[#d5e6d1] bg-[var(--admin-accent-soft)] p-4" aria-live="polite">
                <div className="mb-2 flex items-center gap-2 text-xs font-medium text-[var(--admin-forest)]"><Wallet className="h-4 w-4" aria-hidden="true" />保存后的账户余额</div>
                <div className="flex flex-wrap items-center gap-3 text-lg font-semibold tabular-nums text-[var(--admin-text)]"><span className="text-[var(--admin-muted)]">¥{fenToYuan(editing?.balance_fen ?? 0)}</span><ArrowRight className="h-4 w-4 text-[var(--admin-muted)]" aria-hidden="true" /><span>¥{fenToYuan(balanceAfterFen)}</span><span className="text-xs font-medium text-[var(--admin-forest)]">{form.balance_dir === "decrease" ? "减少" : "增加"} ¥{fenToYuan(amountFen)}</span></div>
              </div> : null}
            </fieldset> : null}
          </section>
          {saveError ? <p role="alert" className="admin-form-error rounded-lg border border-[#f0d9d5] bg-[#fcf0ee] px-3 py-2.5 text-sm leading-relaxed text-[#aa3d37]">{saveError}</p> : null}
        </form>
      </AdminModal>
    </div>
  );
}
