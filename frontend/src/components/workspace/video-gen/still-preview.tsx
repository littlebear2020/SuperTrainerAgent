"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { fetchStillPreview, stillPreview } from "@/core/video-gen/api";
import { layoutFallbackImage } from "@/core/video-gen/types";

/**
 * T7（2026-09-08 remotion_course）：逐帧静帧真实渲染预览（D8 默认开启）。
 *
 * - POST still-preview（202+轮询，2s 间隔，上限 30s）
 * - 命中缓存秒回（D9：换版式/改品牌才重渲）
 * - 30s 超时 / 失败 → 降级 M0 版式示意图（D15，不阻塞操作）
 * - layoutKey 变化（用户换版式）自动重渲
 */
const POLL_INTERVAL_MS = 2000;
const TIMEOUT_MS = 30_000;

interface StillPreviewProps {
  taskId: string;
  frameIndex: number;
  layout: string | null | undefined;
  className?: string;
}

export function StillPreview({
  taskId,
  frameIndex,
  layout,
  className,
}: StillPreviewProps) {
  const [state, setState] = useState<"idle" | "loading" | "ready" | "fallback">(
    "idle",
  );
  const [url, setUrl] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  const cleanup = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    pollRef.current = null;
    timeoutRef.current = null;
  }, []);

  // T-E6（R21）：旧 blob URL 即时 revoke——修前只在卸载时释放，连续换版式/
  // 刷新会泄漏（每张预览图几 MB blob 常驻内存）。
  const revokeBlob = useCallback(() => {
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
  }, []);

  useEffect(() => {
    revokeBlob();
    return () => {
      revokeBlob();
      cleanup();
    };
  }, [cleanup, revokeBlob]);

  // T-E6（R21）：手动刷新（素材就绪/重生后用户主动重渲预览）。
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let terminal = false; // T-E6：终态后不再起轮询间隔（修前 ready/fallback 后
    // 仍会再设一个 2s interval、空打一次服务端才自清）
    cleanup();
    setState("loading");
    setUrl(null);

    const fallback = () => {
      cleanup();
      if (!cancelled) setState("fallback");
    };
    timeoutRef.current = setTimeout(fallback, TIMEOUT_MS);

    void (async () => {
      try {
        const start = await stillPreview(taskId, frameIndex);
        if (cancelled) return;
        const poll = async () => {
          try {
            const r = await fetchStillPreview(
              taskId,
              frameIndex,
              start.cache_key,
            );
            if (cancelled) return;
            if (r.status === "ready" && r.url) {
              cleanup();
              terminal = true;
              revokeBlob(); // 换新图前释放旧 blob（T-E6）
              blobUrlRef.current = r.url;
              setUrl(r.url);
              setState("ready");
            } else if (r.status === "failed") {
              terminal = true;
              fallback();
            }
            // running → 继续轮询（超时兜底在 TIMEOUT_MS）
          } catch {
            // 网络抖动不当失败，等下一轮
          }
        };
        await poll();
        if (!cancelled && !terminal && !pollRef.current) {
          pollRef.current = setInterval(() => {
            void poll();
          }, POLL_INTERVAL_MS);
        }
      } catch {
        fallback();
      }
    })();

    return () => {
      cancelled = true;
      cleanup();
    };
  }, [taskId, frameIndex, layout, refreshTick, cleanup, revokeBlob]);

  if (state === "idle") return null;

  const src = state === "ready" && url ? url : layoutFallbackImage(layout);
  const isFallback = state !== "ready";

  return (
    <div className={className}>
      <div className="border-muted bg-muted/30 relative overflow-hidden rounded-md border">
        {}
        <img
          loading="lazy"
          src={src}
          alt={`版式预览 ${layout ?? "auto"}`}
          className="w-full object-contain"
        />
        {state === "loading" && (
          <div className="bg-background/60 text-muted-foreground absolute inset-0 flex items-center justify-center text-xs">
            正在渲染预览…
          </div>
        )}
        {isFallback && state !== "loading" && (
          <div className="bg-background/80 text-muted-foreground absolute right-1 bottom-1 rounded px-1.5 py-0.5 text-[10px]">
            版式示意图（预览降级）
          </div>
        )}
        {/* T-E6（R21）：素材就绪后的手动刷新入口 */}
        {state !== "loading" && (
          <button
            type="button"
            onClick={() => setRefreshTick((n) => n + 1)}
            className="bg-background/80 text-muted-foreground hover:text-foreground absolute top-1 right-1 rounded px-1.5 py-0.5 text-[10px]"
            title="重新渲染预览（素材更新后使用）"
          >
            刷新
          </button>
        )}
      </div>
    </div>
  );
}
