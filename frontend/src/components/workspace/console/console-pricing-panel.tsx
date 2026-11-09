"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  buildConfirmOptions,
  useConfirm,
} from "@/components/workspace/dialog-provider";
import { ConsoleApiError } from "@/core/console/api";
import {
  useModelPricing,
  useUpdateModelPricing,
  useMediaPricing,
  useUpdateMediaPricing,
  usePatchMediaPricing,
  useDeleteMediaPricing,
} from "@/core/console/hooks";
import { providerLabel } from "@/core/console/provider-labels";
import type {
  MediaPricingEntry,
  ModelPricingEntry,
} from "@/core/console/types";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";

interface Props {
  /** Whether the panel may render at all. Callers must gate on admin. */
  enabled: boolean;
}

interface RowState {
  currency: string;
  input_per_million: string;
  output_per_million: string;
  /** Raw string for the cache-hit field. "" = null (billed at miss price). */
  cache_hit: string;
  source: "db" | "yaml";
}

/** True when the row has no meaningful price (both input and output are empty or zero). */
function isUnpriced(row: RowState): boolean {
  const inp = parseFloat(row.input_per_million) || 0;
  const out = parseFloat(row.output_per_million) || 0;
  return inp <= 0 && out <= 0;
}

/**
 * Admin-only model pricing editor.
 *
 * Returns `null` for non-admins — same gating convention as
 * `ConsoleUserPicker`. The panel shows the **effective** prices (yaml
 * fallback + DB overlay) matching what the console uses for spend.
 *
 * PUT replaces the entire DB-layer table (full-table semantics, not a
 * patch). The payload therefore includes every model, even unedited ones,
 * so a save never accidentally clears a price.
 */
export function ConsolePricingPanel({ enabled }: Props) {
  const { t } = useI18n();
  const c = t.console;
  const { models } = useModels({ enabled });
  const pricingQuery = useModelPricing(enabled);
  const updateMutation = useUpdateModelPricing();
  const mediaPricingQuery = useMediaPricing(enabled);
  const mediaUpdateMutation = useUpdateMediaPricing();
  const mediaPatchMutation = usePatchMediaPricing();
  const mediaDeleteMutation = useDeleteMediaPricing();
  // 弹层统一（OBS-3）：别名 confirmDialog——本文件局部函数（buildPayload/doPatch）
  // 有名为 confirm 的布尔参数，避免遮蔽。
  const confirmDialog = useConfirm();

  // --- Media pricing state ---
  const mediaEntries = mediaPricingQuery.data?.pricing ?? [];
  const [mediaEdits, setMediaEdits] = useState<Record<string, string>>({});
  // C2: model_id 行内编辑（空字符串 = provider 级默认行）。
  const [mediaModelEdits, setMediaModelEdits] = useState<
    Record<string, string>
  >({});

  useEffect(() => {
    setMediaEdits({});
    setMediaModelEdits({});
  }, [mediaPricingQuery.data]);

  // Row key: provider-level rows keep the legacy `provider:media_type` testid
  // shape; model-level rows extend it to `provider:model_id:media_type`.
  const mediaKey = (e: {
    provider: string;
    model_id: string | null;
    media_type: string;
  }) =>
    e.model_id
      ? `${e.provider}:${e.model_id}:${e.media_type}`
      : `${e.provider}:${e.media_type}`;

  const handleMediaSave = async () => {
    // Build payload with the same principle as LLM pricing (A4):
    // - Only include rows that should exist in DB.
    // - Edited rows with a valid price >0: include with new price.
    // - Edited rows cleared to empty/0: OMIT (full-table replacement = delete
    //   the DB row -> falls back to default seed or becomes unpriced).
    // - Unedited DB rows: include to preserve them.
    // - Unedited default rows: OMIT (don't固化 built-in defaults into DB).
    const buildPayload = (confirm: boolean) => ({
      confirm,
      pricing: mediaEntries
        .filter((e) => {
          const key = mediaKey(e);
          const edited = mediaEdits[key];
          const modelEdited = mediaModelEdits[key];
          // F3（2026-08-27 评审修复）：只改 model_id（未改价）同样视为已编辑——
          // 修前这类行被当"未编辑"排除（种子行 model_id 修改被整表保存静默丢弃，
          // toast 照常成功）。价格取编辑值、否则原行价（种子行=以种子价为底建
          // 新键）；价格清空/非法仍按"删除意图"排除。
          if (edited !== undefined || modelEdited !== undefined) {
            const val = parseFloat(edited ?? String(e.unit_price));
            return !isNaN(val) && val > 0;
          }
          // Unedited: include only if it's already in DB
          return e.source === "db";
        })
        .map((e) => {
          const key = mediaKey(e);
          const edited = mediaEdits[key];
          const modelEdited = mediaModelEdits[key];
          return {
            provider: e.provider,
            model_id: (modelEdited ?? e.model_id ?? "").trim() || null,
            media_type: e.media_type,
            unit: e.unit,
            unit_price:
              edited !== undefined ? parseFloat(edited) || 0 : e.unit_price,
            currency: e.currency,
          };
        }),
    });
    mediaUpdateMutation.mutate(buildPayload(false), {
      onSuccess: () => {
        toast.success(c.pricingSaveSuccess);
      },
      onError: (error) => {
        const msg =
          error instanceof ConsoleApiError ? error.message : c.pricingSaveError;
        // C7（A12）：>5× 幅度告警 422 带二次确认通道--运营确认后带
        // confirm=true 重发一次，不必回表找开关。
        if (error instanceof ConsoleApiError && /confirm/iu.test(msg)) {
          void (async () => {
            const confirmed = await confirmDialog(
              buildConfirmOptions(`${msg}\n\n${c.pricingMagnitudeConfirm}`),
            );
            if (confirmed) {
              mediaUpdateMutation.mutate(buildPayload(true), {
                onSuccess: () => toast.success(c.pricingSaveSuccess),
                onError: (err2) =>
                  toast.error(
                    err2 instanceof ConsoleApiError
                      ? err2.message
                      : c.pricingSaveError,
                  ),
              });
              return;
            }
            toast.error(msg);
          })();
          return;
        }
        toast.error(msg);
      },
    });
  };

  // C7（A20 用户拍板 2026-08-27）：行级保存/删除——改一行不必整表重发。
  const handleMediaRowSave = async (e: MediaPricingEntry) => {
    const key = mediaKey(e);
    const edited = mediaEdits[key];
    const modelEdited = mediaModelEdits[key];
    const nextModelId = (modelEdited ?? e.model_id ?? "").trim() || null;
    const priceVal = parseFloat(edited ?? String(e.unit_price));
    if (isNaN(priceVal) || priceVal <= 0) {
      toast.error(c.mediaPricingInvalidPrice);
      return;
    }
    const entry = {
      provider: e.provider,
      model_id: nextModelId,
      media_type: e.media_type,
      unit: e.unit,
      unit_price: priceVal,
      currency: e.currency,
    };
    const doPatch = (confirm: boolean) =>
      mediaPatchMutation.mutate(
        { entry, confirm },
        {
          onSuccess: () => toast.success(c.pricingSaveSuccess),
          onError: (error) => {
            const msg =
              error instanceof ConsoleApiError
                ? error.message
                : c.pricingSaveError;
            // C7（A12）：>5× 幅度告警 422 → 确认后带 confirm=true 重发（与整表保存同通道）。
            if (
              !confirm &&
              error instanceof ConsoleApiError &&
              /confirm/iu.test(msg)
            ) {
              void (async () => {
                if (
                  await confirmDialog(
                    buildConfirmOptions(
                      `${msg}\n\n${c.pricingMagnitudeConfirm}`,
                    ),
                  )
                ) {
                  doPatch(true);
                  return;
                }
                toast.error(msg);
              })();
              return;
            }
            toast.error(msg);
          },
        },
      );
    // model_id 变更 = 以新键 upsert（旧键行保留，可用「删除」移除）——先告知再执行。
    if (
      nextModelId !== (e.model_id ?? null) &&
      !(await confirmDialog(buildConfirmOptions(c.mediaPricingModelChangeWarn)))
    ) {
      return;
    }
    doPatch(false);
  };

  const handleMediaRowDelete = async (e: MediaPricingEntry) => {
    const label = `${providerLabel(e.provider, c)}${e.model_id ? ` / ${e.model_id}` : ""} / ${e.media_type}`;
    const confirmed = await confirmDialog(
      buildConfirmOptions(c.mediaPricingDeleteConfirm(label), {
        variant: "destructive",
      }),
    );
    if (!confirmed) return;
    mediaDeleteMutation.mutate(
      { provider: e.provider, mediaType: e.media_type, modelId: e.model_id },
      {
        onSuccess: () => toast.success(c.pricingSaveSuccess),
        onError: (error) =>
          toast.error(
            error instanceof ConsoleApiError
              ? error.message
              : c.pricingSaveError,
          ),
      },
    );
  };

  // Build the server-side row set: config models (skeleton) ∪ pricing GET
  // keys (prevents a deleted-config model with a lingering DB row from
  // being invisible to the operator).
  const serverRows = useMemo((): Record<string, RowState> | null => {
    if (!pricingQuery.data) return null;
    const pricingData = pricingQuery.data.pricing;
    const allNames = new Set<string>([
      ...models.map((m) => m.name),
      ...Object.keys(pricingData),
    ]);

    const result: Record<string, RowState> = {};
    for (const name of allNames) {
      const entry: ModelPricingEntry | undefined = pricingData[name];
      result[name] = {
        currency: entry?.currency ?? "CNY",
        input_per_million: entry ? String(entry.input_per_million) : "0",
        output_per_million: entry ? String(entry.output_per_million) : "0",
        // null → empty string (placeholder "billed at miss price")
        cache_hit:
          entry?.input_cache_hit_per_million != null
            ? String(entry.input_cache_hit_per_million)
            : "",
        source: entry?.source ?? "yaml",
      };
    }
    return result;
  }, [pricingQuery.data, models]);

  // Local edits overlaid on server data.
  const [overrides, setOverrides] = useState<Record<string, Partial<RowState>>>(
    {},
  );

  // Reset overrides when server data changes (initial load, refetch, or
  // post-mutation invalidation).
  useEffect(() => {
    setOverrides({});
  }, [serverRows]);

  // Merged view: server rows with local overrides applied.
  const editing = useMemo((): Record<string, RowState> | null => {
    if (!serverRows) return null;
    const merged: Record<string, RowState> = {};
    for (const [name, row] of Object.entries(serverRows)) {
      merged[name] = { ...row, ...overrides[name] };
    }
    return merged;
  }, [serverRows, overrides]);

  if (!enabled) return null;

  // 503: memory backend has no SQL database.
  if (
    pricingQuery.error instanceof ConsoleApiError &&
    pricingQuery.error.status === 503
  ) {
    return (
      <div
        data-testid="pricing-panel"
        className="text-muted-foreground rounded-xl border p-4 text-xs"
      >
        {c.pricingNoDatabase}
      </div>
    );
  }

  if (pricingQuery.isLoading || !editing) {
    return (
      <div
        data-testid="pricing-panel"
        className="text-muted-foreground rounded-xl border p-4 text-xs"
      >
        {c.loading}
      </div>
    );
  }

  const names = Object.keys(editing).sort();

  // Determine the global currency from the first row (after overrides).
  const globalCurrency = editing[names[0]!]?.currency ?? "CNY";

  const updateField = (name: string, field: keyof RowState, value: string) => {
    setOverrides((prev) => ({
      ...prev,
      [name]: { ...prev[name], [field]: value },
    }));
  };

  const updateCurrency = (value: string) => {
    // Currency is global — apply to all rows.
    const next: Record<string, Partial<RowState>> = {};
    for (const name of names) {
      next[name] = { ...overrides[name], currency: value };
    }
    setOverrides(next);
  };

  const handleSave = async () => {
    // Build payload: only include models with at least one non-zero price.
    // Empty/unpriced rows are omitted entirely — they won't be written to DB,
    // and if a DB row existed it gets removed via full-table replacement.
    const payloadEntries: [
      string,
      {
        currency: string;
        input_per_million: number;
        output_per_million: number;
        input_cache_hit_per_million: number | null;
      },
    ][] = [];
    const clearedModels: string[] = [];

    for (const name of names) {
      const row = editing[name]!;
      const inputRaw = row.input_per_million.trim();
      const outputRaw = row.output_per_million.trim();
      const inputVal = parseFloat(inputRaw);
      const outputVal = parseFloat(outputRaw);

      // Skip if both fields are empty or zero (unpriced model).
      const hasInput = inputRaw !== "" && !isNaN(inputVal) && inputVal > 0;
      const hasOutput = outputRaw !== "" && !isNaN(outputVal) && outputVal > 0;
      if (!hasInput && !hasOutput) {
        // Track if this model HAD a DB price that will be cleared.
        if (serverRows?.[name]?.source === "db") {
          clearedModels.push(name);
        }
        continue;
      }

      const cacheHitRaw = row.cache_hit.trim();
      payloadEntries.push([
        name,
        {
          currency: globalCurrency,
          input_per_million: parseFloat(inputRaw) || 0,
          output_per_million: parseFloat(outputRaw) || 0,
          // Empty string → null (billed at miss price).
          // Explicit "0" → 0 (hits are free). Never collapse null into 0.
          input_cache_hit_per_million:
            cacheHitRaw === "" ? null : parseFloat(cacheHitRaw) || 0,
        },
      ]);
    }

    const doSave = () => {
      updateMutation.mutate(
        { pricing: Object.fromEntries(payloadEntries) },
        {
          onSuccess: () => {
            toast.success(c.pricingSaveSuccess);
          },
          onError: (error) => {
            const msg =
              error instanceof ConsoleApiError
                ? error.message
                : c.pricingSaveError;
            toast.error(msg);
          },
        },
      );
    };

    // If clearing previously-priced models, ask for confirmation.
    if (clearedModels.length > 0) {
      const confirmed = await confirmDialog(
        buildConfirmOptions(
          c.pricingDeleteConfirm(
            clearedModels.length,
            clearedModels.join(", "),
          ),
          { variant: "destructive" },
        ),
      );
      if (!confirmed) return;
    }

    doSave();
  };

  return (
    <section data-testid="pricing-panel" className="flex flex-col gap-3">
      <div>
        <h2 className="text-sm font-medium">{c.pricingTitle}</h2>
        <p className="text-muted-foreground text-xs">{c.pricingSubtitle}</p>
      </div>

      <div className="bg-card/60 overflow-hidden rounded-xl border">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-muted-foreground border-b">
              <tr>
                <th className="px-3 py-2.5 text-left font-medium">Model</th>
                <th className="px-3 py-2.5 text-left font-medium">
                  {c.pricingCurrency}
                </th>
                <th className="px-3 py-2.5 text-right font-medium">
                  {c.pricingInputPrice}
                </th>
                <th className="px-3 py-2.5 text-right font-medium">
                  {c.pricingOutputPrice}
                </th>
                <th className="px-3 py-2.5 text-right font-medium">
                  {c.pricingCacheHitPrice}
                </th>
                <th className="px-3 py-2.5 text-center font-medium">Source</th>
              </tr>
            </thead>
            <tbody>
              {names.map((name) => {
                const row = editing[name]!;
                return (
                  <tr
                    key={name}
                    className="border-b last:border-0"
                    data-testid={`pricing-row-${name}`}
                  >
                    <td className="px-3 py-2 font-mono">{name}</td>
                    {/* C7：币种输入不再藏在行内（改任一行静默覆盖全表的误导
                        UX）--行内只读展示，全表唯一的币种控制在保存按钮旁。 */}
                    <td className="text-muted-foreground px-3 py-2 font-mono text-xs">
                      {row.currency}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      <Input
                        type="number"
                        min={0}
                        step="0.01"
                        className="h-7 w-24 text-xs tabular-nums"
                        value={row.input_per_million}
                        onChange={(e) =>
                          updateField(name, "input_per_million", e.target.value)
                        }
                      />
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      <Input
                        type="number"
                        min={0}
                        step="0.01"
                        className="h-7 w-24 text-xs tabular-nums"
                        value={row.output_per_million}
                        onChange={(e) =>
                          updateField(
                            name,
                            "output_per_million",
                            e.target.value,
                          )
                        }
                      />
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      <Input
                        type="number"
                        min={0}
                        step="0.01"
                        className="h-7 w-24 text-xs tabular-nums"
                        placeholder={c.pricingCacheHitPlaceholder}
                        value={row.cache_hit}
                        onChange={(e) =>
                          updateField(name, "cache_hit", e.target.value)
                        }
                      />
                    </td>
                    <td className="px-3 py-2 text-center">
                      {row.source === "yaml" && isUnpriced(row) ? (
                        <span className="inline-block rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
                          {c.pricingSourceUnpriced}
                        </span>
                      ) : (
                        <span
                          className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${
                            row.source === "db"
                              ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                              : "bg-muted text-muted-foreground"
                          }`}
                        >
                          {row.source === "db"
                            ? c.pricingSourceDb
                            : c.pricingSourceYaml}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex items-center justify-between gap-2">
        <p className="text-muted-foreground text-xs">
          {c.pricingReplaceWarning}
        </p>
        <div className="flex items-center gap-2">
          {/* C7：全表唯一的币种控制（显式作用域，不再行内覆盖全部） */}
          <label className="text-muted-foreground flex items-center gap-1 text-xs">
            {c.pricingCurrency}
            <Input
              className="h-7 w-20 text-xs"
              value={globalCurrency}
              onChange={(e) => updateCurrency(e.target.value)}
              data-testid="pricing-currency-global"
            />
          </label>
          <Button
            size="sm"
            className="h-7 text-xs"
            disabled={updateMutation.isPending}
            onClick={handleSave}
            data-testid="pricing-save"
          >
            {updateMutation.isPending ? c.pricingSaving : c.pricingSave}
          </Button>
        </div>
      </div>

      {/* C2/D1: Media pricing section */}
      {mediaEntries.length > 0 && (
        <div className="flex flex-col gap-2">
          <div>
            <h3 className="text-sm font-medium">{c.mediaPricingTitle}</h3>
            <p className="text-muted-foreground text-xs">
              {c.mediaPricingSubtitle}
            </p>
          </div>
          <div className="bg-card/60 overflow-hidden rounded-xl border">
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-muted-foreground border-b">
                  <tr>
                    <th className="px-3 py-2.5 text-left font-medium">
                      {c.mediaPricingProvider}
                    </th>
                    <th className="px-3 py-2.5 text-left font-medium">
                      {c.mediaPricingModelId}
                    </th>
                    <th className="px-3 py-2.5 text-left font-medium">
                      {c.mediaPricingType}
                    </th>
                    <th className="px-3 py-2.5 text-left font-medium">
                      {c.mediaPricingUnit}
                    </th>
                    <th className="px-3 py-2.5 text-right font-medium">
                      {c.mediaPricingPrice}
                    </th>
                    <th className="px-3 py-2.5 text-center font-medium">
                      {c.mediaPricingSourceLabel}
                    </th>
                    <th className="px-3 py-2.5 text-center font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {mediaEntries.map((e) => {
                    const key = mediaKey(e);
                    const edited = mediaEdits[key];
                    const modelEdited = mediaModelEdits[key];
                    return (
                      <tr
                        key={key}
                        className="border-b last:border-0"
                        data-testid={`media-pricing-row-${key}`}
                      >
                        <td className="px-3 py-2">
                          {providerLabel(e.provider, c)}
                        </td>
                        <td className="px-3 py-2">
                          <Input
                            className="h-7 w-44 font-mono text-xs"
                            placeholder={c.mediaPricingModelPlaceholder}
                            value={modelEdited ?? e.model_id ?? ""}
                            onChange={(ev) =>
                              setMediaModelEdits((prev) => ({
                                ...prev,
                                [key]: ev.target.value,
                              }))
                            }
                            data-testid={`media-pricing-model-${key}`}
                          />
                        </td>
                        <td className="px-3 py-2">
                          {e.media_type === "image"
                            ? c.externalCallsTabImage
                            : e.media_type === "video"
                              ? c.externalCallsTabVideo
                              : e.media_type === "tts"
                                ? c.externalCallsTabTts
                                : e.media_type}
                        </td>
                        <td className="text-muted-foreground px-3 py-2 font-mono">
                          {e.unit}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          <Input
                            type="number"
                            min={0}
                            step="0.001"
                            className="h-7 w-24 text-xs tabular-nums"
                            value={edited ?? String(e.unit_price)}
                            onChange={(ev) =>
                              setMediaEdits((prev) => ({
                                ...prev,
                                [key]: ev.target.value,
                              }))
                            }
                          />
                        </td>
                        <td className="px-3 py-2 text-center">
                          <span
                            className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${
                              e.source === "db"
                                ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                                : "bg-muted text-muted-foreground"
                            }`}
                          >
                            {e.source === "db"
                              ? c.pricingSourceDb
                              : c.mediaPricingSourceDefault}
                          </span>
                        </td>
                        {/* C7（A20）：行级操作——有编辑时出现「保存此行」；
                            DB 行可出现「删除」。种子行不可删（不在 DB）。 */}
                        <td className="px-3 py-2 text-center">
                          <div className="flex items-center justify-center gap-1">
                            {(edited !== undefined ||
                              modelEdited !== undefined) && (
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-6 px-2 text-[11px]"
                                disabled={mediaPatchMutation.isPending}
                                onClick={() => handleMediaRowSave(e)}
                                data-testid={`media-pricing-row-save-${key}`}
                              >
                                {c.mediaPricingRowSave}
                              </Button>
                            )}
                            {e.source === "db" && (
                              <Button
                                size="sm"
                                variant="ghost"
                                className="h-6 px-2 text-[11px] text-red-600 dark:text-red-400"
                                disabled={mediaDeleteMutation.isPending}
                                onClick={() => handleMediaRowDelete(e)}
                                data-testid={`media-pricing-row-delete-${key}`}
                              >
                                {c.mediaPricingRowDelete}
                              </Button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
          <div className="flex items-center justify-end gap-2">
            <Button
              size="sm"
              className="h-7 text-xs"
              disabled={mediaUpdateMutation.isPending}
              onClick={handleMediaSave}
              data-testid="media-pricing-save"
            >
              {mediaUpdateMutation.isPending ? c.pricingSaving : c.pricingSave}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
