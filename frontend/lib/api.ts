import type { DashboardState, MarketSnapshot } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_MATS_API_URL ?? "http://127.0.0.1:8765";

export async function fetchState(): Promise<DashboardState> {
  const response = await fetch(`${API_URL}/api/state`, { cache: "no-store" });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { error?: string } | null;
    throw new Error(body?.error ?? `Dashboard API returned ${response.status}.`);
  }
  return (await response.json()) as DashboardState;
}

export async function fetchMarketData(
  symbol: string,
  timeframe: string,
): Promise<MarketSnapshot> {
  const query = new URLSearchParams({ symbol, timeframe });
  const response = await fetch(`${API_URL}/api/market?${query}`, { cache: "no-store" });
  const body = (await response.json()) as MarketSnapshot | { error?: string };
  if (!response.ok) {
    throw new Error(
      "error" in body && typeof body.error === "string"
        ? body.error
        : `Market data request failed (${response.status}).`,
    );
  }
  return body as MarketSnapshot;
}

export async function sendAction(
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const response = await fetch(`${API_URL}/api/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    throw new Error(typeof body.error === "string" ? body.error : `Action failed (${response.status}).`);
  }
  return body;
}

export function createStateStream(onState: (state: DashboardState) => void, onError: () => void) {
  const stream = new EventSource(`${API_URL}/api/events`);
  stream.onmessage = (event) => {
    try {
      onState(JSON.parse(event.data) as DashboardState);
    } catch {
      onError();
    }
  };
  stream.onerror = onError;
  return stream;
}
