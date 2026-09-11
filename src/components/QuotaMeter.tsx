import { cn } from "@/lib/utils";
import type { ProviderRow } from "@/lib/types";

/**
 * How much of today's free allowance is left, per provider.
 *
 * This exists because the whole product runs on free tiers: a user needs to see "Groq: 62% of
 * today's requests used" *before* the pipeline stalls, not after. Providers that are unlimited
 * or keyless say so instead of showing a meaningless bar.
 */
export function QuotaMeter({ provider, compact = false }: { provider: ProviderRow; compact?: boolean }) {
  const usage = provider.usage;
  const limited = typeof usage.percent === "number" && (usage.limit_day || usage.limit_minute);
  const percent = usage.percent ?? 0;
  const tone: "accent" | "success" | "danger" | "muted" = usage.in_cooldown || percent >= 90
    ? "danger"
    : percent >= 70
      ? "accent"
      : usage.free_forever
        ? "success"
        : "muted";

  const barTone = {
    accent: "bg-accent",
    success: "bg-teal-400",
    danger: "bg-danger-solid",
    muted: "bg-surface-500",
  }[tone];

  return (
    <div className={cn("flex items-center gap-3", compact ? "text-xs" : "text-sm")}>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className={cn("truncate text-mint-100", compact && "text-xs")}>{provider.name}</span>
          <span className="shrink-0 text-muted">
            {usage.in_cooldown
              ? `cooling down ${Math.ceil(usage.cooldown_remaining_s)}s`
              : limited
                ? `${percent}% of today`
                : provider.free_forever
                  ? "no daily limit"
                  : provider.one_time_credit
                    ? "credit remaining"
                    : "not connected"}
          </span>
        </div>
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-charcoal-800">
          <div
            className={cn("h-full rounded-full transition-transform duration-base", barTone)}
            style={{ width: `${limited ? Math.min(100, percent) : provider.free_forever ? 8 : 0}%` }}
          />
        </div>
        {!compact && usage.last_error ? (
          <p className="text-xs text-danger-soft mt-1 truncate">{usage.last_error}</p>
        ) : null}
      </div>
    </div>
  );
}

/**
 * The single number in the command bar: how much of the whole studio's daily free allowance is
 * left, averaged across the providers that are actually in use.
 */
export function aggregateQuota(providers: ProviderRow[]): { percent: number; used: number; total: number } {
  const measured = providers.filter((provider) => typeof provider.usage.percent === "number");
  if (measured.length === 0) return { percent: 0, used: 0, total: 0 };
  const total = measured.reduce((sum, provider) => sum + (provider.usage.percent ?? 0), 0);
  return { percent: Math.round(total / measured.length), used: measured.length, total: measured.length };
}
