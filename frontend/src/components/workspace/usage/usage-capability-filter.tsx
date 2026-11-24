import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import type { UsageCapabilityFilter as CapabilityFilter } from "@/core/usage/types";
import { WORK_CAPABILITIES } from "@/core/workspace/work-capabilities";
import { cn } from "@/lib/utils";

// Both themes set --ring to transparent, so the shared Button's own focus ring
// is invisible; every filter item carries an explicit indigo ring instead.
function itemClass(active: boolean) {
  return cn(
    "h-8 rounded-lg px-3 text-sm focus-visible:ring-indigo-500",
    active
      ? "bg-violet-50 text-violet-600 hover:bg-violet-50 hover:text-violet-600 dark:bg-indigo-400/10 dark:text-indigo-300 dark:hover:bg-indigo-400/10 dark:hover:text-indigo-300"
      : "text-muted-foreground hover:text-foreground",
  );
}

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
      data-testid="usage-filter-toolbar"
      className="bg-card flex min-w-0 flex-col gap-2 rounded-2xl border px-4 py-3 shadow-sm min-[820px]:flex-row min-[820px]:items-center min-[820px]:gap-3"
      role="group"
      aria-label={c.filterLabel}
    >
      <span className="text-muted-foreground shrink-0 text-sm">
        {c.filterLabel}
      </span>
      {/* Below 600px the seven items share one hidden-scrollbar row; from 600px they wrap. */}
      <div
        data-testid="usage-filter-scroll"
        className="scrollbar-none -mx-4 -my-1 flex min-w-0 flex-nowrap gap-1 overflow-x-auto px-4 py-1 min-[600px]:mx-0 min-[600px]:flex-wrap min-[600px]:overflow-visible min-[600px]:px-0 min-[600px]:py-0"
      >
        <Button
          type="button"
          size="sm"
          variant="ghost"
          aria-pressed={value === "all"}
          data-testid="usage-capability-all"
          className={itemClass(value === "all")}
          onClick={() => onChange("all")}
        >
          {c.all}
        </Button>
        {WORK_CAPABILITIES.map((item) => (
          <Button
            key={item.id}
            type="button"
            size="sm"
            variant="ghost"
            aria-pressed={value === item.id}
            data-testid={`usage-capability-${item.id}`}
            className={itemClass(value === item.id)}
            onClick={() => onChange(item.id)}
          >
            {t.workspace.home.courseItems[item.id].title}
          </Button>
        ))}
      </div>
    </div>
  );
}
