"use client";

import {
  Activity,
  AlertCircle,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  ChevronRight,
  Circle,
  FileJson,
  Filter,
  GitBranch,
  ListChecks,
  LoaderCircle,
  LockKeyhole,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Square,
  TrendingUp,
  Wallet,
  X,
  Zap,
} from "lucide-react";
import Image from "next/image";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { createStateStream, fetchState, sendAction } from "@/lib/api";
import type {
  DashboardState,
  DashboardTab,
  Run,
  StrategyAttempt,
  SystemEvent,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { PersonaAvatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { LiveMarketChart } from "@/components/live-market-chart";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";

const COINS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"];
const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"];
const EMPTY_RUNS: Run[] = [];

const AGENTS = [
  { id: "data_worker", name: "Data worker", role: "Market data", persona: "/personas/data.png" },
  { id: "multi_timeframe_analyst", name: "Market analyst", role: "Multi-timeframe analysis", persona: "/personas/Analyst.png" },
  { id: "strategy_builder", name: "Strategy builder", role: "Strategy design", persona: "/personas/Strategy%20Builder.png" },
  { id: "backtest", name: "Backtester", role: "Backtesting", persona: "/personas/Backtester.png" },
  { id: "critique", name: "Critique", role: "Quality review", persona: "/personas/Critique.png" },
  { id: "risk_assessment", name: "Risk analyst", role: "Position sizing", persona: "/personas/RiskAnalyst.png" },
  { id: "coder", name: "Coder", role: "Execution logic", persona: "/personas/Coder.png" },
  { id: "execution", name: "Execution", role: "Signal and orders", persona: "/personas/Execution.png" },
  { id: "monitor", name: "Monitor", role: "Position monitoring", persona: "/personas/monitor.jpg" },
];

const TABS: Array<{ id: DashboardTab; label: string; icon: typeof Activity }> = [
  { id: "graph", label: "Activity", icon: Activity },
  { id: "agent", label: "Agent details", icon: ListChecks },
  { id: "strategy", label: "Backtest", icon: BarChart3 },
  { id: "positions", label: "Positions", icon: Wallet },
  { id: "events", label: "Events", icon: GitBranch },
];

type Notice = { kind: "success" | "error" | "info"; message: string };

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function objectRows(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter(isObject) : [];
}

function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : Number(value) || 0;
}

function textValue(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.length ? value : fallback;
}

function money(value: unknown, digits = 2): string {
  const amount = numberValue(value);
  return `${amount < 0 ? "−" : ""}$${Math.abs(amount).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

function dateLabel(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function clockLabel(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function orderTime(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return dateLabel(new Date(value).toISOString());
  }
  return dateLabel(typeof value === "string" ? value : null);
}

function formatDuration(start: string, finish?: string | null): string {
  const from = new Date(start).getTime();
  const to = finish ? new Date(finish).getTime() : Date.now();
  if (!Number.isFinite(from) || !Number.isFinite(to)) return "—";
  const seconds = Math.max(0, Math.round((to - from) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function statusStyle(status: string): string {
  switch (status.toLowerCase()) {
    case "completed":
    case "filled":
    case "approve":
    case "approved":
    case "open":
      return "border-positive/20 bg-positive/10 text-positive";
    case "failed":
    case "rejected":
    case "reject":
    case "error":
      return "border-negative/20 bg-negative/10 text-negative";
    case "running":
    case "watching":
    case "dry_run":
    case "stopped":
    case "hold":
    case "paused":
    case "triggered":
      return "border-accent/20 bg-accent/10 text-accent";
    default:
      return "border-border bg-foreground/[0.03] text-muted";
  }
}

function agentStatus(run: Run | null, agentId: string, operations: DashboardState["operations"]): string {
  if (!run) return "idle";
  if (agentId === "monitor") {
    return operations.filter((operation) => operation.name === "monitor").at(-1)?.status ?? "idle";
  }
  if (agentId === "critique" && run.strategies.at(-1)?.decision === "REJECT") {
    return "rejected";
  }
  const steps = run.steps.filter((step) => step.agent_name === agentId);
  return steps.at(-1)?.status ?? "idle";
}

function latestAttempt(run: Run | null): StrategyAttempt | null {
  if (!run?.strategies.length) return null;
  return [...run.strategies].sort((a, b) => b.version - a.version)[0] ?? null;
}

function agentWorkingLabel(agentId: string, symbol: string): string {
  const action: Record<string, string> = {
    initialize: "Preparing the research run",
    data_worker: "Collecting market data",
    multi_timeframe_analyst: "Reviewing market structure",
    strategy_builder: "Building a strategy candidate",
    backtest: "Backtesting strategy performance",
    critique: "Reviewing risk-adjusted results",
    risk_assessment: "Sizing risk for the proposed trade",
    coder: "Preparing execution logic",
    execution: "Checking the execution signal",
    monitor: "Monitoring open positions",
  };
  return `${action[agentId] ?? "Working on the next step"} for ${symbol}`;
}

function stepSummary(step: Run["steps"][number], symbol: string): string {
  if (step.error) return step.error;
  if (step.status === "running") return agentWorkingLabel(step.agent_name, symbol);
  const output = step.output;
  if (!output) return step.status === "completed" ? "Step completed." : "Waiting for this step.";
  const describe = (value: unknown, depth = 0): string | null => {
    if (typeof value === "string" && value.trim() && value.toLowerCase() !== "completed") {
      return value.trim();
    }
    if (!isObject(value) || depth > 1) return null;
    for (const key of ["summary", "finding", "decision", "reason", "message", "action", "template_name", "name"]) {
      const summary = describe(value[key], depth + 1);
      if (summary) return summary;
    }
    return null;
  };
  const candidates = [
    "analyst_findings",
    "strategy",
    "backtest_report",
    "critique",
    "risk_assessment",
    "live_signal",
    "execution_result",
    "strategy_diagnostics",
    "market_context",
    "summary",
    "finding",
    "decision",
    "reason",
    "message",
    "action",
  ];
  for (const key of candidates) {
    const summary = describe(output[key]);
    if (summary) return summary;
  }
  const text = Object.entries(output).find(
    ([key, value]) => key !== "status" && typeof value === "string" && value.trim(),
  );
  if (text) return `${text[0].replaceAll("_", " ")} · ${String(text[1]).trim()}`;
  const count = Object.keys(output).length;
  return count ? `Completed and saved ${count} structured result${count === 1 ? "" : "s"}.` : "Step completed.";
}

function agentName(agentId: string): string {
  return AGENTS.find((agent) => agent.id === agentId)?.name
    ?? agentId.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function toggleSelection(
  value: string,
  selected: string[],
  setter: (value: string[]) => void,
): void {
  setter(selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]);
}

function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const dot =
    ["completed", "filled", "approve", "approved", "open"].includes(normalized)
      ? "bg-positive"
      : ["failed", "rejected", "reject", "error"].includes(normalized)
        ? "bg-negative"
        : ["running", "dry_run"].includes(normalized)
          ? "bg-accent"
          : "bg-muted-foreground";
  return (
    <Badge className={cn("border-transparent bg-foreground/[0.04] px-2.5 py-1 text-[10px] normal-case tracking-normal", statusStyle(status))}>
      <span className={cn("h-1.5 w-1.5 rounded-full", dot, normalized === "running" && "agent-live")} />
      {status.replaceAll("_", " ")}
    </Badge>
  );
}

function CoinMark({ symbol, small = false }: { symbol: string; small?: boolean }) {
  const base = symbol.split("/")[0]?.toUpperCase() ?? "";
  const size = small ? 28 : 34;
  const logos: Record<string, string> = {
    BTC: "https://cryptologos.cc/logos/bitcoin-btc-logo.png",
    ETH: "https://cryptologos.cc/logos/ethereum-eth-logo.png",
    SOL: "https://cryptologos.cc/logos/solana-sol-logo.png",
    XRP: "https://cryptologos.cc/logos/xrp-xrp-logo.png",
    BNB: "https://cryptologos.cc/logos/bnb-bnb-logo.png",
    ADA: "https://cryptologos.cc/logos/cardano-ada-logo.png",
  };
  const logo = logos[base];
  const [failedLogo, setFailedLogo] = useState<string | null>(null);
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full border border-border bg-background text-[10px] font-bold text-foreground"
      style={{ width: size, height: size }}
      aria-label={`${base || "generic"} coin`}
    >
      {logo && failedLogo !== base ? (
        <Image
          src={logo}
          alt=""
          width={size - 10}
          height={size - 10}
          className="object-contain"
          onError={() => setFailedLogo(base)}
        />
      ) : (
        <span>{base.slice(0, 1) || "$"}</span>
      )}
    </span>
  );
}

function RawSection({
  title,
  value,
  defaultOpen = false,
}: {
  title: string;
  value: unknown;
  defaultOpen?: boolean;
}) {
  return (
    <details className="group rounded-xl border border-border bg-background/70" open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-xs font-medium text-foreground">
        <span className="flex items-center gap-2">
          <FileJson className="h-4 w-4 text-muted" />
          {title}
        </span>
        <ChevronRight className="h-4 w-4 text-muted transition-transform group-open:rotate-90" />
      </summary>
      <pre className="max-h-96 overflow-auto border-t border-border px-4 py-3 font-mono text-[11px] leading-5 text-muted">
        {JSON.stringify(value ?? null, null, 2)}
      </pre>
    </details>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="grid min-h-52 place-items-center rounded-xl border border-dashed border-border px-6 text-center">
      <div>
        <Circle className="mx-auto h-6 w-6 text-muted" />
        <p className="mt-3 text-sm font-medium">{title}</p>
        <p className="mt-1 max-w-md text-xs leading-5 text-muted">{body}</p>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "positive" | "negative";
}) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <p className="text-[11px] text-muted">{label}</p>
      <p className={cn("mt-2 text-lg font-semibold tabular-nums", tone === "positive" ? "text-positive" : tone === "negative" ? "text-negative" : "")}>{value}</p>
    </div>
  );
}

export function Dashboard() {
  const [data, setData] = useState<DashboardState | null>(null);
  const [connection, setConnection] = useState<"connecting" | "online" | "offline">("connecting");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runGroupIds, setRunGroupIds] = useState<string[]>([]);
  const [selectedAgent, setSelectedAgent] = useState("data_worker");
  const [feedAgentId, setFeedAgentId] = useState("all");
  const [tab, setTab] = useState<DashboardTab>("graph");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [showStart, setShowStart] = useState(false);
  const [search, setSearch] = useState("");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [selectedPositions, setSelectedPositions] = useState<string[]>([]);
  const [startSymbols, setStartSymbols] = useState<string[]>([]);
  const [startTimeframes, setStartTimeframes] = useState<string[]>([]);
  const [startDefaultsLoaded, setStartDefaultsLoaded] = useState(false);
  const [accountEquity, setAccountEquity] = useState("");
  const [startDryRun, setStartDryRun] = useState(true);
  const [executionStatusFilter, setExecutionStatusFilter] = useState("");
  const notifiedFailures = useRef(new Set<string>());
  const [wideViewport, setWideViewport] = useState<boolean | null>(null);

  const updateState = useCallback((incoming: DashboardState) => {
    setData(incoming);
    setConnection("online");
    setSelectedRunId((current) => {
      if (current && incoming.runs.some((run) => run.run_id === current)) return current;
      return incoming.runs[0]?.run_id ?? null;
    });
  }, []);

  useEffect(() => {
    let active = true;
    fetchState()
      .then((state) => {
        if (active) updateState(state);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setConnection("offline");
        setNotice({
          kind: "error",
          message: error instanceof Error ? error.message : "Could not load dashboard state.",
        });
      });
    const stream = createStateStream(
      (state) => {
        if (active) updateState(state);
      },
      () => {
        if (active) setConnection("offline");
      },
    );
    return () => {
      active = false;
      stream.close();
    };
  }, [updateState]);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(min-width: 1100px)");
    const updateViewport = () => setWideViewport(mediaQuery.matches);
    updateViewport();
    mediaQuery.addEventListener("change", updateViewport);
    return () => mediaQuery.removeEventListener("change", updateViewport);
  }, []);

  useEffect(() => {
    if (!data || startDefaultsLoaded) return;
    setStartSymbols(data.settings.symbols);
    setStartTimeframes(data.settings.timeframes);
    setStartDryRun(data.settings.dry_run);
    setStartDefaultsLoaded(true);
  }, [data, startDefaultsLoaded]);

  useEffect(() => {
    if (!data) return;
    for (const operation of data.operations) {
      if (operation.status !== "failed" || notifiedFailures.current.has(operation.operation_id)) continue;
      notifiedFailures.current.add(operation.operation_id);
      setNotice({ kind: "error", message: operation.error ?? "Monitor action failed." });
    }
    for (const run of data.runs) {
      if (run.status !== "failed" || notifiedFailures.current.has(run.run_id)) continue;
      notifiedFailures.current.add(run.run_id);
      const event = run.events.find((item) => /fail|error/i.test(`${item.type} ${item.message}`));
      setNotice({
        kind: "error",
        message: event?.message ?? `Run ${run.run_id} failed. Inspect system events for details.`,
      });
    }
  }, [data]);

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(
      () => setNotice(null),
      notice.kind === "error" ? 8000 : 4500,
    );
    return () => window.clearTimeout(timeout);
  }, [notice]);

  const runs = data?.runs ?? EMPTY_RUNS;
  const activeRunIds = data?.active_run_ids ?? [];
  const entryWatchers = data?.entry_watchers ?? [];
  const dryRunEnabled = data?.settings.dry_run ?? true;
  const killSwitchEnabled = data?.settings.kill_switch ?? true;
  const selectedRun = runs.find((run) => run.run_id === selectedRunId) ?? null;
  const selectedEntryWatcher = selectedRun
    ? entryWatchers.find((watcher) => watcher.run_id === selectedRun.run_id)
    : null;
  const selectedBatchId = selectedRun?.config.dashboard_batch_id;
  const groupedRuns =
    runGroupIds.length > 1
      ? runGroupIds
          .map((runId) => runs.find((run) => run.run_id === runId))
          .filter((run): run is Run => run !== undefined)
      : typeof selectedBatchId === "string"
        ? runs.filter((run) => run.config.dashboard_batch_id === selectedBatchId)
        : [];
  const activeAttempt = latestAttempt(selectedRun);
  const tokensForRun = selectedRun?.steps.reduce((sum, step) => sum + step.tokens_used, 0) ?? 0;
  const runSymbols = useMemo(
    () => [...new Set([...(data?.settings.symbols ?? []), ...COINS, ...runs.map((run) => run.symbol)])],
    [data?.settings.symbols, runs],
  );
  const visibleRuns = runs.filter(
    (run) => `${run.symbol} ${run.run_id}`.toLowerCase().includes(search.trim().toLowerCase()),
  );
  const openPositions = (data?.positions ?? []).filter((position) => position.status === "open");
  const filteredPositions = openPositions.filter(
    (position) => !symbolFilter || position.symbol === symbolFilter,
  );
  const executions = runs.flatMap((run) =>
    run.executions.map((execution) => ({ ...execution, symbol: run.symbol })),
  );
  const filteredExecutions = executions.filter(
    (execution) =>
      (!symbolFilter || execution.symbol === symbolFilter) &&
      (!executionStatusFilter || execution.status === executionStatusFilter),
  );
  const allEvents = data?.system_events ?? [];
  const activeAgent = AGENTS.find((agent) => agent.id === selectedAgent) ?? AGENTS[0];
  const feedSteps = useMemo(
    () =>
      (selectedRun?.steps ?? [])
        .filter((step) => feedAgentId === "all" || step.agent_name === feedAgentId)
        .sort((left, right) => left.started_at.localeCompare(right.started_at)),
    [feedAgentId, selectedRun],
  );

  const act = useCallback(
    async (
      action: string,
      payload: Record<string, unknown> = {},
      successMessage?: string,
    ): Promise<Record<string, unknown> | null> => {
      setBusyAction(action);
      setNotice(null);
      try {
        const result = await sendAction({ action, ...payload });
        setNotice({ kind: "success", message: successMessage ?? String(result.message ?? "Action completed.") });
        try {
          await fetchState().then(updateState);
        } catch {
          setConnection("offline");
          setNotice({ kind: "error", message: "Action accepted, but the dashboard could not refresh its state." });
        }
        return result;
      } catch (error) {
        const message = error instanceof Error ? error.message : "Action failed.";
        setNotice({ kind: "error", message });
        return null;
      } finally {
        setBusyAction(null);
      }
    },
    [updateState],
  );

  async function submitStart(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!startSymbols.length || !startTimeframes.length) {
      setNotice({ kind: "error", message: "Select at least one symbol and timeframe." });
      return;
    }
    if (
      !startDryRun &&
      !data?.settings.kill_switch &&
      !window.confirm("Dry-run and the kill switch are both off. This can place guarded Bybit testnet orders. Continue?")
    ) {
      return;
    }
    const payload: Record<string, unknown> = {
      symbols: startSymbols,
      timeframes: startTimeframes,
      dry_run: startDryRun,
    };
    if (accountEquity.trim()) payload.account_equity = Number(accountEquity);
    const result = await act(
      "start",
      payload,
      `${startSymbols.length} ${startSymbols.length === 1 ? "run" : "runs"} started in parallel.`,
    );
    if (result) {
      const ids = result.run_ids;
      if (Array.isArray(ids)) {
        const validIds = ids.filter((id): id is string => typeof id === "string");
        setRunGroupIds(validIds);
        if (validIds[0]) setSelectedRunId(validIds[0]);
      }
      setShowStart(false);
    }
  }

  function openStartDialog() {
    if (data) setStartDryRun(data.settings.dry_run);
    setShowStart(true);
  }

  async function toggleSetting(name: "dry_run" | "kill_switch", enabled: boolean) {
    if (!data) return;
    if (
      enabled === false &&
      !window.confirm(`Disable ${name === "dry_run" ? "dry-run protection" : "the kill switch"}?`)
    ) {
      return;
    }
    await act("toggle", {
      name,
      enabled,
      confirmed: true,
    });
  }

  async function submitStop() {
    if (!selectedRun) return;
    if (!window.confirm("Stop this run or entry watcher and enable the kill switch?")) return;
    await act("stop", { run_id: selectedRun.run_id }, "Stop requested; live entry watching will halt.");
  }

  async function submitResume() {
    if (!selectedRun) return;
    await act(
      "resume",
      { run_id: selectedRun.run_id },
      selectedEntryWatcher ? "Entry watcher resume requested." : "Resume requested.",
    );
  }

  async function submitStopAll() {
    if (!data?.active_run_ids.length) return;
    if (!window.confirm(`Stop all ${data.active_run_ids.length} active run(s) and enable the kill switch?`)) return;
    await act("stop_all");
  }

  async function submitResumeAll() {
    if (!data) return;
    const resumable = data.runs.filter(
      (run) =>
        ["failed", "stopped", "running"].includes(run.status) &&
        !activeRunIds.includes(run.run_id),
    );
    const resumableWatchers = entryWatchers.filter(
      (watcher) =>
        ["stopped", "failed"].includes(watcher.status) &&
        !data.active_run_ids.includes(watcher.run_id),
    );
    if (!resumable.length && !resumableWatchers.length) return;
    if (!window.confirm(`Resume ${resumable.length + resumableWatchers.length} inactive run(s) or watchers?`)) return;
    await act("resume_all");
  }

  const hasResumableWork =
    Boolean(data?.runs.some(
      (run) =>
        ["failed", "stopped", "running"].includes(run.status) &&
        !data.active_run_ids.includes(run.run_id),
    )) ||
    entryWatchers.some(
      (watcher) =>
        ["stopped", "failed"].includes(watcher.status) &&
        !activeRunIds.includes(watcher.run_id),
    );

  async function submitFlatten() {
    if (!selectedPositions.length) return;
    if (!window.confirm(`Flatten ${selectedPositions.length} selected position(s) on Bybit testnet?`)) {
      return;
    }
    const result = await act(
      "flatten",
      { run_ids: selectedPositions, confirmed: true },
      "Flatten request completed.",
    );
    if (result) setSelectedPositions([]);
  }

  function renderWorkflow() {
    if (!selectedRun) {
      return (
        <EmptyState
          title="No run selected"
          body="Start a research run or select one from the sidebar to inspect its agent workflow."
        />
      );
    }
    if (!feedSteps.length) {
      return (
        <div className="flex min-h-64 flex-col items-center justify-center px-6 text-center">
          <div className="mb-4 grid h-12 w-12 place-items-center rounded-full bg-surface text-muted">
            <Activity className="h-5 w-5" />
          </div>
          <p className="text-sm font-medium">No activity from this agent yet</p>
          <p className="mt-1 max-w-xs text-xs leading-5 text-muted">
            Saved inputs, tool calls, and results will appear here as the run progresses.
          </p>
        </div>
      );
    }
    return (
      <div className="space-y-1">
        {feedSteps.map((step) => {
          const agent = AGENTS.find((item) => item.id === step.agent_name);
          const isRunning = step.status === "running";
          const tools = step.tool_calls ?? [];
          return (
            <article key={step.step_id} className="group flex gap-3 rounded-2xl px-3 py-4 transition-colors hover:bg-surface/70 sm:gap-4 sm:px-4">
              <PersonaAvatar name={agent?.name ?? agentName(step.agent_name)} src={agent?.persona} size={40} />
              <div className="min-w-0 flex-1">
                <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-sm font-semibold">{agent?.name ?? agentName(step.agent_name)}</span>
                  <span className="text-xs text-muted-foreground">{isRunning ? "is working" : step.status === "failed" ? "encountered an error" : "finished a step"}</span>
                  {isRunning && <span className="h-1.5 w-1.5 rounded-full bg-accent agent-live" aria-label="In progress" />}
                  <time className="ml-auto text-[11px] text-muted-foreground" title={dateLabel(step.finished_at ?? step.started_at)}>{clockLabel(step.finished_at ?? step.started_at)}</time>
                </header>
                <p className={cn("mt-2 text-sm leading-6", step.error ? "text-negative" : "text-foreground/90")}>
                  {stepSummary(step, selectedRun.symbol)}
                </p>
                {!!tools.length && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {tools.map((tool, index) => (
                      <span key={`${step.step_id}-tool-${index}`} className="rounded-full bg-surface px-2.5 py-1 text-[11px] text-muted">
                        Used {textValue(tool.name ?? tool.tool_name, "tool")}
                      </span>
                    ))}
                  </div>
                )}
                <details className="group mt-3 max-w-3xl">
                  <summary className="inline-flex cursor-pointer list-none items-center gap-2 rounded-full px-2.5 py-1.5 text-[11px] text-muted transition-colors hover:bg-foreground/[0.05] hover:text-foreground">
                    <ChevronRight className="h-3 w-3 transition-transform group-open:rotate-90" />
                    Step context and evidence
                  </summary>
                  <div className="mt-2 space-y-2 rounded-xl bg-surface p-3">
                    <RawSection title="Input context" value={step.input} />
                    {!!tools.length && <RawSection title={`Tool calls (${tools.length})`} value={tools} />}
                    <RawSection title="Saved output" value={step.output} />
                    {step.error && <RawSection title="Error" value={{ status: step.status, error: step.error }} />}
                    <p className="px-1 text-[10px] text-muted-foreground">
                      {step.tokens_used.toLocaleString()} tokens · {money(step.cost, 4)} · {formatDuration(step.started_at, step.finished_at)}
                    </p>
                  </div>
                </details>
              </div>
            </article>
          );
        })}
      </div>
    );
  }

  function renderAgentDetail() {
    const attempts = selectedRun?.steps.filter((step) => step.agent_name === selectedAgent) ?? [];
    const latest = attempts.at(-1) ?? null;
    const stepOutput = latest?.output ?? {};
    const isCritique = selectedAgent === "critique";
    const decision = activeAttempt?.decision;
    const executionResult = isObject(stepOutput.execution_result) ? stepOutput.execution_result : {};
    const liveSignal = isObject(stepOutput.live_signal)
      ? stepOutput.live_signal
      : isObject(executionResult.live_signal)
        ? executionResult.live_signal
        : {};
    const executionOrders = objectRows(executionResult.orders ?? stepOutput.orders);
    const orderWasPlaced = executionOrders.some(
      (order) =>
        Boolean(order.id ?? order.exchange_order_id) &&
        order.status !== "dry_run",
    );
    const actions = latest?.tool_calls ?? [];
    const toolEvidence = actions.map((call) => ({
      ...call,
      result_snippet: JSON.stringify(stepOutput).slice(0, 500),
    }));
    if (!selectedRun) {
      return <EmptyState title="No agent details" body="Choose a run to inspect its inputs, tool calls, output, and errors." />;
    }
    return (
      <div className="space-y-4">
        <Card className="p-5">
          <div className="flex flex-wrap items-center gap-4">
            <PersonaAvatar name={activeAgent.name} src={activeAgent.persona} size={60} />
            <div className="min-w-0 flex-1">
              <p className="text-lg font-semibold">{activeAgent.name}</p>
              <p className="mt-1 text-xs text-muted">{selectedRun.symbol} · {selectedRun.run_id}</p>
            </div>
            <StatusBadge status={latest?.status ?? agentStatus(selectedRun, selectedAgent, data?.operations ?? [])} />
            {isCritique && decision && <StatusBadge status={decision} />}
          </div>
          {latest ? (
            <div className="mt-5 grid gap-3 md:grid-cols-3">
              <Metric label="Tokens" value={latest.tokens_used.toLocaleString()} />
              <Metric label="Recorded cost" value={money(latest.cost, 4)} />
              <Metric label="Duration" value={formatDuration(latest.started_at, latest.finished_at)} />
            </div>
          ) : (
            <p className="mt-5 rounded-xl border border-border p-4 text-xs text-muted">
              No saved attempt for this agent in the selected run.
            </p>
          )}
        </Card>
        {latest && (
          <>
            <Card className="space-y-3 p-5">
              <CardHeader>
                <div>
                  <CardTitle>Step evidence</CardTitle>
                  <CardDescription>Expand each section to inspect the exact persisted JSON.</CardDescription>
                </div>
              </CardHeader>
              <RawSection title="Current / last input summary" value={latest.input} />
              <RawSection title={`Tools called (${actions.length})`} value={toolEvidence} />
              <RawSection title="Structured output" value={stepOutput} defaultOpen />
              {latest.error && <RawSection title="Errors / retries" value={{ error: latest.error, status: latest.status }} defaultOpen />}
            </Card>
            {selectedAgent === "strategy_builder" && activeAttempt && (
              <Card className="p-5">
                <CardTitle>Strategy template</CardTitle>
                <CardDescription className="mt-1">
                  {textValue(activeAttempt.strategy.template_name, "No template recorded")}
                </CardDescription>
                <div className="mt-4"><RawSection title="Template parameters" value={activeAttempt.strategy} defaultOpen /></div>
              </Card>
            )}
            {selectedAgent === "critique" && activeAttempt && (
              <Card className="p-5">
                <CardTitle>Critique decision</CardTitle>
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <Badge className={cn("rounded-xl px-5 py-3 text-sm", statusStyle(textValue(activeAttempt.decision, "unknown")))}>
                    {textValue(activeAttempt.decision, "No decision")}
                  </Badge>
                  {activeAttempt.revision_instructions && (
                    <span className="max-w-2xl text-xs leading-5 text-muted">{activeAttempt.revision_instructions}</span>
                  )}
                </div>
              </Card>
            )}
            {selectedAgent === "execution" && (
              <Card className="p-5">
                <CardTitle>Live signal & order outcome</CardTitle>
                <CardDescription className="mt-1">
                  {textValue(liveSignal.reason, "No live signal reason was recorded.")}
                </CardDescription>
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <StatusBadge status={textValue(liveSignal.action, textValue(executionResult.status, "unknown"))} />
                  <Badge className={orderWasPlaced ? "border-positive/20 bg-positive/10 text-positive" : "border-border bg-background text-muted"}>
                    {orderWasPlaced ? "Order placed" : "No exchange order placed"}
                  </Badge>
                </div>
              </Card>
            )}
          </>
        )}
      </div>
    );
  }

  function renderStrategy() {
    const attempt = activeAttempt;
    if (!selectedRun || !attempt) {
      return <EmptyState title="No strategy or backtest yet" body="Strategy, critique, and backtest data appear when the framework saves an attempt." />;
    }
    const report = attempt.report ?? {};
    const trades = objectRows(report.trades);
    const curve = (attempt.equity_curve ?? objectRows(report.equity_curve)).map((point, index) => ({
      index: index + 1,
      equity: numberValue(point.equity ?? point.value),
      label: textValue(point.timestamp ?? point.time, String(index + 1)),
    }));
    const strategyName = textValue(attempt.strategy.name, "Unnamed strategy");
    const metrics: Array<{ label: string; value: string; tone?: "positive" | "negative" }> = [
      { label: "Net PnL", value: money(report.net_pnl), tone: numberValue(report.net_pnl) >= 0 ? "positive" : "negative" },
      { label: "Max drawdown", value: `${(numberValue(report.max_drawdown_pct) * 100).toFixed(2)}%` },
      { label: "Win rate", value: `${(numberValue(report.win_rate) * 100).toFixed(2)}%` },
      { label: "Total trades", value: String(numberValue(report.total_trades)) },
      { label: "Total fees", value: money(report.total_fees) },
      { label: "Total slippage", value: money(report.total_slippage) },
    ];
    return (
      <div className="space-y-4">
        <Card className="p-5">
          <CardHeader>
            <div>
              <CardTitle>{strategyName}</CardTitle>
              <CardDescription>
                {textValue(attempt.strategy.template_name, "Unknown template")} · version {attempt.version} · {selectedRun.symbol}
              </CardDescription>
            </div>
            {attempt.decision && <StatusBadge status={attempt.decision} />}
          </CardHeader>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {metrics.map((metric) => <Metric key={metric.label} {...metric} />)}
          </div>
        </Card>
        <Card className="p-5">
          <CardHeader>
            <div>
              <CardTitle>Equity curve</CardTitle>
              <CardDescription>Backtest history from the selected strategy attempt.</CardDescription>
            </div>
            <TrendingUp className="h-4 w-4 text-muted" />
          </CardHeader>
          {curve.length ? (
            <div className="mt-5 h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={curve} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="#27272a" vertical={false} />
                  <XAxis dataKey="index" tick={{ fill: "#71717a", fontSize: 10 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: "#71717a", fontSize: 10 }} axisLine={false} tickLine={false} width={66} tickFormatter={(value: number) => money(value, 0)} />
                  <Tooltip
                    contentStyle={{ background: "#141418", border: "1px solid #27272a", borderRadius: 12, color: "#fafafa", fontSize: 12 }}
                    formatter={(value: number) => money(value)}
                    labelFormatter={(value) => curve[Number(value) - 1]?.label ?? `Point ${value}`}
                  />
                  <Area type="monotone" dataKey="equity" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.12} strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="mt-5"><EmptyState title="No equity curve saved" body="The backtest did not include curve points." /></div>
          )}
        </Card>
        <Card className="p-5">
          <CardHeader>
            <div>
              <CardTitle>Strategy definition</CardTitle>
              <CardDescription>Chosen library template and persisted parameters.</CardDescription>
            </div>
          </CardHeader>
          <div className="mt-4"><RawSection title="Strategy JSON" value={attempt.strategy} /></div>
          <div className="mt-3"><RawSection title="Backtest JSON" value={attempt.report} /></div>
          <div className="mt-4 flex flex-wrap gap-4">
            {[
              { label: "StrategyBuilder", agent: "strategy_builder" },
              { label: "Backtest", agent: "backtest" },
            ].map((item) => (
              <button
                key={item.agent}
                type="button"
                className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                onClick={() => {
                  setSelectedAgent(item.agent);
                  setTab("agent");
                }}
              >
                View {item.label} step <ArrowRight className="h-3 w-3" />
              </button>
            ))}
          </div>
          {attempt.decision && (
            <div className="mt-4 rounded-xl border border-border p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium">Critique</span>
                <StatusBadge status={attempt.decision} />
              </div>
              {attempt.issues?.length ? (
                <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-muted">
                  {attempt.issues.map((issue, index) => <li key={`${issue}-${index}`}>{issue}</li>)}
                </ul>
              ) : <p className="mt-2 text-xs text-muted">No critique issues recorded.</p>}
              {attempt.revision_instructions && <p className="mt-3 text-xs leading-5 text-muted">{attempt.revision_instructions}</p>}
              <button
                type="button"
                className="mt-3 inline-flex items-center gap-1 text-xs text-accent hover:underline"
                onClick={() => {
                  setSelectedAgent("critique");
                  setTab("agent");
                }}
              >
                View critique agent step <ArrowRight className="h-3 w-3" />
              </button>
            </div>
          )}
        </Card>
        <Card className="p-5">
          <CardHeader>
            <div>
              <CardTitle>Trade list</CardTitle>
              <CardDescription>Gross and net outcomes with modeled fees and slippage.</CardDescription>
            </div>
          </CardHeader>
          {trades.length ? (
            <div className="mt-5 overflow-x-auto">
              <table className="w-full min-w-[740px] text-left text-xs">
                <thead className="text-[10px] uppercase tracking-wide text-muted">
                  <tr>
                    {["#", "Entry", "Exit", "Gross PnL", "Fees", "Slippage", "Net PnL", "Exit reason"].map((heading) => <th key={heading} className="pb-3 pr-4">{heading}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {trades.map((trade, index) => (
                    <tr key={index} className="border-t border-border text-muted">
                      <td className="py-3 pr-4">{index + 1}</td>
                      <td className="py-3 pr-4">{dateLabel(textValue(trade.entry_time, ""))}</td>
                      <td className="py-3 pr-4">{dateLabel(textValue(trade.exit_time, ""))}</td>
                      <td className={cn("py-3 pr-4", numberValue(trade.gross_pnl) >= 0 ? "text-positive" : "text-negative")}>{money(trade.gross_pnl)}</td>
                      <td className="py-3 pr-4">{money(trade.fees)}</td>
                      <td className="py-3 pr-4">{money(trade.slippage)}</td>
                      <td className={cn("py-3 pr-4", numberValue(trade.net_pnl) >= 0 ? "text-positive" : "text-negative")}>{money(trade.net_pnl)}</td>
                      <td className="py-3">{textValue(trade.exit_reason)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="mt-5"><EmptyState title="No trades recorded" body="This strategy attempt has no completed backtest trades." /></div>
          )}
        </Card>
      </div>
    );
  }

  function renderPositions() {
    const rows = filteredPositions;
    const availableSymbols = [...new Set([
      ...openPositions.map((position) => position.symbol),
      ...executions.map((execution) => execution.symbol),
    ])];
    const allSelected = rows.length > 0 && rows.every((position) => selectedPositions.includes(position.run_id));
    const executionsWithIds = executions.flatMap((execution) =>
      execution.orders
        .filter((order) => Boolean(order.id ?? order.exchange_order_id))
        .map((order) => ({ execution, order })),
    );
    return (
      <div className="space-y-4">
        <Card className="p-5">
          <CardHeader>
            <div>
              <CardTitle>Open positions <span className="ml-1 text-muted">({openPositions.length})</span></CardTitle>
              <CardDescription>Persisted positions from the framework monitor.</CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select
                className="min-h-9 rounded-xl border border-border bg-background px-3 text-xs text-foreground"
                value={symbolFilter}
                onChange={(event) => setSymbolFilter(event.target.value)}
                aria-label="Filter positions by symbol"
              >
                <option value="">All symbols</option>
                {availableSymbols.map((symbol) => <option key={symbol}>{symbol}</option>)}
              </select>
              <Button variant="outline" size="sm" onClick={() => act("monitor", {}, "Monitor cycle started.")} disabled={busyAction === "monitor"}>
                {busyAction === "monitor" ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Force monitor cycle
              </Button>
              <Button variant="danger" size="sm" onClick={submitFlatten} disabled={!selectedPositions.length || busyAction === "flatten" || data?.settings.dry_run}>
                <Square className="h-3.5 w-3.5" />
                Flatten selected
              </Button>
            </div>
          </CardHeader>
          {data?.settings.dry_run && (
            <p className="mt-4 rounded-xl border border-accent/20 bg-accent/5 px-3 py-2 text-xs text-muted">
              Dry-run is on. Disable it before requesting a live position flatten.
            </p>
          )}
          {rows.length ? (
            <div className="mt-5 overflow-x-auto">
              <table className="w-full min-w-[900px] text-left text-xs">
                <thead className="text-[10px] uppercase tracking-wide text-muted">
                  <tr>
                    <th className="pb-3 pr-3"><input type="checkbox" checked={allSelected} onChange={(event) => setSelectedPositions(event.target.checked ? rows.map((row) => row.run_id) : [])} aria-label="Select all positions" /></th>
                    <th className="pb-3 pr-4">Symbol</th>
                    <th className="pb-3 pr-4">Side / size</th>
                    <th className="pb-3 pr-4">Entry / stop</th>
                    <th className="pb-3 pr-4">Strategy</th>
                    <th className="pb-3 pr-4">Edge fade</th>
                    <th className="pb-3 pr-4">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((position) => {
                    const edgeFade = position.events.some((event) => event.type === "edge_fade_exit");
                    return (
                      <tr key={position.run_id} className="border-t border-border text-muted">
                        <td className="py-3 pr-3"><input type="checkbox" checked={selectedPositions.includes(position.run_id)} onChange={() => toggleSelection(position.run_id, selectedPositions, setSelectedPositions)} aria-label={`Select ${position.symbol} position`} /></td>
                        <td className="py-3 pr-4"><span className="flex items-center gap-2 text-foreground"><CoinMark symbol={position.symbol} small />{position.symbol}</span></td>
                        <td className="py-3 pr-4"><span className="capitalize text-foreground">{position.side}</span><span className="ml-2 tabular-nums">{position.amount}</span></td>
                        <td className="py-3 pr-4 tabular-nums">{money(position.entry_price)} <span className="text-muted">/ {money(position.stop_price)}</span></td>
                        <td className="py-3 pr-4">{textValue(position.strategy.name ?? position.strategy_key)}</td>
                        <td className="py-3 pr-4">{edgeFade ? <Badge className="border-negative/20 bg-negative/10 text-negative">Detected</Badge> : "—"}</td>
                        <td className="py-3 pr-4"><StatusBadge status={position.status} /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="mt-5"><EmptyState title="No open positions" body="Open positions saved by the framework will appear here. This panel never calls an exchange from the browser." /></div>
          )}
          {rows.length > 0 && <div className="mt-4"><RawSection title="Open position JSON" value={rows} /></div>}
        </Card>
        <Card className="p-5">
          <CardHeader>
            <div>
              <CardTitle>Historical executions</CardTitle>
              <CardDescription>Saved order payloads, outcomes, and related runs.</CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <select
                className="min-h-9 rounded-xl border border-border bg-background px-3 text-xs text-foreground"
                value={executionStatusFilter}
                onChange={(event) => setExecutionStatusFilter(event.target.value)}
                aria-label="Filter executions by status"
              >
                <option value="">All statuses</option>
                {[...new Set(executions.map((execution) => execution.status))].map((status) => <option key={status}>{status}</option>)}
              </select>
              <Filter className="h-4 w-4 text-muted" />
            </div>
          </CardHeader>
          {!executionsWithIds.length && (
            <p className="mt-4 rounded-xl border border-border px-3 py-2 text-xs text-muted">
              No real order IDs exist yet. Saved dry-run records remain visible below.
            </p>
          )}
          {filteredExecutions.length ? (
            <div className="mt-5 overflow-x-auto">
              <table className="w-full min-w-[750px] text-left text-xs">
                <thead className="text-[10px] uppercase tracking-wide text-muted">
                  <tr>{["Order ID", "Symbol", "Status", "Details", "Run", "Time"].map((heading) => <th key={heading} className="pb-3 pr-4">{heading}</th>)}</tr>
                </thead>
                <tbody>
                  {filteredExecutions.map((execution) =>
                    (execution.orders.length ? execution.orders : [{}]).map((order, index) => (
                      <tr key={`${execution.execution_id}-${index}`} className="border-t border-border text-muted">
                        <td className="py-3 pr-4 font-mono text-foreground">{textValue(order.id ?? order.exchange_order_id, execution.status === "dry_run" ? "dry_run" : "—")}</td>
                        <td className="py-3 pr-4">{execution.symbol}</td>
                        <td className="py-3 pr-4"><StatusBadge status={String(order.status ?? execution.status)} /></td>
                        <td className="py-3 pr-4">{textValue(order.side)} {order.filled ? `· ${String(order.filled)} filled` : ""}</td>
                        <td className="max-w-40 truncate py-3 pr-4 font-mono">{execution.run_id}</td>
                        <td className="py-3">{orderTime(order.timestamp)}</td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </div>
          ) : executions.length ? (
            <div className="mt-5"><EmptyState title="No executions match these filters" body="Change the symbol or status filter to see saved executions." /></div>
          ) : (
            <div className="mt-5"><EmptyState title="No executions saved" body="Framework order attempts will appear here when an execution step is recorded." /></div>
          )}
          {executions.length > 0 && <div className="mt-4"><RawSection title="Execution JSON" value={executions} /></div>}
        </Card>
      </div>
    );
  }

  function renderEvents() {
    if (!allEvents.length) {
      return <EmptyState title="No system events" body="Errors, retries, monitor actions, and run-level system events will appear here." />;
    }
    return (
      <div className="relative space-y-3 before:absolute before:bottom-4 before:left-[15px] before:top-4 before:w-px before:bg-border">
        {allEvents.map((event) => <EventCard key={event.event_id} event={event} />)}
      </div>
    );
  }

  function renderResearchContext() {
    const curve = (activeAttempt?.equity_curve ?? objectRows(activeAttempt?.report?.equity_curve)).map(
      (point, index) => ({
        index: index + 1,
        equity: numberValue(point.equity ?? point.value),
      }),
    );
    const position = openPositions.find((item) => item.run_id === selectedRun?.run_id);
    const activeStep = selectedRun?.steps.find((step) => step.status === "running");
    const activeDefinition = activeStep
      ? AGENTS.find((agent) => agent.id === activeStep.agent_name)
      : null;
    const entryWatcher = selectedRun
      ? data?.entry_watchers?.find((watcher) => watcher.run_id === selectedRun.run_id)
      : null;

    return (
      <div className="space-y-8">
        <LiveMarketChart
          symbol={selectedRun?.symbol ?? data?.settings.symbols[0] ?? "BTC/USDT"}
          enabled={wideViewport === true}
        />
        {entryWatcher && (
          <section aria-label="Entry watcher status">
            <div className="flex items-center justify-between gap-3">
              <p className="text-[11px] font-medium text-muted">Entry watcher</p>
              <StatusBadge status={entryWatcher.status} />
            </div>
            <p className="mt-2 text-xs leading-5 text-muted">
              {entryWatcher.status === "watching"
                ? `Checking for an entry every ${entryWatcher.interval_seconds} seconds.`
                : entryWatcher.status === "paused"
                  ? "Paused while dry-run or the kill switch is enabled."
                  : entryWatcher.error ?? "No entry checks are active."}
            </p>
            {entryWatcher.signal && (
              <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
                Latest signal: {textValue(entryWatcher.signal.action, "unknown")} · {textValue(entryWatcher.signal.reason, "No reason recorded")}
              </p>
            )}
            {entryWatcher.last_checked_at && (
              <p className="mt-1 text-[10px] text-muted-foreground">
                Last checked {dateLabel(entryWatcher.last_checked_at)}
              </p>
            )}
            {entryWatcher.error && entryWatcher.status === "watching" && (
              <p role="status" className="mt-2 text-[10px] leading-4 text-negative">
                Latest check failed: {entryWatcher.error}
              </p>
            )}
          </section>
        )}
        <section>
          <div className="flex items-center justify-between">
            <p className="text-[11px] font-medium text-muted">Research snapshot</p>
            <button type="button" onClick={() => setTab("strategy")} className="text-[11px] text-muted transition-colors hover:text-foreground">
              Full report
            </button>
          </div>
          {selectedRun ? (
            <>
              <div className="mt-4 flex items-center gap-3">
                <CoinMark symbol={selectedRun.symbol} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold">{selectedRun.symbol}</p>
                  <p className="truncate font-mono text-[10px] text-muted-foreground" title={selectedRun.run_id}>{selectedRun.run_id}</p>
                </div>
                <StatusBadge status={selectedRun.status} />
              </div>
              <div className="mt-6 flex items-end justify-between gap-3">
                <div>
                  <p className="text-[11px] text-muted">Backtest equity</p>
                  <p className="mt-1 text-lg font-semibold tabular-nums">
                    {activeAttempt ? money(activeAttempt.report?.net_pnl) : "—"}
                  </p>
                </div>
                {activeAttempt?.decision && <StatusBadge status={activeAttempt.decision} />}
              </div>
              <div className="mt-3 h-28">
                {curve.length > 1 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={curve} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
                      <Tooltip
                        contentStyle={{ background: "#141418", border: "1px solid #27272a", borderRadius: 10, color: "#fafafa", fontSize: 11 }}
                        formatter={(value: number) => money(value)}
                        labelFormatter={(value) => `Point ${value}`}
                      />
                      <Area type="monotone" dataKey="equity" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.1} strokeWidth={1.75} />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="flex h-full items-center justify-center rounded-xl bg-surface text-center text-[11px] text-muted">
                    {selectedRun.status === "running" ? "Equity curve will appear after backtesting" : "No backtest curve saved"}
                  </div>
                )}
              </div>
              <div className="mt-4 flex items-center justify-between gap-3 text-xs">
                <span className="truncate text-muted">{activeAttempt ? textValue(activeAttempt.strategy.template_name, "Strategy candidate") : "Strategy not built yet"}</span>
                <span className="shrink-0 text-muted-foreground">{activeAttempt ? `v${activeAttempt.version}` : ""}</span>
              </div>
            </>
          ) : (
            <p className="mt-4 text-xs leading-5 text-muted">Select a run to see its trading context.</p>
          )}
        </section>

        <section>
          <div className="flex items-center justify-between">
            <p className="text-[11px] font-medium text-muted">Open position</p>
            <button type="button" onClick={() => setTab("positions")} className="text-[11px] text-muted transition-colors hover:text-foreground">
              All positions
            </button>
          </div>
          {position ? (
            <div className="mt-4 space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium">{position.side} · {position.amount}</span>
                <StatusBadge status={position.status} />
              </div>
              <dl className="grid grid-cols-2 gap-y-3 text-xs">
                <dt className="text-muted">Entry</dt><dd className="text-right tabular-nums">{money(position.entry_price)}</dd>
                <dt className="text-muted">Stop</dt><dd className="text-right tabular-nums">{money(position.stop_price)}</dd>
                <dt className="text-muted">Strategy</dt><dd className="truncate text-right">{textValue(position.strategy_key)}</dd>
                <dt className="text-muted">Net PnL</dt>
                <dd className={cn("text-right tabular-nums", numberValue(position.net_pnl) >= 0 ? "text-positive" : "text-negative")}>{money(position.net_pnl)}</dd>
              </dl>
            </div>
          ) : (
            <p className="mt-4 text-xs leading-5 text-muted">No open position on this run.</p>
          )}
        </section>

        <section>
          <p className="text-[11px] font-medium text-muted">Current activity</p>
          {activeStep ? (
            <div className="mt-4 flex items-start gap-3">
              <PersonaAvatar name={activeDefinition?.name ?? agentName(activeStep.agent_name)} src={activeDefinition?.persona} size={36} />
              <div className="min-w-0">
                <p className="text-xs font-medium">{activeDefinition?.name ?? agentName(activeStep.agent_name)}</p>
                <p className="mt-1 text-xs leading-5 text-muted">{agentWorkingLabel(activeStep.agent_name, selectedRun?.symbol ?? "")}</p>
              </div>
            </div>
          ) : (
            <p className="mt-4 text-xs leading-5 text-muted">
              {selectedRun?.status === "completed" ? "All run steps have completed." : "No agent is currently working."}
            </p>
          )}
        </section>
      </div>
    );
  }

  function renderConversationSkeleton() {
    return (
      <div className="space-y-7" aria-label="Loading agent activity">
        {[0, 1, 2].map((item) => (
          <div key={item} className="flex gap-4">
            <div className="skeleton h-10 w-10 shrink-0 rounded-full" />
            <div className="min-w-0 flex-1 space-y-3 pt-1">
              <div className="skeleton h-3 w-36 rounded-full" />
              <div className="skeleton h-3 w-full max-w-xl rounded-full" />
              <div className="skeleton h-3 w-3/4 max-w-lg rounded-full" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background lg:grid lg:grid-cols-[248px_minmax(0,1fr)] min-[1100px]:grid-cols-[248px_minmax(0,1fr)_300px]">
      <aside className="hidden h-screen flex-col bg-surface px-3 py-4 lg:sticky lg:top-0 lg:flex">
        <div className="flex items-center justify-between px-2">
          <div className="flex items-center gap-3">
            <div className="grid h-8 w-8 place-items-center rounded-full bg-accent text-white">
              <Activity className="h-4 w-4" />
            </div>
            <div>
              <p className="text-sm font-semibold tracking-tight">MATS</p>
              <p className="text-[11px] text-muted">Trading workspace</p>
            </div>
          </div>
          <span className={cn("h-2 w-2 rounded-full", connection === "online" ? "bg-positive" : connection === "offline" ? "bg-negative" : "bg-muted agent-live")} title={`API ${connection}`} />
        </div>

        <div className="mt-7 flex min-h-0 flex-1 flex-col">
          <div className="flex items-center justify-between px-2">
            <p className="text-[11px] font-medium text-muted">Agents</p>
            <span className="text-[10px] text-muted-foreground">{AGENTS.length}</span>
          </div>
          <button
            type="button"
            onClick={() => setFeedAgentId("all")}
            className={cn("mt-2 flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left transition-colors", feedAgentId === "all" ? "bg-card text-foreground" : "text-muted hover:bg-card/70 hover:text-foreground")}
          >
            <span className="grid h-8 w-8 place-items-center rounded-full bg-card-hover text-muted"><Activity className="h-4 w-4" /></span>
            <span className="min-w-0 flex-1 text-xs font-medium">All activity</span>
            <span className="text-[10px] text-muted-foreground">{selectedRun?.steps.length ?? 0}</span>
          </button>
          <div className="mt-1 space-y-0.5 overflow-y-auto">
            {AGENTS.map((agent) => {
              const status = agentStatus(selectedRun, agent.id, data?.operations ?? []);
              const isWorking = status === "running";
              const statusLabel = isWorking ? "Working" : status === "completed" ? "Complete" : status === "failed" || status === "rejected" ? "Needs attention" : agent.role;
              return (
                <button
                  type="button"
                  key={agent.id}
                  onClick={() => {
                    setFeedAgentId(agent.id);
                    setSelectedAgent(agent.id);
                    setTab("graph");
                  }}
                  className={cn("flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left transition-colors", feedAgentId === agent.id ? "bg-card text-foreground" : "text-muted hover:bg-card/70 hover:text-foreground")}
                >
                  <span className="relative">
                    <PersonaAvatar name={agent.name} src={agent.persona} size={32} />
                    {isWorking && <span className="absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full bg-accent ring-2 ring-surface agent-live" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium">{agent.name}</span>
                    <span className={cn("mt-0.5 block text-[10px]", isWorking ? "text-accent" : "text-muted-foreground")}>{statusLabel}</span>
                  </span>
                  <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", isWorking ? "bg-accent agent-live" : status === "failed" || status === "rejected" ? "bg-negative" : status === "completed" ? "bg-positive" : "bg-muted-foreground")} />
                </button>
              );
            })}
          </div>

          <div className="mt-7 flex min-h-0 flex-1 flex-col">
            <div className="flex items-center justify-between px-2">
              <p className="text-[11px] font-medium text-muted">Recent runs</p>
              <span className="text-[10px] text-muted-foreground">{runs.length}</span>
            </div>
            <div className="relative mt-2">
              <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
              <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Find a run" className="h-9 min-h-9 rounded-lg border-transparent bg-card pl-9 text-xs focus-visible:border-accent" />
            </div>
            <div className="mt-2 min-h-0 flex-1 space-y-0.5 overflow-y-auto">
              {visibleRuns.slice(0, 30).map((run) => (
                <button
                  key={run.run_id}
                  type="button"
                  onClick={() => {
                    setSelectedRunId(run.run_id);
                    setRunGroupIds([]);
                    setFeedAgentId("all");
                    setTab("graph");
                  }}
                  className={cn("w-full rounded-xl px-2.5 py-2.5 text-left transition-colors", selectedRunId === run.run_id ? "bg-card text-foreground" : "text-muted hover:bg-card/70 hover:text-foreground")}
                >
                  <span className="flex items-center gap-2.5">
                    <CoinMark symbol={run.symbol} small />
                    <span className="min-w-0 flex-1 truncate text-xs font-medium">{run.symbol}</span>
                    <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", run.status === "running" ? "bg-accent agent-live" : run.status === "failed" ? "bg-negative" : run.status === "completed" ? "bg-positive" : "bg-muted-foreground")} />
                  </span>
                  <span className="mt-1.5 block pl-10 text-[10px] text-muted-foreground">
                    {run.status.replaceAll("_", " ")} · {dateLabel(run.started_at)}
                  </span>
                </button>
              ))}
              {!runs.length && <p className="px-2 py-4 text-xs leading-5 text-muted">Your new research runs will appear here.</p>}
              {!!runs.length && !visibleRuns.length && <p className="px-2 py-4 text-xs text-muted">No runs match that search.</p>}
            </div>
          </div>
        </div>

        <div className="mt-4 space-y-1 pt-3">
          <Button className="w-full justify-center rounded-full" variant="primary" onClick={openStartDialog}>
            <Plus className="h-4 w-4" /> New run
          </Button>
          <div className="mt-2 flex gap-1">
            <Button size="sm" className="flex-1 rounded-full" variant="ghost" disabled={!selectedRun || !data?.active_run_ids.includes(selectedRun.run_id) || busyAction === "stop"} onClick={submitStop} aria-label="Stop selected run or entry watcher">
              <Pause className="h-3.5 w-3.5" /> Stop
            </Button>
            <Button size="sm" className="flex-1 rounded-full" variant="ghost" disabled={!selectedRun || (!["failed", "stopped", "running"].includes(selectedRun.status) && !["stopped", "failed"].includes(selectedEntryWatcher?.status ?? "")) || Boolean(data?.active_run_ids.includes(selectedRun.run_id)) || busyAction === "resume"} onClick={submitResume} aria-label="Resume selected run or entry watcher">
              <Play className="h-3.5 w-3.5" />{selectedEntryWatcher && ["stopped", "failed"].includes(selectedEntryWatcher.status) ? " Resume watcher" : " Resume"}
            </Button>
            <Button size="sm" className="flex-1 rounded-full" variant="ghost" disabled={busyAction === "monitor"} onClick={() => act("monitor", {}, "Monitor cycle started.")} aria-label="Run monitor cycle">
              <RefreshCw className="h-3.5 w-3.5" /> Monitor
            </Button>
          </div>
          <div className="mt-3 space-y-0.5 px-1">
            <ToggleRow label="Dry run" enabled={data?.settings.dry_run ?? true} icon={<ShieldCheck className="h-3.5 w-3.5" />} onChange={(enabled) => toggleSetting("dry_run", enabled)} />
            <ToggleRow label="Kill switch" enabled={data?.settings.kill_switch ?? true} icon={<LockKeyhole className="h-3.5 w-3.5" />} onChange={(enabled) => toggleSetting("kill_switch", enabled)} />
          </div>
          <div className="flex justify-between px-2 pt-1">
            <button type="button" onClick={submitStopAll} disabled={!data?.active_run_ids.length || busyAction === "stop_all"} className="text-[10px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40">Stop all</button>
            <button type="button" onClick={submitResumeAll} disabled={!hasResumableWork || busyAction === "resume_all"} className="text-[10px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40">Resume inactive</button>
          </div>
        </div>
      </aside>

      <main className="min-w-0">
        <header className="sticky top-0 z-20 bg-background/90 px-4 py-3 backdrop-blur-xl sm:px-7">
          <div className="flex min-h-10 items-center justify-between gap-4">
            <div className="flex min-w-0 items-center gap-3">
              <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent text-white lg:hidden"><Activity className="h-4 w-4" /></div>
              {selectedRun && <CoinMark symbol={selectedRun.symbol} small />}
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h1 className="truncate text-sm font-semibold">{selectedRun ? `${selectedRun.symbol} research` : "Research workspace"}</h1>
                  {selectedRun && <StatusBadge status={selectedRun.status} />}
                </div>
                <p className="truncate font-mono text-[10px] text-muted-foreground">{selectedRun?.run_id ?? "Select a run to view its activity"}</p>
              </div>
              <span className={cn("hidden h-1.5 w-1.5 rounded-full sm:block", connection === "online" ? "bg-positive" : connection === "offline" ? "bg-negative" : "bg-muted agent-live")} />
              <span className="hidden text-[11px] text-muted sm:block">{connection === "online" ? "Live updates" : connection === "offline" ? "Reconnecting" : "Connecting"}</span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button type="button" disabled={!data} onClick={() => toggleSetting("dry_run", !dryRunEnabled)} className={cn("rounded-full px-3 py-1.5 text-[10px] font-medium transition-colors hover:bg-card disabled:opacity-60", dryRunEnabled ? "bg-card text-muted" : "bg-negative/10 text-negative")} aria-label={`Toggle dry-run; currently ${dryRunEnabled ? "on" : "off"}`}>
                Dry run {dryRunEnabled ? "on" : "off"}
              </button>
              <button type="button" disabled={!data} onClick={() => toggleSetting("kill_switch", !killSwitchEnabled)} className={cn("rounded-full px-3 py-1.5 text-[10px] font-medium transition-colors hover:bg-card disabled:opacity-60", killSwitchEnabled ? "bg-card text-muted" : "bg-negative/10 text-negative")} aria-label={`Toggle kill switch; currently ${killSwitchEnabled ? "on" : "off"}`}>
                Kill switch {killSwitchEnabled ? "on" : "off"}
              </button>
              <Button variant="primary" size="sm" className="rounded-full lg:hidden" onClick={openStartDialog}><Plus className="h-3.5 w-3.5" />Run</Button>
            </div>
          </div>
        </header>

        <div className="px-4 pb-10 sm:px-7">
          {notice && (
            <div
              role={notice.kind === "error" ? "alert" : "status"}
              className={cn(
                "fixed bottom-5 right-5 z-[60] flex w-[min(26rem,calc(100vw-2.5rem))] items-start gap-3 rounded-2xl border border-border bg-card p-4 text-xs",
                notice.kind === "error"
                  ? "text-negative"
                  : notice.kind === "success"
                    ? "text-positive"
                    : "text-accent",
              )}
            >
              {notice.kind === "error" ? (
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              ) : (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
              )}
              <span className="min-w-0 flex-1 leading-5">{notice.message}</span>
              <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss notification"><X className="h-4 w-4" /></button>
            </div>
          )}

          {!data && connection !== "offline" && <div className="pt-8"><p className="mb-8 text-sm text-muted">Connecting to your research workspace…</p>{renderConversationSkeleton()}</div>}
          {!data && connection === "offline" && (
            <div className="mx-auto mt-24 max-w-md text-center">
              <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-surface text-negative"><AlertCircle className="h-5 w-5" /></div>
              <h2 className="mt-4 text-base font-semibold">Can’t reach the local API</h2>
              <p className="mt-2 text-sm leading-6 text-muted">Start the framework service with <code>uv run mats-web</code>. We’ll reconnect automatically when it’s available.</p>
              <button type="button" onClick={() => window.location.reload()} className="mt-4 rounded-full bg-card px-4 py-2 text-xs font-medium hover:bg-card-hover">Try again</button>
            </div>
          )}

          {data && (
            <>
              <div className="mb-5 min-[1100px]:hidden">
                <LiveMarketChart
                  symbol={selectedRun?.symbol ?? data.settings.symbols[0] ?? "BTC/USDT"}
                  enabled={wideViewport === false}
                />
              </div>
              <div className="mb-4 space-y-3 lg:hidden">
                <select
                  aria-label="Select research run"
                  className="min-h-10 w-full border-0 bg-surface px-3 text-xs text-foreground"
                  value={selectedRunId ?? ""}
                  onChange={(event) => {
                    setSelectedRunId(event.target.value || null);
                    setRunGroupIds([]);
                    setFeedAgentId("all");
                  }}
                >
                  <option value="">Select a research run</option>
                  {runs.map((run) => <option key={run.run_id} value={run.run_id}>{run.symbol} · {run.status}</option>)}
                </select>
                <div className="flex gap-2 overflow-x-auto pb-1">
                  <button type="button" onClick={() => setFeedAgentId("all")} className={cn("flex shrink-0 items-center gap-2 rounded-full px-3 py-1.5 text-xs", feedAgentId === "all" ? "bg-card text-foreground" : "text-muted")}>
                    All agents
                  </button>
                  {AGENTS.map((agent) => (
                    <button
                      key={agent.id}
                      type="button"
                      onClick={() => {
                        setFeedAgentId(agent.id);
                        setSelectedAgent(agent.id);
                        setTab("graph");
                      }}
                      className={cn("flex shrink-0 items-center gap-2 rounded-full px-2 py-1.5 text-xs", feedAgentId === agent.id ? "bg-card text-foreground" : "text-muted")}
                    >
                      <PersonaAvatar name={agent.name} src={agent.persona} size={24} />
                      {agent.name}
                    </button>
                  ))}
                </div>
              </div>
              <nav className="mb-5 flex gap-1 overflow-x-auto py-2" aria-label="Workspace views">
                {TABS.map(({ id, label }) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setTab(id)}
                    className={cn("shrink-0 rounded-full px-3 py-1.5 text-xs font-medium transition-colors", tab === id ? "bg-card text-foreground" : "text-muted hover:bg-card/70 hover:text-foreground")}
                  >
                    {label}
                  </button>
                ))}
                <span className="ml-auto hidden shrink-0 items-center gap-1.5 px-2 text-[11px] text-muted sm:flex">
                  <Zap className="h-3 w-3" />{tokensForRun.toLocaleString()} tokens
                </span>
              </nav>

              {tab === "graph" && (
                <section className="mx-auto max-w-3xl">
                  {groupedRuns.length > 1 && (
                    <div className="mb-7">
                      <div className="mb-3 flex items-center justify-between gap-3">
                        <div>
                          <h2 className="text-sm font-semibold">Parallel runs</h2>
                          <p className="mt-1 text-xs text-muted">Each market has its own worker and activity stream.</p>
                        </div>
                        <Badge className="rounded-full bg-card px-2.5 py-1 text-muted">
                          {groupedRuns.filter((run) => run.status === "running").length} active
                        </Badge>
                      </div>
                      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                        {groupedRuns.map((run) => {
                          const currentStep = [...run.steps].reverse().find((step) => step.status === "running")
                            ?? run.steps.at(-1);
                          const definition = currentStep
                            ? AGENTS.find((agent) => agent.id === currentStep.agent_name)
                            : undefined;
                          return (
                            <button
                              key={run.run_id}
                              type="button"
                              onClick={() => setSelectedRunId(run.run_id)}
                              className={cn(
                                "min-w-0 rounded-2xl border border-border bg-card p-4 text-left transition-colors hover:bg-card-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                                selectedRunId === run.run_id && "ring-1 ring-accent/50",
                              )}
                            >
                              <div className="flex items-center justify-between gap-3">
                                <span className="flex min-w-0 items-center gap-2 text-sm font-semibold">
                                  <CoinMark symbol={run.symbol} small />
                                  <span className="truncate">{run.symbol}</span>
                                </span>
                                <StatusBadge status={run.status} />
                              </div>
                              <div className="mt-4 flex min-w-0 items-start gap-3">
                                <PersonaAvatar
                                  name={definition?.name ?? "Agent"}
                                  src={definition?.persona}
                                  size={36}
                                />
                                <div className="min-w-0 flex-1">
                                  <p className="flex items-center gap-1.5 text-xs font-medium">
                                    {run.status === "running" && (
                                      <LoaderCircle className="h-3 w-3 shrink-0 animate-spin text-accent" />
                                    )}
                                    <span className="truncate">
                                      {definition?.name ?? (run.status === "running" ? "Preparing run" : "No active agent")}
                                    </span>
                                  </p>
                                  <p className="mt-1 line-clamp-2 min-h-9 text-xs leading-5 text-muted">
                                    {currentStep
                                      ? stepSummary(currentStep, run.symbol)
                                      : run.status === "running"
                                        ? "The first agent is getting ready."
                                        : "No agent activity has been saved yet."}
                                  </p>
                                </div>
                              </div>
                              <p className="mt-3 border-t border-border pt-3 text-[10px] text-muted-foreground">
                                {run.steps.length} {run.steps.length === 1 ? "step" : "steps"} · {run.steps.reduce((total, step) => total + step.tokens_used, 0).toLocaleString()} tokens
                              </p>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  <div className="mb-5 flex items-center justify-between gap-3">
                    <div>
                      <h2 className="text-base font-semibold">{feedAgentId === "all" ? "Agent activity" : `${agentName(feedAgentId)} activity`}</h2>
                      <p className="mt-1 text-xs text-muted">
                        {selectedRun?.status === "running" ? "Live updates from each step in the research run." : "A chronological record of persisted agent work."}
                      </p>
                    </div>
                    <span className="text-[11px] text-muted-foreground">{feedSteps.length} {feedSteps.length === 1 ? "step" : "steps"}</span>
                  </div>
                  {renderWorkflow()}
                  {selectedRun?.status === "running" && !feedSteps.some((step) => step.status === "running") && (
                    <div className="mt-5 flex items-center gap-3 rounded-2xl bg-surface px-4 py-3">
                      <span className="grid h-8 w-8 place-items-center rounded-full bg-card text-accent"><LoaderCircle className="h-4 w-4 animate-spin" /></span>
                      <div>
                        <p className="text-xs font-medium">Run is moving to the next step</p>
                        <p className="mt-0.5 text-[11px] text-muted">New activity will appear here as it is saved.</p>
                      </div>
                    </div>
                  )}
                  {selectedRun?.status === "failed" && (
                    <div className="mt-5 flex items-center justify-between gap-4 rounded-2xl bg-negative/5 px-4 py-3">
                      <div className="flex items-center gap-3">
                        <AlertCircle className="h-4 w-4 shrink-0 text-negative" />
                        <div>
                          <p className="text-xs font-medium">This run stopped with an error</p>
                          <p className="mt-0.5 text-[11px] text-muted">Review the event log or resume the run when ready.</p>
                        </div>
                      </div>
                      <Button size="sm" variant="ghost" className="rounded-full" disabled={Boolean(data.active_run_ids.includes(selectedRun.run_id)) || busyAction === "resume"} onClick={submitResume}><Play className="h-3.5 w-3.5" />Resume</Button>
                    </div>
                  )}
                </section>
              )}
              {tab === "agent" && renderAgentDetail()}
              {tab === "strategy" && renderStrategy()}
              {tab === "positions" && renderPositions()}
              {tab === "events" && (
                <section className="mx-auto max-w-3xl">
                  <div className="mb-5">
                    <h2 className="text-base font-semibold">System events</h2>
                    <p className="mt-1 text-xs text-muted">Errors, retries, and monitor activity.</p>
                  </div>
                  {renderEvents()}
                </section>
              )}
              <p className="mx-auto mt-8 max-w-3xl text-[10px] leading-5 text-muted-foreground">
                Backtests are historical simulations, not realized returns. Browser controls call the local framework API; exchange access remains in the backend.
              </p>
            </>
          )}
        </div>
      </main>

      <aside className="hidden h-screen overflow-y-auto bg-surface/40 px-5 py-6 min-[1100px]:sticky min-[1100px]:top-0 min-[1100px]:block">
        {data ? renderResearchContext() : (
          <div className="space-y-6 pt-2" aria-label="Loading market context">
            <div className="skeleton h-4 w-32 rounded-full" />
            <div className="flex items-center gap-3"><div className="skeleton h-9 w-9 rounded-full" /><div className="space-y-2"><div className="skeleton h-3 w-28 rounded-full" /><div className="skeleton h-2 w-40 rounded-full" /></div></div>
            <div className="skeleton h-32 w-full rounded-xl" />
            <div className="skeleton h-20 w-full rounded-xl" />
          </div>
        )}
      </aside>
      {showStart && (
        <StartDialog
          symbols={runSymbols}
          selectedSymbols={startSymbols}
          setSelectedSymbols={setStartSymbols}
          timeframes={TIMEFRAMES}
          selectedTimeframes={startTimeframes}
          setSelectedTimeframes={setStartTimeframes}
          accountEquity={accountEquity}
          setAccountEquity={setAccountEquity}
          dryRun={startDryRun}
          setDryRun={setStartDryRun}
          onClose={() => setShowStart(false)}
          onSubmit={submitStart}
          busy={busyAction === "start"}
        />
      )}
    </div>
  );
}

function ToggleRow({
  label,
  enabled,
  icon,
  onChange,
}: {
  label: string;
  enabled: boolean;
  icon: React.ReactNode;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 rounded-xl px-2 py-2 text-xs hover:bg-foreground/[0.03]">
      <span className="flex items-center gap-2 text-muted">{icon}{label}</span>
      <input type="checkbox" checked={enabled} onChange={(event) => onChange(event.target.checked)} aria-label={`Toggle ${label}`} />
    </label>
  );
}

function EventCard({ event }: { event: SystemEvent }) {
  const failed = /fail|error/i.test(`${event.type} ${event.message}`);
  return (
    <article className="relative pl-9">
      <span className={cn("absolute left-[8px] top-3 grid h-4 w-4 place-items-center rounded-full border border-border bg-background", failed ? "text-negative" : "text-accent")}>
        {failed ? <AlertCircle className="h-2.5 w-2.5" /> : <Activity className="h-2.5 w-2.5" />}
      </span>
      <div className="rounded-xl border border-border bg-card px-4 py-3 transition-colors hover:bg-card-hover">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-semibold">{event.type.replaceAll("_", " ")}</p>
              {event.run_id && <span className="max-w-48 truncate font-mono text-[10px] text-muted-foreground">{event.run_id}</span>}
            </div>
            <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-muted">{event.message}</p>
          </div>
          <span className="shrink-0 text-[10px] text-muted-foreground">{dateLabel(event.timestamp)}</span>
        </div>
        {event.traceback && <div className="mt-3"><RawSection title="Traceback" value={event.traceback} /></div>}
        <div className="mt-3"><RawSection title="Event JSON" value={event} /></div>
      </div>
    </article>
  );
}

function StartDialog({
  symbols,
  selectedSymbols,
  setSelectedSymbols,
  timeframes,
  selectedTimeframes,
  setSelectedTimeframes,
  accountEquity,
  setAccountEquity,
  dryRun,
  setDryRun,
  onClose,
  onSubmit,
  busy,
}: {
  symbols: string[];
  selectedSymbols: string[];
  setSelectedSymbols: (value: string[]) => void;
  timeframes: string[];
  selectedTimeframes: string[];
  setSelectedTimeframes: (value: string[]) => void;
  accountEquity: string;
  setAccountEquity: (value: string) => void;
  dryRun: boolean;
  setDryRun: (value: boolean) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  busy: boolean;
}) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-background/80 p-4" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="start-run-title"
        onSubmit={onSubmit}
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-border bg-card p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="start-run-title" className="text-lg font-semibold">Start research run</h2>
            <p className="mt-1 text-xs text-muted">Choose markets and timeframes. New runs use the configured framework and risk gates.</p>
          </div>
          <button type="button" onClick={onClose} className="rounded-xl p-2 text-muted hover:bg-foreground/5 hover:text-foreground" aria-label="Close dialog"><X className="h-4 w-4" /></button>
        </div>
        <fieldset className="mt-6">
          <legend className="text-xs font-semibold">Symbols</legend>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {symbols.map((symbol) => (
              <label key={symbol} className="flex cursor-pointer items-center gap-2 rounded-xl border border-border px-3 py-2.5 text-xs hover:bg-foreground/[0.03]">
                <input type="checkbox" checked={selectedSymbols.includes(symbol)} onChange={() => toggleSelection(symbol, selectedSymbols, setSelectedSymbols)} />
                <CoinMark symbol={symbol} small />{symbol}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="mt-5">
          <legend className="text-xs font-semibold">Timeframes</legend>
          <div className="mt-3 flex flex-wrap gap-2">
            {timeframes.map((timeframe) => (
              <label key={timeframe} className={cn("flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-xs", selectedTimeframes.includes(timeframe) ? "border-accent/40 bg-accent/5" : "border-border")}>
                <input type="checkbox" checked={selectedTimeframes.includes(timeframe)} onChange={() => toggleSelection(timeframe, selectedTimeframes, setSelectedTimeframes)} />
                {timeframe}
              </label>
            ))}
          </div>
        </fieldset>
        <label className="mt-5 block text-xs font-medium" htmlFor="account-equity">Total account equity</label>
        <Input id="account-equity" type="number" min="0.01" step="any" value={accountEquity} onChange={(event) => setAccountEquity(event.target.value)} placeholder="Optional" className="mt-2" />
        <p className="mt-2 text-[11px] leading-5 text-muted">
          Split evenly across selected markets. Live runs use the available testnet quote balance when left empty.
        </p>
        <label className="mt-5 flex cursor-pointer items-center justify-between gap-4 rounded-xl border border-border px-4 py-3">
          <span>
            <span className="block text-xs font-medium">Dry-run mode</span>
            <span className="mt-1 block text-[10px] text-muted">No exchange order placement. Default: on.</span>
          </span>
          <input type="checkbox" checked={dryRun} onChange={(event) => setDryRun(event.target.checked)} aria-label="Start in dry-run mode" />
        </label>
        <div className="mt-6 flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            Start {selectedSymbols.length > 1 ? `${selectedSymbols.length} runs` : "run"}
          </Button>
        </div>
      </form>
    </div>
  );
}
