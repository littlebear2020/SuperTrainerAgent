"use client";

import {
  ChevronDown,
  ChevronUp,
  Film,
  Loader2,
  Pencil,
  Upload,
  Volume2,
  XCircle,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  SpeedSliderControl,
  VolumeSliderControl,
} from "@/components/workspace/video-gen/audio-sliders";
import { CourseProjectSelect } from "@/components/workspace/video-gen/course-project-select";
import {
  useBGMList,
  useProviders,
  useBGMUpload,
  useCourseProjects,
  useDefaultVoice,
  useExtractScript,
  useSubmitVideo,
  useVideoVoices,
} from "@/core/video-gen/hooks";
import {
  OUTPUT_FORMAT_OPTIONS,
  type UiOutputFormat,
} from "@/core/video-gen/output-formats";
import {
  buildProviderOptions,
  DEFAULT_PROVIDER_VALUE,
  providerFieldForTaskType,
  providerValueToPayload,
} from "@/core/video-gen/provider-options";
import {
  STYLE_PRESETS,
  styleAfterTypeChange,
  stylePreviewImage,
  taskStyleOptions,
} from "@/core/video-gen/style-presets";
import {
  SUBTITLE_STYLES,
  type SubtitleStyle,
  isSubtitleStyle,
} from "@/core/video-gen/subtitle-styles";
import { LANG_OPTIONS } from "@/core/video-gen/types";
import type { VideoTaskBrief } from "@/core/video-gen/types";
import {
  filterVoicesForTaskType,
  isPresetVoice,
  vimaxVoiceBlocked,
} from "@/core/video-gen/voice-filtering";
import { cn } from "@/lib/utils";

import { BgmPicker } from "./bgm-picker";
import {
  buildCreatePayload,
  isTargetDurationVisible,
  isValidTargetDuration,
  resolveTargetDurationForPayload,
  resolveSecondsPerFrameForPayload,
  SECONDS_PER_FRAME_MAX,
  SECONDS_PER_FRAME_MIN,
  TARGET_DURATION_MAX,
  TARGET_DURATION_MIN,
} from "./create-payload";
import { shouldShowStyleSelector } from "./style-visibility";

// ── Task type tabs ──────────────────────────────────────────────────────

// ── Task type config ────────────────────────────────────────────────────

/** C7（决策 3/5）：绑项目任务表单三下拉「跟随项目」哨兵——后端按未显式处理
 * （service._resolve_effective_style / _resolve_voice_id / _resolve_subtitle_style）。 */
const FOLLOW_PROJECT = "follow_project";

export const TYPE_CONFIG: Record<
  string,
  {
    label: string;
    desc: string;
    placeholder: string;
    supportsGenerate: boolean;
  }
> = {
  ai_image: {
    label: "AI 生图",
    desc: "AI 生成静态画面 + Ken Burns 推拉，适合知识科普、概念演示",
    placeholder:
      "每句话对应一个画面。例如：\n人工智能正在改变世界。\n从自动驾驶到智能医疗，AI无处不在。",
    supportsGenerate: true,
  },
  ai_video: {
    label: "AI 生视频",
    // FP2-11: 告知硬限制——单镜时长上限 + seedance 人脸审查拦真人参考图，
    // 修前用户提交后才发现被拦/被截。
    desc: "AI 生成运动镜头，适合需要真实动态场景的视频。单镜最长 15 秒；真人照片参考不可用（将被内容审核拦截），可用 AI 生成角色",
    placeholder:
      "每句话对应一个画面。例如：\n人工智能正在改变世界。\n从自动驾驶到智能医疗，AI无处不在。",
    supportsGenerate: true,
  },
  // T-F1（D1）：remotion（chat-handoff）项删除——创建入口归一到 remotion_course
  // （Tab 名「文字动效」）；存量 remotion 任务只在列表/筛选出现。
  remotion_course: {
    label: "文字动效",
    // 2026-09-08 T4 + T-F1（D1 入口归一）：后端任务管线（remotion-worker 独立池
    // 渲染 course-standard 19 版式模板 + 品牌包 + 逐词字幕）。
    // 2026-09-10 独立管线计划 T12（决策 12）：话题生成模式移除（supportsGenerate
    // = false）——remotion_course 走「粘贴/上传课程脚本 → 结构化版式分镜」流程，
    // 话题生成语义（AI 编稿）与本管线保真契约不匹配。实施时验证副作用：切换回
    // fixed 已由 handleTypeChange 既有逻辑兜底（supportsGenerate=false → setMode
    // ("fixed")）；存量 generate 任务与 API 直调不受影响（后端分支保留）。
    desc: "把课程脚本变成有版式、有配音的课程视频：19 种版式 + 品牌包 + 逐词字幕，适合系列课程",
    placeholder:
      "粘贴/上传课程脚本，AI 将按章节与知识点组织成版式分镜；标题与列表会显示在画面上，不会逐字念出。",
    supportsGenerate: false,
  },
  vimax: {
    label: "ViMax 故事",
    desc: "ViMax 引擎：角色三视角肖像 + 参考图链，跨镜头角色一致，适合故事/剧情视频（写实默认）",
    placeholder:
      "每句话为一个故事场景（剧本模式）。例如：\n小明独自走进古老的森林，四周静悄悄的。\n突然，他听到了远处传来的奇怪声响，心跳加速。",
    supportsGenerate: false,
  },
};

// ── Create task dialog ──────────────────────────────────────────────────

/**
 * 风格下拉项缩略图（2026-09-09 设计 §3.4）：图缺失（开发环境漏跑生成脚本）
 * onError 静默隐藏自身，下拉项退化为纯文字不裂图；useState 而非 DOM 副作用
 * （2026-09-10 review P2——Radix 复用 DOM 节点，内联 display:none 不会复位）。
 */
function StyleThumb({ src }: { src: string }) {
  const [err, setErr] = useState(false);
  if (err) return null;
  return (
    <img
      loading="lazy"
      src={src}
      alt=""
      className="h-8 w-14 shrink-0 rounded object-cover"
      onError={() => setErr(true)}
    />
  );
}

/**
 * 风格选中预览区（2026-09-09 风格预览图设计 §3.4，2026-09-10 修订为通栏）：
 * CreateTaskDialog 的 grid-cols-3 下方独占一行——对话框 sm:max-w-2xl（672px）
 * × 三列，列内 16:9 图仅 ~200×112px 看不清风格细节。仅当选中值有一级 7 key
 * 预览图时渲染内容（default / 跟随项目 / 存量承载项返回 null 不占位）；预览
 * 是风格示意，不承诺与最终生成画面一致（免责小字管理预期）；图缺失（开发
 * 环境漏跑生成脚本）onError 静默隐藏整块（生产图随前端构建打包，不会缺失）。
 * 失败按 src 追踪（failedSrc）而非布尔——切换风格后 src 变化自动复位，
 * 不会出现「A 图 404 隐藏整块、切到 B 仍不显示」（2026-09-10 review P2）。
 */
function StylePreviewPanel({ styleValue }: { styleValue: string }) {
  const previewSrc = stylePreviewImage(styleValue);
  const preset = STYLE_PRESETS.find((p) => p.value === styleValue);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  if (!previewSrc || !preset || failedSrc === previewSrc) return null;
  return (
    <div className="flex items-start gap-4 rounded-lg border p-3">
      <img
        loading="lazy"
        src={previewSrc}
        alt={`${preset.label}风格预览`}
        className="aspect-video w-64 shrink-0 rounded-md object-cover"
        onError={() => setFailedSrc(previewSrc)}
      />
      <div className="min-w-0 space-y-1">
        <p className="text-sm font-medium">{preset.label}</p>
        {preset.description ? (
          <p className="text-muted-foreground text-sm">{preset.description}</p>
        ) : null}
        <p className="text-muted-foreground/70 text-xs">
          预览仅为风格效果参考，实际画面以生成为准
        </p>
      </div>
    </div>
  );
}

// exported for unit tests (Task 19: 草稿/正式提交对齐)
/**
 * 2026-10-06 拆分批：创建任务表单状态/逻辑抽为 hook（25+ useState 与全部
 * handler 上提；CreateTaskDialog 只剩 JSX 编排）。返回名册与解构一一对应。
 */
function useCreateTaskForm({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [taskType, setTaskType] = useState<
    "ai_image" | "ai_video" | "remotion_course" | "vimax"
  >("ai_image");
  const [mode, setMode] = useState<"fixed" | "generate">("fixed");
  // T7/T6（2026-09-10 独立管线计划）：remotion_course 解析双档 + docx 图片暂存信息
  const [courseParseMode, setCourseParseMode] = useState<
    "faithful" | "rewrite"
  >("faithful");
  const [scriptExtract, setScriptExtract] = useState<{
    extractId: string;
    expiresAt: string;
    imageCount: number;
    ignoredCount: number;
    /** M5（2026-09-11 二轮审查）：提取时的文本快照——提交前比对【图片N】标记集，
     * 手动改贴新脚本（标记变化）时不再携带旧 extract_id（错配/静默丢图）。 */
    text: string;
  } | null>(null);
  // M10（2026-09-11 审查）：pdf 提示落地——修前 pdfHint 字段无 UI 消费、后端
  // 提示分支因前端只对 docx 发 with_images 永不触发（§5.4 死路径）。
  const [pdfNote, setPdfNote] = useState(false);
  const [script, setScript] = useState("");
  const [topic, setTopic] = useState("");
  // 2026-10-20 Task 5：创建时自定义标题（可选；空则后端按脚本前 30 字兜底）。
  const [title, setTitle] = useState("");
  const [aspect, setAspect] = useState<"9:16" | "16:9" | "1:1">("9:16");
  const [style, setStyle] = useState("default");
  // "none"（无字幕）从第一天就能在下拉里选中，却因类型不含它、靠 `as` 强转
  // 蒙过编译器——正是这个强转让下游全部缺失处理无人发现（2026-07-29 字幕死锁）。
  // C7：联合哨兵——绑项目时默认「跟随项目」，取消防绑重置回 "default"
  const [subtitleStyle, setSubtitleStyle] = useState<
    SubtitleStyle | typeof FOLLOW_PROJECT
  >("default");
  const [bgm, setBgm] = useState<string | null>(null);
  const [bgmDucking, setBgmDucking] = useState(true);
  const [voiceId, setVoiceId] = useState<string | null>(null);
  const [lang, setLang] = useState("zh");
  // Task 3（2026-08-01）：类型收窄为 mp4|gif，WebM 已从 UI 移除。
  // 后端 schema 仍保留 webm 成员以兼容存量草稿（详见 core/video-gen/output-formats.ts）。
  const [outputFormat, setOutputFormat] = useState<UiOutputFormat>("mp4");
  // Task 6：任务级生成模型。null = 跟随后端 config 默认（且保留降级链）。
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [quality, setQuality] = useState<
    "draft" | "standard" | "high" | "preview"
  >("standard");
  const [targetDuration, setTargetDuration] = useState<number | "">("");
  const [secondsPerFrame, setSecondsPerFrame] = useState<number | "">("");
  // T3（2026-09-01）：任务级图文镜占比上限。默认 30%（守「防半部片翻 PPT」回调
  // 史）；上限≠保障——实际占比由课程内容决定，仅 ai_image/ai_video 消费（vimax
  // 引擎层不走该护栏；remotion 不 POST /create）。
  const [graphicRatio, setGraphicRatio] = useState<30 | 40 | 50 | 70>(30);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  // T-E11（D6 review #1）：null = 跟随（绑项目=项目语速 > config 缺省）——默认态。
  // 修前 useState(1.0) 恒显式下发，项目级语速在 UI 主路径被架空（后端三级链
  // 恒命中第一级）。
  const [ttsSpeed, setTtsSpeed] = useState<number | null>(null);
  const [bgmVolume, setBgmVolume] = useState(15);
  const [courseProjectId, setCourseProjectId] = useState<string | undefined>(
    undefined,
  );
  // 条目 39（2026-09-12 缺陷批）：取消「跟随」时的「当前生效值」——绑项目 = 项目
  // tts_speed（useCourseProjects 缓存查表）；未绑/项目未设置 = config 缺省 1.2。
  const { data: courseProjects } = useCourseProjects();
  const projectTtsSpeed = courseProjectId
    ? courseProjects?.find((p) => p.id === courseProjectId)?.tts_speed
    : undefined;
  const [playingAudio, setPlayingAudio] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const bgmUploadRef = useRef<HTMLInputElement | null>(null);
  const scriptUploadRef = useRef<HTMLInputElement | null>(null);

  // C7（决策 3/5）：绑定项目时三下拉注入「跟随项目」并设为默认；取消绑定重置为
  // "default"。仅转换「未显式」状态（default/null）——已显式选择的值保持用户意图。
  useEffect(() => {
    if (courseProjectId) {
      setStyle((v) => (v === "default" ? FOLLOW_PROJECT : v));
      setVoiceId((v) => v ?? FOLLOW_PROJECT);
      setSubtitleStyle((v) => (v === "default" ? FOLLOW_PROJECT : v));
    } else {
      setStyle((v) => (v === FOLLOW_PROJECT ? "default" : v));
      setVoiceId((v) => (v === FOLLOW_PROJECT ? null : v));
      setSubtitleStyle((v) => (v === FOLLOW_PROJECT ? "default" : v));
    }
  }, [courseProjectId]);

  const togglePreview = (url: string) => {
    if (playingAudio === url) {
      audioRef.current?.pause();
      setPlayingAudio(null);
      return;
    }
    if (!audioRef.current) {
      audioRef.current = new Audio();
      audioRef.current.onended = () => setPlayingAudio(null);
    }
    audioRef.current.src = url;
    audioRef.current.play().catch(() => {
      // P1-U4 残余 / Task 17：试听失败（edge-tts 不可达/音色不存在）不再静默
      setPlayingAudio(null);
      toast.error("试听失败：音色暂不可用，请稍后再试");
    });
    setPlayingAudio(url);
  };

  const submitMut = useSubmitVideo();
  const router = useRouter();
  const voicesQuery = useVideoVoices(lang);
  const bgmQuery = useBGMList();
  // Task 6：可选生成模型（可用性 = 后端 key 是否配置，不发上游请求）
  const providersQuery = useProviders();
  const bgmUploadMut = useBGMUpload();
  const scriptExtractMut = useExtractScript();
  const isPending = submitMut.isPending;
  const cfg = TYPE_CONFIG[taskType]!;

  const currentDefaultVoice =
    LANG_OPTIONS.find((l) => l.value === lang)?.defaultVoiceId ??
    "zh-CN-YunjianNeural";

  // FP1-5: 试听 voice_id —— 显式选择优先；「默认」(voiceId=null) 走后端解析的
  // 真实默认音色（doubao:<speaker>/preset:146，与成品一致）；仅当接口不可达时
  // 才退化到 types.ts LANG_OPTIONS 的静态 edge-tts 值（兜底降级，评审预案 #8）。
  // Plan B：ViMax 的「默认」试听走 ViMax 默认解析（zh 兜底不是 preset:146）
  const { data: resolvedDefaultVoice } = useDefaultVoice(
    lang,
    taskType === "vimax" ? "vimax" : undefined,
  );
  // C7：follow_project 哨兵 = 未显式（跟随项目）——试听回落后端解析默认，同 null 语义
  const explicitVoiceId =
    voiceId !== null && voiceId !== FOLLOW_PROJECT ? voiceId : null;
  const previewVoiceId =
    explicitVoiceId ?? resolvedDefaultVoice ?? currentDefaultVoice;

  // Auto-select uploaded BGM once mutation succeeds
  useEffect(() => {
    if (bgmUploadMut.isSuccess && bgmUploadMut.data?.id) {
      setBgm(bgmUploadMut.data.id);
      bgmUploadMut.reset();
    }
  }, [bgmUploadMut]);

  // Task 19（D2）：草稿与正式提交共用一套校验 + 一份 payload 构造。
  //
  // 修复前：2 万字守卫只在 handleSubmit 里，草稿按钮自己抄了一份 buildCreatePayload
  // 调用并**漏了 secondsPerFrame** → 同样的表单，存草稿和直接生成算出的帧时长不同；
  // 超长脚本存草稿还必吃后端 422（schemas.py script max_length=20000）。
  const validateForm = (): boolean => {
    if (mode === "fixed" && script.length > 20000) {
      toast.error(
        `脚本当前 ${script.length} 字，超过 20000 字上限，请精简后再提交`,
      );
      return false;
    }
    // Plan B（2026-10-19，D4）：ViMax + preset（显式选择或类型切换残留）→ 提交
    // 阻断（后端 /create 也会 422，前端先拦给可行动提示）。
    if (vimaxVoiceBlocked(taskType, voiceId)) {
      toast.error("ViMax 任务不支持 360 音色，请改选豆包或 Edge 音色后再提交");
      return false;
    }
    if (targetDurationInvalid) {
      toast.error(
        `目标时长需在 ${TARGET_DURATION_MIN}–${TARGET_DURATION_MAX} 秒之间，或留空自动`,
      );
      return false;
    }
    if (secondsPerFrameInvalid) {
      toast.error(
        `每图时长需在 ${SECONDS_PER_FRAME_MIN}–${SECONDS_PER_FRAME_MAX} 秒之间，或留空自动`,
      );
      return false;
    }
    return true;
  };

  // Task 6：仅 ai_image/ai_video 有模型选择；哨兵默认项映射回 null（不指定）
  const providerField = providerFieldForTaskType(taskType);
  const providerOptions = buildProviderOptions(
    providerField === "image"
      ? providersQuery.data?.image
      : providerField === "video"
        ? providersQuery.data?.video
        : undefined,
  );
  const providerForPayload = providerValueToPayload(selectedProvider);

  // M5（2026-09-11 二轮审查）：提取态守卫——用户手动改贴新脚本（【图片N】标记集
  // 与提取快照不一致）时不再携带旧 extract_id（错配进旧 docx 图 / 静默丢图）。
  // 标记集一致 = 用户只是挪动/微调标记（图仍可按 ref 认领），保留。
  const extractMarkersMatch = (): boolean => {
    if (!scriptExtract) return true; // 无提取态不拦
    const markerSet = (s: string) =>
      new Set([...s.matchAll(/【\s*图片\s*(\d+)\s*】/g)].map((m) => m[1]));
    const a = markerSet(script);
    const b = markerSet(scriptExtract.text ?? "");
    return a.size === b.size && [...a].every((x) => b.has(x));
  };

  const currentPayload = () =>
    buildCreatePayload({
      taskType,
      mode,
      title,
      script,
      topic,
      lang,
      aspect,
      style,
      subtitleStyle,
      bgm,
      voiceId: voiceId ?? null,
      outputFormat,
      quality,
      ttsSpeed,
      bgmVolume: bgmVolume / 100,
      bgmDucking,
      imageProvider: providerField === "image" ? providerForPayload : null,
      videoProvider: providerField === "video" ? providerForPayload : null,
      courseProjectId,
      targetDuration: resolveTargetDurationForPayload(
        taskType,
        mode,
        targetDuration,
      ),
      secondsPerFrame: resolveSecondsPerFrameForPayload(mode, secondsPerFrame),
      // T3：默认 30 的省略逻辑在 buildCreatePayload（所有调用方一致）
      graphicRatio,
      // auto_render 默认 false → 出分镜草稿，不直渲
      // T7/T6（独立管线）：仅 remotion_course 消费
      courseParseMode:
        taskType === "remotion_course" ? courseParseMode : undefined,
      scriptExtractId:
        taskType === "remotion_course" && extractMarkersMatch()
          ? (scriptExtract?.extractId ?? null)
          : null,
    });

  // T6（决策 B）+ M11（2026-09-11 审查）：暂存过期事前拦截——提交/存草稿共用
  //（修前只挂 handleSubmit，草稿路径仍把过期 extract_id 发出去）。
  // PR-1 purity 修复：Date.now() 是渲染期不纯调用，改为惰性初值 + 到期定时翻转——
  // 语义比旧的「等下次渲染才采样」更准（到期即翻 true，不等渲染）。
  const extractExpiresAtMs = scriptExtract
    ? new Date(scriptExtract.expiresAt).getTime()
    : null;
  const [nowTs, setNowTs] = useState(() => Date.now());
  useEffect(() => {
    if (extractExpiresAtMs === null) return;
    // 已过期（remain ≤ 0）退化为 1ms 定时——保持 setState 全走异步定时器，
    // 不落 set-state-in-effect 的同步 setState 模式。
    const timer = setTimeout(
      () => setNowTs(Date.now()),
      Math.max(extractExpiresAtMs - Date.now(), 0) + 1,
    );
    return () => clearTimeout(timer);
  }, [extractExpiresAtMs]);
  const extractExpired =
    extractExpiresAtMs !== null && extractExpiresAtMs < nowTs;

  const handleSaveDraft = () => {
    if (!validateForm()) return;
    if (extractExpired) {
      toast.error("配图暂存已过期，请重新上传文档以提取配图，再保存草稿");
      return;
    }
    if (scriptExtract && !extractMarkersMatch()) {
      toast.info(
        "脚本已修改且图片标记变化，文档配图未随草稿提交——如需配图请重新上传文档",
      );
    }
    submitMut.mutate(currentPayload(), {
      onSuccess: () => onOpenChange(false),
    });
  };

  const handleSubmit = () => {
    // T-F1（D1）：chat-handoff 分支删除——「文字动效」提交即走 remotion_course
    // 管线（/create → 分镜 → 渲染）。Agent Chat 创作能力保留在 Agent 工作台
    // （course-video agent，不经本页）。
    // P1-U1: 脚本字数前置拦截——后端 schemas.py script max_length=20000，此前前端不拦
    // → fixed 模式超长脚本必 422（ai-video T11 已加 toast，但仍需往返）。提交前就地拦截。
    if (!validateForm()) return;
    // T6（决策 B）：暂存过期事前拦截——把「丢图」从事后留痕变成事前可防。
    if (extractExpired) {
      toast.error("配图暂存已过期，请重新上传文档以提取配图，再提交任务");
      return;
    }
    if (scriptExtract && !extractMarkersMatch()) {
      toast.info(
        "脚本已修改且图片标记变化，文档配图未随任务提交——如需配图请重新上传文档",
      );
    }
    submitMut.mutate(currentPayload(), {
      onSuccess: (tasks) => {
        onOpenChange(false);
        // 跳到分镜页：用户在分镜页编辑/确认后点「开始渲染」才直渲
        const firstId = tasks?.[0]?.id;
        if (firstId) router.push(`/workspace/video-gen/${firstId}/storyboard`);
      },
    });
  };

  const handleClose = () => {
    audioRef.current?.pause();
    audioRef.current = null;
    submitMut.reset();
    bgmUploadMut.reset();
    setCourseProjectId(undefined);
    // M5（2026-09-11 二轮审查）：对话框恒挂载——关闭即重置提取态，防下次打开
    // 残留旧 extract_id / pdf 提示 / 解析档位（错配用户素材）。
    setScriptExtract(null);
    setPdfNote(false);
    setCourseParseMode("faithful");
    onOpenChange(false);
  };

  // Reset input when switching type
  const handleTypeChange = (v: string) => {
    const next = v as typeof taskType;
    setTaskType(next);
    // A6（2026-09-11 产品确认）：切到文字动效时比例 state 归一 16:9——比例 UI 虽已
    // 整块隐藏，但默认 "9:16" 会残留在 state/payload（此前仅载荷层兜底转 16:9）。
    if (next === "remotion_course") {
      setAspect("16:9");
    }
    // Task 6：切换视频类型必须清空已选模型 —— 生图与生视频是两个独立命名空间，
    // 残留的旧选择（如 ai_image 选了 nano-banana 后切到 ai_video）会被后端白名单
    // 校验拒成 422。清空 = 回到「跟随默认」。
    setSelectedProvider(null);
    // T1（2026-08-18）：风格按引擎分表（ai_image 图片表 / ai_video 视频表 /
    // vimax 并集），切类型时残留值可能不在新类型选项集（如 ai_video 的
    // 「纸艺剧场」切到 ai_image）→ 回落 default，与清空模型同语义。
    setStyle(styleAfterTypeChange(next, style));
    const newCfg = TYPE_CONFIG[v];
    if (!newCfg?.supportsGenerate && mode === "generate") {
      setMode("fixed");
    }
  };

  // Switch language → reset voice to default for that lang
  const handleLangChange = (newLang: string) => {
    setLang(newLang);
    const defaultVoice =
      LANG_OPTIONS.find((l) => l.value === newLang)?.defaultVoiceId ?? null;
    // C7：绑项目时语言切换重置为「跟随项目」（而非显式 lang 默认——那会压过项目音色）
    setVoiceId(courseProjectId ? FOLLOW_PROJECT : defaultVoice);
  };

  const canSubmit =
    mode === "generate" ? topic.trim().length > 0 : script.trim().length > 0;

  const targetDurationVisible = isTargetDurationVisible(taskType, mode);
  const targetDurationInvalid =
    targetDurationVisible && !isValidTargetDuration(targetDuration);
  const secondsPerFrameVisible = mode === "generate";
  const secondsPerFrameValue = Number(secondsPerFrame);
  const secondsPerFrameInvalid =
    secondsPerFrameVisible &&
    secondsPerFrame !== "" &&
    (!Number.isFinite(secondsPerFrameValue) ||
      secondsPerFrameValue < 3 ||
      secondsPerFrameValue > 8);

  return {
    taskType,
    setTaskType,
    mode,
    setMode,
    courseParseMode,
    setCourseParseMode,
    scriptExtract,
    setScriptExtract,
    pdfNote,
    setPdfNote,
    script,
    setScript,
    title,
    setTitle,
    topic,
    setTopic,
    aspect,
    setAspect,
    style,
    setStyle,
    subtitleStyle,
    setSubtitleStyle,
    bgm,
    setBgm,
    bgmDucking,
    setBgmDucking,
    voiceId,
    setVoiceId,
    lang,
    setLang,
    outputFormat,
    setOutputFormat,
    selectedProvider,
    setSelectedProvider,
    quality,
    setQuality,
    targetDuration,
    setTargetDuration,
    targetDurationInvalid,
    secondsPerFrameInvalid,
    secondsPerFrame,
    setSecondsPerFrame,
    graphicRatio,
    setGraphicRatio,
    advancedOpen,
    setAdvancedOpen,
    ttsSpeed,
    setTtsSpeed,
    bgmVolume,
    setBgmVolume,
    courseProjectId,
    setCourseProjectId,
    courseProjects,
    projectTtsSpeed,
    playingAudio,
    setPlayingAudio,
    audioRef,
    bgmUploadRef,
    scriptUploadRef,
    togglePreview,
    submitMut,
    router,
    voicesQuery,
    bgmQuery,
    providersQuery,
    bgmUploadMut,
    scriptExtractMut,
    isPending,
    cfg,
    currentDefaultVoice,
    resolvedDefaultVoice,
    explicitVoiceId,
    previewVoiceId,
    validateForm,
    providerField,
    providerOptions,
    providerForPayload,
    extractMarkersMatch,
    currentPayload,
    extractExpired,
    handleSaveDraft,
    handleSubmit,
    handleClose,
    handleTypeChange,
    handleLangChange,
    canSubmit,
  };
}

/**
 * 2026-10-06 拆分批：配音选择 + 试听行（自 CreateTaskDialog JSX 抽出）。
 * Plan B（2026-10-19，D4）：接收 taskType——ViMax 不展示 360 音色、残留 preset
 * 提示并阻断试听（提交阻断在 validateForm）；其他类型目录不变。
 */
export function DialogVoiceRow(
  props: Pick<
    ReturnType<typeof useCreateTaskForm>,
    | "taskType"
    | "mode"
    | "script"
    | "voiceId"
    | "setVoiceId"
    | "lang"
    | "courseProjectId"
    | "playingAudio"
    | "togglePreview"
    | "voicesQuery"
    | "previewVoiceId"
  >,
) {
  const {
    taskType,
    mode,
    script,
    voiceId,
    setVoiceId,
    lang,
    courseProjectId,
    playingAudio,
    togglePreview,
    voicesQuery,
    previewVoiceId,
  } = props;
  // Plan B：ViMax 过滤 360 条目；残留 preset 当前值保留为禁用提示项
  const isVimax = taskType === "vimax";
  const allVoices = voicesQuery.data ?? [];
  const voices = filterVoicesForTaskType(
    allVoices,
    isVimax ? "vimax" : undefined,
  );
  const presetResidual = isVimax && isPresetVoice(voiceId);
  return (
    <>
      {/* 配音 + 试听：独立整行，避免窄列下溢出/横向滚动 */}
      <div className="space-y-2">
        <label className="text-sm font-medium">配音</label>
        <div className="flex items-center gap-1">
          <Select
            value={voiceId ?? "default"}
            onValueChange={(v) => setVoiceId(v === "default" ? null : v)}
          >
            <SelectTrigger className="min-w-0 flex-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {/*
              C7（决策 5）：绑项目时默认「跟随项目」（项目默认音色 > lang 默认）；
              未绑项目保持「默认」（lang 默认链，现状不变）。
            */}
              {courseProjectId ? (
                <SelectItem value={FOLLOW_PROJECT}>跟随项目</SelectItem>
              ) : (
                <SelectItem value="default">默认</SelectItem>
              )}
              {presetResidual && (
                <SelectItem value={voiceId!} disabled>
                  {voiceId}（ViMax 不可用，请改选豆包/Edge）
                </SelectItem>
              )}
              {(() => {
                const groups: Record<string, typeof voices> = {};
                for (const v of voices) {
                  const provider = v.provider ?? "edge-tts";
                  (groups[provider] ??= []).push(v);
                }
                const providerLabels: Record<string, string> = {
                  "edge-tts": "Edge TTS（免费）",
                  qihu: "360 声音（高质量）",
                  doubao: "豆包 seed-tts-2.0（字节默认）",
                };
                return Object.entries(groups).map(
                  ([provider, providerVoices]) => (
                    <SelectGroup key={provider}>
                      <SelectLabel>
                        {providerLabels[provider] ?? provider}
                      </SelectLabel>
                      {providerVoices.map((v) => (
                        <SelectItem key={v.id} value={v.id}>
                          {v.name} ({v.gender})
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  ),
                );
              })()}
            </SelectContent>
          </Select>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-9 w-9 shrink-0"
            disabled={presetResidual}
            onClick={() => {
              // FP1-5: 「默认」时 vid 为后端解析值（doubao/preset），试听=成品声
              const vid = previewVoiceId;
              const params = new URLSearchParams();
              if (mode === "fixed" && script.trim()) {
                params.set("text", script.slice(0, 100));
              }
              // 边界收口（2026-10-19 P1-1）：ViMax 试听带 task_type——后端按策略
              // 走（缺字节 key 的 doubao 直落 Edge），不触 360；其他类型不变。
              if (taskType === "vimax") {
                params.set("task_type", "vimax");
              }
              const qs = params.toString();
              togglePreview(
                `/api/video/voices/${encodeURIComponent(vid)}/preview${qs ? `?${qs}` : ""}`,
              );
            }}
            title="试听配音"
          >
            <Volume2
              className={cn(
                "h-4 w-4",
                playingAudio?.includes("/voices/") && "text-indigo-500",
              )}
            />
          </Button>
        </div>
        {/*
        C7（决策 5）：绑项目 + 显式选其他音色 → 提示（一次性、无二次确认）。
      */}
        {courseProjectId && voiceId !== null && voiceId !== FOLLOW_PROJECT && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            将使用所选音色，不再沿用课程项目设置。
          </p>
        )}
        {presetResidual && (
          <p
            data-testid="vimax-preset-blocked-hint"
            className="text-xs text-amber-600 dark:text-amber-400"
          >
            ViMax 任务不支持 360 音色（{voiceId}），请改选豆包或 Edge
            音色后再提交。
          </p>
        )}
      </div>
    </>
  );
}

/**
 * 2026-10-06 拆分批：高级设置折叠区（BGM/语速/比例/模型/字幕样式等）。
 */
function TargetDurationField({
  value,
  onChange,
  invalid,
  placeholder,
  hint,
}: {
  value: number | "";
  onChange: (value: number | "") => void;
  invalid: boolean;
  placeholder: string;
  hint?: string;
}) {
  return (
    <div className="space-y-2">
      <label className="text-sm font-medium">目标时长（秒，可选）</label>
      <input
        type="number"
        min={TARGET_DURATION_MIN}
        max={TARGET_DURATION_MAX}
        placeholder={placeholder}
        value={value}
        onChange={(e) =>
          onChange(e.target.value === "" ? "" : Number(e.target.value))
        }
        className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
      />
      {invalid && (
        <p className="text-destructive text-xs">
          请输入 {TARGET_DURATION_MIN}–{TARGET_DURATION_MAX} 秒，或留空自动
        </p>
      )}
      {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
    </div>
  );
}

function DialogAdvancedSettings(
  props: Pick<
    ReturnType<typeof useCreateTaskForm>,
    | "taskType"
    | "mode"
    | "style"
    | "setStyle"
    | "subtitleStyle"
    | "setSubtitleStyle"
    | "bgm"
    | "setBgm"
    | "bgmDucking"
    | "setBgmDucking"
    | "outputFormat"
    | "setOutputFormat"
    | "selectedProvider"
    | "setSelectedProvider"
    | "quality"
    | "setQuality"
    | "targetDuration"
    | "setTargetDuration"
    | "targetDurationInvalid"
    | "secondsPerFrameInvalid"
    | "secondsPerFrame"
    | "setSecondsPerFrame"
    | "graphicRatio"
    | "setGraphicRatio"
    | "advancedOpen"
    | "setAdvancedOpen"
    | "ttsSpeed"
    | "setTtsSpeed"
    | "bgmVolume"
    | "setBgmVolume"
    | "courseProjectId"
    | "projectTtsSpeed"
    | "playingAudio"
    | "bgmUploadRef"
    | "togglePreview"
    | "bgmQuery"
    | "bgmUploadMut"
    | "isPending"
    | "providerField"
    | "providerOptions"
  >,
) {
  const {
    taskType,
    mode,
    style,
    setStyle,
    subtitleStyle,
    setSubtitleStyle,
    bgm,
    setBgm,
    bgmDucking,
    setBgmDucking,
    outputFormat,
    setOutputFormat,
    selectedProvider,
    setSelectedProvider,
    quality,
    setQuality,
    targetDuration,
    setTargetDuration,
    targetDurationInvalid,
    secondsPerFrameInvalid,
    secondsPerFrame,
    setSecondsPerFrame,
    graphicRatio,
    setGraphicRatio,
    advancedOpen,
    setAdvancedOpen,
    ttsSpeed,
    setTtsSpeed,
    bgmVolume,
    setBgmVolume,
    courseProjectId,
    projectTtsSpeed,
    playingAudio,
    bgmUploadRef,
    togglePreview,
    bgmQuery,
    bgmUploadMut,
    isPending,
    providerField,
    providerOptions,
  } = props;
  return (
    <>
      {/* ── Advanced settings (collapsible) ─────────────────── */}
      <div className="border-t pt-3">
        <button
          type="button"
          aria-expanded={advancedOpen}
          onClick={() => setAdvancedOpen((v) => !v)}
          className="text-muted-foreground hover:text-foreground flex w-full items-center gap-1 text-xs font-medium transition-colors"
        >
          {advancedOpen ? (
            <ChevronUp className="h-3 w-3" />
          ) : (
            <ChevronDown className="h-3 w-3" />
          )}
          高级设置
        </button>
        {advancedOpen && (
          <div className="space-y-4 pt-3">
            {/* 背景音乐：独立整行（2026-07-31，原 grid-cols-3 窄列溢出重叠） */}
            <div className="space-y-2">
              <label className="text-sm font-medium">背景音乐</label>
              <div className="flex items-center gap-2">
                <BgmPicker
                  builtIn={bgmQuery.data?.built_in ?? []}
                  custom={bgmQuery.data?.custom ?? []}
                  value={bgm}
                  onChange={setBgm}
                  playingUrl={playingAudio}
                  onTogglePreview={togglePreview}
                  className="min-w-0 flex-1"
                />
                {bgm && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-9 w-9 shrink-0"
                    onClick={() =>
                      togglePreview(`/api/video/bgm/${bgm}/preview`)
                    }
                    title="试听已选 BGM"
                  >
                    <Volume2
                      className={cn(
                        "h-4 w-4",
                        playingAudio?.includes("/bgm/") && "text-indigo-500",
                      )}
                    />
                  </Button>
                )}
                {bgm && (
                  <Button
                    type="button"
                    variant={bgmDucking ? "default" : "outline"}
                    size="sm"
                    className="h-9 shrink-0 text-xs"
                    onClick={() => setBgmDucking((v) => !v)}
                    title={
                      bgmDucking
                        ? "智能降噪开启：人声出现时自动降低 BGM"
                        : "智能降噪关闭：BGM 全程固定音量"
                    }
                  >
                    降噪{bgmDucking ? "开" : "关"}
                  </Button>
                )}
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className={cn(
                    "h-9 w-9 shrink-0",
                    bgmUploadMut.isError && "text-red-500",
                  )}
                  onClick={() => bgmUploadRef.current?.click()}
                  disabled={bgmUploadMut.isPending}
                  title={
                    bgmUploadMut.isError
                      ? `上传失败: ${bgmUploadMut.error?.message}`
                      : "上传背景音乐"
                  }
                >
                  {bgmUploadMut.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : bgmUploadMut.isError ? (
                    <XCircle className="h-4 w-4" />
                  ) : (
                    <Upload className="h-4 w-4" />
                  )}
                </Button>
                <input
                  ref={bgmUploadRef}
                  type="file"
                  accept="audio/*"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    bgmUploadMut.mutate(file);
                    e.target.value = "";
                  }}
                />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-2">
                <label className="text-sm font-medium">字幕样式</label>
                <Select
                  value={subtitleStyle}
                  onValueChange={(v) => {
                    // 不用 `as typeof subtitleStyle` 蒙混——那正是「无字幕」
                    // 未被下游处理却无人发现的原因。这里显式校验。
                    // C7：follow_project 哨兵是合法受控值（后端按未显式处理）。
                    if (v === FOLLOW_PROJECT || isSubtitleStyle(v))
                      setSubtitleStyle(v);
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {courseProjectId && (
                      <SelectItem value={FOLLOW_PROJECT}>跟随项目</SelectItem>
                    )}
                    {SUBTITLE_STYLES.map((s) => (
                      <SelectItem key={s.value} value={s.value}>
                        <span className="flex items-center gap-2">
                          <span>{s.label}</span>
                          {s.value !== "none" && (
                            <span className="rounded bg-neutral-800 px-1.5 py-0.5">
                              <span className={s.previewClassName}>
                                字幕预览
                              </span>
                            </span>
                          )}
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {/*
                C7（决策 5）：绑项目 + 显式选其他字幕样式 → 提示（一次性、无二次确认）。
              */}
                {courseProjectId &&
                  subtitleStyle !== FOLLOW_PROJECT &&
                  subtitleStyle !== "default" && (
                    <p className="text-xs text-amber-600 dark:text-amber-400">
                      将使用所选字幕样式，不再沿用课程项目设置。
                    </p>
                  )}
              </div>
              {/*
              Task 6（2026-08-03）：remotion 不显示风格下拉——风格是生成
              提示词前缀，remotion 无生成步骤（planner 对它直接 return），
              展示等于承诺一个静默失效的旋钮。
            */}
              {shouldShowStyleSelector(taskType) && (
                <div className="space-y-2">
                  <label className="text-sm font-medium">风格</label>
                  {/*
                  T1（2026-08-18 五风格化）：任务级按引擎分表——ai_image 出
                  图片五风格、ai_video 出视频五风格（vimax/ai_visual 并集
                  7 项），两表不同（三风格两用、四档专属）；旧 7 项移出
                  任务级 UI，受控旧值由 taskStyleOptions 追加承载项兜底
                  （存量旧风格任务不回落清空）。项目级 2026-08-19 同步收
                  敛为不设置 + 推荐 7，存量旧值由 projectStyleOptions 动态
                  承载（见 style-presets.ts）。
                */}
                  <Select value={style} onValueChange={setStyle}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {/*
                      C7（决策 3）：绑项目时注入「跟随项目」默认项；显式选其他
                      风格 = 完全脱离项目资产（含角色三视图），下方警告条告知。
                    */}
                      {courseProjectId && (
                        <SelectItem value={FOLLOW_PROJECT}>跟随项目</SelectItem>
                      )}
                      {taskStyleOptions(taskType, style).map((p) => {
                        // 风格缩略图（2026-09-09 设计 §3.4）：仿字幕样式
                        // 内嵌预览先例，一级 7 key 项内嵌小图；default /
                        // 跟随项目 / 存量承载项无图纯文字。
                        const thumbSrc = stylePreviewImage(p.value);
                        return (
                          <SelectItem key={p.value} value={p.value}>
                            <span className="flex items-center gap-2">
                              {thumbSrc && <StyleThumb src={thumbSrc} />}
                              <span>{p.label}</span>
                            </span>
                          </SelectItem>
                        );
                      })}
                    </SelectContent>
                  </Select>
                  {/*
                  C7（决策 3 全断口径，v7 确认文案）：绑项目 + 显式选其他风格 →
                  警告条（非 toast、一次性展示、无二次确认）。
                */}
                  {courseProjectId &&
                    style !== FOLLOW_PROJECT &&
                    style !== "default" && (
                      <div className="rounded-lg border border-amber-300/50 bg-amber-50 p-3 dark:bg-amber-950/30">
                        <p className="text-xs text-amber-700 dark:text-amber-300">
                          选择该风格后，本任务将完全不使用课程项目资产（含风格锚图与角色三视图）。
                        </p>
                      </div>
                    )}
                </div>
              )}
            </div>
            {/*
            风格选中预览（2026-09-09 设计 §3.4，2026-09-10 修订）：通栏放
            grid-cols-3 下方独占一行（列内 16:9 图仅 ~200px 宽，核心价值
            打折）；remotion 等无风格选择器的类型整体不渲染。
          */}
            {shouldShowStyleSelector(taskType) && (
              <StylePreviewPanel styleValue={style} />
            )}

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <label className="text-sm font-medium">输出格式</label>
                <Select
                  value={outputFormat}
                  onValueChange={(v) =>
                    setOutputFormat(v as typeof outputFormat)
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {OUTPUT_FORMAT_OPTIONS.map((o) => (
                      <SelectItem key={o.value} value={o.value}>
                        {o.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {/* T12：remotion_course 隐藏画质——props 不消费 quality（模板原生 1080p）。 */}
              {taskType !== "remotion_course" && (
                <div className="space-y-2">
                  <label className="text-sm font-medium">画质</label>
                  <Select
                    value={quality}
                    onValueChange={(v) => setQuality(v as typeof quality)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="draft">草稿（快速）</SelectItem>
                      <SelectItem value="preview">快速预览（360p）</SelectItem>
                      <SelectItem value="standard">标准</SelectItem>
                      <SelectItem value="high">高清（较慢）</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>

            {/*
            Task 6（2026-08-01 反馈 8）：生成模型选择。
            可选值由 GET /api/video/providers 动态给出（可用性 = key 是否配置），
            不前端硬编码。label 只给模型名（决策 6/7 对 label 依旧成立）。
            E5（2026-08-21 360 主力切换）：二级 note 行按后端 annotations
            元数据渲染（决策 7/D5 新口径：实测结论 / 「未检测」，不推断）。
            仅 ai_image / ai_video 有此选项（remotion 不走后端；vimax 自带引擎链）。
          */}
            {providerField && providerOptions.length > 0 && (
              <div className="space-y-2">
                <label className="text-sm font-medium">生成模型</label>
                <Select
                  value={selectedProvider ?? DEFAULT_PROVIDER_VALUE}
                  onValueChange={(v) =>
                    setSelectedProvider(v === DEFAULT_PROVIDER_VALUE ? null : v)
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {providerOptions.map((o) => (
                      <SelectItem key={o.value} value={o.value}>
                        <span className="flex flex-col items-start gap-0.5">
                          <span>{o.label}</span>
                          {o.note && (
                            <span className="text-muted-foreground text-xs">
                              {o.note}
                            </span>
                          )}
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            {/*
            T3（2026-09-01）：任务级图文镜占比上限。仅 ai_image/ai_video 消费
            （vimax 引擎层不走 visual_routing 护栏）。默认 30%（推荐）守
            「防半部片翻 PPT」回调史；说明文案澄清「上限≠保障」——选项只是
            不超过该值的约束，实际占比由调度表按课程内容规划，内容没要点时
            选 70% 也不会硬凑。
          */}
            {(taskType === "ai_image" || taskType === "ai_video") && (
              <div className="space-y-2">
                <label className="text-sm font-medium">图文镜占比上限</label>
                <Select
                  value={String(graphicRatio)}
                  onValueChange={(v) =>
                    setGraphicRatio(Number(v) as 30 | 40 | 50 | 70)
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="30">30%（推荐）</SelectItem>
                    <SelectItem value="40">40%</SelectItem>
                    <SelectItem value="50">50%</SelectItem>
                    <SelectItem value="70">70%</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-muted-foreground text-xs">
                  图文镜（数字/要点卡片画面）占比上限——实际占比由课程内容
                  决定，不会超过所选值
                </p>
              </div>
            )}

            {/* 决策 1（2026-07-31）：移除「生成数量」下拉。它提交的 N 个任务除
              内部 version_index 外完全相同，5 倍成本换来近乎一样的成片，且
              实际无人使用。需要第二版时再点一次「生成视频」即可。
              移除后本行只剩两个 generate 模式字段 → 整行随该模式一起显示，
              列数 3→2（否则 fixed 模式会留一个空网格）。*/}
            {mode === "generate" && (
              <div className="grid grid-cols-2 gap-3">
                <>
                  <TargetDurationField
                    value={targetDuration}
                    onChange={setTargetDuration}
                    invalid={targetDurationInvalid}
                    placeholder="如 30"
                    hint="引导镜数下限"
                  />
                  <div className="space-y-2">
                    <label className="text-sm font-medium">
                      每图时长（秒，可选；密度控件 3-8）
                    </label>
                    <input
                      type="number"
                      min={SECONDS_PER_FRAME_MIN}
                      max={SECONDS_PER_FRAME_MAX}
                      placeholder="5"
                      value={secondsPerFrame}
                      onChange={(e) =>
                        setSecondsPerFrame(
                          e.target.value === "" ? "" : Number(e.target.value),
                        )
                      }
                      className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
                    />
                    {secondsPerFrameInvalid && (
                      <p className="text-destructive text-xs">
                        请输入 {SECONDS_PER_FRAME_MIN}–{SECONDS_PER_FRAME_MAX}
                        秒，或留空自动
                      </p>
                    )}
                  </div>
                </>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                {/*
                T-E11（D6 review #1）：语速加「跟随」态——绑项目=项目语速 >
                config 缺省（1.2）；未绑=config。修前滑条恒显式 1.0 下发，
                项目级语速在 UI 主路径被架空。交互与项目页默认语速控件一致。
              */}
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">语速</label>
                  <label className="text-muted-foreground flex items-center gap-1.5 text-xs">
                    <input
                      type="checkbox"
                      checked={ttsSpeed === null}
                      onChange={(e) =>
                        // 条目 39（2026-09-12 缺陷批）：取消跟随不再硬编码重置
                        // 1.0——修前非 null 即显式下发 tts_speed:1.0，三级链命中
                        // 任务显式级，压过项目语速和 config 缺省（配音真按 1.0x 走）。
                        // 绑项目 → 项目 tts_speed（useCourseProjects 缓存查）；
                        // 未绑 → 1.2（= backend config.py:185 TTS_SPEED 缺省——
                        // 后端无 defaults 端点，硬编码以注释锚定防漂移）。
                        setTtsSpeed(
                          e.target.checked ? null : (projectTtsSpeed ?? 1.2),
                        )
                      }
                      className="accent-indigo-500"
                    />
                    {courseProjectId ? "跟随项目" : "跟随默认"}
                  </label>
                </div>
                <SpeedSliderControl value={ttsSpeed} onChange={setTtsSpeed} />
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">BGM 音量</label>
                  <span className="text-muted-foreground text-xs">
                    {bgmVolume}%
                  </span>
                </div>
                <VolumeSliderControl
                  value={bgmVolume}
                  onChange={setBgmVolume}
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

/**
 * 2026-10-06 拆分批：脚本输入区（固定脚本 + 文档上传/解析档位段）。
 */
function DialogScriptInput(
  props: Pick<
    ReturnType<typeof useCreateTaskForm>,
    | "taskType"
    | "mode"
    | "courseParseMode"
    | "setCourseParseMode"
    | "scriptExtract"
    | "setScriptExtract"
    | "pdfNote"
    | "setPdfNote"
    | "script"
    | "setScript"
    | "aspect"
    | "setAspect"
    | "lang"
    | "scriptUploadRef"
    | "scriptExtractMut"
    | "isPending"
    | "cfg"
    | "handleLangChange"
  >,
) {
  const {
    taskType,
    mode,
    courseParseMode,
    setCourseParseMode,
    scriptExtract,
    setScriptExtract,
    pdfNote,
    setPdfNote,
    script,
    setScript,
    aspect,
    setAspect,
    lang,
    scriptUploadRef,
    scriptExtractMut,
    isPending,
    cfg,
    handleLangChange,
  } = props;
  return (
    <>
      {mode === "fixed" && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium">脚本</label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1 text-xs"
              onClick={() => scriptUploadRef.current?.click()}
              disabled={scriptExtractMut.isPending}
            >
              {scriptExtractMut.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Upload className="h-3 w-3" />
              )}
              上传文档
            </Button>
            <input
              ref={scriptUploadRef}
              type="file"
              accept=".docx,.doc,.txt,.md,.pdf"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) {
                  // T6（独立管线）：remotion_course 的 docx 走原位图片提取
                  // （文本含【图片N】标记 + staging 暂存）；pdf 也带 with_images
                  // （M10：触发后端"图片不支持自动分配"提示），其余走 markitdown 口径不变。
                  const isCourse = taskType === "remotion_course";
                  const isDocx = f.name.toLowerCase().endsWith(".docx");
                  const isPdf = f.name.toLowerCase().endsWith(".pdf");
                  scriptExtractMut.mutate(
                    { file: f, withImages: isCourse && (isDocx || isPdf) },
                    {
                      onSuccess: (data) => {
                        setPdfNote(isCourse && isPdf);
                        if (data.text) setScript(data.text);
                        if (
                          isCourse &&
                          "extract_id" in data &&
                          data.extract_id
                        ) {
                          setScriptExtract({
                            extractId: data.extract_id,
                            expiresAt: data.expires_at,
                            imageCount: data.images?.length ?? 0,
                            ignoredCount: data.ignored_images ?? 0,
                            text: data.text,
                          });
                        } else {
                          setScriptExtract(null);
                          if (isCourse && data.warning) {
                            toast.info(data.warning);
                          }
                        }
                      },
                    },
                  );
                }
                e.target.value = "";
              }}
            />
          </div>
          <Textarea
            value={script}
            onChange={(e) => setScript(e.target.value)}
            placeholder={cfg.placeholder}
            rows={5}
          />
          <p className="text-muted-foreground text-xs">
            每句话对应一个画面，用句号或换行分隔；可上传 docx/doc/txt/md/pdf
            文档自动解析
            <span
              className={cn("ml-2", script.length > 20000 && "text-red-500")}
            >
              {script.length} / 20000 字
              {script.length > 20000 && "（超限，请缩短）"}
            </span>
          </p>
          {taskType === "remotion_course" && (
            <p className="text-muted-foreground text-xs">
              文档中的【图片N】标记是配图位置，请勿删除；标记可随文字移动。
            </p>
          )}
          {taskType === "remotion_course" && pdfNote && (
            <p className="text-muted-foreground text-xs">
              PDF 中的图片暂不支持自动分配，可在分镜编辑器手动上传。
            </p>
          )}
          {taskType === "remotion_course" && scriptExtract && (
            <div className="text-muted-foreground rounded border border-emerald-500/40 bg-emerald-500/5 p-2 text-xs">
              检测到 {scriptExtract.imageCount}{" "}
              张配图，创建后将自动分配到对应分镜
              {scriptExtract.ignoredCount > 0 &&
                `（其中 ${scriptExtract.ignoredCount} 张过小或格式不支持已忽略）`}
              <span className="ml-1">
                ·配图暂存将于{" "}
                {new Date(scriptExtract.expiresAt).toLocaleTimeString()} 过期
              </span>
            </div>
          )}
        </div>
      )}

      {/* T7（决策 13/15 配套）：解析方式 radio——仅保真/改写两档，不设零 AI 档 */}
      {taskType === "remotion_course" && mode === "fixed" && (
        <div className="space-y-2">
          <label className="text-sm font-medium">解析方式</label>
          <div className="space-y-1">
            <label className="flex items-start gap-2 text-sm font-normal">
              <input
                type="radio"
                name="course-parse-mode"
                className="mt-1"
                checked={courseParseMode === "faithful"}
                onChange={() => setCourseParseMode("faithful")}
              />
              保真解析（默认）——只优化表达使其适合口播，不改大意与数据
            </label>
            <label className="flex items-start gap-2 text-sm font-normal">
              <input
                type="radio"
                name="course-parse-mode"
                className="mt-1"
                checked={courseParseMode === "rewrite"}
                onChange={() => setCourseParseMode("rewrite")}
              />
              AI 改写——改写为口语讲解稿
            </label>
          </div>
          <div className="text-muted-foreground bg-muted/30 space-y-1 rounded border p-2 text-xs">
            <p>同一段原文在不同档下的旁白示例（两档都不改数字）：</p>
            <p>原文：本季度营收达到 300 万元，同比增长 20%。</p>
            <p>保真：本季度营收达到了 300 万元，同比增长 20%。</p>
            <p>
              改写：这个季度我们的营收做到了 300 万元，比去年同期增长了 20%。
            </p>
          </div>
        </div>
      )}

      {/* T12（决策 12 配套）：话题模式移除后的出口提示 */}
      {taskType === "remotion_course" && (
        <p className="text-muted-foreground text-xs">
          只有主题没有脚本？可先用 AI 写好讲稿后粘贴到此处，或切换到「AI
          生图」用话题直接生成。
        </p>
      )}

      <div className="grid grid-cols-2 gap-3">
        {/* T12（2026-09-10 独立管线计划）：remotion_course 隐藏画面比例——渲染恒
          1920×1080（模板原生），留着是静默失效旋钮。 */}
        {taskType !== "remotion_course" && (
          <div className="space-y-2">
            <label className="text-sm font-medium">画面比例</label>
            <Select
              value={aspect}
              onValueChange={(v) => setAspect(v as typeof aspect)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="9:16">9:16 竖屏</SelectItem>
                <SelectItem value="16:9">16:9 横屏</SelectItem>
                <SelectItem value="1:1">1:1 方形</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
        <div className="space-y-2">
          <label className="text-sm font-medium">语言</label>
          <Select value={lang} onValueChange={handleLangChange}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LANG_OPTIONS.map((l) => (
                <SelectItem key={l.value} value={l.value}>
                  {l.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
    </>
  );
}

export function CreateTaskDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  // T-E5（R20）：联合补全 remotion_course——修前 4 值联合 + as 强转把
  // remotion_course 挤到联合外（Select 值实际可为它）。
  const {
    taskType,
    setTaskType,
    mode,
    setMode,
    courseParseMode,
    setCourseParseMode,
    scriptExtract,
    setScriptExtract,
    pdfNote,
    setPdfNote,
    script,
    setScript,
    title,
    setTitle,
    topic,
    setTopic,
    aspect,
    setAspect,
    style,
    setStyle,
    subtitleStyle,
    setSubtitleStyle,
    bgm,
    setBgm,
    bgmDucking,
    setBgmDucking,
    voiceId,
    setVoiceId,
    lang,
    setLang,
    outputFormat,
    setOutputFormat,
    selectedProvider,
    setSelectedProvider,
    quality,
    setQuality,
    targetDuration,
    setTargetDuration,
    targetDurationInvalid,
    secondsPerFrameInvalid,
    secondsPerFrame,
    setSecondsPerFrame,
    graphicRatio,
    setGraphicRatio,
    advancedOpen,
    setAdvancedOpen,
    ttsSpeed,
    setTtsSpeed,
    bgmVolume,
    setBgmVolume,
    courseProjectId,
    setCourseProjectId,
    courseProjects,
    projectTtsSpeed,
    playingAudio,
    setPlayingAudio,
    audioRef,
    bgmUploadRef,
    scriptUploadRef,
    togglePreview,
    submitMut,
    router,
    voicesQuery,
    bgmQuery,
    providersQuery,
    bgmUploadMut,
    scriptExtractMut,
    isPending,
    cfg,
    currentDefaultVoice,
    resolvedDefaultVoice,
    explicitVoiceId,
    previewVoiceId,
    validateForm,
    providerField,
    providerOptions,
    providerForPayload,
    extractMarkersMatch,
    currentPayload,
    extractExpired,
    handleSaveDraft,
    handleSubmit,
    handleClose,
    handleTypeChange,
    handleLangChange,
    canSubmit,
  } = useCreateTaskForm({ open, onOpenChange });
  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Film className="h-5 w-5 text-indigo-500" />
            创建视频任务
          </DialogTitle>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto py-4">
          {/* ── Basic settings ──────────────────────────────────── */}
          <div className="space-y-2">
            <label className="text-sm font-medium">视频类型</label>
            <Select value={taskType} onValueChange={handleTypeChange}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {/* T12（四轮修订）：两个创建 tab 间加选型指引 */}
                <p className="text-muted-foreground mt-1 text-xs">
                  有成稿/课件 → 文字动效；只有想法/主题 → AI 生图
                </p>
                {Object.entries(TYPE_CONFIG).map(([key, val]) => (
                  <SelectItem key={key} value={key}>
                    {val.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">{cfg.desc}</p>
          </div>

          {/* 2026-10-20 Task 5：任务标题（所有类型可见；空则取脚本前 30 字）。 */}
          <div className="space-y-2">
            <label className="text-sm font-medium">任务标题（可选）</label>
            <input
              type="text"
              maxLength={80}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="留空则取脚本前 30 字"
              className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
            />
          </div>

          {/* Course Project (optional) */}
          <div className="space-y-2">
            <label className="text-sm font-medium">课程项目</label>
            <CourseProjectSelect
              value={courseProjectId}
              onChange={setCourseProjectId}
            />
            <p className="text-muted-foreground text-xs">
              关联项目后自动继承全局视觉风格
            </p>
          </div>

          {cfg.supportsGenerate && (
            <div className="space-y-2">
              <label className="text-sm font-medium">生成方式</label>
              <div className="border-border/50 bg-muted/30 flex gap-1 rounded-lg border p-1">
                <button
                  type="button"
                  onClick={() => setMode("generate")}
                  className={cn(
                    "flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                    mode === "generate"
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  话题生成
                </button>
                <button
                  type="button"
                  onClick={() => setMode("fixed")}
                  className={cn(
                    "flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                    mode === "fixed"
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  固定脚本
                </button>
              </div>
            </div>
          )}

          {mode === "generate" && cfg.supportsGenerate && (
            <div className="space-y-2">
              <label className="text-sm font-medium">话题</label>
              <Textarea
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="输入一个话题（如：新员工入职培训），AI 自动生成旁白。长脚本请切到「固定脚本」模式。"
                rows={2}
              />
            </div>
          )}

          <DialogScriptInput
            taskType={taskType}
            mode={mode}
            courseParseMode={courseParseMode}
            setCourseParseMode={setCourseParseMode}
            scriptExtract={scriptExtract}
            setScriptExtract={setScriptExtract}
            pdfNote={pdfNote}
            setPdfNote={setPdfNote}
            script={script}
            setScript={setScript}
            aspect={aspect}
            setAspect={setAspect}
            lang={lang}
            scriptUploadRef={scriptUploadRef}
            scriptExtractMut={scriptExtractMut}
            isPending={isPending}
            cfg={cfg}
            handleLangChange={handleLangChange}
          />

          {/* 2026-10-20 Task 5（已拍板）：Vimax fixed 目标时长放在创建主表单，
              不折叠进高级设置；留空按草稿实际时长，显式 60 秒约 8 镜/64 秒。 */}
          {taskType === "vimax" && mode === "fixed" && (
            <TargetDurationField
              value={targetDuration}
              onChange={setTargetDuration}
              invalid={targetDurationInvalid}
              placeholder="留空按草稿实际时长"
              hint="留空按草稿实际旁白时长派生（如 35 秒 → 5 镜）；填 60 秒约 8 镜、64 秒成片。"
            />
          )}

          <DialogVoiceRow
            taskType={taskType}
            mode={mode}
            script={script}
            voiceId={voiceId}
            setVoiceId={setVoiceId}
            lang={lang}
            courseProjectId={courseProjectId}
            playingAudio={playingAudio}
            togglePreview={togglePreview}
            voicesQuery={voicesQuery}
            previewVoiceId={previewVoiceId}
          />
          <DialogAdvancedSettings
            taskType={taskType}
            mode={mode}
            style={style}
            setStyle={setStyle}
            subtitleStyle={subtitleStyle}
            setSubtitleStyle={setSubtitleStyle}
            bgm={bgm}
            setBgm={setBgm}
            bgmDucking={bgmDucking}
            setBgmDucking={setBgmDucking}
            outputFormat={outputFormat}
            setOutputFormat={setOutputFormat}
            selectedProvider={selectedProvider}
            setSelectedProvider={setSelectedProvider}
            quality={quality}
            setQuality={setQuality}
            targetDuration={targetDuration}
            setTargetDuration={setTargetDuration}
            targetDurationInvalid={targetDurationInvalid}
            secondsPerFrame={secondsPerFrame}
            setSecondsPerFrame={setSecondsPerFrame}
            secondsPerFrameInvalid={secondsPerFrameInvalid}
            graphicRatio={graphicRatio}
            setGraphicRatio={setGraphicRatio}
            advancedOpen={advancedOpen}
            setAdvancedOpen={setAdvancedOpen}
            ttsSpeed={ttsSpeed}
            setTtsSpeed={setTtsSpeed}
            bgmVolume={bgmVolume}
            setBgmVolume={setBgmVolume}
            courseProjectId={courseProjectId}
            projectTtsSpeed={projectTtsSpeed}
            playingAudio={playingAudio}
            bgmUploadRef={bgmUploadRef}
            togglePreview={togglePreview}
            bgmQuery={bgmQuery}
            bgmUploadMut={bgmUploadMut}
            isPending={isPending}
            providerField={providerField}
            providerOptions={providerOptions}
          />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            取消
          </Button>
          {/* T-F1（D1）：chat-handoff 删除后草稿按钮对全部类型可用
              （remotion_course 走正常 /create 草稿链路）。 */}
          <Button
            variant="outline"
            disabled={isPending || !canSubmit}
            onClick={handleSaveDraft}
          >
            <Pencil className="mr-1 h-4 w-4" />
            保存草稿
          </Button>
          <Button onClick={handleSubmit} disabled={isPending || !canSubmit}>
            {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            生成视频
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
