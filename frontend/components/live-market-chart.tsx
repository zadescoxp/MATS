"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  type UTCTimestamp,
} from "lightweight-charts";
import { AlertCircle, Radio, RefreshCw } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

interface Candle {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

type ConnectionStatus = "connecting" | "live" | "reconnecting";

const TIMEFRAMES = [
  { label: "1m", interval: "1" },
  { label: "3m", interval: "3" },
  { label: "5m", interval: "5" },
  { label: "15m", interval: "15" },
  { label: "30m", interval: "30" },
  { label: "1h", interval: "60" },
  { label: "2h", interval: "120" },
  { label: "4h", interval: "240" },
  { label: "6h", interval: "360" },
  { label: "12h", interval: "720" },
  { label: "1d", interval: "D" },
  { label: "1w", interval: "W" },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function numberField(value: unknown): number | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseCandle(row: unknown): Candle | null {
  if (!isRecord(row)) return null;
  const start = numberField(row.start);
  const open = numberField(row.open);
  const high = numberField(row.high);
  const low = numberField(row.low);
  const close = numberField(row.close);
  const volume = numberField(row.volume);
  if (
    start === null ||
    open === null ||
    high === null ||
    low === null ||
    close === null ||
    volume === null
  ) {
    return null;
  }
  return {
    time: Math.floor(start / 1000) as UTCTimestamp,
    open,
    high,
    low,
    close,
    volume,
  };
}

function parseHistoryRow(row: unknown): Candle | null {
  if (!Array.isArray(row) || row.length < 6) return null;
  return parseCandle({
    start: row[0],
    open: row[1],
    high: row[2],
    low: row[3],
    close: row[4],
    volume: row[5],
  });
}

function coinSymbol(symbol: string): string {
  return symbol.replace("/", "").replace(":", "");
}

async function fetchHistory(
  symbol: string,
  interval: string,
  signal: AbortSignal,
): Promise<Candle[]> {
  const query = new URLSearchParams({
    category: "spot",
    symbol: coinSymbol(symbol),
    interval,
    limit: "300",
  });
  const response = await fetch(`https://api.bybit.com/v5/market/kline?${query}`, { signal });
  if (!response.ok) throw new Error(`Bybit candle history returned ${response.status}.`);
  const payload: unknown = await response.json();
  if (!isRecord(payload) || payload.retCode !== 0 || !isRecord(payload.result)) {
    const message = isRecord(payload) && typeof payload.retMsg === "string"
      ? payload.retMsg
      : "Bybit returned an invalid candle history response.";
    throw new Error(message);
  }
  if (!Array.isArray(payload.result.list)) {
    throw new Error("Bybit candle history did not contain a candle list.");
  }
  return payload.result.list
    .map(parseHistoryRow)
    .filter((candle): candle is Candle => candle !== null)
    .sort((left, right) => left.time - right.time);
}

export function LiveMarketChart({
  symbol,
  enabled,
}: {
  symbol: string;
  enabled: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const replaceHistoryRef = useRef<((candles: Candle[]) => void) | null>(null);
  const updateCandleRef = useRef<((candle: Candle) => void) | null>(null);
  const latestChartTimeRef = useRef<number | null>(null);
  const latestLiveCandleRef = useRef<Candle | null>(null);
  const timeframeId = useId();
  const [timeframe, setTimeframe] = useState("1m");
  const [lastPrice, setLastPrice] = useState<number | null>(null);
  const [connection, setConnection] = useState<ConnectionStatus>("connecting");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const selectedTimeframe = TIMEFRAMES.find((item) => item.label === timeframe) ?? TIMEFRAMES[0];

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !enabled) return;

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#71717a",
        fontFamily: "var(--font-mono), monospace",
        fontSize: 10,
      },
      grid: {
        vertLines: { color: "#1c1c20" },
        horzLines: { color: "#1c1c20" },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: { vertLine: { color: "#3f3f46" }, horzLine: { color: "#3f3f46" } },
      localization: {
        priceFormatter: (price: number) =>
          price.toLocaleString(undefined, { maximumFractionDigits: 8 }),
      },
    });
    const priceSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#34d399",
      downColor: "#f87171",
      borderVisible: false,
      wickUpColor: "#34d399",
      wickDownColor: "#f87171",
      priceLineVisible: true,
    });
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });

    replaceHistoryRef.current = (candles) => {
      priceSeries.setData(
        candles.map(({ time, open, high, low, close }) => ({
          time,
          open,
          high,
          low,
          close,
        })),
      );
      volumeSeries.setData(
        candles.map(({ time, open, close, volume }) => ({
          time,
          value: volume,
          color: close >= open ? "#34d39955" : "#f8717155",
        })),
      );
      latestChartTimeRef.current = candles.at(-1)?.time ?? null;
      if (candles.length) chart.timeScale().fitContent();
    };
    updateCandleRef.current = (candle) => {
      if (latestChartTimeRef.current !== null && candle.time < latestChartTimeRef.current) {
        return;
      }
      priceSeries.update({
        time: candle.time,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      });
      volumeSeries.update({
        time: candle.time,
        value: candle.volume,
        color: candle.close >= candle.open ? "#34d39955" : "#f8717155",
      });
      latestChartTimeRef.current = candle.time;
    };

    return () => {
      replaceHistoryRef.current = null;
      updateCandleRef.current = null;
      latestChartTimeRef.current = null;
      chart.remove();
    };
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let heartbeatTimer: number | undefined;
    let reconnectDelay = 1_000;

    setLoading(true);
    setLastPrice(null);
    setConnection("connecting");
    setError(null);
    setUpdatedAt(null);
    latestLiveCandleRef.current = null;

    void fetchHistory(symbol, selectedTimeframe.interval, controller.signal)
      .then((candles) => {
        if (!active) return;
        const latestLiveCandle = latestLiveCandleRef.current;
        const latestHistoryCandle = candles.at(-1);
        if (latestLiveCandle && (!latestHistoryCandle || latestLiveCandle.time >= latestHistoryCandle.time)) {
          if (latestHistoryCandle?.time === latestLiveCandle.time) {
            candles[candles.length - 1] = latestLiveCandle;
          } else {
            candles.push(latestLiveCandle);
          }
        }
        replaceHistoryRef.current?.(candles);
        if (!latestLiveCandle) setLastPrice(candles.at(-1)?.close ?? null);
        setLoading(false);
        if (!candles.length) setError("Bybit returned no historical candles for this market.");
      })
      .catch((reason: unknown) => {
        if (!active || controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "Could not load candle history.");
        setLoading(false);
      });

    const connect = () => {
      if (!active) return;
      setConnection(reconnectDelay === 1_000 ? "connecting" : "reconnecting");
      socket = new WebSocket("wss://stream.bybit.com/v5/public/spot");

      socket.onopen = () => {
        if (!active || !socket) return;
        reconnectDelay = 1_000;
        setConnection("live");
        socket.send(
          JSON.stringify({
            op: "subscribe",
            args: [
              `kline.${selectedTimeframe.interval}.${coinSymbol(symbol)}`,
              `tickers.${coinSymbol(symbol)}`,
            ],
          }),
        );
        heartbeatTimer = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ op: "ping" }));
          }
        }, 20_000);
      };

      socket.onmessage = (event: MessageEvent<string>) => {
        if (!active) return;
        let payload: unknown;
        try {
          payload = JSON.parse(event.data);
        } catch {
          setError("Bybit sent an unreadable market-data message.");
          return;
        }
        if (!isRecord(payload)) return;
        if (payload.success === false) {
          setError(
            typeof payload.ret_msg === "string"
              ? payload.ret_msg
              : "Bybit rejected the market-data subscription.",
          );
          socket?.close();
          return;
        }
        if (typeof payload.topic === "string" && payload.topic.startsWith("kline.")) {
          if (!Array.isArray(payload.data)) return;
          for (const row of payload.data) {
            const candle = parseCandle(row);
            if (!candle) continue;
            if (
              latestLiveCandleRef.current === null ||
              candle.time >= latestLiveCandleRef.current.time
            ) {
              latestLiveCandleRef.current = candle;
            }
            updateCandleRef.current?.(candle);
            setLastPrice(candle.close);
            setUpdatedAt(new Date());
            setLoading(false);
            setError(null);
          }
        } else if (typeof payload.topic === "string" && payload.topic.startsWith("tickers.")) {
          const ticker = Array.isArray(payload.data) ? payload.data[0] : payload.data;
          if (!isRecord(ticker)) return;
          const price = numberField(ticker.lastPrice);
          if (price !== null) {
            setLastPrice(price);
            setUpdatedAt(new Date());
          }
        }
      };

      socket.onerror = () => {
        if (active) setConnection("reconnecting");
      };
      socket.onclose = () => {
        if (!active) return;
        if (heartbeatTimer !== undefined) window.clearInterval(heartbeatTimer);
        setConnection("reconnecting");
        reconnectTimer = window.setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 30_000);
      };
    };

    connect();
    return () => {
      active = false;
      controller.abort();
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      if (heartbeatTimer !== undefined) window.clearInterval(heartbeatTimer);
      socket?.close();
    };
  }, [enabled, selectedTimeframe.interval, symbol]);

  const statusLabel = connection === "live"
    ? "Bybit live"
    : connection === "reconnecting"
      ? "Reconnecting"
      : "Connecting";

  return (
    <section aria-label={`Live ${symbol} price chart`}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[11px] font-medium text-muted-foreground">{symbol}</p>
          <p className="mt-0.5 text-base font-semibold tabular-nums tracking-tight">
            {lastPrice === null
              ? "—"
              : lastPrice.toLocaleString(undefined, { maximumFractionDigits: 8 })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
            {loading && connection !== "live" ? (
              <RefreshCw className="h-3 w-3 animate-spin" />
            ) : (
              <Radio className={connection === "live" ? "h-3 w-3 text-positive" : "h-3 w-3"} />
            )}
            {statusLabel}
            {updatedAt && (
              <span className="hidden sm:inline">
                {" · "}
                {updatedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
              </span>
            )}
          </span>
          <label className="sr-only" htmlFor={timeframeId}>Chart timeframe</label>
          <select
            id={timeframeId}
            value={timeframe}
            onChange={(event) => setTimeframe(event.target.value)}
            className="rounded-full bg-card px-2.5 py-1.5 text-[11px] text-muted outline-none transition-colors"
          >
            {TIMEFRAMES.map((item) => <option key={item.label} value={item.label}>{item.label}</option>)}
          </select>
        </div>
      </div>
      <div className="relative mt-3 h-52 overflow-hidden rounded-xl bg-background/50 p-1">
        <div ref={containerRef} className="h-full w-full" />
        {loading && latestChartTimeRef.current === null && (
          <div className="absolute inset-0 grid place-items-center px-5">
            <div className="w-full space-y-3" aria-label="Loading live chart">
              <div className="skeleton h-3 w-24 rounded-full" />
              <div className="skeleton h-28 w-full rounded-xl" />
            </div>
          </div>
        )}
        {error && latestChartTimeRef.current === null && (
          <div role="alert" className="absolute inset-0 grid place-items-center px-5 text-center">
            <div className="max-w-56 text-xs leading-5 text-muted">
              <AlertCircle className="mx-auto mb-2 h-4 w-4 text-negative" />
              {error}
            </div>
          </div>
        )}
      </div>
      {error && latestChartTimeRef.current !== null && (
        <p role="status" className="mt-2 truncate text-[10px] text-negative">{error}</p>
      )}
    </section>
  );
}
