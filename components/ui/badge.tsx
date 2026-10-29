import type { HTMLAttributes } from "react";

import { cn } from "@/lib/cn";

export function Badge({
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide",
        className,
      )}
      {...props}
    />
  );
}
