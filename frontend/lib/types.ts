export type RunStatus = "running" | "completed" | "failed" | "stopped" | "rejected" | string;

export interface AgentStep {
  step_id: string;
  run_id: string;
  agent_name: string;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  tool_calls: Array<Record<string, unknown>>;
  status: string;
  error: string | null;
  tokens_used: number;
  cost: number;
  started_at: string;
  finished_at: string | null;
}

export interface StrategyAttempt {
  strategy_id: string;
  run_id: string;
  version: number;
  strategy: Record<string, unknown>;
  backtest_id: string | null;
  report: Record<string, unknown> | null;
  equity_curve: Array<Record<string, unknown>> | null;
  costs: Record<string, unknown> | null;
  decision: string | null;
  issues: string[] | null;
  revision_instructions: string | null;
}

export interface Execution {
  execution_id: string;
  run_id: string;
  orders: Array<Record<string, unknown>>;
  status: string;
}

export interface SystemEvent {
  event_id: number;
  run_id: string | null;
  type: string;
  message: string;
  traceback: string | null;
  timestamp: string;
}

export type DiagnosisKind =
  | "failed"
  | "retired"
  | "stopped"
  | "paused"
  | "waiting"
  | "in_position"
  | "no_trade"
  | "dry_run"
  | "rejected";

export interface RunDiagnosis {
  kind: DiagnosisKind;
  title: string;
  detail: string;
  hint: string | null;
  agent: string | null;
}

export interface Run {
  run_id: string;
  symbol: string;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  config: Record<string, unknown>;
  steps: AgentStep[];
  strategies: StrategyAttempt[];
  executions: Execution[];
  events: SystemEvent[];
  diagnosis: RunDiagnosis | null;
}

export interface Position {
  run_id: string;
  symbol: string;
  strategy_key: string;
  strategy: Record<string, unknown>;
  side: string;
  amount: number;
  entry_price: number;
  stop_price: number;
  take_profit_price: number | null;
  entry_order_id: string | null;
  protective_order_id: string | null;
  status: string;
  stop_reason: string | null;
  net_pnl: number | null;
  events: Array<Record<string, unknown>>;
  opened_at: string;
  updated_at: string;
}

export interface Operation {
  operation_id: string;
  name: string;
  status: string;
  error?: string;
}

export interface EntryWatcher {
  run_id: string;
  interval_seconds: number;
  status: string;
  signal: Record<string, unknown> | null;
  last_checked_at: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface DashboardState {
  runs: Run[];
  positions: Position[];
  system_events: SystemEvent[];
  entry_watchers: EntryWatcher[];
  settings: {
    dry_run: boolean;
    kill_switch: boolean;
    symbols: string[];
    timeframes: string[];
  };
  operations: Operation[];
  active_run_ids: string[];
  connections?: ConnectionsState;
}

export interface SavedModel {
  id: string;
  name: string;
  provider: string;
  model: string;
  base_url: string | null;
  supports_tools: boolean;
  api_key_hint: string | null;
  builtin?: boolean;
}

export interface ConnectionsState {
  bybit_api_key_hint: string | null;
  bybit_api_secret_hint: string | null;
  bybit_configured: boolean;
  bybit_source: "saved" | "environment" | "missing";
  active_model_id: string | null;
  models: SavedModel[];
}

export type DashboardTab =
  | "graph"
  | "agent"
  | "strategy"
  | "positions"
  | "events";
