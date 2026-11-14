import { cn } from "@/lib/utils";
import type { KeyboardEventHandler } from "react";
import { Search, X } from "lucide-react";
import "@/styles/admin-controls.css";

type AdminSearchInputProps = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  onKeyDown?: KeyboardEventHandler<HTMLInputElement>;
};

// 管理端搜索框（带图标）
export function AdminSearchInput({
  value,
  onChange,
  placeholder = "搜索…",
  className,
  onKeyDown,
}: AdminSearchInputProps) {
  return (
    <div className={cn("admin-input-search-wrap admin-filter-search admin-search-control", className)}>
      <Search size={16} className="admin-search-control-icon" aria-hidden="true" />
      <input
        type="search"
        aria-label={placeholder}
        className="admin-input"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(event) => {
          onKeyDown?.(event);
          if (!event.defaultPrevented && event.key === "Escape" && value) {
            onChange("");
            event.stopPropagation();
          }
        }}
      />
      {value ? <button type="button" className="admin-control-clear" aria-label="清除搜索" onMouseDown={(event) => event.preventDefault()} onClick={() => onChange("")}><X size={13} /></button> : null}
    </div>
  );
}
