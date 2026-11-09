"use client";

/**
 * 帧音频试听按钮（2026-10-06 video-gen 模块内合并批）。
 *
 * remotion-course-storyboard 与 video-gen-storyboard（vimax）原各持一份；
 * 以 remotion 版为基准（m12 版本破缓存参数 + play().catch 用户反馈——
 * draft 无音频时拒绝播放须提示，不能静默）。
 */
import { Pause, Play } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useGenerateAudio } from "@/core/video-gen/hooks";

export function FrameAudioButton({
  taskId,
  frameIndex,
  version,
  audioReady,
  onGenerateStart,
}: {
  taskId: string;
  frameIndex: number;
  /** m12：音频缓存版本（任务 updated_at）——重生配音后 URL 变化防旧缓冲。 */
  version?: string | number;
  /** B3 复审 F1：ready 态下 generate-audio 被后端 already_ready 短路——action 是
   * no-op，此时只解释不引导；仅非 ready 态（生成失败/give-up）展示重触发 action。 */
  audioReady?: boolean;
  /** B3 复审 F2：重触发须通知父组件启动音频轮询（setAudioGenerating(true)）——
   * 仅 mutate 不开轮询的话，生成完成后 UI 无感知。无轮询基建的消费方可不传。 */
  onGenerateStart?: () => void;
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  // B3（2026-10-07 遗留批 P2-2）：重触发复用 useGenerateAudio——onSuccess 已
  // invalidate storyboard/video 双 query（状态自动同步），onError 有全局 toast 兜底。
  const generateMut = useGenerateAudio(taskId);
  return (
    <div className="flex items-center gap-1">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-6 text-xs"
        onClick={() => {
          const el = audioRef.current;
          if (!el) return;
          if (playing) el.pause();
          else
            // m12：draft 无音频时 play() 拒绝须有用户反馈（修前静默无响应）；
            // B3：带 action 的一键重触发。F1：ready 态 action 会被 already_ready
            // 短路（no-op）→ 纯解释文案；F2：action 须先 onGenerateStart 开轮询
            // （本组件无 isGenerating 本地态、不渲染 mutation 状态，无需 reset()）。
            el.play().catch(() => {
              if (audioReady) {
                toast.info("本帧旁白已修改，音频待更新");
                return;
              }
              toast.info("本帧音频待重新生成", {
                action: {
                  label: "重新生成配音",
                  onClick: () => {
                    onGenerateStart?.();
                    generateMut.mutate();
                  },
                },
              });
            });
        }}
      >
        {playing ? (
          <Pause className="mr-1 h-3 w-3" />
        ) : (
          <Play className="mr-1 h-3 w-3" />
        )}
        试听本帧
      </Button>
      <audio
        ref={audioRef}
        src={`/api/video/tasks/${taskId}/audio?frame=${frameIndex}${version != null ? `&v=${version}` : ""}`}
        onEnded={() => setPlaying(false)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        preload="none"
        className="hidden"
      />
    </div>
  );
}
