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
  rejected: { className: "border-accent/25 bg-accent/[0.06] text-accent", Icon: Info },
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

export function RejectedRunNotice({
  diagnosis,
  extraAttempts,
  busy,
  onMoreAttempts,
  onChangeSettings,
  onDismiss,
}: {
  diagnosis: RunDiagnosis;
  extraAttempts: number;
  busy: boolean;
  onMoreAttempts: () => void;
  onChangeSettings: () => void;
  onDismiss: () => void;
}) {
  return (
    <section role="status" className="rounded-2xl border border-accent/25 bg-accent/[0.06] px-4 py-3">
      <div className="flex items-start gap-3">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-accent" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-accent">{diagnosis.title}</p>
          {diagnosis.detail.split(" | ").map((line) => (
            <p key={line} className="mt-1 break-words text-xs leading-5 text-foreground/80">{line}</p>
          ))}
          {diagnosis.hint && <p className="mt-2 text-xs leading-5 text-muted">{diagnosis.hint}</p>}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={onMoreAttempts}
              className="inline-flex h-8 items-center rounded-lg bg-foreground px-3.5 text-xs font-medium text-background transition-colors duration-150 hover:bg-foreground/90 disabled:opacity-40"
            >
              Try {extraAttempts} more attempts
            </button>
            <button
              type="button"
              onClick={onChangeSettings}
              className="inline-flex h-8 items-center rounded-lg border border-border px-3.5 text-xs font-medium text-foreground transition-colors duration-150 hover:bg-card"
            >
              Change run settings
            </button>
            <button
              type="button"
              onClick={onDismiss}
              className="inline-flex h-8 items-center rounded-lg px-3.5 text-xs font-medium text-muted transition-colors duration-150 hover:text-foreground"
            >
              Dismiss
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
