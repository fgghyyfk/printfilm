import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Inbox } from "lucide-react";
import "@/styles/admin-primitives.css";

type PageHeaderProps = {
  title?: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
};

// 页面标题、说明与主要操作的统一层级。
export function PageHeader({ title, description, actions, className }: PageHeaderProps) {
  if (!title && !description && !actions) return null;
  return (
    <div className={cn("admin-page-header admin-ui-page-header", className)}>
      {title || description ? <div className="min-w-0">
        {title ? <h1 className="admin-ui-page-title">{title}</h1> : null}
        {description ? <p className="admin-page-desc">{description}</p> : null}
      </div> : null}
      {actions ? <div className="admin-ui-page-actions">{actions}</div> : null}
    </div>
  );
}

type EmptyStateProps = {
  title?: string;
  description?: string;
  className?: string;
};

// 空列表占位
export function EmptyState({
  title = "暂无数据",
  description = "换个筛选条件再试试",
  className,
}: EmptyStateProps) {
  return (
    <div className={cn("admin-empty admin-ui-empty", className)}>
      <div className="admin-empty-icon"><Inbox className="h-6 w-6" strokeWidth={1.5} aria-hidden="true" /></div>
      <div className="admin-empty-title">{title}</div>
      <div className="admin-empty-desc">{description}</div>
    </div>
  );
}

type ToolbarProps = {
  children: ReactNode;
  className?: string;
};

// 筛选 / 搜索工具条（与 AdminFilterBar 一致）
export function Toolbar({ children, className }: ToolbarProps) {
  return (
    <div className={cn("admin-filter-bar", className)}>
      <div className="admin-filter-bar-main">{children}</div>
    </div>
  );
}
