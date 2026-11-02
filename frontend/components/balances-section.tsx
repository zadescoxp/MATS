"use client";

import { Cable, RefreshCw } from "lucide-react";
import Image from "next/image";
import { useState } from "react";

import { cn } from "@/lib/cn";
import { coinLogo, marketName } from "@/lib/markets";
import type { AccountSnapshot, AssetHolding } from "@/lib/types";
import { venueFor } from "@/lib/venues";

const STABLECOINS = new Set(["USDT", "USDC"]);

function usd(value: number | null | undefined, digits = 2) {
  if (value == null) return "—";
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function quantity(value: number) {
  return value.toLocaleString(undefined, { maximumFractionDigits: value >= 1 ? 4 : 8 });
}

function assetName(ticker: string) {
  if (STABLECOINS.has(ticker)) return ticker === "USDT" ? "Tether" : "USD Coin";
  const name = marketName(`${ticker}/USDT`);
  return name === ticker ? "" : name;
}

function Logo({ src, label, size, rounded = "full" }: { src?: string; label: string; size: number; rounded?: "full" | "lg" }) {
  const [failed, setFailed] = useState(false);
  return (
    <span
      className={cn(
        "grid shrink-0 place-items-center overflow-hidden bg-card text-[10px] font-semibold text-foreground",
        rounded === "full" ? "rounded-full" : "rounded-lg",
      )}
      style={{ width: size, height: size }}
      aria-hidden
    >
      {src && !failed ? (
        <Image src={src} alt="" width={size} height={size} className="object-contain" onError={() => setFailed(true)} />
      ) : (
        label.slice(0, 1)
      )}
    </span>
  );
}

function HoldingRow({ asset, total }: { asset: AssetHolding; total: number }) {
  const share = total > 0 && asset.usd_value != null ? asset.usd_value / total : 0;
  const name = assetName(asset.asset);
  return (
    <li className="grid grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,0.9fr)] items-center gap-3 py-2.5">
      <span className="flex min-w-0 items-center gap-2.5">
        <Logo src={coinLogo(asset.asset)} label={asset.asset} size={26} />
        <span className="min-w-0">
          <span className="block truncate text-[13px] font-medium">{asset.asset}</span>
          {name && <span className="block truncate text-[11px] text-muted-foreground">{name}</span>}
        </span>
      </span>
      <span className="min-w-0 text-right">
        <span className="block truncate text-[13px] tabular-nums">{quantity(asset.total)}</span>
        <span className="block truncate text-[11px] text-muted-foreground tabular-nums">
          {asset.locked > 0 ? `${quantity(asset.free)} free · ${quantity(asset.locked)} locked` : "all free"}
          {asset.borrowed > 0 && <span className="text-negative"> · {quantity(asset.borrowed)} borrowed</span>}
        </span>
      </span>
      <span className="min-w-0 text-right">
        <span className="block text-[13px] tabular-nums">{usd(asset.usd_value)}</span>
        <span className="mt-1 flex items-center justify-end gap-1.5">
          <span className="h-1 w-12 overflow-hidden rounded-full bg-white/[0.06]">
            <span className="block h-full rounded-full bg-foreground/60" style={{ width: `${Math.max(share * 100, share > 0 ? 2 : 0)}%` }} />
          </span>
          <span className="w-9 text-right text-[11px] tabular-nums text-muted-foreground">{(share * 100).toFixed(share >= 0.1 ? 0 : 1)}%</span>
        </span>
      </span>
    </li>
  );
}

function VenueCard({ account, busy, onRefresh }: { account: AccountSnapshot; busy: boolean; onRefresh: () => void }) {
  const venue = venueFor(account.venue);
  const name = account.venue_name ?? venue?.name ?? "Venue";
  const assets = account.assets ?? [];
  const total = account.total_equity_usd ?? assets.reduce((sum, asset) => sum + (asset.usd_value ?? 0), 0);
  return (
    <section className="rounded-2xl border border-white/[0.07]" aria-label={`${name} balances`}>
      <header className="flex items-start gap-3 border-b border-white/[0.05] px-4 py-3.5">
        <Logo src={venue?.logo} label={name} size={32} rounded="lg" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <p className="text-sm font-semibold">{name}</p>
            {account.testnet && (
              <span className="rounded-md border border-white/[0.08] px-1.5 py-px text-[10px] font-medium text-muted">Testnet</span>
            )}
            {account.account_type && (
              <span className="rounded-md border border-white/[0.08] px-1.5 py-px text-[10px] font-medium text-muted">
                {account.account_type.charAt(0) + account.account_type.slice(1).toLowerCase()}
              </span>
            )}
          </div>
          <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
            Source: {account.source ?? `${name} API`}
            {account.fetched_at ? ` · updated ${new Date(account.fetched_at).toLocaleTimeString()}` : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={busy}
          aria-label={`Refresh ${name} balances`}
          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted transition-colors hover:bg-card hover:text-foreground disabled:opacity-40"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", busy && "animate-spin")} />
        </button>
      </header>
      {account.error && (
        <p role="alert" className="border-b border-white/[0.05] px-4 py-2 text-[11px] text-negative">
          Could not refresh: {account.error}. Showing the last balances received.
        </p>
      )}
      <div className="grid grid-cols-2 gap-3 px-4 pt-3.5">
        <div>
          <p className="text-[11px] text-muted-foreground">Equity</p>
          <p className="mt-0.5 text-base font-semibold tabular-nums">{usd(account.total_equity_usd)}</p>
        </div>
        <div>
          <p className="text-[11px] text-muted-foreground">Available to trade</p>
          <p className="mt-0.5 text-base font-semibold tabular-nums">{usd(account.available_usd)}</p>
        </div>
      </div>
      <div className="px-4 pb-1 pt-3">
        <div className="grid grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,0.9fr)] gap-3 border-b border-white/[0.05] pb-2 text-[11px] text-muted-foreground">
          <span>Asset</span>
          <span className="text-right">Amount</span>
          <span className="text-right">Value</span>
        </div>
        {assets.length ? (
          <ul className="divide-y divide-white/[0.04]">
            {assets.map((asset) => (
              <HoldingRow key={asset.asset} asset={asset} total={total} />
            ))}
          </ul>
        ) : (
          <p className="py-4 text-[11px] text-muted">This account holds no assets.</p>
        )}
      </div>
    </section>
  );
}

export function BalancesSection({
  accounts,
  busy,
  onRefresh,
  onOpenConnections,
}: {
  accounts: AccountSnapshot[];
  busy: boolean;
  onRefresh: () => void;
  onOpenConnections: () => void;
}) {
  const totalEquity = accounts.reduce((sum, account) => sum + (account.total_equity_usd ?? 0), 0);
  const totalAvailable = accounts.reduce((sum, account) => sum + (account.available_usd ?? 0), 0);
  const assetCount = new Set(accounts.flatMap((account) => (account.assets ?? []).map((asset) => asset.asset))).size;
  return (
    <div className="max-w-2xl">
      <h2 className="text-lg font-semibold tracking-tight">Balances & assets</h2>
      <p className="mt-1 text-xs leading-5 text-muted">
        Read directly from each connected venue every 30 seconds. Live trades only use the balance shown as available.
      </p>
      {accounts.length === 0 ? (
        <div className="mt-6 rounded-2xl border border-white/[0.07] px-5 py-6 text-center">
          <Logo src={venueFor("bybit")?.logo} label="Bybit" size={36} rounded="lg" />
          <p className="mt-3 text-sm font-medium">No venue connected</p>
          <p className="mx-auto mt-1 max-w-sm text-[11px] leading-5 text-muted">
            Add Bybit testnet API keys to see your balances and holdings here.
          </p>
          <button
            type="button"
            onClick={onOpenConnections}
            className="mx-auto mt-4 inline-flex h-8 items-center gap-1.5 rounded-lg bg-foreground px-3 text-xs font-medium text-background transition-colors hover:bg-foreground/90"
          >
            <Cable className="h-3.5 w-3.5" />
            Connect Bybit
          </button>
        </div>
      ) : (
        <>
          <div className="mt-6 grid grid-cols-3 gap-px overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.05]">
            {[
              { label: "Total equity", value: usd(totalEquity) },
              { label: "Available to trade", value: usd(totalAvailable) },
              { label: "Assets held", value: String(assetCount) },
            ].map((item) => (
              <div key={item.label} className="bg-surface px-4 py-3">
                <p className="text-[11px] text-muted-foreground">{item.label}</p>
                <p className="mt-1 text-sm font-semibold tabular-nums">{item.value}</p>
              </div>
            ))}
          </div>
          <div className="mt-4 space-y-4">
            {accounts.map((account) => (
              <VenueCard key={account.venue ?? "venue"} account={account} busy={busy} onRefresh={onRefresh} />
            ))}
          </div>
          <p className="mt-4 text-[11px] text-muted-foreground">Other venues will appear here once they are supported and connected.</p>
        </>
      )}
    </div>
  );
}
