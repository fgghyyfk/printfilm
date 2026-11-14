import * as React from "react";
import { cn } from "@/lib/utils";
import "@/styles/admin-primitives.css";

// 管理端统一文本输入
export function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      className={cn("admin-control admin-ui-input", className)}
      {...props}
    />
  );
}
