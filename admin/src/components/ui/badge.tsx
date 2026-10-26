import * as React from "react";
import { cn } from "@/lib/utils";

// Status / tag badge — soft admin chips
export function Badge({
  className,
  variant = "default",
  ...props
}: React.ComponentProps<"span"> & {
  variant?: "default" | "secondary" | "outline" | "destructive" | "success" | "warning" | "info";
}) {
  const variants = {
    default: "border-transparent bg-[var(--admin-text)] text-white",
    secondary: "border-[#e4e8e0] bg-[#f3f5f0] text-[#626e63]",
    outline: "border-[var(--admin-border)] bg-white text-[var(--admin-muted)]",
    destructive: "border-[#f0d9d5] bg-[#fcf0ee] text-[#aa3d37]",
    success: "border-[#d5e6d1] bg-[var(--admin-accent-soft)] text-[var(--admin-forest)]",
    warning: "border-[#eddfc4] bg-[#fcf6e9] text-[#8e621d]",
    info: "border-[#d8e3e8] bg-[#eff5f7] text-[#456578]",
  };
  return (
    <span
      className={cn(
        "inline-flex min-h-6 items-center gap-1.5 whitespace-nowrap rounded-md border px-2 py-0.5 text-[11px] font-semibold leading-4",
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}
