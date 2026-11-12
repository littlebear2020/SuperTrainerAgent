"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useConsoleExternalCalls,
  useConsoleRuns,
  useConsoleStats,
  useConsoleUsage,
  useIsConsoleAdmin,
} from "@/core/console/hooks";
import type { ConsoleRunSortKey, SortDirection } from "@/core/console/types";
import { useI18n } from "@/core/i18n/hooks";

import { ConsoleCompositionPanel } from "./console-composition-panel";
import { ConsoleCostChart } from "./console-cost-chart";
import { ConsoleExternalCallsPanel } from "./console-external-calls-panel";
import { ConsoleLlmUsagePanel } from "./console-llm-usage-panel";
import { ConsolePricingPanel } from "./console-pricing-panel";
import { ConsoleRunsTable } from "./console-runs-table";
import { ConsoleStatCards } from "./console-stat-cards";
import { ConsoleUsageChart } from "./console-usage-chart";
import { ALL_USERS_VALUE, ConsoleUserPicker } from "./console-user-picker";

const PAGE_SIZE = 20;
const WINDOW_OPTIONS = [7, 14, 30, 90];
const STATUS_OPTIONS = [
  "success",
  "error",
  "timeout",
  "running",
  "pending",
  "interrupted",
];
const ALL_STATUS_VALUE = "__all__";

type TopTab = "analysis" | "pricing";
type ChartTab = "usage" | "cost";

/** C6b-5：新鲜度文案——"数据更新于 N 分钟前"（不写频率，用户拍板 2026-08-26）。 */
function freshnessLabel(
  dataUpdatedAt: number,
  c: { dataFreshness: string; justNow: string; minutesAgo: string },
): string {
  if (!dataUpdatedAt) return "";
  const minutes = Math.max(0, Math.round((Date.now() - dataUpdatedAt) / 60000));
  if (minutes < 1) return c.justNow;
  return c.dataFreshness.replace(
    "{n}",
    c.minutesAgo.replace("{n}", String(minutes)),
  );
}

/**
 * Operations console.
 *
 * Same page for everyone; scope differs. A regular user sees their own runs
 * and spend (identical to upstream's behavior). An admin additionally gets
 * the platform-wide view plus a per-user drill-down. The gateway is the
 * authority on that boundary - this component only decides what to render.
 *
 * C6b 布局（2026-08-27 成本可信化批次）：一级 tab 分离角色视图（用量分析 /
 * 定价管理-仅 admin）；「先总后分」漏斗——总账卡 → 趋势区（用量/花费切换卡）
 * → 构成区（按模型/供应商/类型）→ 明细区 → 管理区；页头新鲜度条；统计卡随
 * 天数选择器（默认 7 天，A17/A5）。
 */
export function ConsolePage() {
  const { t } = useI18n();
  const c = t.console;
  const isAdmin = useIsConsoleAdmin();

  const [topTab, setTopTab] = useState<TopTab>("analysis");
  const effectiveTopTab = isAdmin ? topTab : "analysis";
  const [chartTab, setChartTab] = useState<ChartTab>("usage");
  const [selectedUser, setSelectedUser] = useState(ALL_USERS_VALUE);
  const [days, setDays] = useState(7);
  const [status, setStatus] = useState(ALL_STATUS_VALUE);
  const [sortBy, setSortBy] = useState<ConsoleRunSortKey>("created_at");
  const [sortDir, setSortDir] = useState<SortDirection>("desc");
  const [offset, setOffset] = useState(0);

  // A regular user never sends user_id: the gateway scopes them to themselves
  // anyway, and sending it would be a no-op at best.
  const userId =
    isAdmin && selectedUser !== ALL_USERS_VALUE ? selectedUser : undefined;
  const statusFilter = status === ALL_STATUS_VALUE ? undefined : status;

  // C5: 统计卡随天数选择器（A5）；外部调用查询提升到页层（构成区/花费图共用）。
  const statsQuery = useConsoleStats(userId, days);
  const usageQuery = useConsoleUsage({ days, userId });
  const externalCallsQuery = useConsoleExternalCalls({ days, userId });
  const runsQuery = useConsoleRuns({
    limit: PAGE_SIZE,
    offset,
    status: statusFilter,
    sortBy,
    sortDir,
    userId,
  });

  const currency = statsQuery.data?.currency ?? usageQuery.data?.currency;

  const handleSort = (key: ConsoleRunSortKey) => {
    if (key === sortBy) {
      setSortDir((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(key);
      setSortDir("desc");
    }
    // Any re-ordering invalidates the current offset.
    setOffset(0);
  };

  const resetPaging =
    <T,>(setter: (value: T) => void) =>
    (value: T) => {
      setter(value);
      setOffset(0);
    };

  const refreshAll = () => {
    void statsQuery.refetch();
    void usageQuery.refetch();
    void externalCallsQuery.refetch();
    void runsQuery.refetch();
  };

  const freshness = freshnessLabel(
    Math.max(statsQuery.dataUpdatedAt, usageQuery.dataUpdatedAt),
    c,
  );
  const runsCount = runsQuery.data?.runs.length ?? 0;
  const dailyCost = externalCallsQuery.data?.by_date ?? [];

  return (
    <div className="flex size-full flex-col gap-4 overflow-y-auto p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">
            {isAdmin ? c.titleAdmin : c.titleSelf}
          </h1>
          <p className="text-muted-foreground text-xs">
            {isAdmin && !userId ? c.subtitleAdmin : c.subtitleSelf}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ConsoleUserPicker
            enabled={isAdmin}
            value={selectedUser}
            onChange={resetPaging(setSelectedUser)}
          />
          <Select
            value={String(days)}
            onValueChange={(value) => setDays(Number(value))}
          >
            <SelectTrigger className="h-8 w-[120px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WINDOW_OPTIONS.map((option) => (
                <SelectItem
                  key={option}
                  value={String(option)}
                  className="text-xs"
                >
                  {c.lastNDays.replace("{n}", String(option))}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {/* C6b-5：页头新鲜度条（不写更新频率——实际秒级，频率表述易误导） */}
          <span
            data-testid="console-freshness"
            className="text-muted-foreground text-[11px] tabular-nums"
          >
            {freshness && <>· {freshness}</>}
          </span>
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-xs"
            onClick={refreshAll}
            data-testid="console-refresh"
          >
            {c.refreshNow}
          </Button>
        </div>
      </header>

      {/* C6b-4：一级 tab 分离角色视图——定价管理仅 admin 可见（API 侧
          require_admin 双保险）。 */}
      <div className="flex gap-1" data-testid="console-top-tabs">
        <button
          data-testid="top-tab-analysis"
          onClick={() => setTopTab("analysis")}
          className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
            effectiveTopTab === "analysis"
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:bg-muted"
          }`}
        >
          {c.tabUsageAnalysis}
        </button>
        {isAdmin && (
          <button
            data-testid="top-tab-pricing"
            onClick={() => setTopTab("pricing")}
            className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
              effectiveTopTab === "pricing"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            {c.tabPricingAdmin}
          </button>
        )}
      </div>

      {effectiveTopTab === "pricing" ? (
        isAdmin && <ConsolePricingPanel enabled={isAdmin} />
      ) : (
        <>
          {/* ① 总账卡（先总后分漏斗之首） */}
          <ConsoleStatCards
            stats={statsQuery.data}
            isLoading={statsQuery.isLoading}
            error={statsQuery.error}
            onRetry={() => statsQuery.refetch()}
          />

          {/* ② 趋势区：用量/花费同一卡位切换（用户拍板 2026-08-26——省一半
              纵向空间、保单位分离与对照可达性；两图本体逻辑不变）。 */}
          <div className="bg-card/60 rounded-xl border p-4">
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <div className="flex gap-1">
                {(["usage", "cost"] as ChartTab[]).map((key) => (
                  <button
                    key={key}
                    data-testid={`chart-tab-${key}`}
                    onClick={() => setChartTab(key)}
                    className={`rounded px-2 py-1 text-xs font-medium transition-colors ${
                      chartTab === key
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:bg-muted"
                    }`}
                  >
                    {key === "usage" ? c.chartTabUsage : c.chartTabCost}
                  </button>
                ))}
              </div>
              <span
                className="text-muted-foreground text-[11px]"
                data-testid="chart-panel-note"
              >
                {/* F1（2026-08-27 评审修复）：口径说明随 tab 切换——修前花费 tab
                    下仍显示"不含媒体生成"，与媒体成本图内容相反 */}
                {chartTab === "cost" ? c.costPanelNote : c.usagePanelNote}
              </span>
            </div>
            {chartTab === "usage" ? (
              <ConsoleUsageChart
                usage={usageQuery.data}
                isLoading={usageQuery.isLoading}
                error={usageQuery.error}
                onRetry={() => usageQuery.refetch()}
              />
            ) : (
              <div className="flex flex-col gap-1">
                <ConsoleCostChart data={dailyCost} currency={currency} />
                {/* C6：单价修正分界标注——C0 读时重算致跨修正点趋势断崖，
                    防误读为成本暴跌。 */}
                <p className="text-muted-foreground text-[11px]">
                  {c.priceFixBoundaryNote}
                </p>
              </div>
            )}
          </div>

          {/* ③ 构成区：按模型 / 按供应商 / 按类型 */}
          <ConsoleCompositionPanel
            usage={usageQuery.data}
            externalCalls={externalCallsQuery.data}
            isLoading={usageQuery.isLoading || externalCallsQuery.isLoading}
          />

          {/* ④ 明细区 */}
          <section className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-medium">{c.runsTitle}</h2>
                <p className="text-muted-foreground text-xs">
                  {c.runsPanelNote}
                </p>
              </div>
              <Select value={status} onValueChange={resetPaging(setStatus)}>
                <SelectTrigger className="h-8 w-[150px] text-xs">
                  <SelectValue placeholder={c.allStatuses} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_STATUS_VALUE} className="text-xs">
                    {c.allStatuses}
                  </SelectItem>
                  {STATUS_OPTIONS.map((option) => (
                    <SelectItem key={option} value={option} className="text-xs">
                      {option}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* C6b-2：失败/超阈值撤出卡位，移至任务列表面板顶部两行小字 */}
            <div
              data-testid="runs-panel-headline"
              className="text-muted-foreground flex flex-wrap gap-3 text-[11px]"
            >
              <span>
                {/* F2（2026-08-27 评审修复）：头部为窗口口径（stats 随 days），
                    下方表格为全时段分页——标注窗口避免两口径混读 */}
                {c.runsPanelFailed.replace(
                  "{n}",
                  String(statsQuery.data?.failed_runs ?? 0),
                ) + c.runsPanelWindowSuffix.replace("{days}", String(days))}
              </span>
              <span>
                {c.runsPanelThreshold.replace(
                  "{n}",
                  String(statsQuery.data?.over_threshold_runs ?? 0),
                ) + c.runsPanelWindowSuffix.replace("{days}", String(days))}
              </span>
            </div>

            <ConsoleRunsTable
              page={runsQuery.data}
              isLoading={runsQuery.isLoading}
              isAdmin={isAdmin}
              sortBy={sortBy}
              sortDir={sortDir}
              onSort={handleSort}
              currency={currency}
              error={runsQuery.error}
              onRetry={() => runsQuery.refetch()}
            />

            <div className="flex items-center justify-between gap-2">
              {/* C6：空列表不再显示「第 1–0 条」 */}
              <span className="text-muted-foreground text-xs tabular-nums">
                {runsCount === 0
                  ? c.emptyRunsInWindow
                  : c.showingRange
                      .replace("{from}", String(offset + 1))
                      .replace("{to}", String(offset + runsCount))}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs"
                  disabled={offset === 0}
                  onClick={() =>
                    setOffset((prev) => Math.max(0, prev - PAGE_SIZE))
                  }
                >
                  {c.previous}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs"
                  disabled={!runsQuery.data?.has_more}
                  onClick={() => setOffset((prev) => prev + PAGE_SIZE)}
                >
                  {c.next}
                </Button>
              </div>
            </div>
          </section>

          <ConsoleLlmUsagePanel days={days} userId={userId} />

          <ConsoleExternalCallsPanel
            days={days}
            userId={userId}
            currency={currency}
          />
        </>
      )}
    </div>
  );
}
