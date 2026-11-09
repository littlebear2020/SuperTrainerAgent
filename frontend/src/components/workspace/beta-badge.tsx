import { cn } from "@/lib/utils";

/**
 * Small amber "Beta" chip shown next to beta-stage workspace modules
 * (配音 / 写作 / 视频). Style matches the original inline badge on the
 * voice-twin page so all four call sites render identically.
 */
export function BetaBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-amber-600",
        className,
      )}
    >
      Beta
    </span>
  );
}
