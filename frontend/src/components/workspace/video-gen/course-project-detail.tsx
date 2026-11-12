"use client";

import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Crown,
  ImagePlus,
  Package,
  Pencil,
  Save,
  Trash2,
  Upload,
  Wand2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { SpeedSliderControl } from "@/components/workspace/video-gen/audio-sliders";
import { styleAnchorImageUrl } from "@/core/video-gen/api";
import {
  buildCharacterSheetUploadFormData,
  previewUrl,
  revokePreview,
} from "@/core/video-gen/character-sheet-upload";
import {
  useCourseProjects,
  useDeleteCharacterSheet,
  useDeleteCourseProject,
  useDeletePropAsset,
  useDeleteStyleAnchor,
  useClearStyleAnchorCandidate,
  useGenerateCharacterSheet,
  useGenerateStyleAnchor,
  useSetStyleAnchor,
  useUpdateCourseProject,
  useUploadCharacterSheet,
  useUploadPropAsset,
  useReorderCharacterSheets,
  useVideoTasks,
  useVideoVoices,
} from "@/core/video-gen/hooks";
import {
  optionValueForStored,
  projectStyleOptions,
} from "@/core/video-gen/style-presets";
import { SUBTITLE_STYLES } from "@/core/video-gen/subtitle-styles";
import type { CharacterSheetData, PropAssetData } from "@/core/video-gen/types";

import {
  CharacterSheetLightbox,
  resolveCompositeImgUrl,
  resolveSheetImgUrl,
  SHEET_LABEL,
  type SheetView,
} from "./character-sheet-lightbox";
import type { ColorPalette } from "./color-palette-editor";

// Task 1（2026-08-01）：删本地副本，改引用单一来源（原与 course-project-dialog.tsx
// 完全重复，且 value 是会被原样拼进生图 prompt 的英文字面量）。

/** 15b: 读图片最短边像素（上传规格校验，<512 给 warning）。读失败返回 0。 */
function readImageMinDim(file: File): Promise<number> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(Math.min(img.naturalWidth, img.naturalHeight));
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(0);
    };
    img.src = url;
  });
}

/** T17 (2026-07-27): 仅文本角色描述（无三视图）→ 显示上传三视图引导。
 * 纯文本描述时 i2i 强参考不生效，跨镜人物一致性弱。
 * X11（2026-08-27 方案 B，用户拍板）：「角色参考」失实区块整体删除——后端全链路
 * 把 character_reference 当图片路径消费，文本无主链路消费方；函数随区块退役。 */

/** U5: 风格预设已改且项目有角色图 → 提示旧角色图基于旧风格，建议重新生成。 */
export function shouldShowStaleStyleBanner(
  originalPreset: string,
  currentPreset: string,
  sheetCount: number,
): boolean {
  // R6 (Finding 7): currentPreset 非空（已从 project 同步，非首屏初始 ""）+ 不同 + 有 sheet → 才显横幅。
  // 首屏 currentPreset="" → 不显；""→preset（原无预设→选了新预设，旧 sheet 无风格）→ 应显。
  //
  // Task 1 (2026-08-01): **两侧先归一再比较**。originalPreset 可能是存量英文
  // 字面量（'3D render'），currentPreset 已是当前 key（education_3d）——
  // 同一风格的两种写法，直接比字符串会让老项目一打开就误报「风格已改」。
  const original = optionValueForStored(originalPreset);
  const current = optionValueForStored(currentPreset);
  return sheetCount > 0 && current !== "" && original !== current;
}

/** Task 15: 角色参考图区——展示已有 + 生成(方式A)/上传(方式B)双入口 */

function CharacterSheetsSection({
  projectId,
  sheets,
  originalPreset,
  currentPreset,
}: {
  projectId: string;
  sheets: CharacterSheetData[];
  originalPreset: string;
  currentPreset: string;
}) {
  const genMut = useGenerateCharacterSheet(projectId);
  const uploadMut = useUploadCharacterSheet(projectId);
  const deleteMut = useDeleteCharacterSheet(projectId);
  const reorderMut = useReorderCharacterSheets(projectId);
  // R4 (Finding 4): 任一 mutation pending 时禁用所有行内按钮——防 mutation 成功到 refetch
  // 落地之间的下标过期竞态（reorder/delete 用下标 i，genMut 用 replace_index，过期会移/删/换错行）。
  const isAnyPending =
    reorderMut.isPending || deleteMut.isPending || genMut.isPending;
  // M1: id 寻址的排序辅助——当前顺序的 id 列表
  const sheetIds = sheets.map((x) => x.id ?? "");
  const [desc, setDesc] = useState("");
  const [features, setFeatures] = useState("");
  const [name, setName] = useState("");
  const [sizeWarn, setSizeWarn] = useState<string | null>(null);
  const uploadRef = useRef<HTMLInputElement | null>(null);
  // A1 + M1: 三视图点击放大预览（按稳定 id 寻址）
  const [lb, setLb] = useState<{ sheet: string; view: SheetView } | null>(null);
  // A2 + M1: 正在重新生成的角色表 id（预填 prompt），null=新建态
  const [editingSheetId, setEditingSheetId] = useState<string | null>(null);
  // U3 + M1: 删除确认 AlertDialog 的待删 id
  const [confirmDeleteIdx, setConfirmDeleteIdx] = useState<string | null>(null);
  // A3: 分视图选择 + 即时预览（后端要求 front/side/back 全到齐，故三张选齐后一次性上传）
  const [activeView, setActiveView] = useState<SheetView | null>(null);
  const [picked, setPicked] = useState<Partial<Record<SheetView, File>>>({});
  const [previews, setPreviews] = useState<Partial<Record<SheetView, string>>>(
    {},
  );
  // R7 (Finding 8): 卸载时 revoke 所有 preview blob URL，防泄漏。用 ref 跟踪最新 previews，
  // 空依赖 [] 确保只在卸载时跑（非每次 previews 变化——否则会 revoke 正在显示的 URL）。
  const previewsRef = useRef(previews);
  previewsRef.current = previews;
  useEffect(() => {
    return () => {
      Object.values(previewsRef.current).forEach((u) => {
        if (u) URL.revokeObjectURL(u);
      });
    };
  }, []);

  const onPickFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !activeView) return;
    const dim = await readImageMinDim(file);
    if (dim > 0 && dim < 512) {
      setSizeWarn(
        `${SHEET_LABEL[activeView]} 分辨率 <512px，可能影响一致性，已仍允许`,
      );
    }
    revokePreview(previews[activeView]);
    const url = previewUrl(file);
    setPicked((p) => ({ ...p, [activeView]: file }));
    setPreviews((p) => ({ ...p, [activeView]: url }));
  };

  const clearPicked = () => {
    Object.values(previews).forEach((u) => {
      if (u) URL.revokeObjectURL(u);
    });
    setPicked({});
    setPreviews({});
    setSizeWarn(null);
  };

  const hasFront = !!picked.front; // U1: 仅 front 必填，side/back 可选
  const pickedCount = (["front", "side", "back"] as const).filter(
    (v) => picked[v],
  ).length;

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <h2 className="text-sm font-semibold">角色参考图</h2>
      {shouldShowStaleStyleBanner(
        originalPreset,
        currentPreset,
        sheets.length,
      ) && (
        <div className="rounded-lg border border-amber-300/50 bg-amber-50 p-3 dark:bg-amber-950/30">
          <p className="text-xs text-amber-700 dark:text-amber-300">
            视觉风格已改，已有角色图基于旧风格生成。建议重新生成角色参考图以匹配新风格。
          </p>
        </div>
      )}
      <p className="text-muted-foreground text-xs">
        固定角色正面/侧面/背面，所有绑本项目的视频复用——跨视频角色一致。建议纯色背景、正身、512+。
        可添加多个角色；同一画面最多 3 人同框。
      </p>

      {/* 已有角色参考图 */}
      {sheets.length > 0 && (
        <div className="space-y-2">
          {sheets.map((s, i) => (
            <div
              key={i}
              className="flex items-center gap-2 rounded-md border p-2"
            >
              {/* X11-2：三视图合成图缩略图（注入通道实际下发的图；无则占位） */}
              {s.composite_url ? (
                <img
                  loading="lazy"
                  src={resolveCompositeImgUrl(projectId, s.id ?? "")}
                  alt="三视图合成图"
                  title="三视图合成图（生成视频实际注入的参考图）"
                  className="h-16 w-28 shrink-0 rounded object-contain"
                />
              ) : (
                <div
                  title="生成/上传三视图后自动拼接"
                  className="text-muted-foreground/50 bg-muted flex h-16 w-28 shrink-0 items-center justify-center rounded text-[10px]"
                >
                  合成图生成后展示
                </div>
              )}
              {(["front", "side", "back"] as const).map((view) => {
                // U1/F3: front_url 是 FS 路径仅判有无；src 用 serve-by-index URL
                const has = !!s[`${view}_url` as keyof typeof s];
                return has ? (
                  <img
                    loading="lazy"
                    key={view}
                    src={resolveSheetImgUrl(view, projectId, s.id ?? "")}
                    alt={SHEET_LABEL[view]}
                    className="h-16 w-16 cursor-zoom-in rounded object-cover transition hover:opacity-80"
                    onClick={() => setLb({ sheet: s.id ?? "", view })}
                  />
                ) : (
                  <div
                    key={view}
                    className="text-muted-foreground/40 bg-muted flex h-16 w-16 items-center justify-center rounded text-[10px]"
                  >
                    无{SHEET_LABEL[view]}
                  </div>
                );
              })}
              <div className="flex items-center gap-1">
                <span className="text-xs">{s.name ?? `角色 ${i + 1}`}</span>
                {i === 0 && (
                  <span className="rounded bg-amber-100 px-1 text-[10px] text-amber-700 dark:bg-amber-950 dark:text-amber-300">
                    主角色
                  </span>
                )}
                <button
                  type="button"
                  title="上移"
                  disabled={i === 0 || isAnyPending}
                  className="text-muted-foreground hover:text-foreground rounded p-1 disabled:opacity-30"
                  onClick={() =>
                    reorderMut.mutate({
                      ids: sheetIds.flatMap((id, j) =>
                        j === i - 1
                          ? [sheetIds[i] ?? "", id]
                          : j === i
                            ? []
                            : [id],
                      ),
                    })
                  }
                >
                  <ArrowUp className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  title="下移"
                  disabled={i === sheets.length - 1 || isAnyPending}
                  className="text-muted-foreground hover:text-foreground rounded p-1 disabled:opacity-30"
                  onClick={() =>
                    reorderMut.mutate({
                      ids: sheetIds.flatMap((id, j) =>
                        j === i
                          ? [sheetIds[i + 1] ?? "", id]
                          : j === i + 1
                            ? []
                            : [id],
                      ),
                    })
                  }
                >
                  <ArrowDown className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  title="设为主角色（用于跨视频一致性）"
                  disabled={i === 0 || isAnyPending}
                  className="text-muted-foreground rounded p-1 hover:text-amber-600 disabled:opacity-30"
                  onClick={() =>
                    reorderMut.mutate({
                      ids: [
                        s.id ?? "",
                        ...sheetIds.filter((id) => id !== s.id),
                      ],
                    })
                  }
                >
                  <Crown className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  title="用原描述预填并重新生成"
                  disabled={isAnyPending}
                  className="text-muted-foreground hover:text-foreground rounded p-1 disabled:opacity-30"
                  onClick={() => {
                    // eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing -- intentional: empty features → fall back to name
                    setDesc(s.features || s.name || "");
                    setFeatures(s.features ?? "");
                    setName(s.name ?? `角色 ${i + 1}`);
                    setEditingSheetId(s.id ?? "");
                  }}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  title="删除该角色参考图"
                  className="text-muted-foreground rounded p-1 hover:text-red-600 disabled:opacity-50"
                  disabled={isAnyPending}
                  onClick={() => setConfirmDeleteIdx(s.id ?? "")}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* U3: 删除确认 AlertDialog（替 window.confirm，与项目删除一致） */}
      <AlertDialog
        open={confirmDeleteIdx !== null}
        onOpenChange={(o) => !o && setConfirmDeleteIdx(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除角色参考图</AlertDialogTitle>
            <AlertDialogDescription>
              确定删除「
              {confirmDeleteIdx !== null
                ? (sheets.find((x) => x.id === confirmDeleteIdx)?.name ??
                  "该角色")
                : ""}
              」？此操作无法撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-600 hover:bg-red-700"
              disabled={deleteMut.isPending}
              onClick={() => {
                if (confirmDeleteIdx !== null) {
                  // R5 (Finding 5): 不立即关对话框——等 onSuccess 才关；失败时 onError toast，
                  // 对话框留开让用户看到失败可重试。
                  deleteMut.mutate(confirmDeleteIdx, {
                    onSuccess: () => setConfirmDeleteIdx(null),
                  });
                }
              }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* X11-1：空态引导（无角色时） */}
      {sheets.length === 0 && (
        <div className="text-muted-foreground rounded-md border border-dashed p-3 text-center text-xs">
          暂无角色——在下方添加第一个角色
        </div>
      )}

      {/* X11-1：生成/上传表单分组标题——默认态=添加新角色；重生成态=重新生成：{name}
          （editingSheetId 非空=按 id 原子替换，附「取消改回添加新角色」） */}
      <div className="flex items-center gap-2">
        <h3 className="text-xs font-semibold">
          {editingSheetId !== null
            ? `重新生成：${sheets.find((x) => x.id === editingSheetId)?.name ?? "角色"}`
            : "添加新角色"}
        </h3>
        {editingSheetId !== null && (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-[11px]"
            onClick={() => {
              setEditingSheetId(null);
              setDesc("");
              setFeatures("");
              // 复核 3.4（2026-08-27）：name 一并清空——修前残留刚编辑的角色名，
              // 直接点生成会撞 M1 重名 409。
              setName("");
            }}
          >
            取消改回添加新角色
          </Button>
        )}
      </div>

      {/* 方式 A：文字描述生成 */}
      <div className="space-y-2">
        <p className="text-muted-foreground text-[11px]">
          描述=角色外貌/身份；特征=可复用风格关键词（与描述分开注入生图
          prompt，减少重复）。特征留空会回退用描述。
        </p>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="角色名（如：小明）"
          className="border-input bg-background h-8 w-full rounded-md border px-2 text-xs"
        />
        <input
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          placeholder="描述外貌/服饰（如：短发男孩，蓝色校服）"
          className="border-input bg-background h-8 w-full rounded-md border px-2 text-xs"
        />
        <textarea
          value={features}
          onChange={(e) => setFeatures(e.target.value)}
          rows={2}
          placeholder="特征/风格关键词（可选，如：红发、铠甲、赛博朋克）——与描述分开，减少 prompt 重复"
          className="border-input bg-background w-full rounded-md border px-2 py-1 text-xs"
        />
        <Button
          size="sm"
          disabled={!desc.trim() || genMut.isPending}
          onClick={() => {
            genMut.mutate(
              {
                name: name.trim(),
                description: desc.trim(),
                // A4: features 独立框；留空回退到描述，避免后端重复
                features: features.trim() || desc.trim(),
                // R2 + M1: editingSheetId 非空=重新生成→后端按 id 原子替换；
                // null=新建→append。无需前端 auto-delete（后端 _append 原子替换）
                replace_id: editingSheetId ?? undefined,
              },
              {
                onSuccess: () => {
                  setEditingSheetId(null);
                  // P1-U5: 成功才清空表单（修前 mutate 后无条件清空——失败时输入全丢）
                  setDesc("");
                  setFeatures("");
                },
                onError: (err) => {
                  // P1-U5: 失败 toast 显示后端 detail（修前静默）
                  toast.error(
                    `生成角色三视图失败：${err instanceof Error ? err.message : "未知错误"}`,
                  );
                },
              },
            );
          }}
        >
          {genMut.isPending
            ? // 方案 C（2026-08-31）：预期管理——三视图 side/back 并发后 ≈66s（front 33s
              // + max(side,back) 33s，实测 seedream-360 单张 33s），提示约 1 分钟
              "生成中…约需 1 分钟"
            : editingSheetId !== null
              ? "重新生成"
              : "生成角色参考图"}
        </Button>
      </div>

      {/* 方式 B：分视图上传（A3：正/侧/背各一按钮，选图即时预览，三张齐后上传） */}
      <div className="space-y-2">
        <label className="text-xs font-medium">或上传角色参考图</label>
        <div className="grid grid-cols-3 gap-2">
          {(["front", "side", "back"] as const).map((view) => (
            <button
              key={view}
              type="button"
              onClick={() => {
                setActiveView(view);
                uploadRef.current?.click();
              }}
              className="flex h-28 w-full flex-col items-center justify-center rounded-md border border-dashed p-1 text-center hover:bg-neutral-50"
            >
              {previews[view] ? (
                <img
                  loading="lazy"
                  src={previews[view]}
                  alt={SHEET_LABEL[view]}
                  className="h-full w-full rounded object-contain"
                />
              ) : (
                <>
                  <Upload className="text-muted-foreground/50 mb-1 h-5 w-5" />
                  <span className="text-[10px] font-medium">
                    {SHEET_LABEL[view]}
                  </span>
                </>
              )}
            </button>
          ))}
        </div>
        <input
          ref={uploadRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={onPickFile}
        />
        <p className="text-muted-foreground text-[10px]">
          提示：AI 生视频角色图建议用 AI 生成的三视图（方式
          A），真人照片可能被内容审查拒绝。
        </p>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            disabled={!hasFront || uploadMut.isPending}
            onClick={() => {
              if (!hasFront) return;
              uploadMut.mutate(
                buildCharacterSheetUploadFormData(picked, name || "角色"),
                { onSuccess: clearPicked },
              );
            }}
          >
            {uploadMut.isPending ? "上传中..." : "上传角色参考图"}
          </Button>
          {pickedCount > 0 && !hasFront && (
            <span className="text-muted-foreground text-[11px]">
              请先选择正面图（必填），侧/背可选
            </span>
          )}
        </div>
        {sizeWarn && <span className="text-xs text-amber-600">{sizeWarn}</span>}
      </div>

      {(() => {
        if (!lb) return null;
        if (!sheets.some((x) => x.id === lb.sheet)) return null;
        return (
          <CharacterSheetLightbox
            open
            onOpenChange={(o) => !o && setLb(null)}
            projectId={projectId}
            sheetId={lb.sheet}
            initialView={lb.view}
          />
        );
      })()}
    </div>
  );
}

/** C6（决策 4）：风格锚图管理区块——空态引导 + AI 生成/上传双入口 + 替换/删除。
 *
 * 锚图只作用于 AI 生预览图（分镜图）环节（决策 10/13），视频帧风格经既有预览
 * 图继承传导；引导文案建议抽象场景、避免人脸（降内容泄漏 + T2① 审核风险）。
 * 替换语义：每次渲染执行读取一次，替换时提示关联任务数并同步清理旧磁盘文件
 * （B10 新资产清理纪律——三视图现状不动）。未固化候选的孤儿清理不在计划内。
 */
export function StyleAnchorSection({
  projectId,
  anchor,
  relatedTaskCount,
  candidateName,
}: {
  projectId: string;
  anchor: string | null | undefined;
  relatedTaskCount: number;
  /** T5（2026-08-31 候选持久化）：项目字段 style_anchor_candidate（未固化候选
   * 文件名）——非空时挂载自动恢复候选条，退出/刷新/生成期间切页不再丢。 */
  candidateName: string | null | undefined;
}) {
  const genMut = useGenerateStyleAnchor(projectId);
  const setMut = useSetStyleAnchor(projectId);
  const delMut = useDeleteStyleAnchor(projectId);
  const clearCandMut = useClearStyleAnchorCandidate(projectId);
  // "view" = 查看当前锚图；"create" = AI 生成/上传的录入态
  const [mode, setMode] = useState<"view" | "create">("view");
  const [prompt, setPrompt] = useState("");
  // 方案 B（2026-08-30）：风格图「包含人物」开关，默认关——风格图定风格，
  // 人物长相归三视图管；勾选后人物仅作画面氛围参考。
  const [includePeople, setIncludePeople] = useState(false);
  // T5：path = 同会话生成返回的绝对路径；name = 恢复态文件名（无绝对路径，
  // 固化走 {candidate}）。二者互斥（有 path 优先）。
  const [candidate, setCandidate] = useState<{
    url: string;
    path?: string;
    name?: string;
  } | null>(null);
  const [pendingUpload, setPendingUpload] = useState<File | null>(null);
  const uploadRef = useRef<HTMLInputElement | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // 固化确认（有锚图时替换 / 无锚图时直接固化）
  const [confirmSet, setConfirmSet] = useState(false);

  // T5（2026-08-31 候选持久化）：挂载/字段变化时恢复候选条——字段非空且本地
  // 尚无候选（不覆盖同会话新生成的）。根治「生成后退出即丢」（含生成期间切页
  // 导致 mutate onSuccess 被丢弃的路径——后端生成成功即落字段，与前端无关）。
  useEffect(() => {
    if (candidateName && !candidate && !pendingUpload) {
      setMode("create");
      setCandidate({
        url: styleAnchorImageUrl(projectId, candidateName),
        name: candidateName,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 恢复只在字段/挂载时评估；candidate/pendingUpload 是「勿覆盖」守卫，纳入依赖会在生成后误触发
  }, [candidateName, projectId]);

  const resetCreate = () => {
    setMode("view");
    setPrompt("");
    setCandidate(null);
    setPendingUpload(null);
    // 方案 B：开关随表单一起复位，否则固化后「替换→AI 生成」会静默沿用上次勾选，
    // 违背「默认关」的产品决定。
    setIncludePeople(false);
  };

  const doSet = () => {
    if (candidate) {
      setMut.mutate(
        candidate.path
          ? { candidate_path: candidate.path }
          : { candidate: candidate.name ?? "" },
        {
          onSuccess: () => {
            resetCreate();
            setConfirmSet(false);
          },
          onError: () => setConfirmSet(false),
        },
      );
    } else if (pendingUpload) {
      setMut.mutate(
        { file: pendingUpload },
        {
          onSuccess: () => {
            resetCreate();
            setConfirmSet(false);
          },
          onError: () => setConfirmSet(false),
        },
      );
    } else {
      setConfirmSet(false);
    }
  };

  // T5：「换一张」= 本地弃 + 后端清字段/删文件（防退出重进复活已弃候选）
  const discardCandidate = () => {
    if (candidate?.name || candidate?.path) clearCandMut.mutate();
    setCandidate(null);
  };

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <h2 className="text-sm font-semibold">风格锚图</h2>
      <p className="text-muted-foreground text-xs">
        设置后，本项目新生成的视频将保持统一画风（色调/构图语言）。建议与项目风格预设一致；视频帧风格经预览图继承传导。
      </p>

      {mode === "view" ? (
        anchor ? (
          <div className="flex items-start gap-3">
            {/* 锚图 serve URL（无锚图 404——有值才渲染此分支） */}
            {}
            <img
              loading="lazy"
              src={styleAnchorImageUrl(projectId)}
              alt="风格锚图"
              className="h-24 w-24 rounded-md border object-cover"
            />
            <div className="flex flex-col gap-2">
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setMode("create")}
                >
                  <ImagePlus className="mr-1 h-3.5 w-3.5" />
                  替换
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="text-red-600"
                  disabled={delMut.isPending}
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash2 className="mr-1 h-3.5 w-3.5" />
                  删除
                </Button>
              </div>
              <p className="text-muted-foreground text-[11px]">
                替换后，新生成与重渲染的任务将使用新锚图。
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-3 rounded-md border border-dashed p-4 text-center">
            <p className="text-muted-foreground text-xs">
              还没有风格锚图。设置后，本项目新生成的视频将保持统一画风。
            </p>
            <div className="flex justify-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setMode("create")}
              >
                <Wand2 className="mr-1 h-3.5 w-3.5" />
                AI 生成
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setMode("create");
                  setTimeout(() => uploadRef.current?.click(), 0);
                }}
              >
                <Upload className="mr-1 h-3.5 w-3.5" />
                上传图片
              </Button>
            </div>
          </div>
        )
      ) : (
        <div className="space-y-3">
          {/* AI 生成入口 */}
          <div className="space-y-2">
            <label className="text-xs font-medium">AI 生成风格图</label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={2}
              placeholder="建议描述抽象场景；风格应与项目风格预设一致（如：柔和水彩质感，暖色调）"
              className="border-input bg-background w-full rounded-md border px-2 py-1 text-xs"
            />
            <label className="text-muted-foreground flex cursor-pointer items-center gap-1.5 text-xs">
              <input
                type="checkbox"
                className="accent-primary h-3.5 w-3.5"
                checked={includePeople}
                onChange={(e) => setIncludePeople(e.target.checked)}
              />
              画面包含人物
            </label>
            {includePeople ? (
              <p className="text-muted-foreground text-xs">
                人物仅作画面氛围参考，角色外观以三视图为准
              </p>
            ) : (
              <p className="text-muted-foreground text-xs">
                默认不含人物（角色外观由三视图决定），如需人物氛围请勾选
              </p>
            )}
            <Button
              size="sm"
              disabled={!prompt.trim() || genMut.isPending}
              onClick={() => {
                genMut.mutate(
                  { prompt: prompt.trim(), includePeople },
                  {
                    onSuccess: (r) =>
                      setCandidate({
                        url: r.image_url,
                        path: r.candidate_path,
                        // T5：文件名随候选带走（「换一张」清字段用）
                        name: r.candidate_path.split("/").pop() ?? undefined,
                      }),
                  },
                );
              }}
            >
              {genMut.isPending
                ? // 方案 C（2026-08-31）：单张风格图实测 ≈33s，提示 30-60 秒
                  "生成中…约需 30-60 秒"
                : candidate
                  ? "重新生成"
                  : "生成风格图"}
            </Button>
            {candidate && (
              <div className="flex items-center gap-3 rounded-md border p-2">
                {}
                <img
                  loading="lazy"
                  src={candidate.url}
                  alt="候选风格图"
                  className="h-20 w-20 rounded object-cover"
                />
                <div className="flex flex-col gap-1">
                  <span className="text-xs">满意这张吗？</span>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      disabled={setMut.isPending}
                      onClick={() => (anchor ? setConfirmSet(true) : doSet())}
                    >
                      {/* T5（2026-08-31）：与确认框标题一致——有锚图时是替换，
                          旧文案「设为」让用户对二次确认框没有预期（「点了没反应」误解） */}
                      {anchor ? "替换风格锚图" : "设为风格锚图"}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={discardCandidate}
                    >
                      换一张
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* 手动上传入口（沿用三视图上传通道形态） */}
          <div className="space-y-2">
            <label className="text-xs font-medium">或上传图片</label>
            <input
              ref={uploadRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) setPendingUpload(f);
              }}
            />
            {pendingUpload ? (
              <div className="flex items-center gap-3 rounded-md border p-2">
                {}
                <img
                  loading="lazy"
                  src={URL.createObjectURL(pendingUpload)}
                  alt="待上传锚图"
                  className="h-20 w-20 rounded object-cover"
                />
                <div className="flex flex-col gap-1">
                  <span className="text-xs">{pendingUpload.name}</span>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      disabled={setMut.isPending}
                      onClick={() => (anchor ? setConfirmSet(true) : doSet())}
                    >
                      {/* T5：与确认框标题一致（同候选条文案口径） */}
                      {anchor ? "替换风格锚图" : "设为风格锚图"}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setPendingUpload(null)}
                    >
                      移除
                    </Button>
                  </div>
                </div>
              </div>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={() => uploadRef.current?.click()}
              >
                <Upload className="mr-1 h-3.5 w-3.5" />
                选择图片
              </Button>
            )}
          </div>

          <Button size="sm" variant="ghost" onClick={resetCreate}>
            取消
          </Button>
        </div>
      )}

      {/* 替换确认（B7 在途语义 + 关联任务数提示）。z-50（T5 2026-08-31 可达性）：
          Radix 浮层在 transform/filter 祖先下定位异常时确保堆叠上下文顶层可达——
          「点了没反应」三层根因之 2 的纵深防御。 */}
      <AlertDialog
        open={confirmSet}
        onOpenChange={(o) => !o && setConfirmSet(false)}
      >
        <AlertDialogContent className="z-50">
          <AlertDialogHeader>
            <AlertDialogTitle>替换风格锚图</AlertDialogTitle>
            <AlertDialogDescription>
              本项目的 {relatedTaskCount}{" "}
              个关联任务，之后新生成与重渲染将使用新锚图；旧锚图文件将被删除。此操作无法撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction disabled={setMut.isPending} onClick={doSet}>
              确认替换
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 删除确认（z-50 同替换确认——同一可达性纵深防御） */}
      <AlertDialog
        open={confirmDelete}
        onOpenChange={(o) => !o && setConfirmDelete(false)}
      >
        <AlertDialogContent className="z-50">
          <AlertDialogHeader>
            <AlertDialogTitle>删除风格锚图</AlertDialogTitle>
            <AlertDialogDescription>
              删除后本项目任务不再注入风格锚图（文本风格前缀不受影响）。此操作无法撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-600 hover:bg-red-700"
              disabled={delMut.isPending}
              onClick={() => {
                delMut.mutate(undefined, {
                  onSuccess: () => setConfirmDelete(false),
                });
              }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** M6（决策 12/15/16/18）：道具资产管理——固定产品跨视频稳定出现。
 *
 * 形态：主图 1（实拍、干净/白底）+ 补充角度 0~2（仅实拍；AI 生成补角度有幻觉固化
 * 风险，决策 16 禁止——无 AI 生成入口）。保真边界（决策 18）：logo/文字特写硬保真
 * 不在本计划，上传区常驻文案管理预期。
 */
function PropAssetsSection({
  projectId,
  props: propAssets,
}: {
  projectId: string;
  props: PropAssetData[];
}) {
  const uploadMut = useUploadPropAsset(projectId);
  const deleteMut = useDeletePropAsset(projectId);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [picked, setPicked] = useState<{ main?: File; extras: File[] }>({
    extras: [],
  });
  const [previews, setPreviews] = useState<{ main?: string; extras: string[] }>(
    { extras: [] },
  );
  const mainInputRef = useRef<HTMLInputElement | null>(null);
  const extraInputRef = useRef<HTMLInputElement | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const previewsRef = useRef(previews);
  previewsRef.current = previews;
  useEffect(() => {
    return () => {
      Object.values(previewsRef.current).forEach((u) => {
        if (typeof u === "string") URL.revokeObjectURL(u);
      });
    };
  }, []);

  const pickMain = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    if (previews.main) URL.revokeObjectURL(previews.main);
    setPicked((p) => ({ ...p, main: f }));
    setPreviews((p) => ({ ...p, main: URL.createObjectURL(f) }));
  };

  const pickExtras = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []).slice(
      0,
      2 - picked.extras.length,
    );
    e.target.value = "";
    if (!files.length) return;
    setPicked((p) => ({ ...p, extras: [...p.extras, ...files].slice(0, 2) }));
    setPreviews((p) => ({
      ...p,
      extras: [...p.extras, ...files.map((f) => URL.createObjectURL(f))].slice(
        0,
        2,
      ),
    }));
  };

  const clearPicked = () => {
    if (previews.main) URL.revokeObjectURL(previews.main);
    previews.extras.forEach((u) => URL.revokeObjectURL(u));
    setPicked({ extras: [] });
    setPreviews({ extras: [] });
  };

  const doUpload = () => {
    if (!picked.main || !name.trim()) return;
    const fd = new FormData();
    fd.append("name", name.trim());
    fd.append("description", description.trim());
    fd.append("main_image", picked.main);
    picked.extras.forEach((f) => fd.append("extra_images", f));
    uploadMut.mutate(fd, {
      onSuccess: () => {
        clearPicked();
        setName("");
        setDescription("");
      },
    });
  };

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <h2 className="flex items-center gap-1 text-sm font-semibold">
        <Package className="h-4 w-4" />
        道具资产
      </h2>
      <p className="text-muted-foreground text-xs">
        固定产品（工具/设备）跨视频稳定出现。可添加多个道具；同一画面最多出现 2
        个；每帧自动从主图/补充角度中选 1 张作参考（道具不拼图，多角度供不同镜头
        选用）。主图 1 张（实拍、干净/白底、产品完整清晰）+ 补充角度最多 2 张。
      </p>
      {/* 决策 18 保真边界文案（常驻）：管理 logo/文字特写预期 */}
      <div className="rounded-md border border-blue-300/50 bg-blue-50 p-2 dark:bg-blue-950/30">
        <p className="text-[11px] text-blue-700 dark:text-blue-300">
          产品细节特写镜头建议使用实拍素材，AI 生成不保证 logo/文字不变形。
        </p>
      </div>

      {propAssets.length > 0 && (
        <div className="space-y-2">
          {propAssets.map((pa) => (
            <div
              key={pa.id}
              className="flex items-center gap-3 rounded-md border p-2"
            >
              <img
                loading="lazy"
                src={`/api/video/projects/${projectId}/props/${pa.id}/main`}
                alt={pa.name}
                className="h-16 w-16 rounded border object-cover"
              />
              {pa.extra_images?.map((_, i) => (
                <img
                  loading="lazy"
                  key={i}
                  src={`/api/video/projects/${projectId}/props/${pa.id}/extra${i}`}
                  alt={`${pa.name} 补角度 ${i + 1}`}
                  className="h-16 w-16 rounded border object-cover"
                />
              ))}
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium">{pa.name}</p>
                {pa.description && (
                  <p className="text-muted-foreground truncate text-[11px]">
                    {pa.description}
                  </p>
                )}
              </div>
              <Button
                size="sm"
                variant="ghost"
                className="text-red-600"
                disabled={deleteMut.isPending}
                onClick={() => setConfirmDeleteId(pa.id)}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {/* 上传区（唯一入口——无 AI 生成，决策 15/16） */}
      <div className="space-y-2">
        <label className="text-xs font-medium">添加道具</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="道具名（旁白称呼，如：演示手机）"
          className="border-input bg-background h-8 w-full rounded-md border px-2 text-xs"
        />
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="描述（可选，帮助 AI 识别旁白中的道具称呼，如：黑色直板旗舰机）"
          className="border-input bg-background h-8 w-full rounded-md border px-2 text-xs"
        />
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => mainInputRef.current?.click()}
            className="flex h-24 flex-1 flex-col items-center justify-center rounded-md border border-dashed p-1 hover:bg-neutral-50"
          >
            {previews.main ? (
              <img
                loading="lazy"
                src={previews.main}
                alt="主图"
                className="h-full w-full rounded object-contain"
              />
            ) : (
              <>
                <Upload className="text-muted-foreground/50 mb-1 h-5 w-5" />
                <span className="text-[10px] font-medium">主图（必填）</span>
              </>
            )}
          </button>
          <button
            type="button"
            onClick={() => extraInputRef.current?.click()}
            disabled={picked.extras.length >= 2}
            className="flex h-24 flex-1 flex-col items-center justify-center rounded-md border border-dashed p-1 hover:bg-neutral-50 disabled:opacity-50"
          >
            {previews.extras.length ? (
              <div className="flex h-full w-full items-center gap-1">
                {previews.extras.map((u, i) => (
                  <img
                    loading="lazy"
                    key={i}
                    src={u}
                    alt={`补角度 ${i + 1}`}
                    className="h-full w-1/2 rounded object-contain"
                  />
                ))}
              </div>
            ) : (
              <>
                <Upload className="text-muted-foreground/50 mb-1 h-5 w-5" />
                <span className="text-[10px] font-medium">
                  补角度 0~2（可选）
                </span>
              </>
            )}
          </button>
        </div>
        <input
          ref={mainInputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={pickMain}
        />
        <input
          ref={extraInputRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={pickExtras}
        />
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            disabled={!picked.main || !name.trim() || uploadMut.isPending}
            onClick={doUpload}
          >
            {uploadMut.isPending ? "上传中..." : "上传道具"}
          </Button>
          {(picked.main !== undefined || picked.extras.length > 0) && (
            <Button size="sm" variant="ghost" onClick={clearPicked}>
              清空
            </Button>
          )}
        </div>
      </div>

      <AlertDialog
        open={confirmDeleteId !== null}
        onOpenChange={(o) => !o && setConfirmDeleteId(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除道具</AlertDialogTitle>
            <AlertDialogDescription>
              确定删除「
              {propAssets.find((x) => x.id === confirmDeleteId)?.name ??
                "该道具"}
              」？关联图片文件将一并删除。此操作无法撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-600 hover:bg-red-700"
              disabled={deleteMut.isPending}
              onClick={() => {
                if (confirmDeleteId !== null) {
                  deleteMut.mutate(confirmDeleteId, {
                    onSuccess: () => setConfirmDeleteId(null),
                  });
                }
              }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export function CourseProjectDetailPage({
  projectId,
  onBack,
}: {
  projectId: string;
  onBack: () => void;
}) {
  const projectsQ = useCourseProjects();
  const updateMut = useUpdateCourseProject();
  const deleteMut = useDeleteCourseProject();
  const tasksQ = useVideoTasks();

  const project = projectsQ.data?.find((p) => p.id === projectId);
  const projectTasks = (tasksQ.data ?? []).filter(
    (t) => t.course_project_id === projectId,
  );

  const [title, setTitle] = useState("");
  const [stylePreset, setStylePreset] = useState("");
  // 决策 21（C7 步骤 0）：项目级默认音色/字幕样式。"" = 跟随系统默认（PUT 显式传 "" 清空）
  const [projectVoiceId, setProjectVoiceId] = useState("");
  // T-E11（D6）：项目级默认语速；null = 跟随系统默认（未设置）
  const [projectTtsSpeed, setProjectTtsSpeed] = useState<number | null>(null);
  const [projectSubtitle, setProjectSubtitle] = useState("");
  const voicesQuery = useVideoVoices("zh");
  const [palette, setPalette] = useState<ColorPalette>({});
  // X11（2026-08-27 方案 B）：characterReference state 退役——「角色参考」失实区块删除，
  // handleSave 不再提交 character_reference（PUT exclude_unset 语义下服务端存量值不变）。

  // Task 18（B1）：只在**切换项目**时把服务端值灌进表单，不能每次 refetch 都灌。
  //
  // `project` 是 `projectsQ.data?.find(...)` 每次渲染重新 derive 的**新对象**，
  // 依赖 `[project]` 等于「每次 refetch 都重跑」。四个角色图 mutation 都会
  // invalidateQueries([PROJECT_KEY])，于是「改了标题没保存 → 顺手传张角色图 →
  // 标题被打回服务端值」，是真数据丢失。
  //
  // 用 syncedIdRef 只认 id 变化：切项目必须重新同步（否则会把 A 项目的编辑写进
  // B 项目），同 id 的 refetch 一律不动用户正在输入的内容。
  const syncedIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!project) return;
    if (syncedIdRef.current === project.id) return;
    syncedIdRef.current = project.id;
    setTitle(project.title);
    // Task 1：存量项目存的是英文字面量（'3D render' 等），而下拉选项已改成
    // 语言无关 key。直接灌原值会让原生 select 找不到匹配 option → 静默回落
    // 「不设置」→ 用户一保存就把风格真的清空。故先映射成当前 option value。
    setStylePreset(optionValueForStored(project.visual_style_preset));
    setProjectVoiceId(project.voice_id ?? "");
    setProjectTtsSpeed(
      typeof project.tts_speed === "number" ? project.tts_speed : null,
    );
    setProjectSubtitle(
      project.subtitle_style && project.subtitle_style !== "default"
        ? project.subtitle_style
        : "",
    );
    const cp = project.color_palette ?? {};
    setPalette({
      primary: typeof cp.primary === "string" ? cp.primary : "",
      saturation: typeof cp.saturation === "number" ? cp.saturation : 1.0,
      contrast: typeof cp.contrast === "number" ? cp.contrast : 1.0,
      // P1（三期）：brightness 回灌——修前只回 primary/saturation/contrast，
      // 刷新后亮度滑条恒回 0（保存侧也漏提交，双缺口叠加成「能调不能存」）。
      brightness: typeof cp.brightness === "number" ? cp.brightness : 0.0,
    });
  }, [project]);

  if (projectsQ.isLoading) {
    return (
      <div className="text-muted-foreground p-8 text-center text-sm">
        加载中...
      </div>
    );
  }

  if (!project) {
    return (
      <div className="flex flex-col items-center gap-4 p-8">
        <p className="text-muted-foreground text-sm">项目不存在</p>
        <Button variant="outline" size="sm" onClick={onBack}>
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回
        </Button>
      </div>
    );
  }

  const handleSave = () => {
    updateMut.mutate({
      id: projectId,
      input: {
        title: title.trim(),
        visual_style_preset: stylePreset || undefined,
        // X11（方案 B）：character_reference 不再提交——后端字段与存量数据保留
        // （vimax 图片路径语义不动），PUT exclude_unset 语义下服务端值不变。
        // 决策 21 清除语义（写死）：PUT 走 exclude_unset——不传=不变，清空必须
        // 显式传 ""；「跟随系统默认」选项落 ""（后端 resolver 将 "" 按未设置处理）。
        voice_id: projectVoiceId,
        // T-E11：显式 null = 清空（跟随任务缺省/config）；数字 = 设置项目语速
        tts_speed: projectTtsSpeed,
        subtitle_style: projectSubtitle,
        color_palette: {
          ...(project.color_palette ?? {}),
          // eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing -- intentional falsy→undefined (empty/0 color = unset)
          primary: palette.primary || undefined,
          saturation: palette.saturation,
          contrast: palette.contrast,
          // P1（三期）：brightness 补提交——后端 color_palette 整 dict 直存，worker
          // _colorgrade_vf 读 eq=brightness；修前此字段静默丢弃。
          brightness: palette.brightness,
        },
      },
    });
  };

  const handleDelete = () => {
    deleteMut.mutate(projectId, {
      onSuccess: () => onBack(),
      // onError 由 hook 内 toast 覆盖；失败停留在当前页
    });
  };

  const hasChanges =
    title !== project.title ||
    // Task 1：两边都归一后比较。state 里是当前 key（education_3d），
    // 服务端可能仍是存量英文字面量（'3D render'）—— 直接比字符串会让老项目
    // 一打开就显示「有未保存修改」，误触离开确认框。
    stylePreset !== optionValueForStored(project.visual_style_preset) ||
    projectVoiceId !== (project.voice_id ?? "") ||
    projectTtsSpeed !==
      (typeof project.tts_speed === "number" ? project.tts_speed : null) ||
    projectSubtitle !==
      (project.subtitle_style && project.subtitle_style !== "default"
        ? project.subtitle_style
        : "") ||
    JSON.stringify(palette) !==
      JSON.stringify({
        primary: (project.color_palette?.primary as string) ?? "",
        saturation: (project.color_palette?.saturation as number) ?? 1.0,
        contrast: (project.color_palette?.contrast as number) ?? 1.0,
        // P1（三期）：脏检查同步补 brightness——修前调亮度不会点亮「保存」按钮
        brightness: (project.color_palette?.brightness as number) ?? 0.0,
      });

  return (
    <div className="scrollbar-none mx-auto w-full max-w-3xl space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回项目列表
        </Button>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            onClick={handleSave}
            disabled={!hasChanges || updateMut.isPending}
          >
            <Save className="mr-1 h-4 w-4" />
            {updateMut.isPending ? "保存中..." : "保存"}
          </Button>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="outline" size="sm" className="text-red-600">
                <Trash2 className="mr-1 h-4 w-4" />
                删除项目
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认删除</AlertDialogTitle>
                <AlertDialogDescription>
                  确定要删除项目「{project.title}
                  」吗？关联的视频任务不会被删除，但会失去项目关联。此操作无法撤销。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction
                  onClick={handleDelete}
                  className="bg-red-600 hover:bg-red-700"
                >
                  删除
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      {/* Basic settings */}
      <div className="space-y-4 rounded-lg border p-4">
        <h2 className="text-sm font-semibold">基本设置</h2>
        <div className="space-y-2">
          <label className="text-xs font-medium">项目名称</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="border-input bg-background flex h-9 w-full rounded-md border px-3 text-sm"
            placeholder="如：AI 入门课程"
          />
        </div>
        <div className="space-y-2">
          <label className="text-xs font-medium">视觉风格预设</label>
          <select
            value={stylePreset}
            onChange={(e) => setStylePreset(e.target.value)}
            className="border-input bg-background flex h-9 w-full rounded-md border px-3 text-sm"
          >
            {projectStyleOptions(stylePreset).map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        {/*
          决策 21（2026-08-25 用户确认，C7 步骤 0）：项目级默认音色/字幕设置入口。
          「跟随项目」的任务在未显式指定时继承这两项（B2 后端回落已有，此前前端
          零写入入口导致恒继承 lang 默认）。「跟随系统默认」落 ""（显式清空）。
        */}
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-2">
            <label className="text-xs font-medium">默认音色</label>
            <select
              value={projectVoiceId}
              onChange={(e) => setProjectVoiceId(e.target.value)}
              className="border-input bg-background flex h-9 w-full rounded-md border px-3 text-sm"
            >
              <option value="">跟随系统默认</option>
              {(voicesQuery.data ?? []).map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} ({v.gender})
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-2">
            <label className="text-xs font-medium">默认字幕样式</label>
            <select
              value={projectSubtitle}
              onChange={(e) => setProjectSubtitle(e.target.value)}
              className="border-input bg-background flex h-9 w-full rounded-md border px-3 text-sm"
            >
              <option value="">跟随系统默认</option>
              {SUBTITLE_STYLES.filter((x) => x.value !== "none").map((x) => (
                <option key={x.value} value={x.value}>
                  {x.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        {/*
          T-E11（D6，2026-09-09 feedback-round1）：项目级默认语速——挂项目的任务
          在未显式指定语速时继承此值（后端三级链：任务显式 > 项目 > config）。
          null = 跟随系统默认（保存显式 null 清空）。滑条交互与任务级音频设置区
          （video-gen-detail）一致：0.5x–2.0x，步进 0.1。
        */}
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium">默认语速</label>
            <label className="text-muted-foreground flex items-center gap-1.5 text-xs">
              <input
                type="checkbox"
                checked={projectTtsSpeed === null}
                onChange={(e) =>
                  setProjectTtsSpeed(e.target.checked ? null : 1.0)
                }
                className="accent-indigo-500"
              />
              跟随系统默认
            </label>
          </div>
          {/* 跟随态显示中性占位「—」——后端 config 缺省是部署配置（本机 1.2），
              前端无获取通道，硬编码数值会失真误导 */}
          <SpeedSliderControl
            value={projectTtsSpeed}
            onChange={setProjectTtsSpeed}
          />
        </div>
      </div>

      {/* X11（2026-08-27 方案 B）：「角色参考」文本区块已删除——后端全链路把
          character_reference 当图片路径消费（_character_ref_paths_from_payload /
          vimax 桥），「注入 prompt 前缀」的宣称失实；后端字段与存量数据保留。
          页面顺序：基本设置 → 角色参考图 → 风格锚图 → 道具资产。 */}

      {/* Task 15: 角色参考图（生成/上传双入口，跨视频角色一致） */}
      <CharacterSheetsSection
        projectId={projectId}
        sheets={project.character_sheets ?? []}
        originalPreset={project.visual_style_preset ?? ""}
        currentPreset={stylePreset}
      />

      {/* C6（决策 4）：风格锚图管理（AI 生成 + 上传双入口） */}
      <StyleAnchorSection
        projectId={projectId}
        anchor={project.style_anchor_image ?? null}
        relatedTaskCount={projectTasks.length}
        candidateName={project.style_anchor_candidate ?? null}
      />

      {/* M6（决策 12/15/16/18）：道具资产管理（仅手动上传） */}
      <PropAssetsSection
        projectId={projectId}
        props={project.prop_assets ?? []}
      />

      {/* Task 20c: 重算策略文案——改角色/风格只影响新视频，已生成需手动重新生成 */}
      <div className="rounded-lg border border-blue-300/50 bg-blue-50 p-3 dark:bg-blue-950/30">
        <p className="text-xs text-blue-700 dark:text-blue-300">
          角色/风格修改仅影响之后新生成的视频。已生成的视频如需更新，请在视频详情页点「重新生成」。
        </p>
      </div>
    </div>
  );
}
