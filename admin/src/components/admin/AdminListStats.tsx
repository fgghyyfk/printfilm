import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import "@/styles/admin-primitives.css";

export type AdminListStatItem = {
  label: string;
  value: ReactNode;
  hint?: string;
};

type AdminListStatsProps = {
  items: AdminListStatItem[];
  className?: string;
};

/** 列表页顶部指标卡（白底简洁样式） */
export function AdminListStats({ items, className }: AdminListStatsProps) {
  if (items.length === 0) return null;
  return (
    <dl className={cn("admin-list-stats admin-ui-list-stats", className)} aria-label="列表统计">
      {items.map((item) => (
        <div key={item.label} className="admin-list-stat-card">
          <dt className="admin-list-stat-label">{item.label}</dt>
          <dd className="admin-list-stat-value">{item.value}</dd>
          {item.hint ? <div className="admin-list-stat-hint">{item.hint}</div> : null}
        </div>
      ))}
    </dl>
  );
}
