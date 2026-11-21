import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import type { UsageCapabilityFilter as CapabilityFilter } from "@/core/usage/types";
import { WORK_CAPABILITIES } from "@/core/workspace/work-capabilities";

export function UsageCapabilityFilter({
  value,
  onChange,
}: {
  value: CapabilityFilter;
  onChange: (value: CapabilityFilter) => void;
}) {
  const { t } = useI18n();
  const c = t.personalUsage;
  return (
    <div
      className="flex min-w-0 flex-wrap gap-2"
      role="group"
      aria-label={c.filterLabel}
    >
      <Button
        type="button"
        size="sm"
        variant={value === "all" ? "default" : "outline"}
        aria-pressed={value === "all"}
        data-testid="usage-capability-all"
        onClick={() => onChange("all")}
      >
        {c.all}
      </Button>
      {WORK_CAPABILITIES.map((item) => (
        <Button
          key={item.id}
          type="button"
          size="sm"
          variant={value === item.id ? "default" : "outline"}
          aria-pressed={value === item.id}
          data-testid={`usage-capability-${item.id}`}
          onClick={() => onChange(item.id)}
        >
          {t.workspace.home.courseItems[item.id].title}
        </Button>
      ))}
    </div>
  );
}
