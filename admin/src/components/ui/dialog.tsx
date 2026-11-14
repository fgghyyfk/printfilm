import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import "@/styles/admin-primitives.css";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content>) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="admin-dialog-overlay fixed inset-0 z-50 bg-[#18251f]/40 backdrop-blur-[3px]" />
      <DialogPrimitive.Content
        className={cn(
          "admin-dialog-shell fixed left-1/2 top-1/2 z-50 grid w-[calc(100%-32px)] max-w-lg -translate-x-1/2 -translate-y-1/2 gap-5 overflow-y-auto rounded-2xl border border-[var(--admin-border)] bg-white p-6 shadow-[0_24px_80px_rgba(25,34,30,0.22)] max-h-[min(90dvh,calc(100dvh-32px))]",
          className,
        )}
        {...props}
      >
        {children}
        <DialogPrimitive.Close aria-label="关闭弹窗" className="admin-dialog-close absolute right-4 top-4 z-10 flex h-8 w-8 items-center justify-center rounded-lg text-[var(--admin-muted)] transition-colors hover:bg-[#f1f4ee] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--admin-accent-ring)]">
          <X className="h-4 w-4" aria-hidden="true" />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex flex-col gap-1.5 pr-9 text-left", className)} {...props} />;
}

export function DialogTitle({ className, ...props }: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      className={cn("text-lg font-semibold tracking-tight text-[var(--admin-text)]", className)}
      {...props}
    />
  );
}

export function DialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description className={cn("text-[13px] leading-relaxed text-[var(--admin-muted)]", className)} {...props} />
  );
}
