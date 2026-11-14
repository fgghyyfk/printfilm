import { ChevronLeft, ChevronRight } from "lucide-react";
import { buildPageItems, DEFAULT_PAGE_SIZE } from "@/lib/pagination";
import { cn } from "@/lib/utils";
import { Select } from "@/components/ui/select";
import "@/styles/admin-primitives.css";

type Props = {
  page: number;
  pageSize?: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  pageSizeOptions?: number[];
  className?: string;
};

/**
 * Global admin pagination — default 10 / page, numbered pages + optional size
 */
export function PaginationBar({
  page,
  pageSize = DEFAULT_PAGE_SIZE,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 20, 50],
  className,
}: Props) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const items = buildPageItems(page, totalPages);
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  return (
    <div
      className={cn("admin-pagination admin-ui-pagination", className)}
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="admin-ui-pagination-summary">
          共 <em className="not-italic font-semibold text-[var(--admin-text)]">{total}</em> 条
          <span className="mx-1.5 text-[var(--admin-border)]">·</span>
          {from}-{to}
        </span>
        {onPageSizeChange && (
          <label className="admin-ui-pagination-size flex items-center gap-2 text-xs">
            <span>每页</span>
            <Select
              className="admin-ui-pagination-select !h-9 !min-w-[4.5rem] !text-xs"
              aria-label="每页显示条数"
              value={pageSize}
              onChange={(e) => onPageSizeChange(Number(e.target.value))}
            >
              {pageSizeOptions.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Select>
            <span>条</span>
          </label>
        )}
      </div>

      <nav className="admin-ui-pagination-pages flex items-center gap-1" aria-label="分页导航">
        <button
          type="button"
          className="admin-page-btn"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          aria-label="上一页"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        {items.map((n, idx) =>
          n === -1 ? (
            <span key={`e-${idx}`} className="px-1.5 text-[#c0c4cc]">
              …
            </span>
          ) : (
            <button
              key={n}
              type="button"
              className={cn("admin-page-btn", n === page && "is-active")}
              aria-current={n === page ? "page" : undefined}
              aria-label={`第 ${n} 页`}
              onClick={() => onPageChange(n)}
            >
              {n}
            </button>
          ),
        )}
        <button
          type="button"
          className="admin-page-btn"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
          aria-label="下一页"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </nav>
    </div>
  );
}
