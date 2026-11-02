import { RefreshCw } from "lucide-react";

import { cn } from "@/lib/cn";
import type { AccountSnapshot } from "@/lib/types";

function usd(value: number | null | undefined) {
  if (value == null) return "—";
  return value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
}

function amount(value: number) {
  return value.toLocaleString(undefined, { maximumFractionDigits: value >= 1 ? 4 : 8 });
}

export function AccountPanel({
  account,
  busy,
  onRefresh,
}: {
  account: AccountSnapshot | undefined;
  busy: boolean;
  onRefresh: () => void;
}) {
  if (!account?.connected) {
    return (
      <section className="rounded-xl border border-white/[0.06] px-4 py-3">
        <p className="text-xs font-medium">Bybit account</p>
        <p className="mt-1 text-[11px] leading-5 text-muted">
          Add Bybit testnet API keys under Settings → Connections to see balances. Live orders are
          sized against what the account actually holds.
        </p>
      </section>
    );
  }
  const assets = account.assets ?? [];
  return (
    <section className="rounded-xl border border-white/[0.06] px-4 py-3" aria-label="Bybit account">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium">
            Bybit {account.testnet ? "testnet" : "account"}
            {account.account_type ? <span className="font-normal text-muted"> · {account.account_type.toLowerCase()}</span> : null}
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {account.fetched_at ? `Updated ${new Date(account.fetched_at).toLocaleTimeString()}` : "Not loaded yet"}
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={busy}
          aria-label="Refresh balances"
          className="grid h-7 w-7 place-items-center rounded-lg text-muted transition-colors hover:bg-card hover:text-foreground disabled:opacity-40"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", busy && "animate-spin")} />
        </button>
      </div>
      {account.error && <p className="mt-2 text-[11px] text-negative">Could not refresh: {account.error}</p>}
      <div className="mt-3 grid grid-cols-2 gap-3">
        <div>
          <p className="text-[11px] text-muted-foreground">Total equity</p>
          <p className="mt-0.5 text-sm font-semibold tabular-nums">{usd(account.total_equity_usd)}</p>
        </div>
        <div>
          <p className="text-[11px] text-muted-foreground">Available to trade</p>
          <p className="mt-0.5 text-sm font-semibold tabular-nums">{usd(account.available_usd)}</p>
        </div>
      </div>
      {assets.length > 0 ? (
        <table className="mt-3 w-full text-[11px]">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 text-left font-normal">Asset</th>
              <th className="py-1 text-right font-normal">Free</th>
              <th className="py-1 text-right font-normal">Locked</th>
              <th className="py-1 text-right font-normal">Value</th>
            </tr>
          </thead>
          <tbody>
            {assets.map((asset) => (
              <tr key={asset.asset} className="border-t border-white/[0.04]">
                <td className="py-1.5 font-medium">
                  {asset.asset}
                  {asset.borrowed > 0 && <span className="ml-1.5 text-negative">borrowed {amount(asset.borrowed)}</span>}
                </td>
                <td className="py-1.5 text-right tabular-nums">{amount(asset.free)}</td>
                <td className="py-1.5 text-right tabular-nums text-muted">{asset.locked ? amount(asset.locked) : "—"}</td>
                <td className="py-1.5 text-right tabular-nums">{usd(asset.usd_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="mt-3 text-[11px] text-muted">The account holds no assets.</p>
      )}
    </section>
  );
}
