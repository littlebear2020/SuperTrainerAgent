"use client";

import { Check, ChevronDown, Music, Pause, Play, Search } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  filterBgmTracks,
  formatBgmDuration,
} from "@/core/video-gen/bgm-filter";
import type { BGMTrack } from "@/core/video-gen/types";
import { cn } from "@/lib/utils";

/**
 * BGM Popover 选择器（2026-07-31）：选前逐首试听，替代原来挤在
 * grid-cols-3 格子里的 Select（选中后 4 控件溢出重叠，根因 = 窄列 + 缺 min-w-0）。
 * 模式复刻 voice-twin/voice-selector：Popover + 每行播放/暂停 + 搜索 + 分组。
 */
export function BgmPicker({
  builtIn,
  custom,
  value,
  onChange,
  playingUrl,
  onTogglePreview,
  className,
  defaultOpen = false,
}: {
  builtIn: BGMTrack[];
  custom: BGMTrack[];
  value: string | null;
  onChange: (id: string | null) => void;
  playingUrl: string | null;
  onTogglePreview: (url: string) => void;
  className?: string;
  /** 初始展开态。默认收起；测试与「需要默认展开」的场景可显式打开 */
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [query, setQuery] = useState("");
  const selected = [...builtIn, ...custom].find((t) => t.id === value) ?? null;

  const renderRow = (track: BGMTrack) => {
    const url = track.preview_url;
    const playing = playingUrl === url;
    return (
      <div
        key={track.id}
        role="option"
        aria-selected={value === track.id}
        tabIndex={0}
        className={cn(
          "hover:bg-accent focus-visible:ring-ring flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 focus-visible:ring-2 focus-visible:outline-none",
          value === track.id && "bg-accent",
        )}
        onClick={() => {
          onChange(track.id);
          setOpen(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onChange(track.id);
            setOpen(false);
          }
        }}
      >
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0"
          title={`试听 ${track.name}`}
          onClick={(e) => {
            e.stopPropagation();
            onTogglePreview(url);
          }}
        >
          {playing ? (
            <Pause className="h-3.5 w-3.5" />
          ) : (
            <Play className="h-3.5 w-3.5" />
          )}
        </Button>
        <span className="min-w-0 flex-1 truncate text-sm">{track.name}</span>
        {track.duration ? (
          <span className="text-muted-foreground shrink-0 text-xs">
            {formatBgmDuration(track.duration)}
          </span>
        ) : null}
        {track.tags?.length ? (
          <span className="text-muted-foreground hidden shrink-0 text-xs sm:inline">
            {track.tags.slice(0, 2).join("·")}
          </span>
        ) : null}
        {value === track.id && (
          <Check className="h-4 w-4 shrink-0 text-indigo-500" />
        )}
      </div>
    );
  };

  const filteredBuiltIn = filterBgmTracks(builtIn, query);
  const filteredCustom = filterBgmTracks(custom, query);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          // 稳定的可访问名：e2e/辅助技术据此定位（不随选中曲目变化）
          aria-label="选择背景音乐"
          className={cn("min-w-0 justify-between font-normal", className)}
        >
          <span className="flex min-w-0 items-center gap-2">
            <Music className="text-muted-foreground h-4 w-4 shrink-0" />
            <span className="truncate">{selected ? selected.name : "无"}</span>
          </span>
          <ChevronDown className="text-muted-foreground h-4 w-4 shrink-0" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[380px] p-2" align="start">
        <div className="relative mb-2">
          <Search className="text-muted-foreground absolute top-2.5 left-2.5 h-4 w-4" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索名称或风格…"
            className="pl-8"
          />
        </div>
        {/*
          可视高度：原 max-h-72(288px) 按行高约 34px 只露约 6.7 行，扣掉「无」行与
          分组标题后恰好约 6 首可见 —— 这正是用户报「说是 10 首只看到 6 首」的原因
          （数据一直是 10 首）。改 60vh 并给分组标题加数量，让「下面还有」可发现。
        */}
        <div
          role="listbox"
          aria-label="背景音乐曲目"
          className="max-h-[60vh] overflow-y-auto"
        >
          <div
            role="option"
            aria-selected={value === null}
            tabIndex={0}
            className="hover:bg-accent focus-visible:ring-ring flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 focus-visible:ring-2 focus-visible:outline-none"
            onClick={() => {
              onChange(null);
              setOpen(false);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onChange(null);
                setOpen(false);
              }
            }}
          >
            <span className="text-muted-foreground min-w-0 flex-1 text-sm">
              无
            </span>
            {value === null && <Check className="h-4 w-4 text-indigo-500" />}
          </div>
          {filteredBuiltIn.length > 0 && (
            <>
              <div className="text-muted-foreground px-2 py-1 text-xs font-medium">
                内置 {filteredBuiltIn.length}
              </div>
              {filteredBuiltIn.map(renderRow)}
            </>
          )}
          {filteredCustom.length > 0 && (
            <>
              <div className="text-muted-foreground px-2 py-1 text-xs font-medium">
                我的音乐 {filteredCustom.length}
              </div>
              {filteredCustom.map(renderRow)}
            </>
          )}
          {filteredBuiltIn.length === 0 && filteredCustom.length === 0 && (
            <p className="text-muted-foreground px-2 py-3 text-center text-xs">
              无匹配曲目
            </p>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
