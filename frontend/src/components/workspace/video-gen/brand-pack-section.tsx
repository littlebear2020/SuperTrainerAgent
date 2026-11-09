"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Boxes, Presentation } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
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
import { Textarea } from "@/components/ui/textarea";
import { applyPptTemplateToProject, listPptTemplates } from "@/core/assets/api";
import { uploadProjectLogo } from "@/core/video-gen/api";
import { useUpdateCourseProject } from "@/core/video-gen/hooks";

import { composeBrandPptPrompt, jumpToBrandPptChat } from "./brand-ppt-handoff";

/**
 * T9（2026-09-08 remotion-course）：品牌包配置区。
 *
 * 字段（CourseProject A 类）：remotion_theme（4 预设）/ font_family / LOGO 上传 /
 * intro_outro（片头片尾文案）。右侧实时预览 = 当前主题的 M0 版式示意图
 * （静态资产，不压 still 槽——品牌字段变化足够触发重渲预览见 T7 通道）。
 * 保存走项目 PUT（exclude_unset；清空显式传 ""）。
 */

const THEMES = [
  { value: "dark-blue", label: "深空蓝（默认）" },
  { value: "light", label: "明亮浅色" },
  { value: "dark-green", label: "墨绿沉稳" },
  { value: "warm", label: "暖色纸感" },
] as const;

// T-E7（R22）：每主题用自己的封面渲染图（修前四主题共用同一套 dark-blue 截图，
// 3/4 主题预览配错图）。
const THEME_PREVIEW: Record<string, string> = {
  "dark-blue": "/remotion-layouts/dark-blue/cover-page.png",
  light: "/remotion-layouts/light/cover-page.png",
  "dark-green": "/remotion-layouts/dark-green/cover-page.png",
  warm: "/remotion-layouts/warm/cover-page.png",
};

const FONT_PRESETS = [
  { value: "", label: "跟随主题默认" },
  {
    value: '"PingFang SC","Microsoft YaHei","Noto Sans SC",sans-serif',
    label: "苹方 / 雅黑（默认）",
  },
  { value: 'Georgia,"Songti SC","STSong",serif', label: "宋体衬线（稳重）" },
  {
    value: '"SF Mono","JetBrains Mono",Consolas,monospace',
    label: "等宽（科技感）",
  },
] as const;

interface BrandPackSectionProps {
  projectId: string;
  project: import("@/core/video-gen/types").CourseProject;
}

export function BrandPackSection({
  projectId,
  project,
}: BrandPackSectionProps) {
  const updateMut = useUpdateCourseProject();
  const [theme, setTheme] = useState("");
  const [fontFamily, setFontFamily] = useState("");
  const [introHeadline, setIntroHeadline] = useState("");
  const [introSubtitle, setIntroSubtitle] = useState("");
  const [outroHeadline, setOutroHeadline] = useState("");
  const [outroFollow, setOutroFollow] = useState("");
  const [logoUrl, setLogoUrl] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  // Phase 3.5（2026-09-11）：从 PPT 模板导入品牌（资产中心注册表）
  const qc = useQueryClient();
  const router = useRouter();
  const [importOpen, setImportOpen] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [importMsg, setImportMsg] = useState<string | null>(null);

  // 切项目灌表单（同页内 syncedIdRef 纪律：只在 project.id 变化时灌）
  const syncedIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (syncedIdRef.current === projectId) return;
    syncedIdRef.current = projectId;
    setTheme(project.remotion_theme ?? "");
    setFontFamily(project.font_family ?? "");
    setIntroHeadline(project.intro_outro?.intro?.headline ?? "");
    setIntroSubtitle(project.intro_outro?.intro?.subtitle ?? "");
    setOutroHeadline(project.intro_outro?.outro?.headline ?? "");
    setOutroFollow(project.intro_outro?.outro?.follow ?? "");
  }, [projectId, project]);

  useEffect(() => {
    setLogoUrl(
      project.logo_path
        ? `/api/video/projects/${projectId}/brand/logo-image?v=${encodeURIComponent(project.logo_path)}`
        : null,
    );
  }, [projectId, project.logo_path]);

  const handleSave = () => {
    updateMut.mutate({
      id: projectId,
      input: {
        remotion_theme: theme || "",
        font_family: fontFamily || "",
        intro_outro:
          introHeadline || introSubtitle || outroHeadline || outroFollow
            ? {
                intro: { headline: introHeadline, subtitle: introSubtitle },
                outro: { headline: outroHeadline, follow: outroFollow },
              }
            : {},
      },
    });
  };

  const handleUploadLogo = async (file: File) => {
    // T-E1（R15）：走带 CSRF 的 fetcher（修前裸 fetch 无 X-CSRF-Token，浏览器流必 403）
    try {
      const body = await uploadProjectLogo(projectId, file);
      setLogoUrl(
        `/api/video/projects/${projectId}/brand/logo-image?v=${encodeURIComponent(body.logo_path)}`,
      );
    } catch (err) {
      alert(err instanceof Error ? err.message : "LOGO 上传失败");
    }
  };

  // Phase 3.5c：消费侧反向打通——项目品牌 → ppt-master 新会话（同字段同源）
  const handleJumpToPptMaster = () => {
    const prompt = composeBrandPptPrompt({
      projectTitle: project.title ?? "未命名课程项目",
      remotionTheme: project.remotion_theme ?? null,
      primaryColor: project.color_palette?.primary
        ? String(project.color_palette.primary)
        : null,
      fontFamily: project.font_family ?? null,
      hasLogo: Boolean(project.logo_path),
    });
    // 传 sessionStorage 激活超长提示词兜底通路（修前从不传 → 兜底分支生产死代码）
    jumpToBrandPptChat(
      prompt,
      router,
      typeof window === "undefined" ? null : window.sessionStorage,
    );
  };

  const handleImportFromTemplate = async (templateId: string) => {
    setImportBusy(true);
    setImportMsg(null);
    try {
      const result = await applyPptTemplateToProject(templateId, projectId);
      setImportMsg(
        `已应用：${result.applied_fields.join("、") || "无"}` +
          (result.logo_note ? `（${result.logo_note}）` : ""),
      );
      await qc.invalidateQueries({ queryKey: ["courseProjects"] });
    } catch (err) {
      setImportMsg(err instanceof Error ? err.message : "应用失败");
    } finally {
      setImportBusy(false);
    }
  };

  return (
    <section className="rounded-lg border p-4">
      <h3 className="mb-1 text-sm font-semibold">品牌包（课程视频）</h3>
      <p className="text-muted-foreground mb-4 text-xs">
        课程视频（remotion
        模板渲染）的视觉品牌：主题配色、字体、LOGO、片头片尾。系列课程共享同一品牌包。
      </p>
      <div className="mb-3 flex gap-2">
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          onClick={() => {
            setImportMsg(null);
            setImportOpen(true);
          }}
        >
          <Boxes className="size-3.5" /> 从 PPT 模板导入
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          onClick={handleJumpToPptMaster}
        >
          <Presentation className="size-3.5" /> 用此品牌生成 PPT
        </Button>
      </div>
      <div className="grid grid-cols-[1fr_240px] gap-6">
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1.5">
              <span className="text-muted-foreground text-xs font-medium">
                主题配色
              </span>
              <Select value={theme || "dark-blue"} onValueChange={setTheme}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {THEMES.map((t) => (
                    <SelectItem
                      key={t.value}
                      value={t.value}
                      className="text-xs"
                    >
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <span className="text-muted-foreground text-xs font-medium">
                字体
              </span>
              {/* T-E2（R16）：哨兵 __default__ ↔ 空串映射——Select 值直接进 state 会把
                  "__default__" 存库（font_family 哨兵泄漏）。 */}
              <Select
                value={fontFamily || "__default__"}
                onValueChange={(v) =>
                  setFontFamily(v === "__default__" ? "" : v)
                }
              >
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FONT_PRESETS.map((f) => (
                    <SelectItem
                      key={f.value || "__default__"}
                      value={f.value || "__default__"}
                      className="text-xs"
                    >
                      {f.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="text-muted-foreground text-xs font-medium">
              品牌 LOGO（角标 + 封面/片尾）
            </span>
            <div className="flex items-center gap-3">
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/svg+xml"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void handleUploadLogo(f);
                  e.target.value = "";
                }}
              />
              {logoUrl ? (
                <img
                  loading="lazy"
                  src={logoUrl}
                  alt="LOGO"
                  className="h-10 w-10 rounded border object-contain"
                />
              ) : (
                <div className="border-muted text-muted-foreground flex h-10 w-10 items-center justify-center rounded border text-[10px]">
                  无
                </div>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => fileRef.current?.click()}
              >
                上传 LOGO
              </Button>
              <span className="text-muted-foreground text-[10px]">
                PNG/JPG/WEBP/SVG，≤5MB
              </span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1.5">
              <span className="text-muted-foreground text-xs font-medium">
                片头标题
              </span>
              <Input
                value={introHeadline}
                onChange={(e) => setIntroHeadline(e.target.value)}
                placeholder="如：开学第一课"
                className="h-8 text-xs"
              />
              <span className="text-muted-foreground text-xs font-medium">
                片头副标题
              </span>
              <Input
                value={introSubtitle}
                onChange={(e) => setIntroSubtitle(e.target.value)}
                placeholder="如：欢迎加入本课程"
                className="h-8 text-xs"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <span className="text-muted-foreground text-xs font-medium">
                片尾标题
              </span>
              <Input
                value={outroHeadline}
                onChange={(e) => setOutroHeadline(e.target.value)}
                placeholder="默认：感谢观看"
                className="h-8 text-xs"
              />
              <span className="text-muted-foreground text-xs font-medium">
                片尾引导文案
              </span>
              <Textarea
                value={outroFollow}
                onChange={(e) => setOutroFollow(e.target.value)}
                placeholder="如：关注公众号获取课件"
                className="min-h-[52px] text-xs"
              />
            </div>
          </div>

          <div>
            <Button
              size="sm"
              onClick={handleSave}
              disabled={updateMut.isPending}
            >
              {updateMut.isPending ? "保存中…" : "保存品牌包"}
            </Button>
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <span className="text-muted-foreground text-xs font-medium">
            主题预览
          </span>
          <div className="border-muted overflow-hidden rounded-md border">
            {}
            <img
              loading="lazy"
              src={
                THEME_PREVIEW[theme || "dark-blue"] ??
                THEME_PREVIEW["dark-blue"]
              }
              alt={`${theme || "dark-blue"} 主题预览`}
              className="w-full object-contain"
            />
          </div>
          <p className="text-muted-foreground text-[10px]">
            逐帧所见即所得预览在分镜编辑器（课程视频任务）中查看。
          </p>
        </div>
      </div>

      {/* Phase 3.5：从资产中心 PPT 模板导入品牌（模板列表打开时懒加载） */}
      <ImportFromPptTemplateDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        busy={importBusy}
        message={importMsg}
        onApply={handleImportFromTemplate}
      />
    </section>
  );
}

function ImportFromPptTemplateDialog({
  open,
  onOpenChange,
  busy,
  message,
  onApply,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  busy: boolean;
  message: string | null;
  onApply: (templateId: string) => void | Promise<void>;
}) {
  const [templates, setTemplates] = useState<
    Awaited<ReturnType<typeof listPptTemplates>>
  >([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    listPptTemplates()
      .then((items) => {
        if (!cancelled) setTemplates(items);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setLoadError(err instanceof Error ? err.message : "模板列表加载失败");
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>从 PPT 模板导入</DialogTitle>
          <DialogDescription>
            把资产中心蒸馏出的品牌要素（主色 / 字体 / 主题 /
            LOGO）写入本项目品牌包。
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-64 space-y-2 overflow-y-auto">
          {loadError && <p className="text-destructive text-xs">{loadError}</p>}
          {templates.length === 0 && !loadError && (
            <p className="text-muted-foreground py-4 text-center text-xs">
              还没有 PPT 模板——到「资产中心」上传一份公司 PPT。
            </p>
          )}
          {templates.map((tpl) => (
            <div
              key={tpl.id}
              className="flex items-center gap-3 rounded-md border p-2"
            >
              <span
                className="size-4 shrink-0 rounded border border-black/10"
                style={{ backgroundColor: tpl.palette?.primary ?? "#e2e8f0" }}
                title={tpl.palette?.primary ?? ""}
              />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm">{tpl.name}</div>
                <div className="text-muted-foreground truncate text-xs">
                  {tpl.font_family ?? "无字体"} ·{" "}
                  {tpl.remotion_theme ?? "未映射主题"}
                </div>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => void onApply(tpl.id)}
              >
                导入
              </Button>
            </div>
          ))}
        </div>
        {message && (
          <p className="text-muted-foreground rounded border p-2 text-xs">
            {message}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
