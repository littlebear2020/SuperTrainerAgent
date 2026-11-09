"use client";

/**
 * 资产中心（Phase 3.5，2026-09-11 ppt-master 6.3.0 批次）。
 *
 * 平台级资产页面 `/workspace/assets`：按形态组织的 Tab（品牌是跨形态业务属性，
 * 不作分类）。「PPT 模板」为首个入驻资产类型——上传 PPT → 预览蒸馏出的品牌要素
 * （配色/字体/logo/版式/主题映射）→ 订正确认保存；「应用到课程项目」把
 * trainer_brand 字段写进 CourseProject 品牌包（PPT/视频一套品牌）。
 */

import {
  Boxes,
  FileUp,
  Loader2,
  Palette,
  RotateCw,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { BetaBadge } from "@/components/workspace/beta-badge";
import { projectOptionLabel } from "@/components/workspace/video-gen/course-project-select";
import {
  applyPptTemplateToProject,
  deletePptTemplate,
  listPptTemplates,
  MAX_PPTX_BYTES,
  pptTemplateLogoUrl,
  redistillPptTemplate,
  updatePptTemplate,
  uploadPptTemplate,
  type PptTemplateSummary,
} from "@/core/assets/api";
import { listCourseProjects } from "@/core/video-gen/api";
import type { CourseProject } from "@/core/video-gen/types";

const THEMES = [
  { value: "dark-blue", label: "深空蓝" },
  { value: "light", label: "明亮浅色" },
  { value: "dark-green", label: "墨绿沉稳" },
  { value: "warm", label: "暖色纸感" },
] as const;

function ColorSwatch({ hex, label }: { hex: string; label: string }) {
  return (
    <div className="flex items-center gap-1.5" title={`${label} ${hex}`}>
      <span
        className="size-4 shrink-0 rounded border border-black/10"
        style={{ backgroundColor: hex }}
      />
      <span className="text-muted-foreground font-mono text-[10px]">{hex}</span>
    </div>
  );
}

export default function AssetsPage() {
  const [templates, setTemplates] = useState<PptTemplateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 上传→预览态（distill 是同步的：上传响应即预览数据）
  const [draftName, setDraftName] = useState("");
  const [draftId, setDraftId] = useState<string | null>(null);
  const [draftPalette, setDraftPalette] = useState<Record<string, string>>({});
  const [draftPrimary, setDraftPrimary] = useState<string | null>(null);
  const [draftFont, setDraftFont] = useState<string>("");
  const [draftTheme, setDraftTheme] = useState<string>("dark-blue");
  const [draftLogoUrl, setDraftLogoUrl] = useState<string | null>(null);
  const [draftLayouts, setDraftLayouts] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement | null>(null);

  // 应用到项目
  const [applyOpen, setApplyOpen] = useState(false);
  const [applyTarget, setApplyTarget] = useState<PptTemplateSummary | null>(
    null,
  );
  const [projects, setProjects] = useState<CourseProject[]>([]);
  const [applyProjectId, setApplyProjectId] = useState<string>("");
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState<string | null>(null);

  // 重新蒸馏（E2，2026-09-23 批 2）
  const [redistillingId, setRedistillingId] = useState<string | null>(null);
  const [redistillNote, setRedistillNote] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setTemplates(await listPptTemplates());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleUpload = async (file: File) => {
    // 条目 23（2026-09-12 缺陷批）：客户端大小预检——修前全链路无 file.size 检查，
    // 用户大文件要等上传完才被后端拒。与后端上限同值（core/assets/api.ts）。
    if (file.size > MAX_PPTX_BYTES) {
      setError("文件超过 100MB 上限");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const asset = await uploadPptTemplate(
        file,
        draftName || file.name.replace(/\.pptx?$/i, ""),
      );
      setDraftId(asset.id);
      setDraftName(asset.name);
      setDraftPalette(asset.brand?.palette?.colors ?? {});
      setDraftPrimary(asset.brand?.palette?.primary ?? null);
      setDraftFont(asset.brand?.font_family ?? "");
      setDraftTheme(asset.brand?.style_tendency?.remotion_theme ?? "dark-blue");
      setDraftLogoUrl(
        asset.brand?.logo?.filename ? pptTemplateLogoUrl(asset.id) : null,
      );
      setDraftLayouts(asset.brand?.layouts ?? []);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const handleConfirm = async () => {
    if (!draftId) return;
    setUploading(true);
    try {
      await updatePptTemplate(draftId, {
        name: draftName || undefined,
        brand_overrides: {
          font_family: draftFont,
          primary: draftPrimary ?? undefined,
          remotion_theme: draftTheme as
            | "dark-blue"
            | "light"
            | "dark-green"
            | "warm",
        },
      });
      setDraftId(null);
      setDraftName("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setUploading(false);
    }
  };

  // E2（2026-09-23 批 2）：redistill 确认文案明示"将保留您已确认的品牌字段"；
  // 后端 _confirmed 机制（E0/E1）负责实际保留。
  const handleRedistill = async (tpl: PptTemplateSummary) => {
    if (
      !confirm(
        `对「${tpl.name}」重新蒸馏品牌要素？将保留您已确认的品牌字段（主色 / 字体 / 主题），其余（辅色 / LOGO / 版式）按源文件刷新。`,
      )
    )
      return;
    setRedistillingId(tpl.id);
    setRedistillNote(null);
    try {
      const { brand_diff: diff } = await redistillPptTemplate(tpl.id);
      const preserved = diff.preserved_user_confirmed ?? [];
      setRedistillNote(
        diff.legacy_overwrite_warning
          ? `重新蒸馏完成：${diff.legacy_overwrite_warning}`
          : `重新蒸馏完成：${preserved.length > 0 ? `已保留您确认的字段（${preserved.join("、")}）` : "无已确认字段，全部按源文件刷新"}${diff.changed.length > 0 ? `；本次变化：${diff.changed.join("、")}` : "；无变化"}`,
      );
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "重新蒸馏失败");
    } finally {
      setRedistillingId(null);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("确定删除该模板？")) return;
    try {
      await deletePptTemplate(id);
      if (draftId === id) setDraftId(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    }
  };

  const openApply = async (tpl: PptTemplateSummary) => {
    setApplyTarget(tpl);
    setApplyResult(null);
    setApplyProjectId("");
    setApplyOpen(true);
    try {
      setProjects(await listCourseProjects());
    } catch {
      setProjects([]);
    }
  };

  const handleApply = async () => {
    if (!applyTarget || !applyProjectId) return;
    setApplying(true);
    try {
      const result = await applyPptTemplateToProject(
        applyTarget.id,
        applyProjectId,
      );
      setApplyResult(
        `已应用字段：${result.applied_fields.join("、") || "无"}` +
          (result.logo_note ? `（${result.logo_note}）` : ""),
      );
    } catch (err) {
      setApplyResult(err instanceof Error ? err.message : "应用失败");
    } finally {
      setApplying(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-5xl p-6">
      <div className="mb-6 flex items-center gap-3">
        <Boxes className="size-6" />
        <h1 className="text-xl font-semibold">资产中心</h1>
        <BetaBadge />
      </div>
      <p className="text-muted-foreground mb-6 text-sm">
        平台级资产按形态组织；品牌要素跨 PPT /
        课程视频共享（一套品牌，双产物）。
      </p>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}

      {/* Tab 区（按形态组织；当前仅 PPT 模板，后续视频/声音资产入驻新 Tab） */}
      <div className="mb-4 flex gap-2">
        <div className="bg-muted rounded-lg px-3 py-1.5 text-sm font-medium">
          PPT 模板
        </div>
        <div className="text-muted-foreground rounded-lg border border-dashed px-3 py-1.5 text-sm">
          更多资产类型（视频 / 声音）将陆续入驻
        </div>
      </div>

      {/* 上传 / 预览确认卡片 */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Palette className="size-4" /> 从 PPT 蒸馏品牌模板
          </CardTitle>
          <CardDescription>
            上传一份公司 PPT，自动解析主题配色、字体、LOGO 与版式；确认后保存为
            可复用模板，并可一键应用到课程项目。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-3">
            <Input
              placeholder="模板名称（默认取文件名）"
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              className="w-64"
            />
            <input
              ref={fileRef}
              type="file"
              accept=".pptx"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleUpload(f);
              }}
            />
            <Button
              variant="outline"
              disabled={uploading}
              onClick={() => fileRef.current?.click()}
            >
              {uploading ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <FileUp className="size-4" />
              )}
              {uploading ? "解析中…" : "上传 PPTX"}
            </Button>
          </div>

          {draftId && (
            <div className="rounded-lg border p-4">
              <div className="mb-3 flex items-center justify-between">
                <span className="text-sm font-semibold">
                  解析结果（可订正后保存）
                </span>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    onClick={handleConfirm}
                    disabled={uploading}
                  >
                    确认保存
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setDraftId(null)}
                  >
                    取消
                  </Button>
                </div>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="flex flex-col gap-2">
                  <span className="text-muted-foreground text-xs font-medium">
                    主题色板
                  </span>
                  <div className="flex flex-wrap gap-2">
                    {draftPrimary && (
                      <ColorSwatch
                        hex={draftPrimary}
                        label="primary(accent1)"
                      />
                    )}
                    {Object.entries(draftPalette)
                      .filter(([slot]) => slot !== "accent1")
                      .slice(0, 7)
                      .map(([slot, hex]) => (
                        <ColorSwatch key={slot} hex={hex} label={slot} />
                      ))}
                    {!draftPrimary &&
                      Object.keys(draftPalette).length === 0 && (
                        <span className="text-muted-foreground text-xs">
                          未解析到主题色（PPT 未使用主题色板）
                        </span>
                      )}
                  </div>
                  <span className="text-muted-foreground mt-1 text-xs font-medium">
                    字体
                  </span>
                  <Input
                    value={draftFont}
                    onChange={(e) => setDraftFont(e.target.value)}
                    placeholder="（无）"
                    className="h-8 text-xs"
                  />
                  <span className="text-muted-foreground mt-1 text-xs font-medium">
                    对应课程视频主题
                  </span>
                  <Select value={draftTheme} onValueChange={setDraftTheme}>
                    <SelectTrigger className="h-8 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {THEMES.map((t) => (
                        <SelectItem key={t.value} value={t.value}>
                          {t.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex flex-col gap-2">
                  <span className="text-muted-foreground text-xs font-medium">
                    LOGO
                  </span>
                  {draftLogoUrl ? (
                    <img
                      loading="lazy"
                      src={draftLogoUrl}
                      alt="logo"
                      className="h-24 w-48 rounded border object-contain p-1"
                    />
                  ) : (
                    <span className="text-muted-foreground text-xs">
                      未识别到 LOGO 图
                    </span>
                  )}
                  <span className="text-muted-foreground mt-1 text-xs font-medium">
                    版式清单（{draftLayouts.length}）
                  </span>
                  <div className="flex flex-wrap gap-1">
                    {draftLayouts.slice(0, 10).map((l) => (
                      <Badge
                        key={l}
                        variant="secondary"
                        className="text-[10px]"
                      >
                        {l}
                      </Badge>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 模板列表 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">我的 PPT 模板</CardTitle>
          <CardDescription>
            应用到课程项目后，PPT 大师与课程视频管线共享同一品牌包。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {redistillNote && (
            <div className="text-muted-foreground mb-3 rounded border p-2 text-xs">
              {redistillNote}
            </div>
          )}
          {loading ? (
            <div className="text-muted-foreground flex items-center gap-2 text-sm">
              <Loader2 className="size-4 animate-spin" /> 加载中…
            </div>
          ) : templates.length === 0 ? (
            <div className="text-muted-foreground py-6 text-center text-sm">
              还没有模板——上传一份公司 PPT 开始。
            </div>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {templates.map((tpl) => (
                <div
                  key={tpl.id}
                  className="flex items-center gap-3 rounded-lg border p-3"
                >
                  {tpl.has_logo ? (
                    <img
                      loading="lazy"
                      src={pptTemplateLogoUrl(tpl.id)}
                      alt=""
                      className="size-12 shrink-0 rounded border object-contain p-0.5"
                    />
                  ) : (
                    <div className="bg-muted size-12 shrink-0 rounded" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">
                      {tpl.name}
                    </div>
                    <div className="text-muted-foreground flex items-center gap-2 text-xs">
                      {tpl.palette?.primary && (
                        <ColorSwatch
                          hex={tpl.palette.primary}
                          label="primary"
                        />
                      )}
                      <span className="truncate">
                        {tpl.font_family ?? "无字体"}
                      </span>
                      {tpl.remotion_theme && (
                        <Badge variant="outline" className="text-[10px]">
                          {THEMES.find((t) => t.value === tpl.remotion_theme)
                            ?.label ?? tpl.remotion_theme}
                        </Badge>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => void openApply(tpl)}
                    >
                      应用到课程项目
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label="重新蒸馏品牌要素"
                      disabled={redistillingId === tpl.id}
                      onClick={() => void handleRedistill(tpl)}
                    >
                      {redistillingId === tpl.id ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <RotateCw className="size-4" />
                      )}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label="删除模板"
                      onClick={() => void handleDelete(tpl.id)}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 应用到课程项目弹层 */}
      <Dialog open={applyOpen} onOpenChange={setApplyOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>应用到课程项目</DialogTitle>
            <DialogDescription>
              将「{applyTarget?.name}」的品牌字段（主色 / 字体 / 主题 /
              LOGO）写入所选课程项目。
            </DialogDescription>
          </DialogHeader>
          <Select value={applyProjectId} onValueChange={setApplyProjectId}>
            <SelectTrigger className="text-sm">
              <SelectValue placeholder="选择课程项目" />
            </SelectTrigger>
            <SelectContent>
              {projects.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {/* DSN-013：同名项目可区分（创建日期 + id 尾 4 位） */}
                  {projectOptionLabel(p)}
                </SelectItem>
              ))}
              {projects.length === 0 && (
                <div className="text-muted-foreground px-3 py-2 text-xs">
                  暂无课程项目（在视频页创建）
                </div>
              )}
            </SelectContent>
          </Select>
          {applyResult && (
            <div className="text-muted-foreground rounded border p-2 text-xs">
              {applyResult}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setApplyOpen(false)}>
              关闭
            </Button>
            <Button
              disabled={!applyProjectId || applying}
              onClick={() => void handleApply()}
            >
              {applying && <Loader2 className="size-4 animate-spin" />}
              应用
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
