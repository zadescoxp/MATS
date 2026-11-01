import type { DashboardState } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_MATS_API_URL ?? "http://127.0.0.1:8765";

export async function fetchState(): Promise<DashboardState> {
  const response = await fetch(`${API_URL}/api/state`, { cache: "no-store" });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { error?: string } | null;
    throw new Error(body?.error ?? `Dashboard API returned ${response.status}.`);
  }
  return (await response.json()) as DashboardState;
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
  let stream: EventSource | null = null;
  let closed = false;
  let delay = 1000;
  let retry: number | undefined;

  const connect = () => {
    if (closed) return;
    stream = new EventSource(`${API_URL}/api/events`);
    stream.onmessage = (event) => {
      delay = 1000;
      try {
        onState(JSON.parse(event.data) as DashboardState);
      } catch {
        onError();
      }
    };
    stream.onerror = () => {
      onError();
      stream?.close();
      if (closed) return;
      retry = window.setTimeout(connect, delay);
      delay = Math.min(delay * 2, 10000);
    };
  };

  connect();
  return {
    close() {
      closed = true;
      if (retry !== undefined) window.clearTimeout(retry);
      stream?.close();
    },
  };
}
