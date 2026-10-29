"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  type UTCTimestamp,
} from "lightweight-charts";
import { AlertCircle, RefreshCw } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { fetchMarketData } from "@/lib/api";
import type { MarketCandle } from "@/lib/types";

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

function chartTime(timestamp: string): UTCTimestamp {
  return Math.floor(new Date(timestamp).getTime() / 1000) as UTCTimestamp;
}

export function LiveMarketChart({
  symbol,
  enabled,
}: {
  symbol: string;
  enabled: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const updateChartRef = useRef<((values: MarketCandle[]) => void) | null>(null);
  const fittedRef = useRef(false);
  const timeframeId = useId();
  const [timeframe, setTimeframe] = useState("1m");
  const [candles, setCandles] = useState<MarketCandle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  useEffect(() => {
    fittedRef.current = false;
    setCandles([]);
    setError(null);
    setUpdatedAt(null);
    if (!enabled) return;
    let active = true;
    const refresh = async () => {
      try {
        const snapshot = await fetchMarketData(symbol, timeframe);
        if (!active) return;
        setCandles(snapshot.candles);
        setError(null);
        setUpdatedAt(new Date());
      } catch (reason) {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Could not load market data.");
      } finally {
        if (active) setLoading(false);
      }
    };

    setLoading(true);
    void refresh();
    const interval = window.setInterval(() => void refresh(), 30_000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [enabled, symbol, timeframe]);

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
        vertLines: { color: "#27272a" },
        horzLines: { color: "#27272a" },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: { vertLine: { color: "#3f3f46" }, horzLine: { color: "#3f3f46" } },
      localization: { priceFormatter: (price: number) => price.toLocaleString(undefined, { maximumFractionDigits: 6 }) },
    });
    const priceSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
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
    updateChartRef.current = (values) => {
      priceSeries.setData(
        values.map((candle) => ({
          time: chartTime(candle.timestamp),
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
        })),
      );
      volumeSeries.setData(
        values.map((candle) => ({
          time: chartTime(candle.timestamp),
          value: candle.volume,
          color: candle.close >= candle.open ? "#22c55e40" : "#ef444440",
        })),
      );
      if (values.length && !fittedRef.current) {
        chart.timeScale().fitContent();
        fittedRef.current = true;
      }
    };
    return () => {
      updateChartRef.current = null;
      chart.remove();
    };
  }, [enabled]);

  useEffect(() => {
    updateChartRef.current?.(candles);
  }, [candles]);

  const latest = candles.at(-1);
  return (
    <section aria-label={`Live ${symbol} price chart`}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-medium text-muted">Live market</p>
          <p className="mt-1 truncate text-sm font-semibold">{symbol}</p>
        </div>
        <label className="sr-only" htmlFor={timeframeId}>Chart timeframe</label>
        <select
          id={timeframeId}
          value={timeframe}
          onChange={(event) => setTimeframe(event.target.value)}
          className="rounded-full bg-card px-2.5 py-1.5 text-[11px] text-muted outline-none transition-colors focus:ring-1 focus:ring-accent"
        >
          {TIMEFRAMES.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
      </div>
      <div className="mt-3 flex items-baseline justify-between gap-2">
        <span className="text-lg font-semibold tabular-nums">
          {latest ? latest.close.toLocaleString(undefined, { maximumFractionDigits: 6 }) : "—"}
        </span>
        <span className="flex items-center gap-1 text-[10px] text-muted">
          {loading ? <RefreshCw className="h-3 w-3 animate-spin" /> : updatedAt ? `Updated ${updatedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Waiting for data"}
        </span>
      </div>
      <div className="relative mt-2 h-56 overflow-hidden rounded-xl bg-card/60 p-1">
        <div ref={containerRef} className="h-full w-full" />
        {!candles.length && (
          <div className="absolute inset-0 grid place-items-center px-5 text-center">
            {error ? (
              <div role="alert" className="max-w-56 text-xs leading-5 text-muted">
                <AlertCircle className="mx-auto mb-2 h-4 w-4 text-negative" />
                {error}
              </div>
            ) : loading ? (
              <div className="w-full space-y-3" aria-label="Loading live chart">
                <div className="skeleton h-3 w-24 rounded-full" />
                <div className="skeleton h-28 w-full rounded-xl" />
              </div>
            ) : (
              <p className="text-xs text-muted">No candle data was returned for this market.</p>
            )}
          </div>
        )}
      </div>
      {error && candles.length > 0 && (
        <p role="status" className="mt-2 truncate text-[10px] text-negative">{error}</p>
      )}
    </section>
  );
}
