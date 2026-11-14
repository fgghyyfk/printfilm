import { cn } from "@/lib/utils";
import { AdminField } from "@/components/admin/AdminField";
import { Select } from "@/components/ui/select";
import { useId } from "react";

export type AdminSelectOption = {
  value: string;
  label: string;
};

type AdminSelectProps = {
  label?: string;
  hint?: string;
  value: string;
  options: AdminSelectOption[];
  placeholder?: string;
  className?: string;
  onChange: (value: string) => void;
};

// 管理端下拉选择（统一样式）
export function AdminSelect({
  label,
  hint,
  value,
  options,
  placeholder,
  className,
  onChange,
}: AdminSelectProps) {
  const id = useId();
  const control = (
    <div className={cn("admin-select-container", className)}>
      <Select
        id={id}
        aria-label={label || placeholder || "筛选选项"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {placeholder && !options.some((option) => option.value === "") ? <option value="">{placeholder}</option> : null}
        {options.map((opt) => (
          <option key={opt.value || "__empty"} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </Select>
    </div>
  );

  if (!label && !hint) return control;
  return (
    <AdminField label={label} hint={hint} htmlFor={id}>
      {control}
    </AdminField>
  );
}
