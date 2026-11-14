import { useId, useRef, useState } from "react";
import { Info, Loader2 } from "lucide-react";
import { setAdminTeamBudget } from "@/api/teams";
import { AdminModal } from "@/components/admin/AdminModal";
import { AdminField } from "@/components/admin/AdminField";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { yuanInputToFen } from "@/lib/moneyInput";
import { fenToYuan } from "@/lib/utils";

/** 设置团队或成员月上限，零元停用新消费，空值代表不限额。 */
export function TeamBudgetModal({ teamId, memberId, name, limit, onClose, onSaved }: {
  teamId: number; memberId?: number; name: string; limit: number | null;
  onClose: () => void; onSaved: () => void;
}) {
  const [unlimited, setUnlimited] = useState(limit === null);
  const [amount, setAmount] = useState(limit === null ? "" : fenToYuan(limit));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [amountTouched, setAmountTouched] = useState(false);
  const [submitAttempted, setSubmitAttempted] = useState(false);
  const pending = useRef(false);
  const formId = useId();
  const modeName = useId();
  const amountFen = yuanInputToFen(amount);
  const amountError = unlimited ? "" : !amount.trim()
    ? "请输入每月消费上限"
    : amountFen === null ? "请输入非负金额，最多保留两位小数"
    : amountFen > 100000000 ? "每月消费上限不能超过 1,000,000 元"
    : "";
  const nextLimit = unlimited ? null : amountFen;
  const changed = unlimited ? limit !== null : amountError !== "" || nextLimit !== limit;
  const visibleAmountError = amountTouched || submitAttempted ? amountError : "";

  /** 保存金额只接受两位小数，精确转换为整数分，重复点击不重复请求。 */
  async function save() {
    if (pending.current || !changed) return;
    setSubmitAttempted(true);
    setError("");
    if (amountError) return;
    pending.current = true; setBusy(true); setError("");
    try { await setAdminTeamBudget(teamId, nextLimit, memberId); onSaved(); onClose(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "保存额度失败"); }
    finally { pending.current = false; setBusy(false); }
  }
  return (
    <AdminModal
      open
      onOpenChange={(open) => { if (!open && !pending.current) onClose(); }}
      size="lg"
      title={memberId === undefined ? "团队月度消费上限" : "成员月度消费上限"}
      subtitle={name}
      footer={<>
        <Button type="button" variant="outline" disabled={busy} onClick={onClose}>取消</Button>
        <Button type="submit" form={formId} disabled={busy || !changed} aria-busy={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
          {busy ? "正在保存…" : "保存消费上限"}
        </Button>
      </>}
    >
      <form id={formId} noValidate onSubmit={(event) => { event.preventDefault(); void save(); }} className="space-y-5">
        <div className="admin-form-summary rounded-xl border border-[var(--admin-border)] bg-[#f7f9f4] px-4 py-3">
          <div className="text-xs text-[var(--admin-muted)]">当前每月消费上限</div>
          <div className="mt-1.5 text-xl font-semibold tabular-nums text-[var(--admin-text)]">{limit === null ? "不限额" : `¥${fenToYuan(limit)}`}</div>
        </div>
        <fieldset disabled={busy} className="min-w-0 space-y-3">
          <legend className="mb-3 text-sm font-semibold text-[var(--admin-text)]">消费限制方式</legend>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className={`admin-choice-card flex cursor-pointer items-start gap-3 rounded-xl border p-4 transition-colors ${!unlimited ? "border-[var(--admin-forest)] bg-[var(--admin-accent-soft)]" : "border-[var(--admin-border)] bg-white"} ${busy ? "opacity-60" : ""}`}>
              <input type="radio" name={modeName} value="limited" checked={!unlimited} disabled={busy} onChange={() => { setUnlimited(false); setError(""); }} className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--admin-forest)]" />
              <span><span className="block text-sm font-semibold text-[var(--admin-text)]">设置月度上限</span><span className="mt-1 block text-xs leading-relaxed text-[var(--admin-muted)]">达到限额后暂停新的消费请求</span></span>
            </label>
            <label className={`admin-choice-card flex cursor-pointer items-start gap-3 rounded-xl border p-4 transition-colors ${unlimited ? "border-[var(--admin-forest)] bg-[var(--admin-accent-soft)]" : "border-[var(--admin-border)] bg-white"} ${busy ? "opacity-60" : ""}`}>
              <input type="radio" name={modeName} value="unlimited" checked={unlimited} disabled={busy} onChange={() => { setUnlimited(true); setError(""); }} className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--admin-forest)]" />
              <span><span className="block text-sm font-semibold text-[var(--admin-text)]">不限额</span><span className="mt-1 block text-xs leading-relaxed text-[var(--admin-muted)]">不额外设置{memberId === undefined ? "团队" : "该成员"}月度消费限额</span></span>
            </label>
          </div>
        </fieldset>
        <AdminField label="每月消费上限（元）" required={!unlimited} hint={unlimited ? "已选择不限额，无需填写金额。" : "可设置 0–1,000,000 元，最多两位小数；0 元表示停止新消费。"} error={visibleAmountError}>
          <Input value={amount} inputMode="decimal" placeholder={unlimited ? "不限额，无需填写" : "例如 1000.00"} disabled={unlimited || busy} onBlur={() => setAmountTouched(true)} onChange={(event) => { setAmount(event.target.value); setError(""); }} />
        </AdminField>
        <div className="admin-form-notice flex items-start gap-2.5 rounded-xl border border-[var(--admin-border)] bg-[#f8f9f5] p-4 text-xs leading-7 text-[var(--admin-muted)]">
          <Info className="mt-1 h-4 w-4 shrink-0" aria-hidden="true" />
          <div>
            <p>按上海时区自然月统计，已消费金额与在途预留金额均占用额度。</p>
            <p>{memberId === undefined ? "团队上限统计所有成员消费；成员自己的上限同时生效。" : "成员消费同时受个人上限与团队总上限限制。"}降低上限不会取消已受理任务。</p>
            <p>费用从受理时的团队所有者个人账户扣除，设置上限不会调整账户余额。</p>
          </div>
        </div>
        {error ? <p role="alert" className="admin-form-error rounded-lg border border-[#f0d9d5] bg-[#fcf0ee] px-3 py-2.5 text-sm leading-relaxed text-[#aa3d37]">{error}</p> : null}
      </form>
    </AdminModal>
  );
}
