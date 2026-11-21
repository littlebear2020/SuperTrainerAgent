import { UsageCostSourceBadge } from "@/components/workspace/usage/usage-summary";
import { useI18n } from "@/core/i18n/hooks";
import type { UsageModel } from "@/core/usage/types";
import { formatCost, formatInteger } from "@/core/utils/format";

export function UsageModelBreakdown({
  models,
  currency,
}: {
  models: UsageModel[];
  currency: string | null;
}) {
  const { t } = useI18n();
  const c = t.personalUsage;
  if (models.length === 0) {
    return (
      <p className="text-muted-foreground rounded-lg border border-dashed p-4 text-sm">
        {c.noModels}
      </p>
    );
  }
  return (
    <div
      data-testid="model-table-scroll"
      className="max-w-full min-w-0 overflow-x-auto rounded-lg border"
    >
      <table className="w-full min-w-[640px] border-collapse text-left text-xs">
        <thead className="bg-muted/60 text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">{c.model}</th>
            <th className="px-3 py-2 text-right font-medium">
              {c.inputTokens}
            </th>
            <th className="px-3 py-2 text-right font-medium">
              {c.outputTokens}
            </th>
            <th className="px-3 py-2 text-right font-medium">
              {c.cacheReadTokens}
            </th>
            <th className="px-3 py-2 text-right font-medium">
              {c.modelTotalTokens}
            </th>
            <th className="px-3 py-2 text-right font-medium">{c.modelCost}</th>
          </tr>
        </thead>
        <tbody>
          {models.map((model, index) => (
            <tr key={model.name} className="border-t align-top">
              <td
                data-testid={`model-name-${index}`}
                className="max-w-[240px] min-w-[180px] px-3 py-2 break-all"
              >
                {model.name}
              </td>
              <td
                data-testid={`model-input-${index}`}
                className="px-3 py-2 text-right whitespace-nowrap"
              >
                {formatInteger(model.input_tokens)}
              </td>
              <td
                data-testid={`model-output-${index}`}
                className="px-3 py-2 text-right whitespace-nowrap"
              >
                {formatInteger(model.output_tokens)}
              </td>
              <td
                data-testid={`model-cache-${index}`}
                className="px-3 py-2 text-right whitespace-nowrap"
              >
                {formatInteger(model.cache_read_tokens)}
              </td>
              <td
                data-testid={`model-total-${index}`}
                className="px-3 py-2 text-right font-medium whitespace-nowrap"
              >
                {formatInteger(model.total_tokens)}
              </td>
              <td className="px-3 py-2">
                <div className="flex flex-col items-end gap-1">
                  <span
                    data-testid={`model-cost-${index}`}
                    className="whitespace-nowrap"
                  >
                    {formatCost(model.cost, currency)}
                  </span>
                  <UsageCostSourceBadge
                    source={model.cost_source}
                    testId={`model-cost-source-${index}`}
                  />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
