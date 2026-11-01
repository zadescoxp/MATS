"use client";

import {
  AlertCircle,
  ArrowRight,
  ArrowUp,
  Cable,
  CheckCircle2,
  ChevronRight,
  Circle,
  Gauge,
  History,
  LoaderCircle,
  LockKeyhole,
  Menu,
  Paperclip,
  Play,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Square,
  X,
} from "lucide-react";
import Image from "next/image";
import {
  FormEvent,
  UIEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { PersonaAvatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { BacktestChart } from "@/components/backtest-chart";
import { ConnectionsPanel } from "@/components/connections-panel";
import { Input } from "@/components/ui/input";
import { LiveMarketChart } from "@/components/live-market-chart";
import { PanelResizeHandle, usePanelWidth, useViewportWidth } from "@/components/resizable-panel";
import { RejectedRunNotice, RunDiagnosisBanner } from "@/components/run-diagnosis";
import { StructuredBrief } from "@/components/structured-brief";
import { createStateStream, fetchState, sendAction } from "@/lib/api";
import { cn } from "@/lib/cn";
import { COIN_SYMBOLS, marketName } from "@/lib/markets";
import { parseRunRequest } from "@/lib/run-intent";
import type {
  AgentStep,
  DashboardState,
  DashboardTab,
  EntryWatcher,
  Run,
  StrategyAttempt,
  SystemEvent,
} from "@/lib/types";

const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"];
const EMPTY_RUNS: Run[] = [];
const RAIL_BOUNDS = { defaultWidth: 248, min: 208, max: 420 };
const INSPECTOR_BOUNDS = { defaultWidth: 340, min: 288, max: 620 };
// The activity feed never gets narrower than this when side panels grow.
const FEED_MIN_WIDTH = 440;
const DEFAULT_ATTEMPTS = 3;
const MAX_ATTEMPTS = 10;
const EXTRA_ATTEMPTS = 2;

const AGENTS = [
  { id: "data_worker", name: "Data Worker", role: "Market data", persona: "/personas/data.png" },
  { id: "multi_timeframe_analyst", name: "Analyst Agent", role: "Multi-timeframe analysis", persona: "/personas/Analyst.png" },
  { id: "regime", name: "Regime", role: "Market state", persona: "/personas/Analyst.png" },
  { id: "strategy_builder", name: "Strategy Builder", role: "Strategy design", persona: "/personas/Strategy%20Builder.png" },
  { id: "backtest", name: "Backtest Agent", role: "Backtesting", persona: "/personas/Backtester.png" },
  { id: "critique", name: "Critique Agent", role: "Quality review", persona: "/personas/Critique.png" },
  { id: "risk_assessment", name: "Risk Agent", role: "Position sizing", persona: "/personas/RiskAnalyst.png" },
  { id: "coder", name: "Coder Agent", role: "Execution logic", persona: "/personas/Coder.png" },
  { id: "execution", name: "Execution Agent", role: "Signal and orders", persona: "/personas/Execution.png" },
  { id: "monitor", name: "Monitor Agent", role: "Position monitoring", persona: "/personas/monitor.jpg" },
];

const CHART_TOOLTIP = {
  background: "#1a1a1e",
  border: "1px solid #2a2a2e",
  borderRadius: 12,
  color: "#f5f5f6",
  fontSize: 12,
};

type Notice = { kind: "success" | "error" | "info"; message: string };
type SettingsSection = "connections" | "usage";
type RailTab = "running" | "history";

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

function formatDuration(start: string, finish?: string | null, now = Date.now()): string {
  const from = new Date(start).getTime();
  const to = finish ? new Date(finish).getTime() : now;
  if (!Number.isFinite(from) || !Number.isFinite(to)) return "—";
  const seconds = Math.max(0, Math.round((to - from) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
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
    case "in_position":
      return "border-accent/20 bg-accent/10 text-accent";
    default:
      return "border-border bg-foreground/[0.03] text-muted";
  }
}

function agentName(agentId: string): string {
  return AGENTS.find((agent) => agent.id === agentId)?.name
    ?? agentId.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function agentPersona(agentId: string): string | undefined {
  return AGENTS.find((agent) => agent.id === agentId)?.persona;
}

function agentWorkingLabel(agentId: string, symbol: string): string {
  const action: Record<string, string> = {
    initialize: "Preparing the research run",
    data_worker: "Collecting market data",
    multi_timeframe_analyst: "Reviewing market structure",
    regime: "Reading the market regime",
    strategy_builder: "Choosing a strategy family",
    backtest: "Backtesting strategy performance",
    critique: "Reviewing risk-adjusted results",
    risk_assessment: "Sizing risk for the proposed trade",
    coder: "Preparing execution logic",
    execution: "Checking the execution signal",
    monitor: "Monitoring open positions",
  };
  return `${action[agentId] ?? "Working on the next step"} for ${symbol}`;
}

function stepSummary(step: AgentStep, symbol: string): string {
  if (step.error) return step.error;
  if (step.status === "running") return agentWorkingLabel(step.agent_name, symbol);
  const output = step.output;
  if (!output) return step.status === "completed" ? "Step completed." : "Waiting for this step.";
  const describe = (value: unknown, depth = 0): string | null => {
    if (typeof value === "string" && value.trim() && value.toLowerCase() !== "completed") {
      return value.trim();
    }
    if (!isObject(value) || depth > 1) return null;
    for (const key of ["summary", "recommendation", "finding", "decision", "reason", "message", "action", "template_name", "name"]) {
      const summary = describe(value[key], depth + 1);
      if (summary) return summary;
    }
    return null;
  };
  const candidates = [
    "regime",
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

function latestAttempt(run: Run | null): StrategyAttempt | null {
  if (!run?.strategies.length) return null;
  return [...run.strategies].sort((a, b) => b.version - a.version)[0] ?? null;
}

function equityPoints(attempt: StrategyAttempt | null) {
  return (attempt?.equity_curve ?? objectRows(attempt?.report?.equity_curve)).map((point, index) => ({
    index: index + 1,
    equity: numberValue(point.equity ?? point.value),
    label: textValue(point.timestamp ?? point.time, String(index + 1)),
  }));
}

function isLiveRun(run: Run, activeIds: string[], watchers: EntryWatcher[]): boolean {
  if (run.status === "running" || activeIds.includes(run.run_id)) return true;
  const watcher = watchers.find((item) => item.run_id === run.run_id);
  return ["watching", "paused", "in_position"].includes(watcher?.status ?? "");
}

function runSubtitle(run: Run, watchers: EntryWatcher[]): string {
  const watcher = watchers.find((item) => item.run_id === run.run_id);
  if (run.status === "running") {
    const step = [...run.steps].reverse().find((item) => item.status === "running") ?? run.steps.at(-1);
    return step ? `${agentName(step.agent_name)} working` : "Starting";
  }
  if (watcher?.status === "in_position") return "Trading · position open";
  if (watcher?.status === "watching") return "Trading · waiting for entry";
  if (watcher?.status === "paused") return "Paused by a safety switch";
  if (run.diagnosis?.kind === "rejected") return "No strategy approved";
  if (run.status === "failed") return run.diagnosis?.title ?? "Needs attention";
  if (run.status === "stopped") return "Stopped";
  if (run.status === "completed") return "Completed";
  return run.status.replaceAll("_", " ");
}

function agentStatus(run: Run | null, agentId: string, operations: DashboardState["operations"]): string {
  if (!run) return "idle";
  if (agentId === "monitor") {
    return operations.filter((operation) => operation.name === "monitor").at(-1)?.status ?? "idle";
  }
  if (agentId === "critique" && run.strategies.at(-1)?.decision === "REJECT") return "rejected";
  return run.steps.filter((step) => step.agent_name === agentId).at(-1)?.status ?? "idle";
}

function toggleSelection(value: string, selected: string[], setter: (value: string[]) => void): void {
  setter(selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]);
}

function shortId(value: string): string {
  if (value.length <= 18) return value;
  return `${value.slice(0, 8)}…${value.slice(-4)}`;
}

function ThinkDots() {
  return (
    <span className="think-dots text-accent" aria-hidden="true">
      <span />
      <span />
      <span />
    </span>
  );
}

function Mark({ className }: { className?: string }) {
  return (
    <Image
      src="/Logo.png"
      alt=""
      width={64}
      height={64}
      priority
      className={cn("h-9 w-9 invert mix-blend-screen", className)}
    />
  );
}

function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const dot =
    ["completed", "filled", "approve", "approved", "open"].includes(normalized)
      ? "bg-positive"
      : ["failed", "rejected", "reject", "error"].includes(normalized)
        ? "bg-negative"
        : ["running", "watching", "dry_run"].includes(normalized)
          ? "bg-accent"
          : "bg-muted-foreground";
  return (
    <Badge className={cn("border-transparent bg-foreground/[0.04] px-2 py-0.5 text-[10px] font-medium normal-case tracking-normal", statusStyle(status))}>
      <span className={cn("h-1.5 w-1.5 rounded-full", dot, (normalized === "running" || normalized === "watching") && "agent-live")} />
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
    BNB: "https://cryptologos.cc/logos/bnb-bnb-logo.png",
    XRP: "https://cryptologos.cc/logos/xrp-xrp-logo.png",
    DOGE: "https://cryptologos.cc/logos/dogecoin-doge-logo.png",
    ADA: "https://cryptologos.cc/logos/cardano-ada-logo.png",
    AVAX: "https://cryptologos.cc/logos/avalanche-avax-logo.png",
    LINK: "https://cryptologos.cc/logos/chainlink-link-logo.png",
    DOT: "https://cryptologos.cc/logos/polkadot-new-dot-logo.png",
    TRX: "https://cryptologos.cc/logos/tron-trx-logo.png",
    TON: "https://cryptologos.cc/logos/toncoin-ton-logo.png",
    LTC: "https://cryptologos.cc/logos/litecoin-ltc-logo.png",
    BCH: "https://cryptologos.cc/logos/bitcoin-cash-bch-logo.png",
    ATOM: "https://cryptologos.cc/logos/cosmos-atom-logo.png",
    NEAR: "https://cryptologos.cc/logos/near-protocol-near-logo.png",
    APT: "https://cryptologos.cc/logos/aptos-apt-logo.png",
    SUI: "https://cryptologos.cc/logos/sui-sui-logo.png",
    SEI: "https://cryptologos.cc/logos/sei-sei-logo.png",
    INJ: "https://cryptologos.cc/logos/injective-inj-logo.png",
    TIA: "https://cryptologos.cc/logos/celestia-tia-logo.png",
    FIL: "https://cryptologos.cc/logos/filecoin-fil-logo.png",
    ICP: "https://cryptologos.cc/logos/internet-computer-icp-logo.png",
    HBAR: "https://cryptologos.cc/logos/hedera-hbar-logo.png",
    ETC: "https://cryptologos.cc/logos/ethereum-classic-etc-logo.png",
    UNI: "https://cryptologos.cc/logos/uniswap-uni-logo.png",
    AAVE: "https://cryptologos.cc/logos/aave-aave-logo.png",
    MKR: "https://cryptologos.cc/logos/maker-mkr-logo.png",
    LDO: "https://cryptologos.cc/logos/lido-dao-ldo-logo.png",
    ARB: "https://cryptologos.cc/logos/arbitrum-arb-logo.png",
    OP: "https://cryptologos.cc/logos/optimism-ethereum-op-logo.png",
    STX: "https://cryptologos.cc/logos/stacks-stx-logo.png",
    RUNE: "https://cryptologos.cc/logos/thorchain-rune-logo.png",
    GRT: "https://cryptologos.cc/logos/the-graph-grt-logo.png",
    IMX: "https://cryptologos.cc/logos/immutable-x-imx-logo.png",
    SAND: "https://cryptologos.cc/logos/the-sandbox-sand-logo.png",
    MANA: "https://cryptologos.cc/logos/decentraland-mana-logo.png",
    PEPE: "https://cryptologos.cc/logos/pepe-pepe-logo.png",
    SHIB: "https://cryptologos.cc/logos/shiba-inu-shib-logo.png",
    BONK: "https://cryptologos.cc/logos/bonk1-bonk-logo.png",
    WIF: "https://cryptologos.cc/logos/dogwifhat-wif-logo.png",
    FLOKI: "https://cryptologos.cc/logos/floki-inu-floki-logo.png",
    JUP: "https://cryptologos.cc/logos/jupiter-jup-logo.png",
    PYTH: "https://cryptologos.cc/logos/pyth-network-pyth-logo.png",
    ONDO: "https://cryptologos.cc/logos/ondo-ondo-logo.png",
    ENA: "https://cryptologos.cc/logos/ethena-ena-logo.png",
    TAO: "https://cryptologos.cc/logos/bittensor-tao-logo.png",
    PENDLE: "https://cryptologos.cc/logos/pendle-pendle-logo.png",
    CRV: "https://cryptologos.cc/logos/curve-dao-token-crv-logo.png",
    POL: "https://cryptologos.cc/logos/polygon-matic-logo.png",
    ALGO: "https://cryptologos.cc/logos/algorand-algo-logo.png",
    XLM: "https://cryptologos.cc/logos/stellar-xlm-logo.png",
    KAS: "https://cryptologos.cc/logos/kaspa-kas-logo.png",
    ORDI: "https://cryptologos.cc/logos/ordi-ordi-logo.png",
    WLD: "https://coin-images.coingecko.com/coins/images/31069/small/worldcoin.jpeg?1696529903",
    FET: "https://coin-images.coingecko.com/coins/images/5681/small/ASI.png?1719827289",
  };
  const logo = logos[base];
  const [failedLogo, setFailedLogo] = useState<string | null>(null);
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full bg-card text-[10px] font-semibold text-foreground"
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

function Switch({
  checked,
  label,
  disabled,
  onChange,
}: {
  checked: boolean;
  label: string;
  disabled?: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-6 w-11 shrink-0 rounded-full p-0 transition-colors duration-200 disabled:opacity-40",
        checked ? "bg-foreground" : "bg-white/[0.12]",
      )}
    >
      <span
        className={cn(
          "absolute left-0.5 top-0.5 h-5 w-5 rounded-full transition-transform duration-200",
          checked ? "translate-x-5 bg-background" : "bg-foreground/80",
        )}
      />
    </button>
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
    <details className="group rounded-xl border border-white/[0.06]" open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3.5 py-3 text-xs font-medium text-foreground">
        <span>{title}</span>
        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground transition-transform duration-200 group-open:rotate-90" />
      </summary>
      <StructuredBrief value={value} />
    </details>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="grid min-h-52 place-items-center rounded-2xl bg-card/50 px-6 text-center">
      <div>
        <Circle className="mx-auto h-5 w-5 text-muted-foreground" />
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
    <div className="rounded-2xl bg-background/60 px-4 py-3">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className={cn("mt-1.5 text-lg font-semibold tabular-nums tracking-tight", tone === "positive" ? "text-positive" : tone === "negative" ? "text-negative" : "")}>{value}</p>
    </div>
  );
}

function EquitySpark({ points }: { points: ReturnType<typeof equityPoints> }) {
  if (points.length < 2) return null;
  return (
    <div className="mt-3 h-40 overflow-hidden rounded-xl bg-background/50">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 12, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#222226" vertical={false} />
          <XAxis dataKey="index" hide />
          <YAxis hide />
          <Tooltip
            contentStyle={CHART_TOOLTIP}
            formatter={(value: number) => money(value)}
            labelFormatter={(value) => points[Number(value) - 1]?.label ?? `Point ${value}`}
          />
          <Area type="monotone" dataKey="equity" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.14} strokeWidth={1.75} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function ChainOfThought({ step, symbol }: { step: AgentStep; symbol: string }) {
  const tools = step.tool_calls ?? [];
  const inputKeys = Object.keys(step.input ?? {});
  const running = step.status === "running";
  const failed = step.status === "failed" || Boolean(step.error);
  return (
    <ol className="relative space-y-3 border-l border-white/10 py-1 pl-4">
      <li className="thought-node">
        <p className="text-[11px] font-medium text-foreground/90">Reading context</p>
        <p className="mt-0.5 text-[11px] leading-5 text-muted">
          {inputKeys.length
            ? `${inputKeys.length} saved field${inputKeys.length === 1 ? "" : "s"} from the run.`
            : "No input was saved for this step."}
        </p>
      </li>
      {tools.map((tool, index) => (
        <li key={`${step.step_id}-thought-${index}`} className="thought-node">
          <p className="text-[11px] font-medium text-foreground/90">
            Calling {textValue(tool.name ?? tool.tool_name, "tool")}
          </p>
          <p className="mt-0.5 line-clamp-2 text-[11px] leading-5 text-muted">
            {textValue(tool.arguments ? JSON.stringify(tool.arguments) : tool.description, "Tool call recorded.")}
          </p>
        </li>
      ))}
      <li className="thought-node">
        {running ? (
          <p className="flex items-center gap-2 text-[11px]">
            <span className="text-shimmer">Thinking</span>
            <ThinkDots />
          </p>
        ) : failed ? (
          <>
            <p className="text-[11px] font-medium text-negative">Failed</p>
            <p className="mt-0.5 text-[11px] leading-5 text-negative/90">{step.error ?? "This step failed."}</p>
          </>
        ) : (
          <>
            <p className="text-[11px] font-medium text-foreground/90">Result</p>
            <p className="mt-0.5 text-[11px] leading-5 text-muted">{stepSummary(step, symbol)}</p>
          </>
        )}
      </li>
    </ol>
  );
}

function EventCard({ event }: { event: SystemEvent }) {
  const failed = /fail|error/i.test(`${event.type} ${event.message}`);
  return (
    <li className="grid grid-cols-[3.4rem_minmax(0,7rem)_minmax(0,1fr)] items-baseline gap-2 py-2 text-xs sm:grid-cols-[4.5rem_minmax(0,9rem)_minmax(0,1fr)] sm:gap-3">
      <span className="text-[11px] text-muted-foreground">{clockLabel(event.timestamp)}</span>
      <span className={cn("truncate text-foreground/80", failed && "text-negative")}>{event.type.replaceAll("_", " ")}</span>
      <span className="break-words leading-5 text-muted" title={event.message}>{event.message}</span>
    </li>
  );
}

export function Dashboard() {
  const [data, setData] = useState<DashboardState | null>(null);
  const [connection, setConnection] = useState<"connecting" | "online" | "offline">("connecting");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState("data_worker");
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  const [openSteps, setOpenSteps] = useState<Record<string, boolean>>({});
  const [feedAgentId, setFeedAgentId] = useState("all");
  const [tab, setTab] = useState<DashboardTab>("graph");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [showStart, setShowStart] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>("connections");
  const [railOpen, setRailOpen] = useState(false);
  const [railTab, setRailTab] = useState<RailTab>("running");
  const [search, setSearch] = useState("");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [selectedPositions, setSelectedPositions] = useState<string[]>([]);
  const [startSymbols, setStartSymbols] = useState<string[]>([]);
  const [startTimeframes, setStartTimeframes] = useState<string[]>([]);
  const [startStep, setStartStep] = useState(0);
  const [startDefaultsLoaded, setStartDefaultsLoaded] = useState(false);
  const [accountEquity, setAccountEquity] = useState("");
  const [startDryRun, setStartDryRun] = useState(true);
  const [startAttempts, setStartAttempts] = useState(DEFAULT_ATTEMPTS);
  const [executionStatusFilter, setExecutionStatusFilter] = useState("");
  const [command, setCommand] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const [wideViewport, setWideViewport] = useState<boolean | null>(null);
  const notifiedFailures = useRef(new Set<string>());
  // Failures already in history on first load are shown in the run list, not toasted.
  const historyAcknowledged = useRef(false);
  const rail = usePanelWidth("mats.panel.rail", RAIL_BOUNDS);
  const inspector = usePanelWidth("mats.panel.inspector", INSPECTOR_BOUNDS);
  const viewportWidth = useViewportWidth();
  const stickToEnd = useRef(true);
  const feedRef = useRef<HTMLDivElement>(null);

  const updateState = useCallback((incoming: DashboardState) => {
    setData(incoming);
    setConnection("online");
    setSelectedRunId((current) => {
      if (current && incoming.runs.some((run) => run.run_id === current)) return current;
      return incoming.runs.find((run) =>
        isLiveRun(run, incoming.active_run_ids, incoming.entry_watchers),
      )?.run_id ?? null;
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
    if (!historyAcknowledged.current) {
      historyAcknowledged.current = true;
      for (const run of data.runs) {
        if (["failed", "rejected"].includes(run.status)) notifiedFailures.current.add(run.run_id);
      }
      for (const operation of data.operations) {
        if (operation.status === "failed") notifiedFailures.current.add(operation.operation_id);
      }
      return;
    }
    for (const operation of data.operations) {
      if (operation.status !== "failed" || notifiedFailures.current.has(operation.operation_id)) continue;
      notifiedFailures.current.add(operation.operation_id);
      setNotice({ kind: "error", message: operation.error ?? "Monitor action failed." });
    }
    for (const run of data.runs) {
      if (!["failed", "rejected"].includes(run.status) || notifiedFailures.current.has(run.run_id)) continue;
      notifiedFailures.current.add(run.run_id);
      if (run.diagnosis?.kind === "rejected") {
        setNotice({ kind: "info", message: `${run.symbol}: ${run.diagnosis.title.toLowerCase()}.` });
        continue;
      }
      const event = run.events.find((item) => /fail|error/i.test(`${item.type} ${item.message}`));
      setNotice({
        kind: "error",
        message: event?.message ?? `Run ${run.run_id} failed. Inspect system events for details.`,
      });
    }
  }, [data]);

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(null), notice.kind === "error" ? 8000 : 4500);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (showStart) setShowStart(false);
      else if (settingsOpen) setSettingsOpen(false);
      else if (railOpen) setRailOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [railOpen, settingsOpen, showStart]);

  const runs = data?.runs ?? EMPTY_RUNS;
  const activeRunIds = data?.active_run_ids ?? [];
  const entryWatchers = data?.entry_watchers ?? [];
  const dryRunEnabled = data?.settings.dry_run ?? true;
  const killSwitchEnabled = data?.settings.kill_switch ?? true;
  const selectedRun = runs.find((run) => run.run_id === selectedRunId) ?? null;
  const selectedEntryWatcher = selectedRun
    ? entryWatchers.find((watcher) => watcher.run_id === selectedRun.run_id)
    : null;
  const anyRunning = runs.some((run) => run.status === "running")
    || (selectedRun?.steps.some((step) => step.status === "running") ?? false);

  useEffect(() => {
    if (!anyRunning) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [anyRunning]);

  useEffect(() => {
    if (!selectedRun) {
      setSelectedStepId(null);
      return;
    }
    setSelectedStepId((current) => {
      const steps = selectedRun.steps.filter((step) => step.agent_name !== "initialize");
      if (current && steps.some((step) => step.step_id === current)) return current;
      const running = [...steps].reverse().find((step) => step.status === "running");
      return running?.step_id ?? steps.at(-1)?.step_id ?? null;
    });
  }, [selectedRun]);

  const activeAttempt = latestAttempt(selectedRun);
  const curve = equityPoints(activeAttempt);
  const tokensForRun = selectedRun?.steps.reduce((sum, step) => sum + step.tokens_used, 0) ?? 0;
  const costForRun = selectedRun?.steps.reduce((sum, step) => sum + step.cost, 0) ?? 0;
  const runSymbols = useMemo(
    () => [...new Set([...(data?.settings.symbols ?? []), ...COIN_SYMBOLS, ...runs.map((run) => run.symbol)])],
    [data?.settings.symbols, runs],
  );
  const liveRuns = runs
    .filter((run) => isLiveRun(run, activeRunIds, entryWatchers))
    .sort((left, right) => right.started_at.localeCompare(left.started_at));
  const pastRuns = runs
    .filter((run) => !isLiveRun(run, activeRunIds, entryWatchers))
    .sort((left, right) => right.started_at.localeCompare(left.started_at));
  const railRuns = (railTab === "running" ? liveRuns : pastRuns).filter((run) =>
    `${run.symbol} ${run.run_id}`.toLowerCase().includes(search.trim().toLowerCase()),
  );
  const openPositions = (data?.positions ?? []).filter((position) => position.status === "open");
  const filteredPositions = openPositions.filter((position) => !symbolFilter || position.symbol === symbolFilter);
  const executions = runs.flatMap((run) => run.executions.map((execution) => ({ ...execution, symbol: run.symbol })));
  const filteredExecutions = executions.filter(
    (execution) =>
      (!symbolFilter || execution.symbol === symbolFilter) &&
      (!executionStatusFilter || execution.status === executionStatusFilter),
  );
  const feedSteps = useMemo(
    () =>
      (selectedRun?.steps ?? [])
        .filter((step) => step.agent_name !== "initialize")
        .filter((step) => feedAgentId === "all" || step.agent_name === feedAgentId)
        .sort((left, right) => left.started_at.localeCompare(right.started_at)),
    [feedAgentId, selectedRun],
  );
  const visibleSteps = (selectedRun?.steps ?? []).filter((step) => step.agent_name !== "initialize");
  const focusedStep = visibleSteps.find((step) => step.step_id === selectedStepId)
    ?? [...visibleSteps].reverse().find((step) => step.status === "running")
    ?? visibleSteps.at(-1)
    ?? null;
  const inspectorAgentId = focusedStep?.agent_name ?? selectedAgent;
  const inspectorAgent = AGENTS.find((agent) => agent.id === inspectorAgentId);
  const centerTab: DashboardTab = wideViewport === true && tab === "agent" ? "graph" : tab;
  const selectedIsLive = Boolean(selectedRun && isLiveRun(selectedRun, activeRunIds, entryWatchers));
  const showRun = Boolean(selectedRun && (railTab === "history" || selectedIsLive));
  const selectedIsActive = Boolean(selectedRun && activeRunIds.includes(selectedRun.run_id));
  const resumeDisabled = !selectedRun
    || (
      !["failed", "stopped", "running"].includes(selectedRun.status)
      && !["stopped", "failed"].includes(selectedEntryWatcher?.status ?? "")
    )
    || selectedIsActive
    || busyAction === "resume";
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
  const usageTotals = useMemo(() => {
    return runs.reduce(
      (totals, run) => {
        for (const step of run.steps) {
          totals.tokens += step.tokens_used;
          totals.cost += step.cost;
        }
        return totals;
      },
      { tokens: 0, cost: 0 },
    );
  }, [runs]);
  const agentsAreWorking = Boolean(
    selectedRun?.status === "running"
    || feedSteps.some((step) => step.status === "running")
    || selectedEntryWatcher?.status === "watching",
  );

  useEffect(() => {
    const node = feedRef.current;
    if (!node) return;
    if (centerTab !== "graph") {
      node.scrollTop = 0;
      return;
    }
    if (!stickToEnd.current) return;
    node.scrollTop = node.scrollHeight;
  }, [centerTab, feedSteps.length, selectedRun?.status, selectedRunId]);

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

  async function launchRun(
    symbols: string[],
    timeframes: string[],
    equity: string,
    dryRun: boolean,
    attempts = startAttempts,
  ) {
    if (!symbols.length || !timeframes.length) {
      setNotice({ kind: "error", message: "Select at least one symbol and timeframe." });
      return;
    }
    if (
      !dryRun &&
      !data?.settings.kill_switch &&
      !window.confirm("Dry-run and the kill switch are both off. This can place guarded Bybit testnet orders. Continue?")
    ) {
      return;
    }
    const payload: Record<string, unknown> = { symbols, timeframes, dry_run: dryRun, max_attempts: attempts };
    if (equity.trim()) {
      const value = Number(equity.replace(/,/g, ""));
      if (!Number.isFinite(value) || value <= 0) {
        setNotice({ kind: "error", message: "Capital has to be a positive number." });
        return;
      }
      payload.account_equity = value;
    }
    const modelId = data?.connections?.active_model_id;
    if (modelId) payload.model_id = modelId;
    const result = await act(
      "start",
      payload,
      `${symbols.length} ${symbols.length === 1 ? "run" : "runs"} started in parallel.`,
    );
    if (result) {
      const ids = result.run_ids;
      if (Array.isArray(ids)) {
        const validIds = ids.filter((id): id is string => typeof id === "string");
        if (validIds[0]) setSelectedRunId(validIds[0]);
      }
      setShowStart(false);
      setCommand("");
      setRailTab("running");
      setTab("graph");
    }
  }

  async function submitStart(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await launchRun(startSymbols, startTimeframes, accountEquity, startDryRun);
  }

  async function continueRejectedRun(run: Run) {
    await act(
      "resume",
      { run_id: run.run_id, extra_attempts: EXTRA_ATTEMPTS },
      `${run.symbol}: trying ${EXTRA_ATTEMPTS} more strategy attempts.`,
    );
  }

  function reconfigureRun(run: Run) {
    const config = run.config;
    const timeframes = Array.isArray(config.timeframes_to_consider)
      ? config.timeframes_to_consider.filter((item): item is string => typeof item === "string")
      : [];
    const previousLimit = typeof config.max_revision_loops === "number" ? config.max_revision_loops + 1 : DEFAULT_ATTEMPTS;
    const equity = typeof config.account_equity === "number" ? String(config.account_equity) : "";
    setStartSymbols([run.symbol]);
    if (timeframes.length) setStartTimeframes(timeframes);
    setAccountEquity(equity);
    setStartAttempts(Math.min(previousLimit + EXTRA_ATTEMPTS, MAX_ATTEMPTS));
    if (data) setStartDryRun(data.settings.dry_run);
    setStartStep(1);
    setShowStart(true);
    setRailOpen(false);
  }

  function submitCommand(event?: FormEvent) {
    event?.preventDefault();
    const text = command.trim();
    if (!text || !data) return;
    const parsed = parseRunRequest(text);
    if (!parsed.symbols.length) {
      setNotice({
        kind: "info",
        message: "Name the markets to trade. For example: Start trading BTC/USDT and ETH/USDT.",
      });
      return;
    }
    const dryRun = parsed.dryRun ?? data.settings.dry_run;
    setStartSymbols(parsed.symbols);
    if (parsed.timeframes.length) setStartTimeframes(parsed.timeframes);
    setAccountEquity(parsed.equitySpecified && parsed.equity != null ? String(parsed.equity) : "");
    setStartDryRun(dryRun);
    if (parsed.timeframes.length && parsed.equitySpecified) {
      void launchRun(parsed.symbols, parsed.timeframes, parsed.equity == null ? "" : String(parsed.equity), dryRun);
      return;
    }
    setStartStep(parsed.timeframes.length ? 2 : 1);
    setShowStart(true);
    setRailOpen(false);
    setCommand("");
  }

  function openStartDialog() {
    if (data) setStartDryRun(data.settings.dry_run);
    setStartStep(0);
    setShowStart(true);
    setRailOpen(false);
  }

  async function toggleSetting(name: "dry_run" | "kill_switch", enabled: boolean) {
    if (!data) return;
    if (
      enabled === false &&
      !window.confirm(`Disable ${name === "dry_run" ? "dry-run protection" : "the kill switch"}?`)
    ) {
      return;
    }
    await act("toggle", { name, enabled, confirmed: true });
  }

  async function submitStop() {
    if (!selectedRun) return;
    if (!window.confirm("Stop this run and its strategy algorithm? Other runs keep trading.")) return;
    await act("stop", { run_id: selectedRun.run_id }, "Stop requested; this strategy will stop trading.");
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

  async function submitFlatten() {
    if (!selectedPositions.length) return;
    if (!window.confirm(`Flatten ${selectedPositions.length} selected position(s) on Bybit testnet?`)) return;
    const result = await act("flatten", { run_ids: selectedPositions, confirmed: true }, "Flatten request completed.");
    if (result) setSelectedPositions([]);
  }

  function pickRun(runId: string) {
    setSelectedRunId(runId);
    setFeedAgentId("all");
    setTab("graph");
    setRailOpen(false);
    stickToEnd.current = true;
  }

  function focusAgent(agentId: string) {
    setSelectedAgent(agentId);
    setFeedAgentId(agentId);
    const step = [...(selectedRun?.steps ?? [])].reverse().find((item) => item.agent_name === agentId);
    if (step) setSelectedStepId(step.step_id);
    setTab(wideViewport === true ? "graph" : "agent");
  }

  function stepIsOpen(step: AgentStep): boolean {
    if (Object.prototype.hasOwnProperty.call(openSteps, step.step_id)) return openSteps[step.step_id];
    return step.status === "running" || step.status === "failed";
  }

  function toggleStep(step: AgentStep) {
    const next = !stepIsOpen(step);
    setOpenSteps((current) => ({ ...current, [step.step_id]: next }));
    setSelectedStepId(step.step_id);
    setSelectedAgent(step.agent_name);
  }

  function onFeedScroll(event: UIEvent<HTMLDivElement>) {
    const node = event.currentTarget;
    stickToEnd.current = node.scrollHeight - node.scrollTop - node.clientHeight < 96;
  }

  function renderRail() {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex items-center gap-2.5 px-4 pb-5 pt-4">
          <Mark />
          <span className="text-[15px] font-semibold tracking-tight">MATS</span>
          <span
            className={cn(
              "ml-auto h-1.5 w-1.5 rounded-full",
              connection === "online" ? "bg-positive" : connection === "offline" ? "bg-negative" : "bg-muted-foreground agent-live",
            )}
            title={connection === "online" ? "Live updates" : connection === "offline" ? "Reconnecting" : "Connecting"}
          />
        </div>
        <div className="flex items-center gap-1 px-3">
          <button
            type="button"
            onClick={() => {
              setRailTab("running");
              if (selectedRun && !isLiveRun(selectedRun, activeRunIds, entryWatchers)) setSelectedRunId(null);
            }}
            className={cn(
              "rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
              railTab === "running" ? "bg-card text-foreground" : "text-muted hover:text-foreground",
            )}
          >
            Running
            {liveRuns.length > 0 && <span className="ml-1.5 text-[10px] text-muted-foreground">{liveRuns.length}</span>}
          </button>
          <button
            type="button"
            onClick={() => setRailTab("history")}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
              railTab === "history" ? "bg-card text-foreground" : "text-muted hover:text-foreground",
            )}
          >
            <History className="h-3 w-3" />
            History
          </button>
          <button
            type="button"
            onClick={openStartDialog}
            className="ml-auto grid h-8 w-8 place-items-center rounded-full text-muted transition-colors hover:bg-card hover:text-foreground"
            aria-label="New run"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
        <div className="relative mx-3 mt-3">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search runs"
            className="h-9 min-h-9 rounded-full border-transparent bg-card pl-9 text-xs focus:border-transparent"
            aria-label="Search runs"
          />
        </div>
        <div className="mt-2 min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
          {railRuns.map((run) => {
            const live = isLiveRun(run, activeRunIds, entryWatchers);
            return (
              <button
                key={run.run_id}
                type="button"
                onClick={() => pickRun(run.run_id)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left transition-colors",
                  selectedRunId === run.run_id ? "bg-card text-foreground" : "text-muted hover:bg-card/70 hover:text-foreground",
                )}
              >
                <CoinMark symbol={run.symbol} small />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium text-foreground">{run.symbol}</span>
                  <span className={cn("mt-0.5 block truncate text-[11px]", live ? "text-muted" : "text-muted-foreground")}>
                    {runSubtitle(run, entryWatchers)}
                  </span>
                </span>
                <span
                  className={cn(
                    "h-1.5 w-1.5 shrink-0 rounded-full",
                    live ? "bg-accent agent-live" : run.diagnosis?.kind === "rejected" ? "bg-accent" : run.status === "failed" ? "bg-negative" : run.status === "completed" ? "bg-positive" : "bg-muted-foreground",
                  )}
                />
              </button>
            );
          })}
          {!railRuns.length && (
            <p className="px-3 py-6 text-xs leading-5 text-muted">
              {search.trim()
                ? "No runs match that search."
                : railTab === "running"
                  ? "Nothing is running. Start a run and the agents will show up here."
                  : "Finished runs will collect here."}
            </p>
          )}
        </div>
        <div className="border-t border-white/[0.05] p-2">
          <button
            type="button"
            onClick={() => {
              setSettingsSection("connections");
              setSettingsOpen(true);
            }}
            className="flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left transition-colors hover:bg-card"
          >
            <span className="grid h-8 w-8 place-items-center rounded-full bg-gradient-to-br from-sky-400 via-violet-400 to-orange-300 text-xs font-semibold text-zinc-950">
              Z
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-medium">Zade</span>
              <span className="block text-[11px] text-muted-foreground">Workspace</span>
            </span>
            <Menu className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
      </div>
    );
  }

  function renderActivity() {
    if (!showRun || !selectedRun) return null;
    return (
      <div className="mx-auto max-w-3xl">
        {!feedSteps.length ? (
          <div className="flex min-h-64 flex-col items-center justify-center px-6 text-center">
            <div className="mb-4 grid h-11 w-11 place-items-center rounded-full bg-card text-muted">
              {selectedRun.status === "running" ? <ThinkDots /> : <Circle className="h-4 w-4" />}
            </div>
            <p className="text-sm font-medium">
              {selectedRun.status === "running" ? "Waiting for the first step" : "No activity from this agent yet"}
            </p>
            <p className="mt-1 max-w-xs text-xs leading-5 text-muted">
              Inputs, tool calls, and results appear here as the run is saved.
            </p>
          </div>
        ) : (
          <div className="space-y-1">
            {feedSteps.map((step) => {
              const open = stepIsOpen(step);
              const running = step.status === "running";
              const failed = step.status === "failed" || Boolean(step.error);
              const tools = step.tool_calls ?? [];
              const showCurve = step.agent_name === "backtest"
                && step.step_id === [...feedSteps].reverse().find((item) => item.agent_name === "backtest")?.step_id;
              return (
                <article
                  key={step.step_id}
                  className={cn(
                    "rise-in rounded-2xl px-2 py-3 transition-colors sm:px-3",
                    selectedStepId === step.step_id && "bg-white/[0.03]",
                    running && "step-running",
                    failed && "step-failed bg-negative/[0.04]",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => toggleStep(step)}
                    className="flex w-full items-start gap-3 text-left"
                    aria-expanded={open}
                  >
                    <PersonaAvatar name={agentName(step.agent_name)} src={agentPersona(step.agent_name)} size={36} />
                    <span className="min-w-0 flex-1 pt-0.5">
                      <span className="flex items-center gap-2">
                        <span className="truncate text-sm font-semibold">{agentName(step.agent_name)}</span>
                        {running && <ThinkDots />}
                        <time className="ml-auto shrink-0 text-[11px] text-muted-foreground" dateTime={step.finished_at ?? step.started_at}>
                          {running ? formatDuration(step.started_at, null, now) : clockLabel(step.finished_at ?? step.started_at)}
                        </time>
                      </span>
                      <span className={cn("mt-1 block text-sm leading-6", failed ? "text-negative" : "text-foreground/85")}>
                        {running ? (
                          <span className="text-shimmer">{agentWorkingLabel(step.agent_name, selectedRun.symbol)}</span>
                        ) : failed ? (
                          step.error ?? "This step failed."
                        ) : (
                          stepSummary(step, selectedRun.symbol)
                        )}
                      </span>
                      {!open && !!tools.length && (
                        <span className="mt-2 flex flex-wrap gap-1.5">
                          {tools.slice(0, 3).map((tool, index) => (
                            <span key={`${step.step_id}-pill-${index}`} className="rounded-md border border-white/[0.06] px-1.5 py-0.5 font-mono text-[10px] text-muted">
                              {textValue(tool.name ?? tool.tool_name, "tool")}
                            </span>
                          ))}
                        </span>
                      )}
                    </span>
                    <ChevronRight className={cn("mt-1 h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200", open && "rotate-90")} />
                  </button>
                  {open && (
                    <div className="mt-3 space-y-3 pl-[48px] pr-1">
                      <ChainOfThought step={step} symbol={selectedRun.symbol} />
                      {showCurve && <EquitySpark points={curve} />}
                      <div className="space-y-2">
                        <RawSection title="Input context" value={step.input} />
                        {!!tools.length && <RawSection title={`Tools called (${tools.length})`} value={tools} defaultOpen={running} />}
                        <RawSection title="Structured output" value={step.output} defaultOpen={!running && !failed} />
                        {step.error && <RawSection title="Error" value={{ status: step.status, error: step.error }} defaultOpen />}
                      </div>
                      <p className="text-[10px] text-muted-foreground">
                        {step.tokens_used.toLocaleString()} tokens · {money(step.cost, 4)} · {formatDuration(step.started_at, step.finished_at, now)}
                      </p>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
        {selectedRun.status === "running" && !feedSteps.some((step) => step.status === "running") && (
          <div className="mt-4 flex items-center gap-3 rounded-2xl bg-card px-4 py-3">
            <ThinkDots />
            <div>
              <p className="text-shimmer text-xs font-medium">Moving to the next step</p>
              <p className="mt-0.5 text-[11px] text-muted">New activity shows up as soon as it is saved.</p>
            </div>
          </div>
        )}
        {selectedRun.diagnosis?.kind === "rejected" && (
          <div className="mt-4">
            <RejectedRunNotice
              diagnosis={selectedRun.diagnosis}
              extraAttempts={EXTRA_ATTEMPTS}
              busy={busyAction === "resume"}
              onMoreAttempts={() => continueRejectedRun(selectedRun)}
              onChangeSettings={() => reconfigureRun(selectedRun)}
              onDismiss={() => setSelectedRunId(null)}
            />
          </div>
        )}
        {selectedRun.status !== "failed" && selectedRun.diagnosis && selectedRun.diagnosis.kind !== "rejected" && (
          <div className="mt-4">
            <RunDiagnosisBanner diagnosis={selectedRun.diagnosis} />
          </div>
        )}
        {selectedRun.status === "failed" && selectedRun.diagnosis?.kind !== "rejected" && (
          <div className="step-failed mt-4 flex items-center justify-between gap-4 rounded-2xl bg-negative/10 px-4 py-3">
            <div className="flex items-center gap-3">
              <AlertCircle className="h-4 w-4 shrink-0 text-negative" />
              <div className="min-w-0">
                <p className="text-xs font-medium">{selectedRun.diagnosis?.title ?? "This run stopped with an error"}</p>
                {selectedRun.diagnosis?.detail.split(" | ").map((line) => (
                  <p key={line} className="mt-1 break-words text-[11px] leading-4 text-foreground/80">{line}</p>
                ))}
                <p className="mt-1 text-[11px] leading-4 text-muted">
                  {selectedRun.diagnosis?.hint ?? "Check events, or resume when you are ready."}
                </p>
              </div>
            </div>
            <Button size="sm" variant="ghost" className="rounded-full" disabled={resumeDisabled} onClick={submitResume}>
              <Play className="h-3.5 w-3.5" />
              Resume
            </Button>
          </div>
        )}
      </div>
    );
  }

  function renderInspector() {
    const attempts = selectedRun?.steps.filter((step) => step.agent_name === inspectorAgentId) ?? [];
    const latest = focusedStep?.agent_name === inspectorAgentId ? focusedStep : attempts.at(-1) ?? null;
    const stepOutput = latest?.output ?? {};
    const decision = activeAttempt?.decision;
    const executionResult = isObject(stepOutput.execution_result) ? stepOutput.execution_result : {};
    const liveSignal = isObject(stepOutput.live_signal)
      ? stepOutput.live_signal
      : isObject(executionResult.live_signal)
        ? executionResult.live_signal
        : {};
    const executionOrders = objectRows(executionResult.orders ?? stepOutput.orders);
    const orderWasPlaced = executionOrders.some(
      (order) => Boolean(order.id ?? order.exchange_order_id) && order.status !== "dry_run",
    );
    const actions = latest?.tool_calls ?? [];
    const toolEvidence = actions.map((call) => ({
      ...call,
      result_snippet: JSON.stringify(stepOutput).slice(0, 500),
    }));
    const position = openPositions.find((item) => item.run_id === selectedRun?.run_id);
    const runDuration = selectedRun ? formatDuration(selectedRun.started_at, selectedRun.finished_at, now) : "—";

    return (
      <div className="space-y-6">
        <section>
          <p className="text-[11px] font-medium text-muted-foreground">Agent details</p>
          {latest && selectedRun ? (
            <>
              <div className="mt-3 flex items-center gap-3">
                <PersonaAvatar name={inspectorAgent?.name ?? agentName(inspectorAgentId)} src={inspectorAgent?.persona} size={40} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold">{inspectorAgent?.name ?? agentName(inspectorAgentId)}</p>
                  <p className="truncate text-[11px] text-muted-foreground" title={selectedRun.run_id}>
                    {selectedRun.symbol} · {shortId(selectedRun.run_id)}
                  </p>
                </div>
                {latest.status === "running" && <ThinkDots />}
              </div>
              <div className="mt-4 grid grid-cols-3 gap-2">
                <div className="rounded-xl border border-white/[0.06] px-3 py-2.5">
                  <p className="text-[10px] text-muted-foreground">Tokens</p>
                  <p className="mt-1 text-sm font-semibold tabular-nums">{latest.tokens_used.toLocaleString()}</p>
                </div>
                <div className="rounded-xl border border-white/[0.06] px-3 py-2.5">
                  <p className="text-[10px] text-muted-foreground">Cost</p>
                  <p className="mt-1 text-sm font-semibold tabular-nums">{money(latest.cost, 4)}</p>
                </div>
                <div className="rounded-xl border border-white/[0.06] px-3 py-2.5">
                  <p className="text-[10px] text-muted-foreground">Duration</p>
                  <p className="mt-1 text-sm font-semibold tabular-nums">{formatDuration(latest.started_at, latest.finished_at, now)}</p>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <StatusBadge status={latest.status} />
                {inspectorAgentId === "critique" && decision && <StatusBadge status={decision} />}
              </div>
              <div className="mt-4 space-y-2">
                <RawSection title="Current / last input summary" value={latest.input} />
                <RawSection title={`Tools called (${actions.length})`} value={toolEvidence} defaultOpen={actions.length > 0} />
                <RawSection title="Structured output" value={stepOutput} />
                {latest.error && <RawSection title="Errors / retries" value={{ error: latest.error, status: latest.status }} defaultOpen />}
              </div>
            </>
          ) : (
            <p className="mt-3 text-xs leading-5 text-muted">
              {selectedRun ? "This agent has not saved a step on the selected run." : "Select a run to inspect an agent step."}
            </p>
          )}
          {inspectorAgentId === "strategy_builder" && activeAttempt && (
            <div className="mt-4">
              <p className="text-xs font-medium">Strategy template</p>
              <p className="mt-1 text-[11px] text-muted">{textValue(activeAttempt.strategy.template_name, "No template recorded")}</p>
              <div className="mt-2"><RawSection title="Template parameters" value={activeAttempt.strategy} defaultOpen /></div>
            </div>
          )}
          {inspectorAgentId === "critique" && activeAttempt && (
            <div className="mt-4 rounded-xl border border-white/[0.06] p-3">
              <p className="text-xs font-medium">Critique decision</p>
              <div className="mt-2"><StatusBadge status={textValue(activeAttempt.decision, "unknown")} /></div>
              {activeAttempt.revision_instructions && (
                <p className="mt-2 text-[11px] leading-5 text-muted">{activeAttempt.revision_instructions}</p>
              )}
            </div>
          )}
          {inspectorAgentId === "execution" && latest && (
            <div className="mt-4 rounded-xl border border-white/[0.06] p-3">
              <p className="text-xs font-medium">Live signal and order outcome</p>
              <p className="mt-1 text-[11px] leading-5 text-muted">{textValue(liveSignal.reason, "No live signal reason was recorded.")}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <StatusBadge status={textValue(liveSignal.action, textValue(executionResult.status, "unknown"))} />
                <Badge className={orderWasPlaced ? "border-positive/20 bg-positive/10 text-positive" : "border-transparent bg-background text-muted"}>
                  {orderWasPlaced ? "Order placed" : "No exchange order placed"}
                </Badge>
              </div>
            </div>
          )}
        </section>

        {selectedEntryWatcher && (
          <section>
            <div className="flex items-center justify-between gap-3">
              <p className="text-[11px] font-medium text-muted-foreground">Strategy algorithm</p>
              <StatusBadge status={selectedEntryWatcher.status} />
            </div>
            <p className="mt-2 text-xs leading-5 text-muted">
              {selectedEntryWatcher.status === "watching"
                ? `Checking signal, risk, and news every ${selectedEntryWatcher.interval_seconds} seconds.`
                : selectedEntryWatcher.status === "in_position"
                  ? `Managing the open position every ${selectedEntryWatcher.interval_seconds} seconds.`
                  : selectedEntryWatcher.status === "paused"
                    ? selectedEntryWatcher.error ?? "Paused while dry-run or the kill switch is enabled."
                    : selectedEntryWatcher.error ?? "No live checks are active."}
            </p>
            {selectedEntryWatcher.signal && (
              <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
                Latest signal: {textValue(selectedEntryWatcher.signal.action, "unknown")} · {textValue(selectedEntryWatcher.signal.reason, "No reason recorded")}
              </p>
            )}
            {selectedEntryWatcher.last_checked_at && (
              <p className="mt-1 text-[10px] text-muted-foreground">Last checked {dateLabel(selectedEntryWatcher.last_checked_at)}</p>
            )}
            {selectedEntryWatcher.error && selectedEntryWatcher.status === "watching" && (
              <p role="status" className="mt-2 text-[10px] leading-4 text-negative">Latest check failed: {selectedEntryWatcher.error}</p>
            )}
          </section>
        )}

        <section>
          <div className="flex items-center justify-between">
            <p className="text-[11px] font-medium text-muted-foreground">Research snapshot</p>
            <button type="button" onClick={() => setTab("strategy")} className="text-[11px] text-muted transition-colors hover:text-foreground">
              Full report
            </button>
          </div>
          {selectedRun ? (
            <div className="mt-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-medium">{selectedRun.symbol}</p>
                <StatusBadge status={selectedRun.status} />
              </div>
              <div className="mt-3 flex items-end justify-between">
                <div>
                  <p className="text-[10px] text-muted-foreground">Backtest net PnL</p>
                  <p className={cn("mt-1 text-base font-semibold tabular-nums", activeAttempt && numberValue(activeAttempt.report?.net_pnl) < 0 ? "text-negative" : "")}>
                    {activeAttempt ? money(activeAttempt.report?.net_pnl) : "—"}
                  </p>
                </div>
                <p className="text-[10px] text-muted-foreground">{runDuration} · {tokensForRun.toLocaleString()} tokens</p>
              </div>
              {curve.length > 1 ? (
                <div className="mt-2 h-16">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={curve} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
                      <Area type="monotone" dataKey="equity" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.12} strokeWidth={1.5} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <p className="mt-3 text-[11px] text-muted">
                  {selectedRun.status === "running" ? "The equity curve appears after backtesting." : "No backtest curve saved."}
                </p>
              )}
              <p className="mt-2 truncate text-[11px] text-muted">
                {activeAttempt ? `${textValue(activeAttempt.strategy.template_name, "Strategy candidate")} · v${activeAttempt.version}` : "Strategy not built yet"}
              </p>
            </div>
          ) : (
            <p className="mt-3 text-xs text-muted">Select a run to see its trading context.</p>
          )}
        </section>

        <section>
          <div className="flex items-center justify-between">
            <p className="text-[11px] font-medium text-muted-foreground">Open position</p>
            <button type="button" onClick={() => setTab("positions")} className="text-[11px] text-muted transition-colors hover:text-foreground">
              All positions
            </button>
          </div>
          {position ? (
            <dl className="mt-3 grid grid-cols-2 gap-y-2 text-xs">
              <dt className="text-muted">Side</dt>
              <dd className="text-right capitalize">{position.side} · {position.amount}</dd>
              <dt className="text-muted">Entry</dt>
              <dd className="text-right tabular-nums">{money(position.entry_price)}</dd>
              <dt className="text-muted">Stop</dt>
              <dd className="text-right tabular-nums">{money(position.stop_price)}</dd>
              <dt className="text-muted">Net PnL</dt>
              <dd className={cn("text-right tabular-nums", numberValue(position.net_pnl) >= 0 ? "text-positive" : "text-negative")}>{money(position.net_pnl)}</dd>
            </dl>
          ) : (
            <p className="mt-3 text-xs leading-5 text-muted">No open position on this run.</p>
          )}
        </section>
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
    const strategyName = textValue(attempt.strategy.name, "Unnamed strategy");
    const strategy = attempt.strategy;
    const facts: Array<[string, string]> = [
      ["Template", textValue(strategy.template_name)],
      ["Timeframe", textValue(strategy.chosen_timeframe)],
      ["Fast / slow", `${strategy.fast_period ?? "—"} / ${strategy.slow_period ?? "—"}`],
      ["Stop", strategy.stop_loss_pct == null ? "—" : `${(numberValue(strategy.stop_loss_pct) * 100).toFixed(2)}%`],
      ["Take profit", strategy.take_profit_pct == null ? "—" : `${(numberValue(strategy.take_profit_pct) * 100).toFixed(2)}%`],
      ["Starting capital", money(report.initial_capital)],
    ];
    const metrics: Array<{ label: string; value: string; tone?: "positive" | "negative" }> = [
      { label: "Net PnL", value: money(report.net_pnl), tone: numberValue(report.net_pnl) >= 0 ? "positive" : "negative" },
      { label: "Max drawdown", value: `${(numberValue(report.max_drawdown_pct) * 100).toFixed(2)}%` },
      { label: "Win rate", value: `${(numberValue(report.win_rate) * 100).toFixed(2)}%` },
      { label: "Trades", value: String(numberValue(report.total_trades)) },
      { label: "Fees", value: money(report.total_fees) },
      { label: "Slippage", value: money(report.total_slippage) },
    ];
    return (
      <div className="mx-auto max-w-5xl">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-semibold tracking-tight">{strategyName}</h2>
            <p className="mt-1 text-xs text-muted">Version {attempt.version} · {selectedRun.symbol}</p>
          </div>
          {attempt.decision && <StatusBadge status={attempt.decision} />}
        </div>
        <dl className="mt-6 grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-3">
          {facts.map(([label, value]) => (
            <div key={label}>
              <dt className="text-[11px] text-muted-foreground">{label}</dt>
              <dd className="mt-1 text-sm tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-6 grid grid-cols-2 gap-px overflow-hidden rounded-2xl bg-white/[0.06] sm:grid-cols-3 lg:grid-cols-6">
          {metrics.map((metric) => (
            <div key={metric.label} className="bg-background px-3 py-3">
              <p className="text-[11px] text-muted-foreground">{metric.label}</p>
              <p className={cn("mt-1 text-sm tabular-nums", metric.tone === "positive" && "text-positive", metric.tone === "negative" && "text-negative")}>{metric.value}</p>
            </div>
          ))}
        </div>
        <div className="mt-8">
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="text-sm font-medium">Price</h3>
            <p className="text-[11px] text-muted-foreground">{textValue(strategy.chosen_timeframe, "1d")} candles with each fill marked</p>
          </div>
          <BacktestChart
            symbol={selectedRun.symbol}
            timeframe={textValue(strategy.chosen_timeframe, "1d")}
            trades={trades}
          />
        </div>
        <div className="mt-8 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted">
          {attempt.decision && <span>Critique {attempt.decision.toLowerCase()}{attempt.issues?.length ? ` · ${attempt.issues[0]}` : ""}</span>}
          <button type="button" className="inline-flex items-center gap-1 text-accent hover:underline" onClick={() => focusAgent("strategy_builder")}>Strategy builder <ArrowRight className="h-3 w-3" /></button>
          <button type="button" className="inline-flex items-center gap-1 text-accent hover:underline" onClick={() => focusAgent("backtest")}>Backtest <ArrowRight className="h-3 w-3" /></button>
          <button type="button" className="inline-flex items-center gap-1 text-accent hover:underline" onClick={() => focusAgent("critique")}>Critique <ArrowRight className="h-3 w-3" /></button>
        </div>
        <details className="mt-4">
          <summary className="cursor-pointer text-[11px] text-muted-foreground">Record</summary>
          <div className="mt-3 space-y-3">
            <RawSection title="Strategy" value={attempt.strategy} />
            <RawSection title="Backtest" value={attempt.report} />
          </div>
        </details>
        <section className="mt-10">
          <h3 className="text-sm font-medium">Trades</h3>
          <p className="mt-1 text-[11px] text-muted-foreground">Gross and net outcomes with modeled fees and slippage.</p>
          {trades.length ? (
            <div className="mt-5 overflow-x-auto">
              <table className="w-full min-w-[740px] text-left text-xs">
                <thead className="text-[11px] text-muted-foreground">
                  <tr>
                    {["#", "Entry", "Exit", "Gross PnL", "Fees", "Slippage", "Net PnL", "Exit reason"].map((heading) => (
                      <th key={heading} className="pb-3 pr-4 font-medium">{heading}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {trades.map((trade, index) => (
                    <tr key={index} className="border-t border-white/[0.05] text-muted">
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
        </section>
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
    return (
      <div className="mx-auto max-w-3xl">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted">{openPositions.length} open</p>
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="h-7 rounded-full bg-transparent px-2 text-[11px] text-muted"
              value={symbolFilter}
              onChange={(event) => setSymbolFilter(event.target.value)}
              aria-label="Filter positions by symbol"
            >
              <option value="">All symbols</option>
              {availableSymbols.map((symbol) => <option key={symbol}>{symbol}</option>)}
            </select>
            <button type="button" className="text-[11px] text-muted hover:text-foreground" onClick={() => act("monitor", {}, "Monitor cycle started.")} disabled={busyAction === "monitor"}>
              {busyAction === "monitor" ? "Checking" : "Check"}
            </button>
            <button type="button" className="text-[11px] text-negative disabled:opacity-40" onClick={submitFlatten} disabled={!selectedPositions.length || busyAction === "flatten" || data?.settings.dry_run}>
              Flatten
            </button>
          </div>
        </div>
        {data?.settings.dry_run && (
          <p className="mt-2 text-[11px] text-muted-foreground">Dry-run is on. Turn it off before a live flatten.</p>
        )}
        {rows.length ? (
          <ul className="mt-2 divide-y divide-white/[0.04]">
            <li className="flex items-center gap-3 py-1.5 text-[11px] text-muted-foreground">
              <input type="checkbox" checked={allSelected} onChange={(event) => setSelectedPositions(event.target.checked ? rows.map((row) => row.run_id) : [])} aria-label="Select all positions" />
              <span className="w-28">Symbol</span>
              <span className="w-24">Side</span>
              <span className="w-28">Entry</span>
              <span className="min-w-0 flex-1">Strategy</span>
            </li>
            {rows.map((position) => (
              <li key={position.run_id} className="flex items-center gap-3 py-2 text-xs">
                <input type="checkbox" checked={selectedPositions.includes(position.run_id)} onChange={() => toggleSelection(position.run_id, selectedPositions, setSelectedPositions)} aria-label={`Select ${position.symbol} position`} />
                <span className="flex w-28 items-center gap-2 text-foreground"><CoinMark symbol={position.symbol} small />{position.symbol.split("/")[0]}</span>
                <span className="w-24 capitalize text-muted">{position.side} · {position.amount}</span>
                <span className="w-28 tabular-nums text-muted">{money(position.entry_price)}</span>
                <span className="min-w-0 flex-1 truncate text-muted">{textValue(position.strategy.name ?? position.strategy_key)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-8 text-center text-xs text-muted-foreground">No open positions.</p>
        )}
        <div className="mt-10 flex items-center justify-between gap-3">
          <p className="text-xs text-muted">Executions</p>
          <select
            className="h-7 rounded-full bg-transparent px-2 text-[11px] text-muted"
            value={executionStatusFilter}
            onChange={(event) => setExecutionStatusFilter(event.target.value)}
            aria-label="Filter executions by status"
          >
            <option value="">All statuses</option>
            {[...new Set(executions.map((execution) => execution.status))].map((status) => <option key={status}>{status}</option>)}
          </select>
        </div>
        {filteredExecutions.length ? (
          <ul className="mt-1 divide-y divide-white/[0.04]">
            {filteredExecutions.flatMap((execution) =>
              (execution.orders.length ? execution.orders : [{}]).map((order, index) => (
                <li key={`${execution.execution_id}-${index}`} className="flex items-baseline gap-3 py-2 text-xs">
                  <span className="w-16 shrink-0 text-foreground/90">{execution.symbol.split("/")[0]}</span>
                  <span className="w-20 shrink-0 text-muted">{textValue(order.status ?? execution.status)}</span>
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">
                    {typeof order.side === "string" ? order.side : textValue(order.id ?? order.exchange_order_id, "")}
                  </span>
                </li>
              )),
            )}
          </ul>
        ) : (
          <p className="mt-6 text-center text-xs text-muted-foreground">No executions saved.</p>
        )}
      </div>
    );
  }

  function renderEvents() {
    if (!selectedRun) {
      return <p className="py-16 text-center text-xs text-muted-foreground">Select a run to view its activity log.</p>;
    }
    const runEvents = selectedRun.events ?? [];
    if (!runEvents.length) {
      return <p className="py-16 text-center text-xs text-muted-foreground">No activity recorded for this run yet.</p>;
    }
    return (
      <div className="mx-auto max-w-3xl">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">Activity Log</h2>
            <p className="mt-1 text-[11px] text-muted-foreground">
              Events for {selectedRun.symbol} · {shortId(selectedRun.run_id)}
            </p>
          </div>
          {selectedEntryWatcher && <StatusBadge status={selectedEntryWatcher.status} />}
        </div>
        <ul className="divide-y divide-white/[0.04]">
          {runEvents.map((event) => <EventCard key={event.event_id} event={event} />)}
        </ul>
      </div>
    );
  }

  function renderSettings() {
    const agentUsage = new Map<string, { tokens: number; cost: number; steps: number }>();
    for (const step of selectedRun?.steps ?? []) {
      if (step.agent_name === "initialize") continue;
      const current = agentUsage.get(step.agent_name) ?? { tokens: 0, cost: 0, steps: 0 };
      current.tokens += step.tokens_used;
      current.cost += step.cost;
      current.steps += 1;
      agentUsage.set(step.agent_name, current);
    }
    return (
      <div
        className="fixed inset-0 z-50 grid place-items-center bg-black/55 p-2 backdrop-blur-md sm:p-4"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) setSettingsOpen(false);
        }}
      >
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="settings-title"
          className="rise-in grid h-[min(680px,calc(100vh-2rem))] w-full max-w-4xl overflow-hidden rounded-3xl border border-white/[0.07] bg-surface shadow-[0_20px_60px_rgba(0,0,0,0.5)] sm:grid-cols-[240px_minmax(0,1fr)]"
        >
          <div className="border-b border-white/[0.05] p-5 sm:border-b-0 sm:border-r">
            <p id="settings-title" className="text-sm text-muted">Settings</p>
            <div className="mt-5 flex items-center gap-3">
              <span className="grid h-9 w-9 place-items-center rounded-full bg-gradient-to-br from-sky-400 via-violet-400 to-orange-300 text-xs font-semibold text-zinc-950">
                Z
              </span>
              <div>
                <p className="text-sm font-medium">Zade</p>
                <p className="text-[11px] text-muted-foreground">Local operator</p>
              </div>
            </div>
            <nav className="mt-6 space-y-1" aria-label="Settings sections">
              <button
                type="button"
                onClick={() => setSettingsSection("connections")}
                className={cn("flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-sm transition-colors", settingsSection === "connections" ? "bg-white/10 text-foreground" : "text-muted hover:bg-white/5")}
              >
                <Cable className="h-4 w-4" />
                Connections
              </button>
              <button
                type="button"
                onClick={() => setSettingsSection("usage")}
                className={cn("flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-sm transition-colors", settingsSection === "usage" ? "bg-white/10 text-foreground" : "text-muted hover:bg-white/5")}
              >
                <Gauge className="h-4 w-4" />
                Usage
              </button>
            </nav>
          </div>
          <div className="relative min-h-0 overflow-y-auto p-6 sm:p-8">
            <button
              type="button"
              onClick={() => setSettingsOpen(false)}
              className="absolute right-4 top-4 grid h-8 w-8 place-items-center rounded-full text-muted transition-colors hover:bg-white/10 hover:text-foreground"
              aria-label="Close settings"
            >
              <X className="h-4 w-4" />
            </button>
            {settingsSection === "connections" ? (
              <div className="max-w-lg">
                <h2 className="text-lg font-semibold tracking-tight">Connections</h2>
                <p className="mt-1 text-xs leading-5 text-muted">
                  {connection === "online" ? "Dashboard API connected." : connection === "offline" ? "Dashboard API reconnecting." : "Connecting to the dashboard API."} Exchange and model keys stay in the Python process.
                </p>
                <div className="mt-6">
                  <ConnectionsPanel
                    connections={data?.connections}
                    busy={busyAction}
                    onSaveBybit={(apiKey, apiSecret) => {
                      if (!apiKey && !apiSecret) {
                        void act("save_bybit", { clear: true }, "Bybit is using the environment keys.");
                        return;
                      }
                      void act(
                        "save_bybit",
                        {
                          ...(apiKey ? { api_key: apiKey } : {}),
                          ...(apiSecret ? { api_secret: apiSecret } : {}),
                        },
                        "Bybit keys saved.",
                      );
                    }}
                    onSaveModel={(model) => {
                      void act("save_model", { model }, "Model saved.");
                    }}
                    onDeleteModel={(modelId) => {
                      void act("delete_model", { model_id: modelId }, "Model removed.");
                    }}
                    onSelectModel={(modelId) => {
                      void act("select_model", { model_id: modelId }, "Model selected.");
                    }}
                  />
                </div>
                <div className="mt-8 flex items-center justify-between gap-4 rounded-2xl bg-background/50 px-4 py-3">
                  <div>
                    <p className="flex items-center gap-2 text-sm font-medium"><ShieldCheck className="h-3.5 w-3.5 text-muted" /> Dry run</p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">Blocks exchange order placement. Default is on.</p>
                  </div>
                  <Switch
                    checked={dryRunEnabled}
                    label={`Toggle dry-run; currently ${dryRunEnabled ? "on" : "off"}`}
                    disabled={!data || busyAction === "toggle"}
                    onChange={(enabled) => toggleSetting("dry_run", enabled)}
                  />
                </div>
                <div className="mt-3 flex items-center justify-between gap-4 rounded-2xl bg-background/50 px-4 py-3">
                  <div>
                    <p className="flex items-center gap-2 text-sm font-medium"><LockKeyhole className="h-3.5 w-3.5 text-muted" /> Kill switch</p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">Forces order placement back to dry-run.</p>
                  </div>
                  <Switch
                    checked={killSwitchEnabled}
                    label={`Toggle kill switch; currently ${killSwitchEnabled ? "on" : "off"}`}
                    disabled={!data || busyAction === "toggle"}
                    onChange={(enabled) => toggleSetting("kill_switch", enabled)}
                  />
                </div>
                <div className="mt-6 flex flex-wrap gap-2">
                  <Button type="button" variant="outline" size="sm" className="rounded-full" disabled={!data?.active_run_ids.length || busyAction === "stop_all"} onClick={submitStopAll}>
                    Stop all runs
                  </Button>
                  <Button type="button" variant="outline" size="sm" className="rounded-full" disabled={!hasResumableWork || busyAction === "resume_all"} onClick={submitResumeAll}>
                    Resume inactive
                  </Button>
                  {connection === "offline" && (
                    <Button type="button" variant="ghost" size="sm" className="rounded-full" onClick={() => window.location.reload()}>
                      Try again
                    </Button>
                  )}
                </div>
              </div>
            ) : (
              <div>
                <h2 className="text-lg font-semibold tracking-tight">Usage</h2>
                <p className="mt-1 text-xs leading-5 text-muted">Tokens and recorded model cost across saved runs.</p>
                <div className="mt-6 grid gap-3 sm:grid-cols-3">
                  <Metric label="Tokens" value={usageTotals.tokens.toLocaleString()} />
                  <Metric label="Recorded cost" value={money(usageTotals.cost, 4)} />
                  <Metric label="Runs" value={String(runs.length)} />
                </div>
                {selectedRun && (
                  <div className="mt-6">
                    <p className="text-xs font-medium">{selectedRun.symbol} · this run</p>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      {tokensForRun.toLocaleString()} tokens · {money(costForRun, 4)} · {formatDuration(selectedRun.started_at, selectedRun.finished_at, now)}
                    </p>
                    <div className="mt-3 space-y-1">
                      {[...agentUsage.entries()].map(([agentId, usage]) => (
                        <button
                          key={agentId}
                          type="button"
                          onClick={() => {
                            focusAgent(agentId);
                            setSettingsOpen(false);
                          }}
                          className="flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left transition-colors hover:bg-white/5"
                        >
                          <PersonaAvatar name={agentName(agentId)} src={agentPersona(agentId)} size={28} />
                          <span className="min-w-0 flex-1 truncate text-xs">{agentName(agentId)}</span>
                          <span className="text-[11px] tabular-nums text-muted-foreground">
                            {usage.tokens.toLocaleString()} · {money(usage.cost, 4)}
                          </span>
                        </button>
                      ))}
                      {!agentUsage.size && <p className="text-xs text-muted">No agent usage saved for this run yet.</p>}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  const viewTabs: Array<{ id: DashboardTab; label: string }> = [
    { id: "graph", label: "All activity" },
    { id: "strategy", label: "Backtest" },
    { id: "positions", label: "Positions" },
    ...(wideViewport === true ? [] : [{ id: "agent" as const, label: "Details" }]),
  ];

  const inspectorShown = Boolean(showRun && selectedRun && wideViewport);
  const fitMax = (own: { min: number; max: number }, other: number) =>
    viewportWidth
      ? Math.max(own.min, Math.min(own.max, viewportWidth - other - FEED_MIN_WIDTH))
      : own.max;
  const inspectorWidth = Math.min(inspector.width, fitMax(inspector, rail.width));
  const railMax = fitMax(rail, inspectorShown ? inspectorWidth : 0);
  const railWidth = Math.min(rail.width, railMax);
  const inspectorMax = fitMax(inspector, railWidth);

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <aside
        className="relative hidden shrink-0 border-r border-white/[0.06] lg:block"
        style={{ width: railWidth }}
      >
        {renderRail()}
        <PanelResizeHandle
          side="right"
          label="Resize runs panel"
          width={railWidth}
          min={rail.min}
          max={railMax}
          onResize={rail.setWidth}
          onReset={rail.reset}
        />
      </aside>
      {railOpen && (
        <div className="fixed inset-0 z-40 lg:hidden" onMouseDown={() => setRailOpen(false)}>
          <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" />
          <div
            className="rise-in absolute inset-y-0 left-0 w-[min(100%,280px)] bg-background shadow-2xl"
            onMouseDown={(event) => event.stopPropagation()}
          >
            {renderRail()}
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1">
          <section className="flex min-w-0 flex-1 flex-col">
            <div ref={feedRef} onScroll={onFeedScroll} className="min-h-0 flex-1 overflow-y-auto">
              <header className="sticky top-0 z-20 overflow-hidden bg-background/80 px-4 py-4 backdrop-blur-xl sm:px-6">
                {(busyAction === "start" || busyAction === "command") && (
                  <div className="pointer-events-none absolute inset-x-0 top-0 h-0.5 overflow-hidden">
                    <div className="h-full w-1/3 bg-accent" style={{ animation: "progress-slide 1.2s ease-in-out infinite" }} />
                  </div>
                )}
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    className="grid h-8 w-8 place-items-center rounded-full text-muted hover:bg-card lg:hidden"
                    onClick={() => setRailOpen(true)}
                    aria-label="Open runs"
                  >
                    <Menu className="h-4 w-4" />
                  </button>
                  {showRun && selectedRun && <CoinMark symbol={selectedRun.symbol} small />}
                  <div className="min-w-0">
                    <h1 className="truncate text-[15px] font-semibold tracking-tight">
                      {showRun && selectedRun ? `${selectedRun.symbol} activity` : "Research workspace"}
                    </h1>
                    {showRun && selectedRun && (
                      <p className="truncate text-[11px] text-muted-foreground">{shortId(selectedRun.run_id)}</p>
                    )}
                  </div>
                  <div className="ml-auto flex items-center gap-1.5">
                    <div className="hidden items-center gap-2 sm:flex">
                      <span className={cn("text-[10px] font-medium", dryRunEnabled ? "text-muted" : "text-negative")}>Dry run</span>
                      <Switch
                        checked={dryRunEnabled}
                        label={`Toggle dry-run; currently ${dryRunEnabled ? "on" : "off"}`}
                        disabled={!data || busyAction === "toggle"}
                        onChange={(enabled) => toggleSetting("dry_run", enabled)}
                      />
                      <span className={cn("text-[10px] font-medium", killSwitchEnabled ? "text-muted" : "text-negative")}>Kill switch</span>
                      <Switch
                        checked={killSwitchEnabled}
                        label={`Toggle kill switch; currently ${killSwitchEnabled ? "on" : "off"}`}
                        disabled={!data || busyAction === "toggle"}
                        onChange={(enabled) => toggleSetting("kill_switch", enabled)}
                      />
                    </div>
                    <button
                      type="button"
                      onClick={() => setTab("events")}
                      aria-pressed={centerTab === "events"}
                      className={cn(
                        "inline-flex h-8 w-8 items-center justify-center gap-1.5 rounded-full text-muted transition-colors hover:bg-card hover:text-foreground sm:w-auto sm:px-2.5",
                        centerTab === "events" && "bg-card text-foreground",
                      )}
                      aria-label="Open Activity Log"
                      title="Activity Log"
                    >
                      <History className="h-4 w-4" />
                      <span className="hidden text-xs font-medium sm:inline">Activity Log</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setSettingsSection("usage");
                        setSettingsOpen(true);
                      }}
                      className="inline-flex h-8 w-8 items-center justify-center gap-1.5 rounded-full text-muted transition-colors hover:bg-card hover:text-foreground sm:w-auto sm:px-2.5"
                      aria-label="Open usage"
                      title="Usage"
                    >
                      <Gauge className="h-4 w-4" />
                      <span className="hidden text-xs font-medium sm:inline">Usage</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setSettingsSection("connections");
                        setSettingsOpen(true);
                      }}
                      className="grid h-8 w-8 place-items-center rounded-full text-muted transition-colors hover:bg-card hover:text-foreground"
                      aria-label="Open settings"
                    >
                      <Settings2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>
                <div className="mt-4 flex items-center gap-1 overflow-x-auto">
                  {viewTabs.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setTab(item.id)}
                      className={cn(
                        "shrink-0 rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
                        centerTab === item.id ? "bg-card text-foreground" : "text-muted hover:bg-card/60 hover:text-foreground",
                      )}
                    >
                      {item.label}
                    </button>
                  ))}
                  <span className="ml-auto flex shrink-0 items-center gap-3 pl-3 text-[11px] text-muted-foreground">
                    {showRun && <span className="hidden tabular-nums sm:inline">{tokensForRun.toLocaleString()} tokens</span>}
                    <select
                      aria-label="Filter activity by agent"
                      value={feedAgentId}
                      onChange={(event) => {
                        const value = event.target.value;
                        setFeedAgentId(value);
                        if (value === "all") return;
                        setSelectedAgent(value);
                        const step = [...(selectedRun?.steps ?? [])].reverse().find((item) => item.agent_name === value);
                        if (step) setSelectedStepId(step.step_id);
                        setTab("graph");
                      }}
                      className="max-w-[9.5rem] rounded-full bg-transparent py-1 text-[11px] text-muted outline-none sm:max-w-40"
                    >
                      <option value="all">All agents</option>
                      {AGENTS.map((agent) => (
                        <option key={agent.id} value={agent.id}>
                          {agent.name} · {agentStatus(selectedRun, agent.id, data?.operations ?? [])}
                        </option>
                      ))}
                    </select>
                  </span>
                </div>
              </header>

              <div className="px-4 pb-8 sm:px-6">
                {showRun && selectedRun && wideViewport === false && (
                  <div className="mb-5 min-[1100px]:hidden">
                    <LiveMarketChart symbol={selectedRun.symbol} enabled />
                  </div>
                )}

                {!data && connection !== "offline" && (
                  <div className="mx-auto max-w-3xl space-y-6 pt-4" aria-label="Loading agent activity">
                    <p className="text-sm text-muted">Connecting to your research workspace…</p>
                    {[0, 1, 2].map((item) => (
                      <div key={item} className="flex gap-3">
                        <div className="skeleton h-9 w-9 shrink-0 rounded-full" />
                        <div className="flex-1 space-y-2 pt-1">
                          <div className="skeleton h-3 w-32 rounded-full" />
                          <div className="skeleton h-3 w-full max-w-lg rounded-full" />
                          <div className="skeleton h-3 w-2/3 max-w-sm rounded-full" />
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {!data && connection === "offline" && (
                  <div className="mx-auto mt-24 max-w-md text-center">
                    <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-card text-negative">
                      <AlertCircle className="h-5 w-5" />
                    </div>
                    <h2 className="mt-4 text-base font-semibold">Can’t reach the local API</h2>
                    <p className="mt-2 text-sm leading-6 text-muted">
                      Start the framework service with <code>uv run mats-web</code>. The workspace reconnects when it is available.
                    </p>
                    <button type="button" onClick={() => window.location.reload()} className="mt-4 rounded-full bg-card px-4 py-2 text-xs font-medium hover:bg-card-hover">
                      Try again
                    </button>
                  </div>
                )}

                {data && (
                  <>
                    {centerTab === "graph" && renderActivity()}
                    {centerTab === "agent" && <div className="mx-auto max-w-xl">{renderInspector()}</div>}
                    {centerTab === "strategy" && renderStrategy()}
                    {centerTab === "positions" && renderPositions()}
                    {centerTab === "events" && renderEvents()}
                    {(showRun || centerTab !== "graph") && (
                      <p className="mx-auto mt-8 max-w-3xl text-[10px] leading-5 text-muted-foreground">
                        Backtests are historical simulations, not realized returns. Browser controls call the local framework API; exchange access remains in the backend.
                      </p>
                    )}
                  </>
                )}
              </div>
            </div>

            <div className="shrink-0 px-4 pb-4 sm:px-6">
              <form
                className="relative mx-auto flex max-w-3xl items-center gap-1 rounded-2xl border border-white/[0.07] bg-surface py-1.5 pl-1.5 pr-1.5 transition-colors focus-within:border-white/[0.14]"
                onSubmit={submitCommand}
              >
                <button
                  type="button"
                  onClick={() => setModelMenuOpen((open) => !open)}
                  className="grid h-10 w-10 shrink-0 place-items-center rounded-full text-muted transition-colors hover:bg-white/5 hover:text-foreground"
                  aria-label="Choose model"
                  aria-expanded={modelMenuOpen}
                >
                  <Paperclip className="h-4 w-4" />
                </button>
                {modelMenuOpen && (
                  <>
                    <button type="button" className="fixed inset-0 z-30 cursor-default" aria-label="Close model menu" onClick={() => setModelMenuOpen(false)} />
                    <div className="absolute bottom-14 left-0 z-40 w-72 rounded-xl border border-white/[0.08] bg-card p-1.5 shadow-[0_12px_32px_rgba(0,0,0,0.4)]">
                      {(data?.connections?.models ?? []).map((model) => {
                        const selected = model.id === (data?.connections?.active_model_id ?? "environment");
                        return (
                          <button
                            key={model.id}
                            type="button"
                            className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left hover:bg-white/5"
                            onClick={() => {
                              setModelMenuOpen(false);
                              void act("select_model", { model_id: model.id }, `${model.name} selected.`);
                            }}
                          >
                            <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", selected ? "bg-accent" : "bg-transparent")} />
                            <span className="min-w-0">
                              <span className="block truncate text-sm">{model.name}</span>
                              <span className="block truncate text-[11px] text-muted-foreground">{model.model}</span>
                            </span>
                          </button>
                        );
                      })}
                      {!data?.connections?.models.length && (
                        <p className="px-3 py-2 text-xs text-muted">No saved models yet.</p>
                      )}
                      <button
                        type="button"
                        className="mt-1 w-full rounded-xl px-3 py-2 text-left text-xs text-accent hover:bg-white/5"
                        onClick={() => {
                          setModelMenuOpen(false);
                          setSettingsSection("connections");
                          setSettingsOpen(true);
                        }}
                      >
                        Add or edit models
                      </button>
                    </div>
                  </>
                )}
                <div className="relative flex min-h-10 min-w-0 flex-1 items-center">
                  {!command && agentsAreWorking && (
                    <span className="text-shimmer pointer-events-none absolute inset-y-0 left-1 flex items-center text-sm">The agents are working...</span>
                  )}
                  <textarea
                    value={command}
                    onChange={(event) => {
                      setCommand(event.target.value);
                      event.target.style.height = "40px";
                      event.target.style.height = `${Math.min(event.target.scrollHeight, 120)}px`;
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        submitCommand();
                      }
                    }}
                    rows={1}
                    placeholder={
                      agentsAreWorking
                        ? ""
                        : viewportWidth && viewportWidth < 480
                          ? "Trade BTC and ETH…"
                          : "Start trading BTC/USDT and ETH/USDT"
                    }
                    aria-label="Tell the agents what to run"
                    className="max-h-32 min-h-10 w-full resize-none bg-transparent px-1 py-2.5 text-sm leading-5 text-foreground outline-none placeholder:text-muted-foreground"
                  />
                </div>
                <button
                  type="button"
                  onClick={selectedIsActive && !command.trim() ? submitStop : () => submitCommand()}
                  disabled={!data || (selectedIsActive && !command.trim() ? busyAction === "stop" : busyAction === "start")}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-foreground text-background transition-colors duration-150 hover:bg-foreground/85 disabled:opacity-40"
                  aria-label={selectedIsActive && !command.trim() ? "Stop selected run or entry watcher" : "Send"}
                >
                  {busyAction === "start" || busyAction === "stop" ? (
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                  ) : selectedIsActive && !command.trim() ? (
                    <Square className="h-3.5 w-3.5 fill-current" />
                  ) : (
                    <ArrowUp className="h-4 w-4" />
                  )}
                </button>
              </form>
            </div>
          </section>

          {showRun && selectedRun && (
            <aside
              className="relative hidden shrink-0 flex-col border-l border-white/[0.06] min-[1100px]:flex"
              style={{ width: inspectorWidth }}
            >
              <PanelResizeHandle
                side="left"
                label="Resize details panel"
                width={inspectorWidth}
                min={inspector.min}
                max={inspectorMax}
                onResize={inspector.setWidth}
                onReset={inspector.reset}
              />
              <div className="shrink-0 border-b border-white/[0.05] px-4 py-4">
                <LiveMarketChart symbol={selectedRun.symbol} enabled={wideViewport === true} />
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
                {renderInspector()}
              </div>
            </aside>
          )}
        </div>
      </div>

      {notice && (
        <div className="pointer-events-none fixed inset-x-0 bottom-28 z-[60] flex justify-center px-4">
          <div
            role={notice.kind === "error" ? "alert" : "status"}
            className="rise-in pointer-events-auto flex max-w-md items-center gap-3 rounded-xl border border-white/[0.08] bg-card px-4 py-2.5 text-sm text-foreground shadow-[0_12px_32px_rgba(0,0,0,0.4)]"
          >
            {notice.kind === "error" ? <AlertCircle className="h-4 w-4 shrink-0 text-negative" /> : <CheckCircle2 className="h-4 w-4 shrink-0 text-positive" />}
            <span className="min-w-0 leading-5">{notice.message}</span>
            <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss notification" className="text-muted hover:text-foreground">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {settingsOpen && renderSettings()}
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
          attempts={startAttempts}
          setAttempts={setStartAttempts}
          killSwitch={killSwitchEnabled}
          setKillSwitch={(enabled) => toggleSetting("kill_switch", enabled)}
          step={startStep}
          setStep={setStartStep}
          onClose={() => setShowStart(false)}
          onSubmit={submitStart}
          busy={busyAction === "start"}
        />
      )}
    </div>
  );
}

const START_STEPS = ["Coins", "Timeframe", "Capital"] as const;

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
  attempts,
  setAttempts,
  killSwitch,
  setKillSwitch,
  step,
  setStep,
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
  attempts: number;
  setAttempts: (value: number) => void;
  killSwitch: boolean;
  setKillSwitch: (value: boolean) => void;
  step: number;
  setStep: (value: number) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  busy: boolean;
}) {
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const timeframeGroups = [
    { label: "Short-term", detail: "Fast market moves", values: ["1m", "5m", "15m"] },
    { label: "Intraday", detail: "Within the trading day", values: ["30m", "1h", "2h", "4h"] },
    { label: "Long-term", detail: "Broader market trends", values: ["6h", "12h", "1d", "1w"] },
  ];
  const visibleSymbols = symbols.filter((symbol) => {
    if (!needle) return true;
    return `${marketName(symbol)} ${symbol}`.toLowerCase().includes(needle);
  });
  const canContinue = step === 0 ? selectedSymbols.length > 0 : step === 1 ? selectedTimeframes.length > 0 : true;

  function continueStep() {
    if (!canContinue) return;
    if (step < 2) setStep(step + 1);
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/55 p-4 backdrop-blur-md"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="start-run-title"
        aria-busy={busy}
        onSubmit={(event) => {
          event.preventDefault();
          if (step < 2) {
            continueStep();
            return;
          }
          onSubmit(event);
        }}
        className="rise-in relative flex max-h-[calc(100dvh-1rem)] w-full max-w-2xl flex-col overflow-hidden rounded-3xl border border-white/[0.07] bg-surface shadow-[0_20px_60px_rgba(0,0,0,0.5)] sm:max-h-[calc(100dvh-2rem)]"
      >
        {busy && (
          <div
            className="progress-rail pointer-events-none absolute inset-x-0 top-0 z-10 bg-accent/10"
            role="progressbar"
            aria-label="Starting run"
          />
        )}
        <header className="shrink-0 border-b border-white/[0.06] px-4 pb-4 pt-5 sm:px-6 sm:pb-5 sm:pt-6">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="text-[11px] font-medium text-muted-foreground">New analysis</p>
              <h2 id="start-run-title" className="mt-1 text-xl font-semibold tracking-tight">Set up your run</h2>
              <p className="mt-1 text-xs leading-5 text-muted">
                {step === 0 && "Choose the markets to analyze. Each gets its own run."}
                {step === 1 && "Select the candle intervals to compare."}
                {step === 2 && "Review capital and safety settings before starting."}
              </p>
            </div>
            <button type="button" onClick={onClose} className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-muted transition-colors hover:bg-white/[0.07] hover:text-foreground" aria-label="Close dialog">
              <X className="h-4 w-4" />
            </button>
          </div>
          <nav className="mt-5" aria-label="Run setup progress">
            <ol className="flex gap-2 sm:gap-3">
              {START_STEPS.map((label, index) => {
                const reachable = index === 0
                  || (index === 1 && selectedSymbols.length > 0)
                  || (index === 2 && selectedSymbols.length > 0 && selectedTimeframes.length > 0);
                const complete = index < step;
                return (
                  <li key={label} className="min-w-0 flex-1">
                    <button
                      type="button"
                      aria-current={index === step ? "step" : undefined}
                      disabled={!reachable}
                      onClick={() => setStep(index)}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-lg px-1.5 py-1.5 text-left text-[11px] font-medium transition-colors sm:px-2",
                        index === step ? "text-foreground" : complete ? "text-foreground/80" : "text-muted-foreground",
                        !reachable && "cursor-not-allowed opacity-45",
                      )}
                    >
                      <span className={cn(
                        "grid h-6 w-6 shrink-0 place-items-center rounded-full border text-[10px] transition-colors",
                        index === step
                          ? "border-foreground bg-foreground text-background"
                          : complete
                            ? "border-positive/40 bg-positive/10 text-positive"
                            : "border-white/10 bg-white/[0.03] text-muted",
                      )}>
                        {complete ? <CheckCircle2 className="h-3.5 w-3.5" /> : index + 1}
                      </span>
                      <span className="truncate">{label}</span>
                    </button>
                  </li>
                );
              })}
            </ol>
            <div
              className="mt-2 h-1 overflow-hidden rounded-full bg-white/[0.06]"
              role="progressbar"
              aria-label="Run setup progress"
              aria-valuemin={1}
              aria-valuemax={START_STEPS.length}
              aria-valuenow={step + 1}
            >
              <div
                className="h-full rounded-full bg-accent transition-[width] duration-500 ease-out"
                style={{ width: `${((step + 1) / START_STEPS.length) * 100}%` }}
              />
            </div>
          </nav>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6 sm:py-5">
          {step === 0 && (
            <section aria-labelledby="market-step-title">
              <div className="mb-4 flex items-start justify-between gap-3">
                <div>
                  <h3 id="market-step-title" className="text-sm font-semibold">Choose markets</h3>
                  <p className="mt-1 text-xs text-muted">Select one or more assets for independent runs.</p>
                </div>
                <Badge className="shrink-0 border-accent/20 bg-accent/[0.08] text-accent">
                  {selectedSymbols.length} selected
                </Badge>
              </div>
              <label className="relative block" htmlFor="coin-search">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="coin-search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search by name or symbol"
                  autoFocus
                  className="h-10 rounded-xl border-white/[0.07] bg-background/70 pl-10 placeholder:text-muted-foreground"
                />
              </label>
              {selectedSymbols.length > 0 && (
                <div className="mt-3 rounded-xl border border-white/[0.06] bg-background/40 p-2">
                  <div className="flex flex-wrap gap-1.5">
                    {selectedSymbols.map((symbol) => (
                      <button
                        key={symbol}
                        type="button"
                        onClick={() => toggleSelection(symbol, selectedSymbols, setSelectedSymbols)}
                        aria-label={`Remove ${marketName(symbol)}`}
                        className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.07] bg-white/[0.04] py-1 pl-1 pr-2 text-[10px] transition-colors hover:border-negative/30 hover:bg-negative/[0.08]"
                      >
                        <CoinMark symbol={symbol} small />
                        <span className="max-w-24 truncate">{symbol.split("/")[0]}</span>
                        <X className="h-3 w-3 text-muted" />
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className="mt-3 max-h-[min(42dvh,24rem)] overflow-y-auto rounded-2xl border border-white/[0.06] bg-background/30 p-2">
                <div className="grid gap-2 sm:grid-cols-2">
                  {visibleSymbols.map((symbol) => {
                    const selected = selectedSymbols.includes(symbol);
                    return (
                      <label
                        key={symbol}
                        className={cn(
                          "flex min-h-[4.25rem] cursor-pointer items-center gap-3 rounded-xl border px-3 py-2.5 transition-colors",
                          selected
                            ? "border-accent/35 bg-accent/[0.07]"
                            : "border-white/[0.05] bg-white/[0.015] hover:border-white/[0.12] hover:bg-white/[0.04]",
                        )}
                      >
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => toggleSelection(symbol, selectedSymbols, setSelectedSymbols)}
                          aria-label={`Select ${marketName(symbol)} (${symbol})`}
                          style={{ borderRadius: "50%" }}
                        />
                        <CoinMark symbol={symbol} small />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-xs font-medium">{marketName(symbol)}</span>
                          <span className="mt-0.5 block text-[10px] text-muted-foreground">{symbol}</span>
                        </span>
                      </label>
                    );
                  })}
                </div>
                {!visibleSymbols.length && (
                  <p className="px-1 py-8 text-center text-xs text-muted">No markets match “{query.trim()}”.</p>
                )}
              </div>
            </section>
          )}

          {step === 1 && (
            <section aria-labelledby="timeframe-step-title">
              <div className="mb-4 flex items-start justify-between gap-3">
                <div>
                  <h3 id="timeframe-step-title" className="text-sm font-semibold">Choose timeframes</h3>
                  <p className="mt-1 text-xs leading-5 text-muted">Compare short-term moves with broader market trends.</p>
                </div>
                <Badge className="shrink-0 border-accent/20 bg-accent/[0.08] text-accent">
                  {selectedTimeframes.length} selected
                </Badge>
              </div>
              <div className="mb-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setSelectedTimeframes(timeframes)}
                  className="rounded-full border border-white/[0.08] px-3 py-1.5 text-[11px] font-medium text-muted transition-colors hover:border-white/15 hover:text-foreground"
                >
                  Select all
                </button>
                <button
                  type="button"
                  onClick={() => setSelectedTimeframes([])}
                  disabled={!selectedTimeframes.length}
                  className="rounded-full border border-white/[0.08] px-3 py-1.5 text-[11px] font-medium text-muted transition-colors hover:border-white/15 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Clear
                </button>
              </div>
              <div className="space-y-4">
                {timeframeGroups.map((group) => (
                  <fieldset key={group.label}>
                    <legend className="text-xs font-medium">{group.label}</legend>
                    <p className="mt-0.5 text-[10px] text-muted-foreground">{group.detail}</p>
                    <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                      {group.values.filter((timeframe) => timeframes.includes(timeframe)).map((timeframe) => {
                        const selected = selectedTimeframes.includes(timeframe);
                        return (
                          <button
                            key={timeframe}
                            type="button"
                            aria-pressed={selected}
                            onClick={() => toggleSelection(timeframe, selectedTimeframes, setSelectedTimeframes)}
                            className={cn(
                              "flex min-h-11 items-center justify-between rounded-xl border px-3 text-xs font-medium transition-colors",
                              selected
                                ? "border-accent/35 bg-accent/[0.08] text-foreground"
                                : "border-white/[0.07] bg-background/50 text-muted hover:border-white/[0.14] hover:text-foreground",
                            )}
                          >
                            {timeframe}
                            <span className={cn(
                              "grid h-4 w-4 place-items-center rounded-full border",
                              selected ? "border-foreground bg-foreground text-background" : "border-white/15",
                            )}>
                              {selected && <CheckCircle2 className="h-3 w-3" />}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </fieldset>
                ))}
              </div>
              <div className="mt-5 flex items-center gap-2 rounded-xl border border-white/[0.06] bg-background/40 px-3 py-2.5 text-[11px] text-muted">
                <Gauge className="h-4 w-4 shrink-0 text-accent" />
                <span>{selectedSymbols.length} {selectedSymbols.length === 1 ? "market" : "markets"} will be analyzed across {selectedTimeframes.length} {selectedTimeframes.length === 1 ? "timeframe" : "timeframes"}.</span>
              </div>
            </section>
          )}

          {step === 2 && (
            <section aria-labelledby="capital-step-title">
              <div className="mb-4">
                <h3 id="capital-step-title" className="text-sm font-semibold">Capital & safeguards</h3>
                <p className="mt-1 text-xs leading-5 text-muted">These settings help size positions and protect your account.</p>
              </div>
              <div className="rounded-2xl border border-white/[0.07] bg-background/35 p-4">
                <label className="block text-xs font-medium" htmlFor="account-equity">Total account equity <span className="font-normal text-muted">(optional)</span></label>
                <div className="relative mt-2">
                  <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted">$</span>
                  <Input
                    id="account-equity"
                    type="number"
                    min="0.01"
                    step="any"
                    value={accountEquity}
                    onChange={(event) => setAccountEquity(event.target.value)}
                    placeholder="Enter account equity"
                    className="h-11 rounded-xl border-white/[0.07] bg-surface pl-8"
                  />
                </div>
                <p className="mt-2 text-[11px] leading-5 text-muted">
                  Split evenly across selected markets. Leave blank to use the available testnet quote balance for live runs.
                </p>
              </div>
              <div className="mt-3 flex items-center justify-between gap-4 rounded-2xl border border-white/[0.07] bg-background/35 px-4 py-3.5">
                <span className="min-w-0">
                  <span className="block text-xs font-medium" id="attempts-label">Strategy attempts</span>
                  <span className="mt-1 block text-[11px] leading-4 text-muted">
                    How many strategies the critique may review before the run stops without one.
                  </span>
                </span>
                <div className="flex shrink-0 items-center gap-1" role="group" aria-labelledby="attempts-label">
                  <button
                    type="button"
                    aria-label="Fewer attempts"
                    disabled={attempts <= 1}
                    onClick={() => setAttempts(Math.max(1, attempts - 1))}
                    className="grid h-8 w-8 place-items-center rounded-full border border-white/[0.07] text-sm text-muted transition-colors hover:text-foreground disabled:opacity-40"
                  >
                    −
                  </button>
                  <span className="w-7 text-center text-sm font-semibold tabular-nums" aria-live="polite">{attempts}</span>
                  <button
                    type="button"
                    aria-label="More attempts"
                    disabled={attempts >= MAX_ATTEMPTS}
                    onClick={() => setAttempts(Math.min(MAX_ATTEMPTS, attempts + 1))}
                    className="grid h-8 w-8 place-items-center rounded-full border border-white/[0.07] text-sm text-muted transition-colors hover:text-foreground disabled:opacity-40"
                  >
                    +
                  </button>
                </div>
              </div>
              <div className="mt-3 divide-y divide-white/[0.06] rounded-2xl border border-white/[0.07] bg-background/35 px-4">
                <div className="flex items-center justify-between gap-4 py-3.5">
                  <span className="min-w-0">
                    <span className="block text-xs font-medium">Dry run</span>
                    <span className={cn("mt-1 block text-[11px]", dryRun ? "text-muted" : "text-negative")}>
                      {dryRun ? "No exchange orders will be placed." : "Guarded Bybit testnet orders may be placed."}
                    </span>
                  </span>
                  <Switch checked={dryRun} label={`Toggle dry-run; currently ${dryRun ? "on" : "off"}`} onChange={setDryRun} />
                </div>
                <div className="flex items-center justify-between gap-4 py-3.5">
                  <span className="min-w-0">
                    <span className="block text-xs font-medium">Kill switch</span>
                    <span className="mt-1 block text-[11px] text-muted">Forces order placement back to dry-run.</span>
                  </span>
                  <Switch checked={killSwitch} label={`Toggle kill switch; currently ${killSwitch ? "on" : "off"}`} onChange={setKillSwitch} />
                </div>
              </div>
              <div className="mt-4 flex flex-wrap gap-1.5">
                {selectedSymbols.map((symbol) => (
                  <Badge key={symbol} className="border-white/[0.07] bg-white/[0.03] text-muted">
                    {symbol.split("/")[0]}
                  </Badge>
                ))}
                <Badge className="border-accent/20 bg-accent/[0.08] text-accent">
                  {selectedTimeframes.length} timeframes
                </Badge>
              </div>
            </section>
          )}
        </div>

        <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-white/[0.06] bg-surface/95 px-4 py-3.5 sm:px-6">
          <Button type="button" variant="ghost" className="rounded-full" onClick={step === 0 ? onClose : () => setStep(step - 1)}>
            {step === 0 ? "Cancel" : "Back"}
          </Button>
          {/* Distinct keys stop React reusing the button, which made the Continue click submit the form. */}
          {step < 2 ? (
            <Button key="continue" type="button" variant="primary" className="rounded-full px-5" disabled={!canContinue} onClick={continueStep}>
              Continue
              <ArrowRight className="h-4 w-4" />
            </Button>
          ) : (
            <Button key="start" type="submit" variant="primary" className="rounded-full px-5" disabled={busy || !selectedSymbols.length || !selectedTimeframes.length}>
              {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {busy ? "Starting…" : `Start ${selectedSymbols.length > 1 ? `${selectedSymbols.length} runs` : "run"}`}
            </Button>
          )}
        </footer>
      </form>
    </div>
  );
}
