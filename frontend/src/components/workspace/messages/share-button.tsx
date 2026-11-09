import { Share2 } from "lucide-react";
import React, { useState, useEffect, Component, type ReactNode } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogFooter,
} from "@/components/ui/dialog";
import { RecipeEditor } from "@/components/workspace/gallery/recipe-editor";
import { ThumbnailFields } from "@/components/workspace/gallery/thumbnail-fields";
import {
  extractRecipe,
  generateThumbnail,
  loadGalleryCategories,
  shareCase,
  type Recipe,
} from "@/core/gallery/api";

// Error Boundary Class Component for absolute isolation
export class ShareErrorBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean }
> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: unknown, errorInfo: unknown) {
    console.error("Share component failed:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      // Fail silently and render nothing, ensuring conversation remains unbroken
      return null;
    }
    return this.props.children;
  }
}

interface ShareButtonProps {
  promptText: string;
  threadId: string;
}

export function ShareButton({ promptText, threadId }: ShareButtonProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("办公效率");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [recipe, setRecipe] = useState<Recipe | null>(null);
  const [isExtracting, setIsExtracting] = useState(false);
  const [thumbnail, setThumbnail] = useState<string | null>(null);
  const [isGeneratingThumb, setIsGeneratingThumb] = useState(false);

  const handleGenerateThumbnail = async () => {
    setIsGeneratingThumb(true);
    try {
      // 内容感知封面（2026-07-27）：把灵感实际内容带给后端——recipe 目标优先，
      // 其次原始 prompt 截断；后端 GenerateThumbnailRequest.prompt 上限 500。
      const contentHint = (recipe?.goal ? `${recipe.goal}；` : "") + promptText;
      const { thumbnail: url } = await generateThumbnail({
        title,
        category,
        prompt: contentHint.slice(0, 480) || undefined,
      });
      setThumbnail(url);
      toast.success("缩略图已生成");
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "缩略图生成失败，可改用上传",
      );
    } finally {
      setIsGeneratingThumb(false);
    }
  };

  const handleExtractRecipe = async (e: React.MouseEvent) => {
    e.preventDefault();
    if (!title.trim()) {
      toast.error("请先填写案例标题");
      return;
    }
    setIsExtracting(true);
    try {
      const { recipe: extracted } = await extractRecipe(
        threadId,
        title,
        category,
      );
      setRecipe(extracted);
      toast.success("配方提取成功！请预览后确认发布。");
    } catch (err: unknown) {
      const status = (err as { status?: number }).status;
      if (status === 404) {
        toast.error("该对话记录已过期，无法提取配方");
      } else if (status === 422) {
        toast.error("AI 输出格式异常，请重试");
      } else if (typeof status === "number") {
        toast.error(
          err instanceof Error ? err.message : "配方提取失败，请重试",
        );
      } else {
        toast.error("网络异常，请重试");
      }
    } finally {
      setIsExtracting(false);
    }
  };

  const handleShare = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) {
      toast.error("请输入案例标题");
      return;
    }
    if (!promptText.trim()) {
      toast.error("未找到有效的 Prompt");
      return;
    }

    setIsSubmitting(true);
    try {
      await shareCase({
        title: title.trim(),
        prompt: promptText.trim(),
        category,
        thumbnail,
        recipe,
      });
      toast.success("分享成功！已加入技能灵感库。");
      setTitle("");
      setRecipe(null);
      setThumbnail(null);
      setIsOpen(false);
    } catch (err: unknown) {
      console.error("Failed to share case:", err);
      toast.error(err instanceof Error ? err.message : "分享失败，请重试");
    } finally {
      setIsSubmitting(false);
    }
  };

  // Categories are fetched from GET /api/gallery/categories (single source of
  // truth shared with backend validation); seeded with the known whitelist so
  // the dropdown is never empty even before the fetch resolves.
  const [categories, setCategories] = useState<string[]>([
    "办公效率",
    "内容创作",
    "视觉设计",
    "数据分析",
    "视频创作",
  ]);
  useEffect(() => {
    loadGalleryCategories()
      .then((cats) => {
        if (cats.length > 0) setCategories(cats);
      })
      .catch(() => {
        /* keep the seeded defaults on network error */
      });
  }, []);

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <button
          type="button"
          className="text-muted-foreground flex items-center gap-1 rounded-md p-1 transition-colors hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:text-indigo-400"
          title="分享到灵感库"
        >
          <Share2 className="size-4" />
          <span className="pr-0.5 text-[10px]">分享</span>
        </button>
      </DialogTrigger>

      <DialogContent className="glass-dropdown rounded-xl border-0 bg-transparent shadow-lg sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>💡 分享为灵感案例</DialogTitle>
        </DialogHeader>

        <form onSubmit={handleShare} className="space-y-4 py-2">
          <div className="space-y-1.5">
            <label className="text-foreground text-sm font-medium">
              案例标题
            </label>
            <input
              type="text"
              placeholder="例如：生成科技感的小说封面"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={100}
              className="border-input ring-offset-background placeholder:text-muted-foreground focus-visible:ring-ring flex h-10 w-full rounded-md border bg-transparent px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-hidden disabled:cursor-not-allowed disabled:opacity-50"
              required
              disabled={isSubmitting}
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-foreground text-sm font-medium">
              选择分类
            </label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-hidden disabled:cursor-not-allowed disabled:opacity-50"
              disabled={isSubmitting}
            >
              {categories.map((cat) => (
                <option key={cat} value={cat}>
                  {cat}
                </option>
              ))}
            </select>
          </div>

          {/* Thumbnail (optional: upload a screenshot or AI-generate a cover) */}
          <div className="space-y-1.5">
            <label className="text-foreground text-sm font-medium">
              封面缩略图（可选）
            </label>
            <ThumbnailFields
              variant="share"
              thumbnail={thumbnail}
              onThumbnailChange={setThumbnail}
              isGenerating={isGeneratingThumb}
              onGenerate={handleGenerateThumbnail}
              disabled={isSubmitting || !title.trim()}
            />
          </div>

          {/* Recipe extraction */}
          <div className="space-y-1.5">
            <button
              type="button"
              onClick={handleExtractRecipe}
              disabled={isExtracting || !title.trim()}
              className="flex items-center gap-1.5 rounded-lg bg-indigo-500/10 px-3 py-2 text-xs font-medium text-indigo-600 transition-colors hover:bg-indigo-500/20 disabled:opacity-50 dark:text-indigo-400"
            >
              {isExtracting ? (
                <>
                  <div className="h-3 w-3 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
                  正在提取配方...
                </>
              ) : recipe ? (
                <>✓ 配方已提取（点击重新提取）</>
              ) : (
                <>✨ AI 提炼创作配方</>
              )}
            </button>
            {recipe && (
              <RecipeEditor
                recipe={recipe}
                onChange={setRecipe}
                showMethodologyAndPitfalls
              />
            )}
          </div>

          <div className="space-y-1.5">
            <label className="text-foreground text-sm font-medium">
              分享的 Prompt
            </label>
            <textarea
              readOnly
              value={promptText}
              className="border-input bg-muted text-muted-foreground flex max-h-[180px] min-h-[100px] w-full rounded-md border px-3 py-2 text-xs focus-visible:outline-hidden"
              rows={4}
            />
            <p className="text-muted-foreground text-[10px]">
              {recipe
                ? "已附带 AI 提炼的创作配方，其他用户可按配方复刻。"
                : "简化版：未提取配方，仅以纯 Prompt 形式分享。可点上方「AI 提炼创作配方」生成结构化配方。"}
            </p>
          </div>

          <DialogFooter className="pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => setIsOpen(false)}
              disabled={isSubmitting}
            >
              取消
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "发布中..." : "确认发布"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
