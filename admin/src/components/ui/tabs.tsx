import * as React from "react";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

export const Tabs = TabsPrimitive.Root;

export function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn(
        "inline-flex min-h-11 max-w-full items-center gap-1 overflow-x-auto rounded-xl border border-[#e6eadf] bg-[#f0f3eb] p-1 text-[var(--admin-muted)]",
        className,
      )}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "inline-flex min-h-8 shrink-0 items-center justify-center whitespace-nowrap rounded-[8px] px-3.5 py-1.5 text-[13px] font-semibold transition-all data-[state=active]:bg-white data-[state=active]:text-[var(--admin-forest)] data-[state=active]:shadow-[0_1px_4px_rgba(25,34,30,0.1)] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--admin-accent-ring)] disabled:pointer-events-none disabled:opacity-40",
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content className={cn("mt-4 rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--admin-accent-ring)]", className)} {...props} />;
}
