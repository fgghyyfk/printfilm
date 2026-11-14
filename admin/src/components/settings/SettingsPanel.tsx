import type { ReactNode } from "react";
import { useCallback, useEffect, useRef } from "react";
import { Loader2, Save } from "lucide-react";
import { AdminField } from "@/components/admin/AdminField";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useSettingsSaveSlot } from "@/components/settings/SettingsSaveContext";

type PanelProps = {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
};

// 配置分区面板：标题 + 内容
export function SettingsPanel({ title, description, actions, children, className }: PanelProps) {
  return (
    <section className={cn("settings-panel", className)}>
      <div className="settings-panel-header">
        <div className="min-w-0">
          <h3 className="settings-panel-title">{title}</h3>
          {description ? <p className="settings-panel-desc">{description}</p> : null}
        </div>
        {actions ? <div className="settings-panel-actions">{actions}</div> : null}
      </div>
      <div className="settings-panel-body">{children}</div>
    </section>
  );
}

type SettingsTabShellProps = {
  children: ReactNode;
  onSave?: () => void | Promise<void>;
  saving?: boolean;
  saveLabel?: string;
  disabled?: boolean;
};

// Both save entries submit the same form, so keyboard and button validation stay consistent.
export function SettingsTabShell({ children, onSave, saving, saveLabel = "保存当前设置", disabled }: SettingsTabShellProps) {
  const { registerSave } = useSettingsSaveSlot();
  const formRef = useRef<HTMLFormElement>(null);
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;
  const hasSave = Boolean(onSave);
  const submit = useCallback(() => {
    if (saving || disabled || !onSaveRef.current) return;
    const form = formRef.current;
    const invalid = form?.querySelector<HTMLElement>("input:invalid, select:invalid, textarea:invalid, [aria-invalid='true']");
    if (invalid) {
      let parent = invalid.parentElement;
      while (parent && parent !== form) {
        if (parent instanceof HTMLDetailsElement) parent.open = true;
        parent = parent.parentElement;
      }
      invalid.scrollIntoView({ block: "center", behavior: "smooth" });
      invalid.focus({ preventScroll: true });
    }
    if (!form?.reportValidity() || invalid?.getAttribute("aria-invalid") === "true") return;
    return onSaveRef.current();
  }, [saving, disabled]);

  useEffect(() => {
    if (!hasSave) {
      registerSave(null);
      return () => registerSave(null);
    }
    registerSave({
      onSave: submit,
      saving,
      disabled,
      label: saveLabel,
    });
    return () => registerSave(null);
  }, [hasSave, saving, disabled, saveLabel, registerSave, submit]);

  return (
    <form ref={formRef} className="settings-tab-shell" noValidate onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <fieldset disabled={saving} className="settings-tab-body">{children}</fieldset>
      {hasSave ? <div className="settings-save-bar">
        <div><strong>{saving ? "正在保存设置…" : "保存当前分区"}</strong><p>本页修改将在保存成功后生效。</p></div>
        <Button type="submit" disabled={saving || disabled}>{saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}{saving ? "保存中…" : saveLabel}</Button>
      </div> : null}
    </form>
  );
}

// 加载占位
export function SettingsLoading({ label = "加载中…" }: { label?: string }) {
  return (
    <div className="settings-loading">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}
    </div>
  );
}

// 带图标的区块标题
export function SectionTitle({ icon, title }: { icon: ReactNode; title: string }) {
  return (
    <div className="settings-section-title">
      <span className="settings-section-icon">{icon}</span>
      <span>{title}</span>
    </div>
  );
}

// 表单标签 + 控件
export function LabeledControl({
  label,
  hint,
  children,
  className,
  error,
  required,
  optional,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  className?: string;
  error?: string;
  required?: boolean;
  optional?: boolean;
}) {
  return (
    <AdminField label={label} hint={hint} error={error} required={required} optional={optional} className={cn("settings-field", className)}>{children}</AdminField>
  );
}

// 内嵌子面板（浅灰底，用于状态条或独立分组）
export function SettingsSurface({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={cn("settings-surface", className)}>{children}</div>;
}

export type SettingsStatusItem = {
  id: string;
  label: string;
  ready: boolean;
  readyText?: string;
  pendingText?: string;
};

// 顶部就绪/状态条（与模型路由一致）
export function SettingsStatusBar({
  title,
  items,
  extra,
}: {
  title: string;
  items: SettingsStatusItem[];
  extra?: ReactNode;
}) {
  return (
    <SettingsSurface className="settings-readiness-bar">
      <div className="settings-readiness-title-row">
        <div className="settings-readiness-title">{title}</div>
        {extra}
      </div>
      <div className="settings-readiness-row">
        {items.map((item) => (
          <div key={item.id} className={cn("settings-readiness-item", item.ready && "is-ready")}>
            <span className={cn("settings-readiness-dot", item.ready ? "is-on" : "is-off")} />
            <span>{item.label}</span>
            <em>{item.ready ? item.readyText ?? "已配置" : item.pendingText ?? "未就绪"}</em>
          </div>
        ))}
      </div>
    </SettingsSurface>
  );
}
