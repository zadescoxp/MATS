import { AlertCircle, CircleCheck, CirclePause, Info } from "lucide-react";

import { cn } from "@/lib/cn";
import type { DiagnosisKind, RunDiagnosis } from "@/lib/types";

const TONES: Record<DiagnosisKind, { className: string; Icon: typeof AlertCircle }> = {
  failed: { className: "border-negative/25 bg-negative/[0.06] text-negative", Icon: AlertCircle },
  retired: { className: "border-border bg-card text-muted", Icon: Info },
  stopped: { className: "border-border bg-card text-muted", Icon: Info },
  paused: { className: "border-accent/25 bg-accent/[0.06] text-accent", Icon: CirclePause },
  no_trade: { className: "border-accent/25 bg-accent/[0.06] text-accent", Icon: Info },
  dry_run: { className: "border-accent/25 bg-accent/[0.06] text-accent", Icon: Info },
  waiting: { className: "border-positive/25 bg-positive/[0.06] text-positive", Icon: CircleCheck },
  in_position: { className: "border-positive/25 bg-positive/[0.06] text-positive", Icon: CircleCheck },
};

export function RunDiagnosisBanner({ diagnosis }: { diagnosis: RunDiagnosis }) {
  const tone = TONES[diagnosis.kind] ?? TONES.stopped;
  const { Icon } = tone;
  return (
    <section
      role={diagnosis.kind === "failed" ? "alert" : "status"}
      className={cn("rounded-2xl border px-4 py-3", tone.className)}
    >
      <div className="flex items-start gap-3">
        <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">{diagnosis.title}</p>
          {diagnosis.detail.split(" | ").map((line) => (
            <p key={line} className="mt-1 break-words text-xs leading-5 text-foreground/80">{line}</p>
          ))}
          {diagnosis.hint && (
            <p className="mt-2 text-xs leading-5 text-foreground">
              <span className="font-medium">What to do: </span>
              {diagnosis.hint}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
