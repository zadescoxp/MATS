"use client";

import {
  Activity,
  AlertCircle,
  ArrowRight,
  BarChart3,
  ChevronRight,
  Circle,
  Database,
  FileJson,
  Filter,
  GitBranch,
  ListChecks,
  LoaderCircle,
  LockKeyhole,
  PanelLeftClose,
  PanelLeftOpen,
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
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";

const COINS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"];
const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"];
const EMPTY_RUNS: Run[] = [];

const AGENTS = [
  { id: "data_worker", name: "Data Worker" },
  { id: "multi_timeframe_analyst", name: "MultiTimeframeAnalyst", persona: "/personas/Analyst.png" },
  { id: "strategy_builder", name: "StrategyBuilder", persona: "/personas/Strategy%20Builder.png" },
  { id: "backtest", name: "Backtest", persona: "/personas/Backtester.png" },
  { id: "critique", name: "Critique", persona: "/personas/Critique.png" },
  { id: "risk_assessment", name: "RiskAssessment", persona: "/personas/RiskAnalyst.png" },
  { id: "coder", name: "Coder", persona: "/personas/Coder.png" },
  { id: "execution", name: "Execution", persona: "/personas/Execution.png" },
  { id: "monitor", name: "Monitor", persona: "/personas/Execution.png" },
];

const TABS: Array<{ id: DashboardTab; label: string; icon: typeof Activity }> = [
  { id: "graph", label: "Agent graph", icon: GitBranch },
  { id: "agent", label: "Agent detail", icon: ListChecks },
  { id: "strategy", label: "Strategy & backtest", icon: BarChart3 },
  { id: "positions", label: "Positions & executions", icon: Wallet },
  { id: "events", label: "System events", icon: Activity },
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
    case "dry_run":
    case "stopped":
    case "hold":
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

function agentLastAction(run: Run | null, agentId: string): string {
  if (!run) return "Waiting for a run";
  const step = [...run.steps].reverse().find((item) => item.agent_name === agentId);
  if (!step) return "No saved step";
  if (step.error) return step.error;
  const output = step.output;
  if (!output) return "Step has no output";
  const summary = Object.entries(output).find(
    ([key, value]) => key !== "status" && typeof value === "string" && value.length,
  );
  return summary ? String(summary[1]) : step.status;
}

function latestAttempt(run: Run | null): StrategyAttempt | null {
  if (!run?.strategies.length) return null;
  return [...run.strategies].sort((a, b) => b.version - a.version)[0] ?? null;
}

function toggleSelection(
  value: string,
  selected: string[],
  setter: (value: string[]) => void,
): void {
  setter(selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]);
}

function StatusBadge({ status }: { status: string }) {
  return <Badge className={statusStyle(status)}>{status.replaceAll("_", " ")}</Badge>;
}

function CoinMark({ symbol, small = false }: { symbol: string; small?: boolean }) {
  const base = symbol.split("/")[0]?.toUpperCase() ?? "";
  const size = small ? 28 : 34;
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full border border-border bg-background text-[10px] font-bold text-foreground"
      style={{ width: size, height: size }}
      aria-label={`${base || "generic"} coin`}
    >
      {base === "BTC" ? (
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" aria-hidden="true">
          <path d="M9 4v16m6-16v16M7 7h7a3 3 0 0 1 0 6H8m0 0h7a3 3 0 1 1 0 6H7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      ) : base === "ETH" ? (
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" aria-hidden="true">
          <path d="m12 2 7 10-7 4-7-4 7-10Zm0 14 7-4-7 10-7-10 7 4Z" fill="currentColor" />
        </svg>
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
      <pre className="max-h-96 overflow-auto border-t border-border px-4 py-3 text-[11px] leading-5 text-muted">
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
    <div className="rounded-xl border border-border bg-background/70 p-4">
      <p className="text-[11px] text-muted">{label}</p>
      <p className={cn("mt-2 text-lg font-semibold tabular-nums", tone === "positive" ? "text-positive" : tone === "negative" ? "text-negative" : "")}>{value}</p>
    </div>
  );
}

export function Dashboard() {
  const [data, setData] = useState<DashboardState | null>(null);
  const [connection, setConnection] = useState<"connecting" | "online" | "offline">("connecting");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState("data_worker");
  const [tab, setTab] = useState<DashboardTab>("graph");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [showStart, setShowStart] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [search, setSearch] = useState("");
  const [coinSearch, setCoinSearch] = useState("");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedPositions, setSelectedPositions] = useState<string[]>([]);
  const [startSymbols, setStartSymbols] = useState<string[]>([]);
  const [startTimeframes, setStartTimeframes] = useState<string[]>([]);
  const [startDefaultsLoaded, setStartDefaultsLoaded] = useState(false);
  const [accountEquity, setAccountEquity] = useState("");
  const [startDryRun, setStartDryRun] = useState(true);
  const [executionStatusFilter, setExecutionStatusFilter] = useState("");
  const notifiedFailures = useRef(new Set<string>());

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

  const runs = data?.runs ?? EMPTY_RUNS;
  const selectedRun = runs.find((run) => run.run_id === selectedRunId) ?? null;
  const activeAttempt = latestAttempt(selectedRun);
  const tokensForRun = selectedRun?.steps.reduce((sum, step) => sum + step.tokens_used, 0) ?? 0;
  const runSymbols = useMemo(
    () => [...new Set([...(data?.settings.symbols ?? []), ...COINS, ...runs.map((run) => run.symbol)])],
    [data?.settings.symbols, runs],
  );
  const sidebarSymbols = runSymbols.filter((symbol) =>
    symbol.toLowerCase().includes(coinSearch.trim().toLowerCase()),
  );
  const visibleRuns = runs.filter(
    (run) =>
      (!statusFilter || run.status === statusFilter) &&
      `${run.symbol} ${run.run_id}`.toLowerCase().includes(search.trim().toLowerCase()),
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
    const result = await act("start", payload, "Run started. Live updates will appear as steps are saved.");
    if (result) {
      const ids = result.run_ids;
      if (Array.isArray(ids) && typeof ids[0] === "string") setSelectedRunId(ids[0]);
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
    if (!window.confirm("Stop this run at its next agent boundary and enable the kill switch?")) return;
    await act("stop", { run_id: selectedRun.run_id }, "Stop requested; the run will halt between agent steps.");
  }

  async function submitResume() {
    if (!selectedRun) return;
    await act("resume", { run_id: selectedRun.run_id }, "Resume requested.");
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
        !data.active_run_ids.includes(run.run_id),
    );
    if (!resumable.length) return;
    if (!window.confirm(`Resume ${resumable.length} inactive incomplete run(s)?`)) return;
    await act("resume_all");
  }

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
    return (
      <div className="space-y-5">
        <div className="grid gap-3 xl:grid-cols-3">
          {AGENTS.map((agent, index) => {
            const status = agentStatus(selectedRun, agent.id, data?.operations ?? []);
            const step = [...selectedRun.steps].reverse().find((item) => item.agent_name === agent.id);
            return (
              <div className="relative" key={agent.id}>
                <button
                  type="button"
                  onClick={() => {
                    setSelectedAgent(agent.id);
                    setTab("agent");
                  }}
                  className={cn(
                    "w-full rounded-2xl border bg-card p-4 text-left transition-colors hover:border-accent/50",
                    selectedAgent === agent.id ? "border-accent" : "border-border",
                  )}
                  aria-label={`Open ${agent.name} details`}
                >
                  <div className="flex items-start gap-3">
                    <PersonaAvatar name={agent.name} src={agent.persona} size={44} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-xs font-semibold">{agent.name}</p>
                        <StatusBadge status={status} />
                      </div>
                      <p className="mt-2 line-clamp-2 min-h-8 text-[11px] leading-4 text-muted">
                        {agent.id === "monitor"
                          ? "Position monitoring runs on request."
                          : agentLastAction(selectedRun, agent.id)}
                      </p>
                    </div>
                  </div>
                  <div className="mt-4 flex items-center justify-between border-t border-border pt-3 text-[10px] text-muted">
                    <span>{step ? `${step.tokens_used.toLocaleString()} tokens` : "No tokens recorded"}</span>
                    <span>{step ? formatDuration(step.started_at, step.finished_at) : "—"}</span>
                  </div>
                </button>
                {index < AGENTS.length - 1 && (
                  <ArrowRight className="absolute -right-3 top-1/2 z-10 hidden h-4 w-4 -translate-y-1/2 text-muted xl:block" />
                )}
              </div>
            );
          })}
        </div>
        {selectedRun.status === "running" &&
          selectedRun.strategies.some((attempt) => attempt.decision === "REJECT") && (
          <div className="flex items-center gap-2 rounded-xl border border-accent/30 bg-accent/5 px-4 py-3 text-xs text-muted">
            <GitBranch className="h-4 w-4 shrink-0 text-accent" />
            Critique rejected a strategy. Revision loop returns to StrategyBuilder before risk assessment.
          </div>
        )}
        <Card className="p-5">
          <CardHeader>
            <div>
              <CardTitle>Agent step timeline</CardTitle>
              <CardDescription>Persisted step status, time, token usage, and recorded errors.</CardDescription>
            </div>
            <span className="text-xs text-muted">{selectedRun.steps.length} steps</span>
          </CardHeader>
          {selectedRun.steps.length ? (
            <div className="mt-5 overflow-x-auto">
              <table className="w-full min-w-[700px] text-left text-xs">
                <thead className="text-[10px] uppercase tracking-wide text-muted">
                  <tr>
                    <th className="pb-3 pr-4">Agent</th>
                    <th className="pb-3 pr-4">Status</th>
                    <th className="pb-3 pr-4">Started</th>
                    <th className="pb-3 pr-4">Tokens</th>
                    <th className="pb-3 pr-4">Cost</th>
                    <th className="pb-3">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedRun.steps.map((step) => (
                    <tr
                      key={step.step_id}
                      className="cursor-pointer border-t border-border text-muted hover:bg-foreground/[0.02]"
                      onClick={() => {
                        setSelectedAgent(step.agent_name);
                        setTab("agent");
                      }}
                    >
                      <td className="py-3 pr-4 font-medium text-foreground">{step.agent_name}</td>
                      <td className="py-3 pr-4"><StatusBadge status={step.status} /></td>
                      <td className="py-3 pr-4">{dateLabel(step.started_at)}</td>
                      <td className="py-3 pr-4 tabular-nums">{step.tokens_used.toLocaleString()}</td>
                      <td className="py-3 pr-4 tabular-nums">{money(step.cost, 4)}</td>
                      <td className="max-w-72 truncate py-3 text-negative">{step.error ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="mt-5"><EmptyState title="Waiting for agent steps" body="Steps appear here as the run writes to SQLite." /></div>
          )}
        </Card>
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
                  <CartesianGrid stroke="#1f1f1f" vertical={false} />
                  <XAxis dataKey="index" tick={{ fill: "#a1a1aa", fontSize: 10 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: "#a1a1aa", fontSize: 10 }} axisLine={false} tickLine={false} width={66} tickFormatter={(value: number) => money(value, 0)} />
                  <Tooltip
                    contentStyle={{ background: "#111111", border: "1px solid #1f1f1f", borderRadius: 12, color: "#f5f5f5", fontSize: 12 }}
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
                {[...new Set(openPositions.map((position) => position.symbol))].map((symbol) => <option key={symbol}>{symbol}</option>)}
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
      <div className="space-y-3">
        {allEvents.map((event) => <EventCard key={event.event_id} event={event} />)}
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="flex min-h-screen">
        <aside className={cn(
          "sticky top-0 hidden h-screen shrink-0 self-start flex-col overflow-y-auto border-r border-border bg-card/60 p-4 transition-[width] lg:flex",
          sidebarCollapsed ? "w-[76px] items-center" : "w-[260px]",
        )}>
          <div className="flex w-full items-center justify-between gap-2 px-2 py-2">
            <div className="flex min-w-0 items-center gap-3">
            <div className="grid h-9 w-9 place-items-center rounded-xl border border-border bg-background text-accent">
              <Activity className="h-4 w-4" />
            </div>
            {!sidebarCollapsed && <div>
              <p className="text-sm font-bold tracking-wide">MATS</p>
              <p className="text-[10px] text-muted">Research console</p>
            </div>}
            </div>
            <button
              type="button"
              onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
              className="rounded-xl p-2 text-muted hover:bg-foreground/5 hover:text-foreground"
              aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {sidebarCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            </button>
          </div>
          {!sidebarCollapsed && (
          <>
          <div className="mt-7">
            <p className="px-2 text-[10px] font-semibold uppercase tracking-widest text-muted">Symbols</p>
            <div className="relative mt-3">
              <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-muted" />
              <Input value={coinSearch} onChange={(event) => setCoinSearch(event.target.value)} placeholder="Search symbols" className="h-9 min-h-9 pl-9 text-xs" />
            </div>
            <div className="mt-2 max-h-40 space-y-1 overflow-y-auto">
              {sidebarSymbols.map((symbol) => (
                <label key={symbol} className="flex cursor-pointer items-center gap-2 rounded-xl px-2 py-2 text-xs text-muted hover:bg-foreground/[0.03]">
                  <input type="checkbox" checked={startSymbols.includes(symbol)} onChange={() => toggleSelection(symbol, startSymbols, setStartSymbols)} />
                  <CoinMark symbol={symbol} small />
                  <span>{symbol}</span>
                </label>
              ))}
              {!sidebarSymbols.length && <p className="px-2 py-3 text-xs text-muted">No matching symbols.</p>}
            </div>
          </div>
          <div className="mt-7 flex min-h-0 flex-1 flex-col">
            <div className="flex items-center justify-between px-2">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-muted">Runs</p>
              <span className="rounded-full bg-background px-2 py-1 text-[10px] text-muted">{runs.length}</span>
            </div>
            <div className="relative mt-3">
              <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-muted" />
              <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Filter runs" className="h-9 min-h-9 pl-9 text-xs" />
            </div>
            <select
              className="mt-2 min-h-9 rounded-xl border border-border bg-background px-3 text-xs text-muted"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              aria-label="Filter runs by status"
            >
              <option value="">All statuses</option>
              {[...new Set(runs.map((run) => run.status))].map((status) => <option key={status}>{status}</option>)}
            </select>
            <div className="mt-2 min-h-0 flex-1 space-y-1 overflow-y-auto">
              {visibleRuns.slice(0, 30).map((run) => (
                <button
                  key={run.run_id}
                  type="button"
                  onClick={() => setSelectedRunId(run.run_id)}
                  className={cn(
                    "w-full rounded-xl border px-3 py-2.5 text-left",
                    selectedRunId === run.run_id ? "border-accent/40 bg-accent/5" : "border-transparent hover:bg-foreground/[0.03]",
                  )}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2 truncate text-xs font-medium text-foreground">
                      <CoinMark symbol={run.symbol} small />
                      {run.symbol}
                    </span>
                    <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", run.status === "running" ? "bg-accent" : run.status === "failed" ? "bg-negative" : run.status === "completed" ? "bg-positive" : "bg-muted")} />
                  </span>
                  <span className="mt-2 block truncate font-mono text-[9px] text-muted">{run.run_id}</span>
                </button>
              ))}
              {!runs.length && <p className="px-2 py-5 text-xs leading-5 text-muted">No saved runs yet. Start a run to populate the timeline.</p>}
            </div>
            <p className="mt-2 px-2 text-[10px] text-muted">Open positions: {openPositions.length}</p>
          </div>
          <div className="mt-5 space-y-2 border-t border-border pt-4">
            <Button className="w-full justify-start" variant="primary" onClick={openStartDialog}>
              <Plus className="h-4 w-4" /> Start run
            </Button>
            <Button className="w-full justify-start" variant="outline" disabled={!selectedRun || !data?.active_run_ids.includes(selectedRun.run_id) || selectedRun.status !== "running" || busyAction === "stop"} onClick={submitStop}>
              <Pause className="h-4 w-4" /> Stop run
            </Button>
            <Button className="w-full justify-start" variant="outline" disabled={!selectedRun || !["failed", "stopped", "running"].includes(selectedRun.status) || Boolean(data?.active_run_ids.includes(selectedRun.run_id)) || busyAction === "resume"} onClick={submitResume}>
              <Play className="h-4 w-4" /> Resume run
            </Button>
            <Button className="w-full justify-start" variant="outline" disabled={busyAction === "monitor"} onClick={() => act("monitor", {}, "Monitor cycle started.")}>
              <RefreshCw className="h-4 w-4" /> Run monitor cycle
            </Button>
            <Button className="w-full justify-start" variant="danger" disabled={!data?.active_run_ids.length || busyAction === "stop_all"} onClick={submitStopAll}>
              <Pause className="h-4 w-4" /> Stop all active
            </Button>
            <Button className="w-full justify-start" variant="outline" disabled={!data?.runs.some((run) => ["failed", "stopped", "running"].includes(run.status) && !data.active_run_ids.includes(run.run_id)) || busyAction === "resume_all"} onClick={submitResumeAll}>
              <Play className="h-4 w-4" /> Resume all incomplete
            </Button>
          </div>
          <div className="mt-4 space-y-2 border-t border-border pt-4">
            <ToggleRow
              label="Dry-run"
              enabled={data?.settings.dry_run ?? true}
              icon={<ShieldCheck className="h-4 w-4" />}
              onChange={(enabled) => toggleSetting("dry_run", enabled)}
            />
            <ToggleRow
              label="Kill switch"
              enabled={data?.settings.kill_switch ?? true}
              icon={<LockKeyhole className="h-4 w-4" />}
              onChange={(enabled) => toggleSetting("kill_switch", enabled)}
            />
          </div>
          </>
          )}
        </aside>
        <main className="min-w-0 flex-1">
          <header className="sticky top-0 z-20 border-b border-border bg-background/95 px-4 py-3 backdrop-blur md:px-8">
            <div className="mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-3">
                <div className="grid h-9 w-9 place-items-center rounded-xl border border-border bg-card lg:hidden">
                  <Activity className="h-4 w-4 text-accent" />
                </div>
                <div className="min-w-0">
                  <p className="truncate text-xs font-semibold">{selectedRun?.run_id ?? "No run selected"}</p>
                  <p className="mt-1 flex items-center gap-2 text-[10px] text-muted">
                    <span>{selectedRun?.symbol ?? "Select a run"}</span>
                    <span>·</span>
                    <span>{tokensForRun.toLocaleString()} tokens</span>
                    <span className={cn("h-1.5 w-1.5 rounded-full", connection === "online" ? "bg-positive" : connection === "offline" ? "bg-negative" : "bg-accent")} />
                    <span>{connection === "online" ? "Live" : connection === "offline" ? "Offline" : "Connecting"}</span>
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => toggleSetting("dry_run", !(data?.settings.dry_run ?? true))}
                  className={cn("rounded-full border px-3 py-1 text-[10px] font-semibold", data?.settings.dry_run ? "border-accent/20 bg-accent/10 text-accent" : "border-negative/20 bg-negative/10 text-negative")}
                  aria-label={`Toggle dry-run; currently ${data?.settings.dry_run ? "on" : "off"}`}
                >
                  Dry-run {data?.settings.dry_run ? "on" : "off"}
                </button>
                <button
                  type="button"
                  onClick={() => toggleSetting("kill_switch", !(data?.settings.kill_switch ?? true))}
                  className={cn("rounded-full border px-3 py-1 text-[10px] font-semibold", data?.settings.kill_switch ? "border-positive/20 bg-positive/10 text-positive" : "border-negative/20 bg-negative/10 text-negative")}
                  aria-label={`Toggle kill switch; currently ${data?.settings.kill_switch ? "on" : "off"}`}
                >
                  Kill switch {data?.settings.kill_switch ? "on" : "off"}
                </button>
                <Button variant="primary" size="sm" className="lg:hidden" onClick={openStartDialog}>
                  <Plus className="h-3.5 w-3.5" /> Start
                </Button>
              </div>
            </div>
          </header>
          <div className="mx-auto max-w-[1500px] space-y-5 p-4 md:p-8">
            <section className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Trading research / live state</p>
                <h1 className="mt-2 text-2xl font-semibold tracking-tight md:text-3xl">Run observability</h1>
                <p className="mt-2 max-w-2xl text-xs leading-5 text-muted">
                  Inspect each framework step, its saved evidence, modeled strategy results, and guarded testnet execution state.
                </p>
              </div>
              <div className="flex items-center gap-2 text-xs text-muted">
                <Database className="h-4 w-4" />
                SQLite · local API
              </div>
            </section>

            {notice && (
              <div
                role="status"
                className={cn(
                  "flex items-start justify-between gap-3 rounded-xl border px-4 py-3 text-xs",
                  notice.kind === "error" ? "border-negative/25 bg-negative/5 text-negative" : notice.kind === "success" ? "border-positive/25 bg-positive/5 text-positive" : "border-accent/25 bg-accent/5 text-accent",
                )}
              >
                <span>{notice.message}</span>
                <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss notification"><X className="h-4 w-4" /></button>
              </div>
            )}

            {!data && connection !== "offline" && (
              <div className="grid gap-4 md:grid-cols-3">
                {[0, 1, 2].map((item) => <div key={item} className="skeleton h-28" />)}
              </div>
            )}
            {!data && connection === "offline" && (
              <Card className="p-6">
                <div className="flex items-start gap-3">
                  <AlertCircle className="mt-0.5 h-5 w-5 text-negative" />
                  <div>
                    <CardTitle>Dashboard API unavailable</CardTitle>
                    <CardDescription className="mt-2">
                      Start the local service with <code className="rounded bg-background px-1.5 py-1">uv run mats-web</code>, then refresh this page.
                    </CardDescription>
                  </div>
                </div>
              </Card>
            )}

            {data && (
              <>
                <section className="rounded-2xl border border-border bg-card p-3 lg:hidden">
                  <label className="block text-[10px] text-muted" htmlFor="mobile-run-select">Current run</label>
                  <select
                    id="mobile-run-select"
                    className="mt-2 min-h-10 w-full rounded-xl border border-border bg-background px-3 text-xs text-foreground"
                    value={selectedRunId ?? ""}
                    onChange={(event) => setSelectedRunId(event.target.value || null)}
                  >
                    <option value="">No run selected</option>
                    {runs.map((run) => <option key={run.run_id} value={run.run_id}>{run.symbol} · {run.run_id}</option>)}
                  </select>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" disabled={!selectedRun || !data.active_run_ids.includes(selectedRun.run_id) || selectedRun.status !== "running" || busyAction === "stop"} onClick={submitStop}>
                      <Pause className="h-3.5 w-3.5" /> Stop
                    </Button>
                    <Button size="sm" variant="outline" disabled={!selectedRun || !["failed", "stopped", "running"].includes(selectedRun.status) || data.active_run_ids.includes(selectedRun.run_id) || busyAction === "resume"} onClick={submitResume}>
                      <Play className="h-3.5 w-3.5" /> Resume
                    </Button>
                    <Button size="sm" variant="danger" disabled={!data.active_run_ids.length || busyAction === "stop_all"} onClick={submitStopAll}>
                      <Pause className="h-3.5 w-3.5" /> Stop all
                    </Button>
                    <Button size="sm" variant="outline" disabled={!data.runs.some((run) => ["failed", "stopped", "running"].includes(run.status) && !data.active_run_ids.includes(run.run_id)) || busyAction === "resume_all"} onClick={submitResumeAll}>
                      <Play className="h-3.5 w-3.5" /> Resume all
                    </Button>
                    <Button size="sm" variant="outline" disabled={busyAction === "monitor"} onClick={() => act("monitor", {}, "Monitor cycle started.")}>
                      <RefreshCw className="h-3.5 w-3.5" /> Monitor cycle
                    </Button>
                  </div>
                </section>
                <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <SummaryCard label="Open positions" value={String(openPositions.length)} note="Persisted live monitor state" icon={<Wallet className="h-4 w-4" />} />
                  <SummaryCard label="Selected run tokens" value={tokensForRun.toLocaleString()} note="Recorded agent-step usage" icon={<Zap className="h-4 w-4" />} />
                  <SummaryCard label="Run status" value={selectedRun?.status ?? "idle"} note={selectedRun ? `Started ${dateLabel(selectedRun.started_at)}` : "No run selected"} icon={<Activity className="h-4 w-4" />} />
                  <SummaryCard label="Latest net backtest" value={money(activeAttempt?.report?.net_pnl)} note={activeAttempt ? `Version ${activeAttempt.version} · after modeled costs` : "No strategy attempt"} icon={<BarChart3 className="h-4 w-4" />} tone={numberValue(activeAttempt?.report?.net_pnl) >= 0 ? "positive" : "negative"} />
                </section>

                <nav className="flex gap-1 overflow-x-auto rounded-2xl border border-border bg-card p-1" aria-label="Dashboard views">
                  {TABS.map(({ id, label, icon: Icon }) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => setTab(id)}
                      className={cn(
                        "flex shrink-0 items-center gap-2 rounded-xl px-3 py-2.5 text-xs font-medium",
                        tab === id ? "bg-accent text-foreground" : "text-muted hover:bg-foreground/[0.04] hover:text-foreground",
                      )}
                    >
                      <Icon className="h-3.5 w-3.5" />{label}
                    </button>
                  ))}
                </nav>

                {tab === "graph" && (
                  <section className="space-y-4">
                    <Card className="p-5">
                      <CardHeader>
                        <div>
                          <CardTitle>Framework workflow</CardTitle>
                          <CardDescription>Click a node to open the corresponding persisted agent details.</CardDescription>
                        </div>
                        <StatusBadge status={selectedRun?.status ?? "idle"} />
                      </CardHeader>
                    </Card>
                    {renderWorkflow()}
                  </section>
                )}
                {tab === "agent" && renderAgentDetail()}
                {tab === "strategy" && renderStrategy()}
                {tab === "positions" && renderPositions()}
                {tab === "events" && (
                  <section className="space-y-4">
                    <Card className="p-5">
                      <CardHeader>
                        <div><CardTitle>System events & errors</CardTitle><CardDescription>Latest 200 events across saved runs.</CardDescription></div>
                        <span className="text-xs text-muted">{allEvents.length} records</span>
                      </CardHeader>
                    </Card>
                    {renderEvents()}
                  </section>
                )}
                <p className="text-[10px] leading-5 text-muted">
                  Backtests are historical simulations, not realized returns. Browser controls call the local framework API; exchange access remains in the backend.
                </p>
              </>
            )}
          </div>
        </main>
      </div>
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

function SummaryCard({
  label,
  value,
  note,
  icon,
  tone,
}: {
  label: string;
  value: string;
  note: string;
  icon: React.ReactNode;
  tone?: "positive" | "negative";
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted">{label}</p>
        <span className="text-muted">{icon}</span>
      </div>
      <p className={cn("mt-4 truncate text-xl font-semibold capitalize tabular-nums", tone === "positive" ? "text-positive" : tone === "negative" ? "text-negative" : "")}>{value}</p>
      <p className="mt-1 truncate text-[10px] text-muted">{note}</p>
    </Card>
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
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          {failed ? <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-negative" /> : <Activity className="mt-0.5 h-4 w-4 shrink-0 text-accent" />}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-xs font-semibold">{event.type.replaceAll("_", " ")}</p>
              {event.run_id && <span className="max-w-48 truncate font-mono text-[10px] text-muted">{event.run_id}</span>}
            </div>
            <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-muted">{event.message}</p>
          </div>
        </div>
        <span className="shrink-0 text-[10px] text-muted">{dateLabel(event.timestamp)}</span>
      </div>
      {event.traceback && <div className="mt-3"><RawSection title="Traceback" value={event.traceback} /></div>}
      <div className="mt-3"><RawSection title="Event JSON" value={event} /></div>
    </Card>
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
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-border bg-card p-5 shadow-2xl"
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
        <label className="mt-5 block text-xs font-medium" htmlFor="account-equity">Optional account equity</label>
        <Input id="account-equity" type="number" min="0.01" step="any" value={accountEquity} onChange={(event) => setAccountEquity(event.target.value)} placeholder="Leave empty to use framework defaults" className="mt-2" />
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
            Start run
          </Button>
        </div>
      </form>
    </div>
  );
}
