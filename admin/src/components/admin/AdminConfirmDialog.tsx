import { AlertTriangle, Check, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AdminModal } from "@/components/admin/AdminModal";

type AdminConfirmDialogProps = {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  loading?: boolean;
  destructive?: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
};

// 管理端确认弹窗（删除等二次确认）
export function AdminConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "确认",
  cancelLabel = "取消",
  loading = false,
  destructive = false,
  onOpenChange,
  onConfirm,
}: AdminConfirmDialogProps) {
  return (
    <AdminModal
      open={open}
      onOpenChange={(nextOpen) => { if (!loading) onOpenChange(nextOpen); }}
      size="md"
      title={<span className="admin-ui-confirm-title"><span className={destructive ? "admin-ui-confirm-icon is-danger" : "admin-ui-confirm-icon"}>{destructive ? <AlertTriangle className="h-5 w-5" aria-hidden="true" /> : <Check className="h-5 w-5" aria-hidden="true" />}</span><span>{title}</span></span>}
      subtitle={description}
      footer={
        <>
          <Button variant="outline" disabled={loading} onClick={() => onOpenChange(false)}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? "destructive" : "default"}
            disabled={loading}
            aria-busy={loading}
            onClick={onConfirm}
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            {confirmLabel}
          </Button>
        </>
      }
    />
  );
}
