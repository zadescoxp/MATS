import { cn } from "@/lib/cn";

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function labelFor(key: string): string {
  return key
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function primitive(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value.toLocaleString();
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return null;
}

function BriefValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value == null || value === "") {
    return <p className="text-[12px] text-muted-foreground">Nothing recorded.</p>;
  }
  const text = primitive(value);
  if (text !== null) {
    return <p className="whitespace-pre-wrap text-[12px] leading-5 text-foreground/90">{text}</p>;
  }
  if (Array.isArray(value)) {
    if (!value.length) return <p className="text-[12px] text-muted-foreground">None.</p>;
    const records = value.filter(isRecord);
    if (records.length === value.length && depth < 2) {
      const keys = [...new Set(records.flatMap((row) => Object.keys(row)))]
        .filter((key) => records.some((row) => primitive(row[key]) !== null))
        .slice(0, 6);
      if (keys.length) {
        return (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[28rem] text-left text-[11px]">
              <thead className="text-muted-foreground">
                <tr>
                  {keys.map((key) => (
                    <th key={key} className="pb-2 pr-3 font-medium">{labelFor(key)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {records.slice(0, 12).map((row, index) => (
                  <tr key={index} className="border-t border-white/[0.05] text-foreground/85">
                    {keys.map((key) => (
                      <td key={key} className="max-w-52 truncate py-2 pr-3 align-top">{primitive(row[key]) ?? "—"}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {records.length > 12 && (
              <p className="mt-2 text-[10px] text-muted-foreground">{records.length - 12} more not shown. The raw record is below.</p>
            )}
          </div>
        );
      }
    }
    return (
      <ul className="space-y-2">
        {value.slice(0, 8).map((item, index) => (
          <li key={index} className="rounded-xl bg-white/[0.03] px-3 py-2">
            <BriefValue value={item} depth={depth + 1} />
          </li>
        ))}
      </ul>
    );
  }
  if (isRecord(value)) {
    const entries = Object.entries(value).filter(([, item]) => item != null && item !== "");
    if (!entries.length) return <p className="text-[12px] text-muted-foreground">Nothing recorded.</p>;
    if (depth >= 3) {
      return <p className="text-[12px] text-muted-foreground">{entries.length} nested fields. Open the raw record to inspect them.</p>;
    }
    return (
      <dl className="space-y-2.5">
        {entries.map(([key, item]) => {
          const simple = primitive(item);
          return (
            <div key={key} className={cn(simple ? "grid grid-cols-[9rem_minmax(0,1fr)] gap-3" : "space-y-1.5")}>
              <dt className="text-[11px] text-muted-foreground">{labelFor(key)}</dt>
              <dd className="min-w-0 text-[12px] leading-5 text-foreground/90">
                {simple ?? <BriefValue value={item} depth={depth + 1} />}
              </dd>
            </div>
          );
        })}
      </dl>
    );
  }
  return <p className="text-[12px] text-muted-foreground">Unsupported value.</p>;
}

export function StructuredBrief({ value }: { value: unknown }) {
  return (
    <div className="space-y-3 px-3.5 pb-3">
      <BriefValue value={value} />
      <details className="group/raw">
        <summary className="cursor-pointer list-none text-[10px] text-muted-foreground transition-colors hover:text-foreground">
          Raw record
        </summary>
        <pre className="mt-2 max-h-72 overflow-auto font-mono text-[11px] leading-5 text-muted">
          {JSON.stringify(value ?? null, null, 2)}
        </pre>
      </details>
    </div>
  );
}
