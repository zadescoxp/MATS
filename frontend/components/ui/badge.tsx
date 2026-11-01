import type { HTMLAttributes } from "react";

import { cn } from "@/lib/cn";

export function Badge({
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-0.5 text-[11px] font-medium",
        className,
      )}
      {...props}
    />
  );
}
