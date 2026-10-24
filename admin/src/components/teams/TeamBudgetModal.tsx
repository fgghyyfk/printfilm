import { useRef, useState } from "react";
import { setAdminTeamBudget } from "@/api/teams";
import { AdminModal } from "@/components/admin/AdminModal";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

/** 设置团队或成员月上限，零元停用新消费，空值代表不限额。 */
export function TeamBudgetModal({ teamId, memberId, name, limit, onClose, onSaved }: {
  teamId: number; memberId?: number; name: string; limit: number | null;
  onClose: () => void; onSaved: () => void;
}) {
  const [unlimited, setUnlimited] = useState(limit === null);
  const [amount, setAmount] = useState(((limit ?? 0) / 100).toFixed(2));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  /** 保存金额只接受两位小数，精确转换为整数分，重复点击不重复请求。 */
  async function save() {
    if (pending.current) return;
    if (!unlimited && !/^\d+(\.\d{1,2})?$/.test(amount.trim())) { setError("请输入非负金额，最多两位小数"); return; }
    const fen = unlimited ? null : Math.round(Number(amount) * 100);
    if (fen !== null && (!Number.isSafeInteger(fen) || fen > 100000000)) { setError("额度不得超过 1,000,000 元"); return; }
    pending.current = true; setBusy(true); setError("");
    try { await setAdminTeamBudget(teamId, fen, memberId); onSaved(); onClose(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "保存额度失败"); }
    finally { pending.current = false; setBusy(false); }
  }
  return <AdminModal open onOpenChange={(open) => { if (!open && !busy) onClose(); }} size="md" title={memberId === undefined ? "团队月度消费上限" : "成员月度消费上限"} subtitle={name}
    footer={<><Button variant="outline" disabled={busy} onClick={onClose}>取消</Button><Button disabled={busy} onClick={save}>{busy ? "正在保存…" : "保存消费上限"}</Button></>}>
    <div className="space-y-4"><p className="text-sm text-[var(--admin-muted)]">按上海时区自然月统计，已消费及在途预留均占用额度。设为 0 元停止新消费，降低上限不会取消已受理任务。费用由受理时的所有者个人账户支付。</p>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={unlimited} disabled={busy} onChange={(e) => setUnlimited(e.target.checked)} />不限额</label>
      <label className="block space-y-2 text-sm"><span>每月上限（元）</span><Input value={amount} inputMode="decimal" disabled={unlimited || busy} onChange={(e) => setAmount(e.target.value)} /></label>
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    </div>
  </AdminModal>;
}
