import { useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api, type AdminTaskDetail } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fenToYuan } from "@/lib/utils";

type TaskBillingReviewPanelProps = {
  task: AdminTaskDetail;
  onResolved: (task: AdminTaskDetail) => void;
};

// 把输入的元转成分；空值按 0，非法或负数返回 null。
function yuanToFen(value: string): number | null {
  const text = value.trim();
  if (!text) return 0;
  const yuan = Number(text);
  if (!Number.isFinite(yuan) || yuan < 0) return null;
  return Math.round(yuan * 100);
}

// 待核对任务的费用核对表单：成本只进平台账，收费才向用户收取，其余预扣退回。
export function TaskBillingReviewPanel({ task, onResolved }: TaskBillingReviewPanelProps) {
  /* 表单与提交状态 */
  const [cost, setCost] = useState("");
  const [charge, setCharge] = useState("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const estimateFen = task.billing_estimate_fen ?? 0;

  // 校验后二次确认并提交；结算不可撤销。
  async function handleSubmit() {
    const costFen = yuanToFen(cost);
    const chargeFen = yuanToFen(charge);
    if (costFen === null || chargeFen === null) {
      toast.error("金额格式不正确");
      return;
    }
    if (chargeFen > estimateFen) {
      toast.error(`向用户收取的金额不能超过预扣额 ¥${fenToYuan(estimateFen)}`);
      return;
    }
    if (!note.trim()) {
      toast.error("请填写核对依据");
      return;
    }
    const refundFen = estimateFen - chargeFen;
    const confirmed = window.confirm(
      `确定结算任务 #${task.id}？\n向用户收取 ¥${fenToYuan(chargeFen)}，退回 ¥${fenToYuan(refundFen)}。\n结算后不可撤销。`,
    );
    if (!confirmed) return;
    setSubmitting(true);
    try {
      const updated = await api<AdminTaskDetail>(`/api/admin/tasks/${task.id}/billing-review`, {
        method: "POST",
        body: JSON.stringify({ cost_fen: costFen, charge_fen: chargeFen, note: note.trim() }),
      });
      toast.success("费用已核对并结算");
      onResolved(updated);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "核对失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="border-b border-amber-200 bg-amber-50 px-4 py-3 text-sm">
      <p className="font-medium text-amber-900">
        结果或费用未知，预扣 ¥{fenToYuan(estimateFen)} 已保留。请到上游核对后结算。
      </p>
      <p className="mt-1 text-xs text-amber-800">
        成本只进平台账；未交付的任务通常不向用户收费，收取金额填 0 即全额退回。
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="grid gap-1 text-xs text-amber-900">
          核定上游成本（元）
          <Input className="w-32 bg-white" inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} />
        </label>
        <label className="grid gap-1 text-xs text-amber-900">
          向用户收取（元）
          <Input className="w-32 bg-white" inputMode="decimal" value={charge} onChange={(e) => setCharge(e.target.value)} />
        </label>
        <label className="grid min-w-64 flex-1 gap-1 text-xs text-amber-900">
          核对依据
          <Input className="bg-white" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        <Button disabled={submitting} onClick={() => void handleSubmit()}>
          {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          核对并结算
        </Button>
      </div>
    </div>
  );
}
