import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import "@/styles/admin-primitives.css";

const buttonVariants = cva(
  "admin-ui-button inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-[10px] text-[13px] font-semibold transition-all duration-150 focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--admin-accent-ring)] focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-45 aria-busy:cursor-wait active:translate-y-px",
  {
    variants: {
      variant: {
        default:
          "border border-transparent bg-[var(--admin-forest)] text-white shadow-[0_2px_3px_rgba(25,34,30,0.08)] hover:bg-[var(--admin-forest-deep)]",
        secondary:
          "border border-[var(--admin-border)] bg-white text-[var(--admin-text)] shadow-[0_1px_2px_rgba(25,34,30,0.03)] hover:border-[#b9c7bc] hover:bg-[#f8faf6]",
        outline:
          "border border-[var(--admin-border)] bg-white text-[var(--admin-text)] hover:border-[#b9c7bc] hover:bg-[#f8faf6] hover:text-[var(--admin-forest)]",
        ghost: "text-[var(--admin-muted)] hover:bg-[var(--admin-accent-soft)] hover:text-[var(--admin-forest)]",
        destructive:
          "border border-transparent bg-[#bd3f3a] text-white shadow-sm hover:bg-[#a73430] focus-visible:ring-[#bd3f3a]/20",
        soft: "border border-transparent bg-[var(--admin-accent-soft)] text-[var(--admin-forest)] hover:bg-[#dfebd8]",
      },
      size: {
        default: "h-10 px-4",
        sm: "h-8 rounded-[8px] px-3 text-xs",
        lg: "h-11 rounded-[10px] px-6 text-sm",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

// Admin primary action button
export function Button({ className, variant, size, asChild = false, type = "button", ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size, className }))} {...(!asChild ? { type } : {})} {...props} />;
}
