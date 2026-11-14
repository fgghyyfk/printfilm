import * as React from "react";
import { cn } from "@/lib/utils";
import "@/styles/admin-primitives.css";

// Table primitives tuned for admin lists
export function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div className="admin-data-table admin-ui-table">
      <table className={cn("w-full caption-bottom text-sm", className)} {...props} />
    </div>
  );
}

export function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return <thead className={cn("admin-data-table-head [&_tr]:border-0", className)} {...props} />;
}

export function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return <tbody className={cn("[&_tr:last-child]:border-0", className)} {...props} />;
}

export function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      className={cn(
        "border-b border-[var(--admin-border)] transition-colors hover:bg-[#f8faf6] data-[state=selected]:bg-[var(--admin-accent-soft)]",
        className,
      )}
      {...props}
    />
  );
}

export function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      className={cn(
        "h-11 whitespace-nowrap px-5 text-left align-middle text-xs font-semibold tracking-wide text-[var(--admin-table-head-text)]",
        className,
      )}
      {...props}
    />
  );
}

export function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return <td className={cn("px-5 py-4 align-middle text-[13px] leading-relaxed text-[var(--admin-text)]", className)} {...props} />;
}
