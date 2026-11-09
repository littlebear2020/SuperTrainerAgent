"use client";

import { Check, ChevronDown, Loader2, Pause, Play, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { useI18n } from "@/core/i18n/hooks";
import { getVoicePreviewURL } from "@/core/voice-twin/api";
import {
  usePresetVoices,
  useVoicePreview,
  useVoiceTwins,
} from "@/core/voice-twin/hooks";
import type { PresetVoice } from "@/core/voice-twin/types";
import { VOICE_PROVIDER_LABELS } from "@/core/voice-twin/types";
import type { VoiceFilterState } from "@/core/voice-twin/voice-filters";
import {
  collectLocales,
  EMPTY_VOICE_FILTERS,
  filterCatalogVoices,
  groupCatalogVoices,
  loadVoiceFilters,
  saveVoiceFilters,
  toggleInList,
} from "@/core/voice-twin/voice-filters";
import { cn } from "@/lib/utils";

interface VoiceSelectorProps {
  value: string;
  onChange: (voiceId: string) => void;
  className?: string;
  /** Optional text to preview (e.g. the script being dubbed). Defaults to a server sample. */
  previewText?: string;
}

/** 我的克隆组的扁平选项（克隆音色无目录标签，保持简单行）。 */
interface ClonedOption {
  id: string;
  name: string;
}

const PROVIDER_BADGE_STYLE: Record<string, string> = {
  "edge-tts": "bg-emerald-500/10 text-emerald-500",
  qihu: "bg-indigo-500/10 text-indigo-500",
  doubao: "bg-sky-500/10 text-sky-500",
};

// ---------------------------------------------------------------------------
// Module-level sub-components (fix M3, 2026-08-10)
//
// These were previously defined inside VoiceSelector's function body, which
// caused React to treat them as new component types on every render → subtree
// remount → search input / focus loss during filtering.
// ---------------------------------------------------------------------------

/** Shared preview-controller props for components that contain a PreviewButton. */
interface PreviewProps {
  playingId: string | null;
  loadingId: string | null;
  onToggle: (id: string, url: string) => void;
  previewUrlFn: (id: string) => string;
}

function PreviewButton({ id, ...preview }: { id: string } & PreviewProps) {
  const isPlaying = preview.playingId === id;
  const isLoading = preview.loadingId === id;
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={isPlaying ? "暂停试听" : "试听音色"}
      className="h-7 w-7 shrink-0"
      disabled={isLoading}
      onClick={(e) => {
        e.stopPropagation();
        preview.onToggle(id, preview.previewUrlFn(id));
      }}
    >
      {isLoading ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : isPlaying ? (
        <Pause className="h-3.5 w-3.5" />
      ) : (
        <Play className="h-3.5 w-3.5" />
      )}
    </Button>
  );
}

function ClonedRow({
  opt,
  selected,
  onSelect,
  myVoicesLabel,
  ...preview
}: {
  opt: ClonedOption;
  selected: boolean;
  onSelect: () => void;
  myVoicesLabel: string;
} & PreviewProps) {
  return (
    <div
      role="option"
      aria-selected={selected}
      className="hover:bg-accent flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5"
      onClick={onSelect}
    >
      <PreviewButton id={opt.id} {...preview} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{opt.name}</div>
        <div className="text-muted-foreground truncate text-xs">
          {myVoicesLabel}
        </div>
      </div>
      {selected && <Check className="text-primary h-4 w-4 shrink-0" />}
    </div>
  );
}

function CatalogRow({
  voice,
  selected,
  onSelect,
  ...preview
}: {
  voice: PresetVoice;
  selected: boolean;
  onSelect: () => void;
} & PreviewProps) {
  const tags = [...voice.scenario, ...voice.tone].slice(0, 3);
  return (
    <div
      role="option"
      aria-selected={selected}
      className="hover:bg-accent flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5"
      onClick={onSelect}
    >
      <PreviewButton id={voice.id} {...preview} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-sm font-medium">{voice.name}</span>
          <span className="text-muted-foreground shrink-0 text-xs">
            {voice.gender === "male" ? "♂" : "♀"} {voice.locale}
          </span>
          <span
            className={cn(
              "shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium",
              PROVIDER_BADGE_STYLE[voice.provider] ??
                "bg-slate-500/10 text-slate-500",
            )}
          >
            {VOICE_PROVIDER_LABELS[voice.provider]}
          </span>
        </div>
        <div className="text-muted-foreground truncate text-xs">
          {voice.description}
        </div>
        {tags.length > 0 && (
          <div className="mt-0.5 flex flex-wrap gap-1">
            {tags.map((tag) => (
              <span
                key={tag}
                className="text-muted-foreground rounded bg-slate-500/10 px-1 text-[10px]"
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
      {selected && <Check className="text-primary h-4 w-4 shrink-0" />}
    </div>
  );
}

function Chip({
  label,
  active,
  onToggle,
}: {
  label: string;
  active: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={cn(
        "rounded-full border px-2 py-0.5 text-[11px] transition-colors",
        active
          ? "border-indigo-400/60 bg-indigo-500/15 text-indigo-500"
          : "border-transparent bg-slate-500/10 text-slate-400 hover:bg-slate-500/20",
      )}
    >
      {label}
    </button>
  );
}

function FilterBar({
  filters,
  onFiltersChange,
  localeOptions,
  scenarioOptions,
  toneOptions,
}: {
  filters: VoiceFilterState;
  onFiltersChange: (next: VoiceFilterState) => void;
  localeOptions: string[];
  scenarioOptions: string[];
  toneOptions: string[];
}) {
  const hasActiveFilters =
    filters.locales.length > 0 ||
    filters.genders.length > 0 ||
    filters.scenarios.length > 0 ||
    filters.tones.length > 0;

  return (
    <div className="mb-2 space-y-1.5 border-b pb-2">
      <div className="flex items-center gap-1.5">
        <select
          aria-label="按语言筛选"
          value={filters.locales[0] ?? ""}
          onChange={(e) =>
            onFiltersChange({
              ...filters,
              locales: e.target.value ? [e.target.value] : [],
            })
          }
          className="bg-background rounded border px-1.5 py-1 text-xs outline-none"
        >
          <option value="">全部语言</option>
          {localeOptions.map((loc) => (
            <option key={loc} value={loc}>
              {loc}
            </option>
          ))}
        </select>
        <Chip
          label="男声"
          active={filters.genders.includes("male")}
          onToggle={() =>
            onFiltersChange({
              ...filters,
              genders: toggleInList(filters.genders, "male"),
            })
          }
        />
        <Chip
          label="女声"
          active={filters.genders.includes("female")}
          onToggle={() =>
            onFiltersChange({
              ...filters,
              genders: toggleInList(filters.genders, "female"),
            })
          }
        />
        {hasActiveFilters && (
          <button
            type="button"
            onClick={() => onFiltersChange(EMPTY_VOICE_FILTERS)}
            className="text-muted-foreground ml-auto text-[11px] hover:underline"
          >
            清除筛选
          </button>
        )}
      </div>
      {scenarioOptions.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {scenarioOptions.map((s) => (
            <Chip
              key={s}
              label={s}
              active={filters.scenarios.includes(s)}
              onToggle={() =>
                onFiltersChange({
                  ...filters,
                  scenarios: toggleInList(filters.scenarios, s),
                })
              }
            />
          ))}
        </div>
      )}
      {toneOptions.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {toneOptions.map((toneItem) => (
            <Chip
              key={toneItem}
              label={toneItem}
              active={filters.tones.includes(toneItem)}
              onToggle={() =>
                onFiltersChange({
                  ...filters,
                  tones: toggleInList(filters.tones, toneItem),
                })
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

export function VoiceSelector({
  value,
  onChange,
  className,
  previewText,
}: VoiceSelectorProps) {
  const { t } = useI18n();
  const vt = t.voiceTwin;

  const voicesQuery = useVoiceTwins();
  const presetQuery = usePresetVoices();
  const { playingId, loadingId, toggle } = useVoicePreview();

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  // 筛选状态：挂载时从 localStorage 恢复（容错在 loadVoiceFilters 内）。
  const [filters, setFilters] = useState<VoiceFilterState>(EMPTY_VOICE_FILTERS);
  useEffect(() => {
    setFilters(loadVoiceFilters());
  }, []);
  const updateFilters = (next: VoiceFilterState) => {
    setFilters(next);
    saveVoiceFilters(next);
  };

  const clonedVoices = useMemo<ClonedOption[]>(
    () =>
      (voicesQuery.data ?? [])
        .filter((v) => v.status === "ready")
        .map((v) => ({ id: v.id, name: v.voice_name })),
    [voicesQuery.data],
  );

  const catalogVoices = useMemo(
    () => presetQuery.data ?? [],
    [presetQuery.data],
  );
  const localeOptions = useMemo(
    () => collectLocales(catalogVoices),
    [catalogVoices],
  );

  // 搜索 + 筛选只作用于目录音色；我的克隆组始终全量（不参与筛选）。
  const visibleCatalog = useMemo(() => {
    const q = query.trim().toLowerCase();
    const searched = q
      ? catalogVoices.filter(
          (v) =>
            v.name.toLowerCase().includes(q) ||
            v.description.toLowerCase().includes(q),
        )
      : catalogVoices;
    return filterCatalogVoices(searched, filters);
  }, [catalogVoices, query, filters]);

  const filteredCloned = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return clonedVoices;
    return clonedVoices.filter((v) => v.name.toLowerCase().includes(q));
  }, [clonedVoices, query]);

  const groups = useMemo(
    () => groupCatalogVoices(visibleCatalog),
    [visibleCatalog],
  );

  const scenarioOptions = useMemo(
    () => [...new Set(catalogVoices.flatMap((v) => v.scenario))],
    [catalogVoices],
  );
  const toneOptions = useMemo(
    () => [...new Set(catalogVoices.flatMap((v) => v.tone))],
    [catalogVoices],
  );

  const selectedName = useMemo(() => {
    const cloned = clonedVoices.find((v) => v.id === value);
    if (cloned) return cloned.name;
    return catalogVoices.find((v) => v.id === value)?.name ?? null;
  }, [clonedVoices, catalogVoices, value]);

  // Layout-only classes from callers (mb-3, w-full, ...). Visual styling is
  // fixed below to match the project's shadcn SelectTrigger token system so
  // every VoiceSelector looks identical regardless of call site.
  const layoutClass = className ?? "w-full";
  const triggerClass = cn(
    "flex w-full items-center justify-between rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none transition-[color,box-shadow] dark:bg-input/30",
    // `.ws-glass-input` provides a visible focus glow (the project's --ring
    // token is transparent, so ring-ring/50 alone would be invisible).
    "ws-glass-input",
    layoutClass,
  );

  const previewUrl = (id: string) => getVoicePreviewURL(id, previewText);

  // Preview controller props shared by all rows containing a PreviewButton.
  const previewProps: PreviewProps = {
    playingId,
    loadingId,
    onToggle: toggle,
    previewUrlFn: previewUrl,
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-controls="voice-selector-listbox"
          className={triggerClass}
        >
          <span
            className={cn("truncate", !selectedName && "text-muted-foreground")}
          >
            {selectedName ?? vt.selectVoice ?? "选择音色"}
          </span>
          <ChevronDown className="h-4 w-4 shrink-0 opacity-60" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="flex max-h-[var(--radix-popover-content-available-height)] w-96 flex-col p-2"
        align="start"
      >
        <div className="relative mb-2 shrink-0">
          <Search className="text-muted-foreground absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={vt.searchVoice ?? "搜索音色"}
            className="ws-glass-input bg-background w-full rounded-md border py-1.5 pr-2 pl-7 text-sm outline-none"
          />
        </div>

        {catalogVoices.length > 0 && (
          <div className="shrink-0">
            <FilterBar
              filters={filters}
              onFiltersChange={updateFilters}
              localeOptions={localeOptions}
              scenarioOptions={scenarioOptions}
              toneOptions={toneOptions}
            />
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          <div id="voice-selector-listbox" className="space-y-2">
            {/* 我的克隆组（不参与筛选） */}
            <div className="space-y-0.5">
              <div className="text-muted-foreground px-2 py-1 text-xs font-semibold tracking-wide uppercase">
                {vt.myVoices ?? "我的音色"}
              </div>
              {voicesQuery.isError ? (
                <QueryErrorHint
                  message="克隆音色加载失败"
                  onRetry={() => void voicesQuery.refetch()}
                />
              ) : filteredCloned.length === 0 ? (
                <p className="text-muted-foreground px-2 py-1.5 text-xs leading-relaxed">
                  {vt.noVoicesHint}
                </p>
              ) : (
                filteredCloned.map((opt) => (
                  <ClonedRow
                    key={opt.id}
                    opt={opt}
                    selected={opt.id === value}
                    onSelect={() => {
                      onChange(opt.id);
                      setOpen(false);
                    }}
                    myVoicesLabel={vt.myVoices ?? "我的音色"}
                    {...previewProps}
                  />
                ))
              )}
            </div>

            {/* 目录音色组（按 provider 分组） */}
            {presetQuery.isLoading ? (
              <div className="space-y-1.5 px-2 py-1">
                {[0, 1, 2].map((i) => (
                  <div
                    key={i}
                    className="bg-muted h-9 animate-pulse rounded-md"
                  />
                ))}
              </div>
            ) : presetQuery.isError ? (
              <QueryErrorHint
                message="音色加载失败"
                onRetry={() => void presetQuery.refetch()}
              />
            ) : (
              groups.map((g) => (
                <div key={g.provider} className="space-y-0.5">
                  <div className="text-muted-foreground px-2 py-1 text-xs font-semibold tracking-wide uppercase">
                    {VOICE_PROVIDER_LABELS[g.provider]}
                  </div>
                  {g.voices.map((v) => (
                    <CatalogRow
                      key={v.id}
                      voice={v}
                      selected={v.id === value}
                      onSelect={() => {
                        onChange(v.id);
                        setOpen(false);
                      }}
                      {...previewProps}
                    />
                  ))}
                </div>
              ))
            )}

            {!presetQuery.isLoading &&
              !presetQuery.isError &&
              groups.length === 0 &&
              filteredCloned.length === 0 && (
                <div className="text-muted-foreground px-2 py-4 text-center text-xs">
                  {vt.noVoiceFound ?? "未找到匹配音色"}
                </div>
              )}
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function QueryErrorHint({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex items-center gap-2 px-2 py-1.5">
      <span className="text-xs text-red-400">{message}</span>
      <button
        type="button"
        onClick={onRetry}
        className="text-xs text-indigo-500 hover:underline"
      >
        重试
      </button>
    </div>
  );
}
