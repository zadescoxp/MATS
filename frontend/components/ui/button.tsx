import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

type ButtonVariant = "default" | "primary" | "outline" | "ghost" | "danger";

const variants: Record<ButtonVariant, string> = {
  default: "bg-card text-foreground hover:bg-foreground/10",
  primary: "bg-foreground text-background hover:bg-foreground/90",
  outline: "border border-border bg-transparent text-foreground hover:bg-foreground/5",
  ghost: "bg-transparent text-muted hover:bg-foreground/5 hover:text-foreground",
  danger: "bg-negative/10 text-negative hover:bg-negative/20",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md";
}

export function Button({
  className,
  variant = "default",
  size = "md",
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg border border-transparent font-medium transition-colors duration-150 disabled:pointer-events-none disabled:opacity-40",
        size === "sm" ? "min-h-8 px-3 text-xs" : "min-h-10 px-4 text-sm",
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}
