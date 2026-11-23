import { CoinsIcon } from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import { formatTokenCount, type TokenUsage } from "@/core/messages/usage";
import { cn } from "@/lib/utils";

export function TurnTokenUsage({
  className,
  usage,
  missingCount,
}: {
  className?: string;
  usage: TokenUsage | null;
  missingCount: number;
}) {
  const { t } = useI18n();

  return (
    <div
      className={cn(
        "text-muted-foreground border-border/60 mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-2 text-[11px]",
        className,
      )}
      data-testid="turn-token-usage"
    >
      <span className="inline-flex items-center gap-1 font-medium">
        <CoinsIcon className="size-3" />
        {t.tokenUsage.label}
      </span>
      {usage ? (
        <>
          <span>
            {t.tokenUsage.input}: {formatTokenCount(usage.inputTokens)}
          </span>
          <span>
            {t.tokenUsage.output}: {formatTokenCount(usage.outputTokens)}
          </span>
          <span className="font-medium">
            {t.tokenUsage.total}: {formatTokenCount(usage.totalTokens)}
          </span>
          {missingCount > 0 && (
            <span>
              {t.tokenUsage.unavailableShort} ×{missingCount}
            </span>
          )}
        </>
      ) : (
        <span>{t.tokenUsage.unavailableShort}</span>
      )}
    </div>
  );
}
