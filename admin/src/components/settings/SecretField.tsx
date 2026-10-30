import { useState } from "react";
import { Check, Eye, EyeOff, RotateCcw, Trash2 } from "lucide-react";
import { LabeledControl } from "@/components/settings/SettingsPanel";
import { Button } from "@/components/ui/button";

type SecretFieldProps = {
  label: string;
  hint?: string;
  value: string;
  configured: boolean;
  placeholder?: string;
  onChange: (value: string) => void;
  onClear?: () => void;
  clearPending?: boolean;
  onUndoClear?: () => void;
  disabled?: boolean;
  required?: boolean;
  error?: string;
};

// Only the new draft can be revealed; existing secrets are never loaded into this field.
export function SecretField({
  label,
  hint,
  value,
  configured,
  placeholder,
  onChange,
  onClear,
  clearPending = false,
  onUndoClear,
  disabled = false,
  required = false,
  error,
}: SecretFieldProps) {
  const [visible, setVisible] = useState(false);
  const hasDraft = value.trim().length > 0;
  const status = clearPending ? "保存后清除" : hasDraft ? "新凭证待保存" : configured ? "已配置" : "未配置";
  const inputRequired = required && !configured && !clearPending;
  const fieldError = error ?? (value.length > 0 && !hasDraft && !clearPending ? "凭证不能只包含空格。" : undefined);
  const guidance = clearPending
    ? "当前只是标记清除，保存后生效；可撤销清除。"
    : configured
      ? "已有凭证不会回显；留空保留，输入新值后保存以替换。"
      : "填写新凭证后保存；显示按钮仅展示本次输入。";
  return (
    <LabeledControl
      label={label}
      hint={[hint, guidance].filter(Boolean).join(" ")}
      required={inputRequired}
      error={fieldError}
    >
      <div className="admin-secret-field">
        <div className="admin-secret-field-row settings-secret-row">
          <input
            type={visible && !clearPending ? "text" : "password"}
            className="settings-input is-secret"
            placeholder={clearPending ? "已标记清除" : placeholder ?? (configured ? "留空保留已有凭证" : "输入新凭证")}
            value={clearPending ? "" : value}
            disabled={disabled || clearPending}
            required={inputRequired}
            onChange={(e) => onChange(e.target.value)}
            autoComplete="new-password"
            autoCapitalize="none"
            spellCheck={false}
          />
          <Button type="button" variant="outline" size="icon" disabled={disabled || clearPending || !hasDraft}
            aria-label={`${visible ? "隐藏" : "显示"}新${label}`} aria-pressed={visible}
            title={visible ? "隐藏本次输入" : "显示本次输入"} onClick={() => setVisible((current) => !current)}>
            {visible ? <EyeOff size={16} /> : <Eye size={16} />}
          </Button>
          {clearPending && onUndoClear ? <Button type="button" variant="outline" disabled={disabled} onClick={onUndoClear}>
            <RotateCcw size={14} />撤销
          </Button> : (configured || hasDraft) && onClear ? <Button type="button" variant="outline" disabled={disabled} onClick={() => { setVisible(false); onClear(); }}>
            <Trash2 size={14} />清除
          </Button> : null}
        </div>
        <span className="admin-secret-status inline-flex items-center gap-1" role="status">
          {configured && !clearPending && !hasDraft ? <Check size={12} /> : null}{status}
        </span>
      </div>
    </LabeledControl>
  );
}
