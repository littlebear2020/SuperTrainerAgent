"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useSubtitle, useUpdateSubtitle } from "@/core/video-gen/hooks";
import type {
  RefInjectionRecord,
  StoryboardData,
} from "@/core/video-gen/types";
import { cn } from "@/lib/utils";

// ── Info row ──────────────────────────────────────────────────────────────

// ── 字幕编辑区（2026-07-27 T15）：ready/failed 可改，保存后「重新合成」烧入 ──

/** 与后端 _srt_ts_to_seconds 同口径（routers/video_gen.py）：格式 + mm/ss < 60。 */
const SUBTITLE_TS_RE = /^\d{2}:\d{2}:\d{2},\d{3}$/;

export function isValidSubtitleTs(ts: string): boolean {
  if (!SUBTITLE_TS_RE.test(ts)) return false;
  const parts = ts.split(":");
  const mm = parts[1] ?? "";
  const ss = (parts[2] ?? "").split(",")[0] ?? "";
  return Number(mm) < 60 && Number(ss) < 60;
}

export function subtitleTsToSeconds(ts: string): number | null {
  if (!isValidSubtitleTs(ts)) return null;
  const parts = ts.split(":");
  const hh = parts[0] ?? "0";
  const mm = parts[1] ?? "0";
  const secParts = (parts[2] ?? "").split(",");
  const ss = secParts[0] ?? "0";
  const ms = secParts[1] ?? "0";
  return Number(hh) * 3600 + Number(mm) * 60 + Number(ss) + Number(ms) / 1000;
}

const SUBTITLE_TEXT_MAX = 200;

type SubtitleDraftCue = { start: string; end: string; text: string };

/** 保存前的前端校验（与后端 422 规则对齐，避免无谓往返）。 */
export function subtitleCueIssues(cue: SubtitleDraftCue): {
  start: boolean;
  end: boolean;
  text: boolean;
} {
  const startSec = subtitleTsToSeconds(cue.start);
  const endSec = subtitleTsToSeconds(cue.end);
  const ordered = startSec !== null && endSec !== null && startSec < endSec;
  const trimmed = cue.text.trim();
  return {
    start: startSec === null || !ordered,
    end: endSec === null || !ordered,
    text: trimmed.length === 0 || cue.text.length > SUBTITLE_TEXT_MAX,
  };
}

export function subtitleCuesHaveIssues(cues: SubtitleDraftCue[]): boolean {
  return cues.some((c) => {
    const iss = subtitleCueIssues(c);
    return iss.start || iss.end || iss.text;
  });
}

export function subtitleCuesEqual(
  a: SubtitleDraftCue[],
  b: SubtitleDraftCue[],
): boolean {
  if (a.length !== b.length) return false;
  return a.every((c, i) => {
    const other = b[i];
    if (!other) return false;
    return (
      c.start === other.start && c.end === other.end && c.text === other.text
    );
  });
}

export function SubtitleEditSection({
  taskId,
  taskStatus,
}: {
  taskId: string;
  taskStatus: string;
}) {
  const [open, setOpen] = useState(false);
  const subtitleQuery = useSubtitle(taskId, open);
  const updateSubtitleMut = useUpdateSubtitle(taskId);
  const [cues, setCues] = useState<SubtitleDraftCue[]>([]);
  // T11 dirty baseline：必须与 cues 在同一个 effect 内 set，否则首渲染 baseline
  // 为空 → isDirty 恒 true。
  const [initialCues, setInitialCues] = useState<SubtitleDraftCue[]>([]);
  const loaded = subtitleQuery.data;
  // 打开时一次性灌入本地编辑态（避免编辑过程中被轮询覆盖）
  useEffect(() => {
    if (loaded && cues.length === 0) {
      const next = loaded.cues.map((c) => ({
        start: c.start,
        end: c.end,
        text: c.text,
      }));
      setCues(next);
      setInitialCues(next);
    }
  }, [loaded, cues.length]);

  const isDirty = useMemo(
    () => !subtitleCuesEqual(cues, initialCues),
    [cues, initialCues],
  );
  const hasIssues = useMemo(() => subtitleCuesHaveIssues(cues), [cues]);

  // 与后端端点状态门一致：ready/failed 可编辑（draft 尚无 SRT）
  const editable = taskStatus === "ready" || taskStatus === "failed";
  if (!editable) return null;

  return (
    <div
      data-testid="subtitle-edit-section"
      className="border-border/50 space-y-3 rounded-lg border p-4"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">字幕</h3>
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs"
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "收起" : "编辑字幕"}
        </Button>
      </div>
      {open && (
        <>
          {subtitleQuery.isLoading && (
            <p className="text-muted-foreground text-xs">加载中…</p>
          )}
          {subtitleQuery.isError && (
            <div
              data-testid="subtitle-load-error"
              className="text-destructive flex items-center gap-1 text-xs"
            >
              <span>
                字幕加载失败：
                {subtitleQuery.error?.message ?? "未知错误"}
              </span>
              <Button
                size="sm"
                variant="link"
                className="h-5 px-1 text-xs"
                onClick={() => void subtitleQuery.refetch()}
              >
                重试
              </Button>
            </div>
          )}
          {loaded && loaded.cues.length < 1 && (
            <p className="text-muted-foreground text-xs">本片无字幕条目。</p>
          )}
          {loaded && loaded.cues.length > 0 && (
            <>
              {loaded.locked && (
                <p className="text-xs text-amber-600 dark:text-amber-400">
                  字幕已被手工修改；改讲解词/重配音频后字幕将按新旁白重新生成（届时手工修改将失效）。
                </p>
              )}
              <div className="max-h-64 space-y-2 overflow-y-auto">
                {cues.map((cue, i) => {
                  const issues = subtitleCueIssues(cue);
                  return (
                    <div key={i} className="flex items-center gap-2">
                      <input
                        type="text"
                        aria-label={`第 ${i + 1} 条字幕开始时间`}
                        value={cue.start}
                        onChange={(e) => {
                          const next = [...cues];
                          next[i] = { ...cue, start: e.target.value };
                          setCues(next);
                        }}
                        className={cn(
                          "w-28 shrink-0 rounded-md border bg-transparent px-1 py-1 font-mono text-[10px]",
                          issues.start && "border-destructive",
                        )}
                      />
                      <input
                        type="text"
                        aria-label={`第 ${i + 1} 条字幕结束时间`}
                        value={cue.end}
                        onChange={(e) => {
                          const next = [...cues];
                          next[i] = { ...cue, end: e.target.value };
                          setCues(next);
                        }}
                        className={cn(
                          "w-28 shrink-0 rounded-md border bg-transparent px-1 py-1 font-mono text-[10px]",
                          issues.end && "border-destructive",
                        )}
                      />
                      <input
                        type="text"
                        required
                        maxLength={SUBTITLE_TEXT_MAX}
                        aria-label={`第 ${i + 1} 条字幕文本`}
                        value={cue.text}
                        onChange={(e) => {
                          const next = [...cues];
                          next[i] = { ...cue, text: e.target.value };
                          setCues(next);
                        }}
                        className={cn(
                          "w-full rounded-md border bg-transparent px-2 py-1 text-xs",
                          issues.text && "border-destructive",
                        )}
                      />
                    </div>
                  );
                })}
              </div>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                disabled={
                  updateSubtitleMut.isPending ||
                  cues.length === 0 ||
                  hasIssues ||
                  !isDirty
                }
                onClick={() => {
                  // toast 由 useUpdateSubtitle 的 onSuccess 统一发（它才知道
                  // 是否触发了「无字幕」自动恢复）——此处再发一次会双弹
                  updateSubtitleMut.mutate(cues, {
                    onSuccess: () => setInitialCues([...cues]),
                  });
                }}
              >
                {updateSubtitleMut.isPending
                  ? "保存中…"
                  : isDirty
                    ? "保存字幕"
                    : "已保存"}
              </Button>
              {hasIssues && (
                <p className="text-destructive text-xs">
                  存在非法时间戳（须 HH:MM:SS,mmm
                  且开始早于结束）或空/超长文本，已暂时禁用保存。
                </p>
              )}
              <p className="text-muted-foreground text-xs">
                字幕按原文保存，不会自动去标点；仅支持单行，换行将合并为空格。
              </p>
            </>
          )}
        </>
      )}
    </div>
  );
}
