"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  SpeedSliderControl,
  VolumeSliderControl,
} from "@/components/workspace/video-gen/audio-sliders";
import { getStoryboard } from "@/core/video-gen/api";
import {
  useRegenerateAllAudio,
  useRecomposeTask,
  useReRenderTask,
  useUpdateStoryboardConfig,
  useVideoVoices,
  STORYBOARD_KEY,
} from "@/core/video-gen/hooks";
import {
  SUBTITLE_STYLES,
  isSubtitleStyle,
  subtitleStyleLabel,
  subtitleStylePreviewClass,
} from "@/core/video-gen/subtitle-styles";
import type {
  RefInjectionRecord,
  StoryboardData,
} from "@/core/video-gen/types";
import {
  filterVoicesForTaskType,
  isPresetVoice,
} from "@/core/video-gen/voice-filtering";

// ── Info row ──────────────────────────────────────────────────────────────

// ── 音频设置区（2026-07-27 T14：voice/语速/BGM 音量生成后可调） ─────────────

export function AudioSettingsSection({
  taskId,
  storyboard,
  taskStatus,
}: {
  taskId: string;
  storyboard: StoryboardData;
  taskStatus: string;
}) {
  const cfg = storyboard.config;
  // Plan B（2026-10-19，D4）：仅 ViMax 任务过滤 360 音色与阻断 preset（残留值
  // 以禁用 option + 提示呈现，不静默清空）；其他视频类型语义不变。
  const isVimax = cfg.video_type === "vimax";
  const [voiceId, setVoiceId] = useState(cfg.voice_id ?? "");
  const [ttsSpeed, setTtsSpeed] = useState(cfg.tts_speed ?? 1.0);
  const [bgmVolume, setBgmVolume] = useState(
    Math.round((cfg.bgm_volume ?? 0.15) * 100),
  );
  // 初值读 storyboard.config（不是创建 payload）——才能反映
  // _restore_subtitle_style_if_none 的自动回填结果。
  const [subtitleStyle, setSubtitleStyle] = useState<string>(
    cfg.subtitle_style ?? "default",
  );
  // F3（2026-09-28 edits 修复批）：同步基线=上次已知服务端值；dirty 由「表单值 vs
  // 基线」逐字段换算得出（bgm 走 0-1 float ↔ 0-100 int 同一换算）。
  const [baseline, setBaseline] = useState(() => ({
    voice_id: cfg.voice_id ?? null,
    tts_speed: cfg.tts_speed ?? 1.0,
    bgm_volume: cfg.bgm_volume ?? 0.15,
    subtitle_style: cfg.subtitle_style ?? "default",
  }));
  // F1：voice/speed 变化后的三选弹层 + 批量重生进度监看
  const [regenModalOpen, setRegenModalOpen] = useState(false);
  const [regenWatch, setRegenWatch] = useState<{
    /** 12min 客户端兜底超时的起点（客户端时钟，仅用于看门狗） */
    startedAtMs: number;
    /** 判代基线 = 批量发起前盘面各帧 regen_state_at 最大值（服务端时钟域）——与
     *  后续帧时间戳同域比较，客户端时钟漂移不影响判代（review 收尾 2026-09-28） */
    baselineAtMs: number;
    frames: number[];
  } | null>(null);

  // 2026-07-28 产品评审 T3-P1：默认折叠——头部行常驻显示当前值摘要，
  // 帧视图不被挤占；编辑才展开。
  const [expanded, setExpanded] = useState(false);
  const voicesQuery = useVideoVoices();
  const updateConfigMut = useUpdateStoryboardConfig(taskId);
  const regenAllMut = useRegenerateAllAudio(taskId);
  const regenAutoRecomposeMut = useRecomposeTask(taskId);
  const rerenderMut = useReRenderTask(taskId);

  // F1：批量重生进度轮询——与父级共享 [STORYBOARD_KEY, taskId] 缓存，watch 期间
  // 本观察者的 refetchInterval 生效（帧级 regen_state 由后端逐帧置/清）。
  useQuery({
    queryKey: [STORYBOARD_KEY, taskId],
    queryFn: () => getStoryboard(taskId),
    enabled: regenWatch !== null,
    refetchInterval: regenWatch ? 2000 : false,
  });

  // F4：字段级三分态——未动=键不发；「默认/跟随项目」=显式 null；选定=值。
  const voiceChanged = (voiceId || null) !== baseline.voice_id;
  const speedChanged = ttsSpeed !== baseline.tts_speed;
  const bgmChanged = bgmVolume !== Math.round(baseline.bgm_volume * 100);
  const styleChanged = subtitleStyle !== baseline.subtitle_style;
  const formDirty = voiceChanged || speedChanged || bgmChanged || styleChanged;

  // F3：服务端 config 外部变化（他人保存/re-render 回写/refetch）且表单非 dirty →
  // 同步本地 state；dirty 时不同步（防打断输入）；F1 弹层开着延后（用户决策中）。
  // 头部摘要行读同一批 state，顺带治好摘要陈旧（F3④）。
  useEffect(() => {
    if (regenModalOpen || formDirty) return;
    const nb = {
      voice_id: cfg.voice_id ?? null,
      tts_speed: cfg.tts_speed ?? 1.0,
      bgm_volume: cfg.bgm_volume ?? 0.15,
      subtitle_style: cfg.subtitle_style ?? "default",
    };
    if (
      nb.voice_id === baseline.voice_id &&
      nb.tts_speed === baseline.tts_speed &&
      nb.bgm_volume === baseline.bgm_volume &&
      nb.subtitle_style === baseline.subtitle_style
    )
      return;
    setVoiceId(cfg.voice_id ?? "");
    setTtsSpeed(cfg.tts_speed ?? 1.0);
    setBgmVolume(Math.round((cfg.bgm_volume ?? 0.15) * 100));
    setSubtitleStyle(cfg.subtitle_style ?? "default");
    setBaseline(nb);
    // baseline 取快照比较，避免每渲染重置（cfg 对象引用随轮询变化）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    cfg.voice_id,
    cfg.tts_speed,
    cfg.bgm_volume,
    cfg.subtitle_style,
    formDirty,
    regenModalOpen,
  ]);

  // F1：全部帧 regen_state 到达本次批量之后的终态（regen_state_at 时间戳判代，
  // 防复用上一次单帧重生的旧 done）→ 全成功自动 recompose；部分失败不自动（决策 2）。
  useEffect(() => {
    if (!regenWatch) return;
    const frames = storyboard.frames ?? [];
    let done = 0;
    let failed = 0;
    for (const idx of regenWatch.frames) {
      const f = frames.find((fr) => fr.index === idx);
      const state = f?.regen_state;
      const atMs = f?.regen_state_at ? Date.parse(f.regen_state_at) : NaN;
      const settled =
        Number.isFinite(atMs) &&
        atMs > regenWatch.baselineAtMs && // 服务端时钟域基线判代——不受客户端时钟漂移影响
        (state === "done" || state === "error");
      if (!settled) continue;
      if (state === "done") done += 1;
      else failed += 1;
    }
    if (done + failed >= regenWatch.frames.length) {
      setRegenWatch(null);
      if (failed > 0) {
        toast.error(
          `配音重生完成：${failed}/${regenWatch.frames.length} 帧失败——请到分镜页对失败帧单独「重生配音」后再「重新合成」`,
        );
      } else {
        toast.success("全部配音已重生，正在自动重新合成…");
        // F1 决策：弹层选项1 已获用户同意（「完成后自动重新合成」文案明示）——
        // 此处直调不属 P1/BUG-008 的「无确认直调」回退面，静态守卫按同意标记放行。
        regenAutoRecomposeMut.mutate();
      }
      return;
    }
    if (Date.now() - regenWatch.startedAtMs > 12 * 60 * 1000) {
      // 后端 fold_stale_running_states 10min TTL 已兜底折算 error——12min 客户端
      // 兜底只防轮询/事件丢失，不抢跑
      setRegenWatch(null);
      toast.error("配音批量重生超时（>12 分钟），请刷新查看结果");
    }
  }, [storyboard, regenWatch, regenAutoRecomposeMut]);

  // 与后端端点状态门一致：draft/failed/ready 可改
  const editable =
    taskStatus === "draft" || taskStatus === "failed" || taskStatus === "ready";
  if (!editable) return null;

  const voices = filterVoicesForTaskType(
    voicesQuery.data ?? [],
    isVimax ? "vimax" : undefined,
  );
  const voiceInList = voices.some((v) => v.id === voiceId);
  const voiceName =
    voices.find((v) => v.id === voiceId)?.name ?? voiceId ?? "默认";
  // Plan B：ViMax 残留/新选 preset → 保存禁用（后端会 422，前端先拦）
  const presetBlocked = isVimax && isPresetVoice(voiceId);
  const frameCount = storyboard.frames?.length ?? 0;

  const handleSave = () => {
    const patch: {
      voice_id?: string | null;
      tts_speed?: number;
      bgm_volume?: number;
      subtitle_style?: string;
    } = {};
    if (voiceChanged) patch.voice_id = voiceId || null; // F4：默认/跟随项目=显式 null
    if (speedChanged) patch.tts_speed = ttsSpeed;
    if (bgmChanged) patch.bgm_volume = bgmVolume / 100;
    if (styleChanged) patch.subtitle_style = subtitleStyle;
    // F1：voice/speed 变化且 ready/failed → 弹层三选（成本文案见弹层）；draft 不弹
    //（B5 已让「重新准备音频」生效）；仅样式/BGM → toast 引导「重新合成」。
    const willOfferRegen =
      (voiceChanged || speedChanged) &&
      (taskStatus === "ready" || taskStatus === "failed") &&
      frameCount > 0;
    updateConfigMut.mutate(patch, {
      onSuccess: () => {
        setBaseline((b) => ({
          voice_id: voiceChanged ? voiceId || null : b.voice_id,
          tts_speed: speedChanged ? ttsSpeed : b.tts_speed,
          bgm_volume: bgmChanged ? bgmVolume / 100 : b.bgm_volume,
          subtitle_style: styleChanged ? subtitleStyle : b.subtitle_style,
        }));
        if (willOfferRegen) {
          setRegenModalOpen(true); // 弹层出现时不再 toast（去重，F1 v2 提示③）
        } else if (taskStatus === "draft" && (voiceChanged || speedChanged)) {
          toast.success("音频设置已保存，重新「准备音频」后生效");
        } else if (styleChanged || bgmChanged) {
          toast.success("音频设置已保存，点「重新合成」烧入画面");
        } else {
          toast.success("音频设置已保存");
        }
      },
    });
  };

  const startRegenAll = () => {
    setRegenModalOpen(false);
    // 判代基线取发起前盘面（此时 prop 尚为批量前快照）各帧 regen_state_at 的最大值。
    // 修前用客户端 Date.now()-2s 容差，客户端时钟快 >2s 会把新代终态误判为旧代而
    // 永不结案（12min 才兜底）——改为服务端时钟域同域比较，窗口彻底消除。
    const baselineAtMs = Math.max(
      -1,
      ...(storyboard.frames ?? []).map((f) =>
        f.regen_state_at ? Date.parse(f.regen_state_at) : -1,
      ),
    );
    regenAllMut.mutate(undefined, {
      onSuccess: (data) => {
        setRegenWatch({
          startedAtMs: Date.now(),
          baselineAtMs,
          frames: data.frames,
        });
        toast.success(
          `已发起全部配音重生（${data.frames.length} 帧），完成后将自动重新合成`,
        );
      },
    });
  };

  return (
    <div
      data-testid="audio-settings-section"
      className="border-border/50 space-y-3 rounded-lg border p-4"
    >
      <button
        type="button"
        className="flex w-full items-center justify-between"
        onClick={() => setExpanded((v) => !v)}
        title={expanded ? "收起音频设置" : "展开音频设置"}
      >
        <h3 className="text-sm font-semibold">音频设置</h3>
        <span className="text-muted-foreground text-xs">
          {voiceName} · {ttsSpeed.toFixed(1)}x · BGM {bgmVolume}% ·{" "}
          {subtitleStyleLabel(subtitleStyle)} {expanded ? "▲" : "▼"}
        </span>
      </button>
      {expanded && (
        <>
          <div className="space-y-1">
            <label className="text-muted-foreground text-xs">配音音色</label>
            <select
              data-testid="voice-select"
              value={voiceId}
              onChange={(e) => setVoiceId(e.target.value)}
              className="w-full rounded-md border bg-transparent px-2 py-1.5 text-sm"
            >
              {/* F4：显式空值选项——「默认/跟随项目」提交为 null（清空回退链） */}
              <option value="">默认 / 跟随项目</option>
              {voices.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} ({v.gender})
                </option>
              ))}
              {/* 当前 config 音色不在列表（如 doubao speaker 无 key 被门控）也显示原值；
                  Plan B：ViMax 任务的 preset 残留值显示为「不可用」禁用项（不静默清空，
                  改选豆包/Edge 后可保存） */}
              {voiceId && !voiceInList && (
                <option value={voiceId} disabled={presetBlocked}>
                  {presetBlocked
                    ? `${voiceId}（ViMax 不可用，请改选豆包/Edge）`
                    : voiceId}
                </option>
              )}
            </select>
            {presetBlocked && (
              <p className="text-xs text-amber-600">
                ViMax 任务不支持 360 音色，请改选豆包或 Edge 音色后再保存
              </p>
            )}
          </div>
          <div className="space-y-1">
            <label className="text-muted-foreground text-xs">字幕样式</label>
            <select
              data-testid="subtitle-style-select"
              value={subtitleStyle}
              onChange={(e) => setSubtitleStyle(e.target.value)}
              className="w-full rounded-md border bg-transparent px-2 py-1.5 text-sm"
            >
              {SUBTITLE_STYLES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
              {/* config 里是后端新增的未知枚举时也显示原值，不静默改写 */}
              {!isSubtitleStyle(subtitleStyle) && (
                <option value={subtitleStyle}>{subtitleStyle}</option>
              )}
            </select>
            {subtitleStyle !== "none" && (
              <div className="flex items-center gap-2 pt-1">
                <span className="text-muted-foreground text-[10px]">预览</span>
                <span className="rounded bg-neutral-800 px-2 py-1">
                  <span className={subtitleStylePreviewClass(subtitleStyle)}>
                    字幕效果示例
                  </span>
                </span>
              </div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <label className="text-muted-foreground text-xs">语速</label>
                <span className="text-muted-foreground text-xs">
                  {ttsSpeed.toFixed(1)}x
                </span>
              </div>
              <SpeedSliderControl
                value={ttsSpeed}
                onChange={(v) => setTtsSpeed(v ?? 1.0)}
              />
            </div>
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <label className="text-muted-foreground text-xs">
                  BGM 音量
                </label>
                <span className="text-muted-foreground text-xs">
                  {bgmVolume}%
                </span>
              </div>
              <VolumeSliderControl value={bgmVolume} onChange={setBgmVolume} />
            </div>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            disabled={
              updateConfigMut.isPending ||
              !formDirty ||
              regenWatch !== null ||
              presetBlocked
            }
            onClick={handleSave}
          >
            {updateConfigMut.isPending
              ? "保存中…"
              : regenWatch !== null
                ? "配音重生中…"
                : "保存音频设置"}
          </Button>
          {regenWatch && (
            <p
              data-testid="audio-regen-progress"
              className="text-xs text-amber-600"
            >
              配音批量重生中（{regenWatch.frames.length} 帧）…
              完成后自动重新合成
            </p>
          )}
          <p className="text-muted-foreground text-xs">
            音色/语速：保存后可整片重生配音（弹层）或对单帧点「重生配音」；BGM
            音量：保存后直接点「重新合成」即生效。
          </p>
        </>
      )}
      {/* F1：voice/speed 变化三选弹层（仅 ready/failed）——文案含帧数与成本预期 */}
      <AlertDialog open={regenModalOpen} onOpenChange={setRegenModalOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>音色/语速已更新</AlertDialogTitle>
            <AlertDialogDescription>
              将为 {frameCount} 帧重新配音（{frameCount} 次付费 TTS
              调用，无省费空间），完成后自动重新合成。注意：将按新配音重排字幕，
              手动修改的字幕文本将被覆盖。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex flex-col gap-2">
            <Button
              data-testid="regen-all-audio-btn"
              size="sm"
              disabled={regenAllMut.isPending}
              onClick={startRegenAll}
            >
              {regenAllMut.isPending
                ? "发起中…"
                : "重生全部配音并重新合成（推荐）"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setRegenModalOpen(false);
                rerenderMut.mutate();
              }}
            >
              整片重新生成（视觉一并重跑，耗时更长）
            </Button>
            <AlertDialogAction>仅保存（稍后逐帧重生配音）</AlertDialogAction>
          </div>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
