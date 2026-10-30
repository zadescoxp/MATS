"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

interface Candle {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface TradeMark {
  entryTime: number;
  exitTime: number;
  entryPrice: number;
  exitPrice: number;
  netPnl: number;
  exitReason: string;
}

const INTERVALS: Record<string, string> = {
  "1m": "1",
  "3m": "3",
  "5m": "5",
  "15m": "15",
  "30m": "30",
  "1h": "60",
  "2h": "120",
  "4h": "240",
  "6h": "360",
  "12h": "720",
  "1d": "D",
  "1w": "W",
};

const BAR_SECONDS: Record<string, number> = {
  "1m": 60,
  "3m": 180,
  "5m": 300,
  "15m": 900,
  "30m": 1800,
  "1h": 3600,
  "2h": 7200,
  "4h": 14400,
  "6h": 21600,
  "12h": 43200,
  "1d": 86400,
  "1w": 604800,
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function numberField(value: unknown): number | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function unixSeconds(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 1e12 ? Math.floor(value / 1000) : Math.floor(value);
  }
  if (typeof value === "string" && value) {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
  }
  return null;
}

function parseHistoryRow(row: unknown): Candle | null {
  if (!Array.isArray(row) || row.length < 6) return null;
  const start = numberField(row[0]);
  const open = numberField(row[1]);
  const high = numberField(row[2]);
  const low = numberField(row[3]);
  const close = numberField(row[4]);
  const volume = numberField(row[5]);
  if (start === null || open === null || high === null || low === null || close === null || volume === null) {
    return null;
  }
  return { time: Math.floor(start / 1000) as UTCTimestamp, open, high, low, close, volume };
}

function coinSymbol(symbol: string): string {
  return symbol.replace("/", "").replace(":", "");
}

function readTrades(trades: Array<Record<string, unknown>>): TradeMark[] {
  return trades.flatMap((trade) => {
    const entryTime = unixSeconds(trade.entry_time);
    const exitTime = unixSeconds(trade.exit_time);
    const entryPrice = numberField(trade.entry_price);
    const exitPrice = numberField(trade.exit_price);
    const netPnl = numberField(trade.net_pnl);
    if (entryTime === null || exitTime === null || entryPrice === null || exitPrice === null || netPnl === null) {
      return [];
    }
    return [{
      entryTime,
      exitTime,
      entryPrice,
      exitPrice,
      netPnl,
      exitReason: typeof trade.exit_reason === "string" ? trade.exit_reason.replaceAll("_", " ") : "exit",
    }];
  });
}

async function fetchCandles(
  symbol: string,
  interval: string,
  startSec: number | null,
  endSec: number | null,
  signal: AbortSignal,
): Promise<Candle[]> {
  const collected: Candle[] = [];
  let end = endSec === null ? undefined : endSec * 1000;
  for (let page = 0; page < 6; page += 1) {
    const query = new URLSearchParams({
      category: "spot",
      symbol: coinSymbol(symbol),
      interval,
      limit: "1000",
    });
    if (end !== undefined) query.set("end", String(end));
    const response = await fetch(`https://api.bybit.com/v5/market/kline?${query}`, { signal });
    if (!response.ok) throw new Error(`Bybit candle history returned ${response.status}.`);
    const payload: unknown = await response.json();
    if (!isRecord(payload) || payload.retCode !== 0 || !isRecord(payload.result) || !Array.isArray(payload.result.list)) {
      const message = isRecord(payload) && typeof payload.retMsg === "string"
        ? payload.retMsg
        : "Bybit returned an invalid candle history response.";
      throw new Error(message);
    }
    const batch = payload.result.list
      .map(parseHistoryRow)
      .filter((candle): candle is Candle => candle !== null)
      .sort((left, right) => left.time - right.time);
    if (!batch.length) break;
    collected.push(...batch);
    const oldest = batch[0].time as number;
    if (startSec === null || oldest <= startSec || batch.length < 1000) break;
    end = oldest * 1000 - 1;
  }
  const unique = new Map<number, Candle>();
  for (const candle of collected) unique.set(candle.time as number, candle);
  return [...unique.values()].sort((left, right) => left.time - right.time);
}

function snapTime(candles: Candle[], seconds: number): UTCTimestamp | null {
  if (!candles.length) return null;
  let low = 0;
  let high = candles.length - 1;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if ((candles[mid].time as number) < seconds) low = mid + 1;
    else high = mid;
  }
  const next = candles[low];
  const previous = candles[Math.max(0, low - 1)];
  const nextDiff = Math.abs((next.time as number) - seconds);
  const previousDiff = Math.abs((previous.time as number) - seconds);
  return (previousDiff <= nextDiff ? previous.time : next.time);
}

function pnlText(value: number): string {
  const sign = value >= 0 ? "+" : "−";
  return `${sign}${Math.abs(value).toFixed(0)}`;
}

function tradeMarkers(candles: Candle[], trades: TradeMark[]): SeriesMarker<UTCTimestamp>[] {
  const markers: SeriesMarker<UTCTimestamp>[] = [];
  for (const trade of trades) {
    const entryTime = snapTime(candles, trade.entryTime);
    const exitTime = snapTime(candles, trade.exitTime);
    const win = trade.netPnl >= 0;
    if (entryTime) {
      markers.push({
        time: entryTime,
        position: "atPriceMiddle",
        price: trade.entryPrice,
        shape: "arrowUp",
        color: "#34d399",
        text: "Long",
        size: 1.4,
      });
    }
    if (exitTime) {
      markers.push({
        time: exitTime,
        position: "atPriceMiddle",
        price: trade.exitPrice,
        shape: "arrowDown",
        color: win ? "#34d399" : "#f87171",
        text: pnlText(trade.netPnl),
        size: 1.4,
      });
    }
  }
  return markers.sort((left, right) => (left.time as number) - (right.time as number));
}

export function BacktestChart({
  symbol,
  timeframe,
  trades,
}: {
  symbol: string;
  timeframe: string;
  trades: Array<Record<string, unknown>>;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState<string | null>(null);
  const tradesRef = useRef(trades);
  tradesRef.current = trades;
  const signature = JSON.stringify(trades.map((trade) => [
    trade.entry_time,
    trade.exit_time,
    trade.entry_price,
    trade.exit_price,
    trade.net_pnl,
    trade.exit_reason,
  ]));
  const interval = INTERVALS[timeframe] ?? "D";
  const barSeconds = BAR_SECONDS[timeframe] ?? BAR_SECONDS["1d"];

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
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
      timeScale: { borderVisible: false, timeVisible: timeframe !== "1d" && timeframe !== "1w", secondsVisible: false },
      crosshair: { vertLine: { color: "#3f3f46" }, horzLine: { color: "#3f3f46" } },
      localization: {
        priceFormatter: (price: number) => price.toLocaleString(undefined, { maximumFractionDigits: 8 }),
      },
    });
    const priceSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#34d399",
      downColor: "#f87171",
      borderVisible: false,
      wickUpColor: "#34d399",
      wickDownColor: "#f87171",
      priceLineVisible: false,
    });
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const markers = createSeriesMarkers(priceSeries, []);

    const marks = readTrades(tradesRef.current);
    const controller = new AbortController();
    const earliest = marks.reduce<number | null>((min, trade) => (min === null ? trade.entryTime : Math.min(min, trade.entryTime)), null);
    const latest = marks.reduce<number | null>((max, trade) => (max === null ? trade.exitTime : Math.max(max, trade.exitTime)), null);
    const padding = barSeconds * 24;
    const start = earliest === null ? null : earliest - padding;
    const end = latest === null ? null : latest + padding;

    setStatus("loading");
    setMessage(null);
    void fetchCandles(symbol, interval, start, end, controller.signal)
      .then((candles) => {
        priceSeries.setData(candles.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
        volumeSeries.setData(candles.map(({ time, open, close, volume }) => ({
          time,
          value: volume,
          color: close >= open ? "#34d39955" : "#f8717155",
        })));
        markers.setMarkers(tradeMarkers(candles, marks));
        if (candles.length) chart.timeScale().fitContent();
        setStatus(candles.length ? "ready" : "error");
        setMessage(candles.length ? null : "Bybit returned no candles for this backtest window.");
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setStatus("error");
        setMessage(reason instanceof Error ? reason.message : "Could not load the backtest chart.");
      });

    return () => {
      controller.abort();
      chart.remove();
    };
  }, [barSeconds, interval, signature, symbol, timeframe]);

  return (
    <div className="mt-3">
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5"><span className="text-positive">▲</span> Entry</span>
        <span className="inline-flex items-center gap-1.5"><span className="text-positive">▼</span> Exit in profit</span>
        <span className="inline-flex items-center gap-1.5"><span className="text-negative">▼</span> Exit at a loss</span>
        <span>Unmarked candles were flat</span>
      </div>
      <div className="relative h-[420px] w-full">
        <div ref={containerRef} className="h-full w-full" />
        {status === "loading" && (
          <p className="absolute left-3 top-3 text-xs text-muted">Loading {symbol} {timeframe} candles…</p>
        )}
        {status === "error" && message && (
          <p className="absolute left-3 top-3 max-w-sm text-xs leading-5 text-negative">{message}</p>
        )}
      </div>
    </div>
  );
}
