"use client";

import DOMPurify from "dompurify";
import { toPng } from "html-to-image";
import {
  AlertCircle,
  Check,
  Clipboard,
  Download,
  Image as ImageIcon,
  Loader2,
  Palette,
  RefreshCw,
  Sparkles,
  Upload,
  Wand2,
} from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  type BrandType,
  parseBrandVars,
  renderBrandTemplate,
} from "@/components/workspace/writer/brand-templates";
import { streamAssist } from "@/core/writer/api";
import {
  ACCEPTED_UPLOAD_MIME_TYPES,
  generateImage,
  type GenerateImageErrorInfo,
  type GeneratedImage,
  isValidUploadFile,
  MAX_UPLOAD_BYTES,
  parseGenerateImageError,
  uploadImage,
  UPLOAD_ACCEPT_ATTR,
} from "@/core/writer/assets-api";
import { cn } from "@/lib/utils";

// ── Types ────────────────────────────────────────────────────────────────

type DesignTab = "ai" | "upload" | "brand";

interface DesignPanelProps {
  markdown: string;
  docTitle?: string;
  onInsertImage: (src: string, alt?: string) => void;
}

// ── Brand design types ───────────────────────────────────────────────────

const BRAND_TYPES: { value: BrandType; label: string }[] = [
  { value: "cover", label: "文章封面海报" },
  { value: "social", label: "社媒分享卡" },
  { value: "chart", label: "数据可视化图表" },
];

// ── Upload tab validation (W21) ───────────────────────────────────────────
// B6-T6: constants moved to assets-api.ts for reuse by editor-toolbar.
// Re-exported here so existing imports from design-panel don't break.
export {
  ACCEPTED_UPLOAD_MIME_TYPES,
  isValidUploadFile,
  MAX_UPLOAD_BYTES,
  UPLOAD_ACCEPT_ATTR,
};

// ── Component ────────────────────────────────────────────────────────────

export function DesignPanel({
  markdown,
  docTitle,
  onInsertImage,
}: DesignPanelProps) {
  const [tab, setTab] = useState<DesignTab>("ai");

  const tabs: { id: DesignTab; label: string; icon: typeof Sparkles }[] = [
    { id: "ai", label: "AI 生图", icon: Sparkles },
    { id: "upload", label: "上传", icon: Upload },
    { id: "brand", label: "设计", icon: Palette },
  ];

  return (
    <div className="flex h-full flex-col gap-2">
      {/* Sub-tab buttons */}
      <div className="flex shrink-0 gap-1 border-b border-[var(--border)] px-2 pb-1.5">
        {tabs.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={cn(
                "flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors",
                tab === t.id
                  ? "bg-[var(--primary)]/15 text-[var(--primary)]"
                  : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
              )}
            >
              <Icon size={11} />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "ai" && <AIGenerateTab onInsertImage={onInsertImage} />}
        {tab === "upload" && <UploadTab onInsertImage={onInsertImage} />}
        {tab === "brand" && (
          <BrandDesignTab
            markdown={markdown}
            docTitle={docTitle}
            onInsertImage={onInsertImage}
          />
        )}
      </div>
    </div>
  );
}

// ── L2: AI Generate Tab ──────────────────────────────────────────────────

const SIZE_OPTIONS = [
  { value: "1024x1024", label: "1:1 方形" },
  { value: "1152x2048", label: "9:16 竖屏" },
  { value: "2048x1152", label: "16:9 横屏" },
];

function AIGenerateTab({
  onInsertImage,
}: {
  onInsertImage: (src: string, alt?: string) => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [size, setSize] = useState("1024x1024");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<GeneratedImage | null>(null);
  const [error, setError] = useState<GenerateImageErrorInfo | null>(null);

  const handleGenerate = useCallback(async () => {
    if (!prompt.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const img = await generateImage({ prompt: prompt.trim(), size });
      setResult(img);
    } catch (err) {
      setError(parseGenerateImageError(err));
    } finally {
      setLoading(false);
    }
  }, [prompt, size]);

  return (
    <div className="flex h-full flex-col gap-2 p-3">
      {/* Prompt input */}
      <div>
        <label className="mb-1 block text-xs text-[var(--muted-foreground)]">
          描述
        </label>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="描述你想要的图片，例如：科技感未来城市夜景，霓虹灯光..."
          rows={3}
          className="w-full resize-none rounded-md border border-[var(--border)] bg-[var(--secondary)]/50 px-2.5 py-1.5 text-xs text-[var(--foreground)] focus:outline-none"
        />
      </div>

      {/* Size */}
      <div>
        <label className="mb-1 block text-xs text-[var(--muted-foreground)]">
          尺寸
        </label>
        <div className="flex gap-1">
          {SIZE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setSize(opt.value)}
              className={cn(
                "rounded-md px-2 py-1 text-xs transition-colors",
                size === opt.value
                  ? "bg-[var(--primary)]/15 text-[var(--primary)]"
                  : "bg-[var(--secondary)]/50 text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Generate button */}
      <Button
        size="sm"
        onClick={handleGenerate}
        disabled={loading || !prompt.trim()}
      >
        {loading ? (
          <>
            <Loader2 size={13} className="animate-spin" />
            生成中...
          </>
        ) : (
          <>
            <Wand2 size={13} />
            生成图片
          </>
        )}
      </Button>

      {/* Error */}
      {error && (
        <div className="space-y-1.5 rounded-md border border-rose-500/30 bg-rose-500/5 p-2">
          <div className="flex items-center gap-1.5 text-xs text-rose-500">
            <AlertCircle size={12} />
            {error.message}
          </div>
          {error.action && (
            <div className="text-xs text-[var(--muted-foreground)]">
              {error.action}
            </div>
          )}
          <Button
            size="sm"
            variant="secondary"
            onClick={handleGenerate}
            disabled={loading || !prompt.trim()}
            className="w-full"
          >
            <RefreshCw size={11} />
            重试
          </Button>
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="space-y-1.5">
          <img
            loading="lazy"
            src={result.url}
            alt={prompt}
            className="w-full rounded-md border border-[var(--border)]"
          />
          <div className="flex gap-1.5">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onInsertImage(result.url, prompt.slice(0, 50))}
              className="flex-1"
            >
              <ImageIcon size={11} />
              插入
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={handleGenerate}
              className="flex-1"
            >
              <Sparkles size={11} />
              重新生成
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── L2: Upload Tab (W21 — 本地上传 as first-class 配图 tab) ──────────────

function UploadTab({
  onInsertImage,
}: {
  onInsertImage: (src: string, alt?: string) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(
    async (file: File) => {
      if (!isValidUploadFile(file)) {
        const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
        if (
          file.type === "image/svg+xml" ||
          file.name.toLowerCase().endsWith(".svg")
        ) {
          toast.error("不支持 SVG 格式，请上传 PNG/JPEG/GIF/WebP 图片");
        } else if (file.size > MAX_UPLOAD_BYTES) {
          toast.error(`图片大小 ${sizeMb}MB 超过 10MB 限制`);
        } else {
          toast.error("不支持的图片格式，仅支持 PNG/JPEG/GIF/WebP");
        }
        return;
      }

      setLoading(true);
      setPreview(null);
      try {
        const result = await uploadImage(file);
        setPreview(result.url);
        onInsertImage(result.url, file.name);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "上传失败，请重试");
      } finally {
        setLoading(false);
      }
    },
    [onInsertImage],
  );

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) void handleFile(file);
      // Reset so the same file can be re-selected
      e.target.value = "";
    },
    [handleFile],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files?.[0];
      if (file) void handleFile(file);
    },
    [handleFile],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  }, []);

  return (
    <div className="flex h-full flex-col gap-2 p-3">
      {/* Drop zone / file picker */}
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-md border border-dashed px-3 py-6 text-center transition-colors",
          dragOver
            ? "border-[var(--primary)] bg-[var(--primary)]/10"
            : "border-[var(--border)] hover:border-[var(--primary)]/50 hover:bg-[var(--secondary)]/30",
        )}
      >
        {loading ? (
          <>
            <Loader2 size={20} className="animate-spin text-[var(--primary)]" />
            <span className="text-xs text-[var(--muted-foreground)]">
              上传中...
            </span>
          </>
        ) : (
          <>
            <Upload size={20} className="text-[var(--muted-foreground)]" />
            <span className="text-xs text-[var(--foreground)]">
              点击或拖放图片到此上传
            </span>
            <span className="text-[10px] text-[var(--muted-foreground)]">
              PNG / JPEG / GIF / WebP，最大 10MB
            </span>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          accept={UPLOAD_ACCEPT_ATTR}
          onChange={handleInputChange}
          className="hidden"
          disabled={loading}
        />
      </div>

      {/* Preview + insert confirmation */}
      {preview && !loading && (
        <div className="space-y-1.5">
          <img
            loading="lazy"
            src={preview}
            alt="上传预览"
            className="w-full rounded-md border border-[var(--border)]"
          />
          <div className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
            <Check size={11} className="text-green-500" />
            已插入文档
          </div>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => onInsertImage(preview)}
            className="w-full"
          >
            <ImageIcon size={11} />
            再次插入
          </Button>
        </div>
      )}
    </div>
  );
}

// ── L3: Brand Design Tab (W20 — templated generation, no heavy agent) ─────

function BrandDesignTab({
  markdown,
  docTitle,
  onInsertImage,
}: {
  markdown: string;
  docTitle?: string;
  onInsertImage: (src: string, alt?: string) => void;
}) {
  const [designType, setDesignType] = useState<BrandType>("cover");
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [renderedHtml, setRenderedHtml] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  // B6-T5: PNG export state
  const [pixelRatio, setPixelRatio] = useState(2);
  const [exporting, setExporting] = useState(false);
  const exportContainerRef = useRef<HTMLDivElement>(null);

  // Security: sanitize renderedHtml before injecting into the hidden export container.
  // The iframe preview has sandbox="" (C9 2026-10-05 审计 §5-1：allow-same-origin
  // 同样不给——同源 iframe 仍可触 DOM/cookie 面；对齐 SEC-H10 写法);
  // the hidden div runs in the main document context where on* handlers fire.
  const sanitizedRenderedHtml = useMemo(
    () =>
      renderedHtml
        ? DOMPurify.sanitize(renderedHtml, {
            ADD_ATTR: ["width", "height", "style"],
            FORBID_TAGS: ["script"],
          })
        : "",
    [renderedHtml],
  );

  const handleStart = useCallback(async () => {
    setLoading(true);
    setError(null);
    setStreamingText("");
    setRenderedHtml("");
    setCopied(false);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    // Safety timeout: abort if the stream hangs for > 2 minutes
    const timeout = setTimeout(() => controller.abort(), 120_000);

    try {
      const typeLabel =
        BRAND_TYPES.find((t) => t.value === designType)?.label ?? "设计";
      const instruction = [
        `请根据以下文档内容，为一个${typeLabel}生成品牌设计文案。`,
        `返回纯JSON格式（不要markdown代码围栏），结构如下：`,
        `{"title":"主标题","subtitle":"副标题","points":["要点1","要点2","要点3"],"footer":"页脚","colors":{"primary":"#主色hex","secondary":"#辅色hex","accent":"#强调色hex","bg":"#背景色hex"}}`,
        `只返回JSON对象，不要其他文字。`,
        note ? `补充说明：${note}` : "",
      ]
        .filter(Boolean)
        .join("\n");

      const text = [
        docTitle ? `文档标题：${docTitle}` : "",
        `文档内容摘要：${markdown.slice(0, 500)}`,
      ]
        .filter(Boolean)
        .join("\n");

      // Light AI fill via streamAssist (/api/ai/assist) — NO heavy
      // creative-studio agent. The LLM returns JSON vars; we parse +
      // render locally (trusted template + escaped vars → no sanitizeHtml).
      let accumulated = "";
      for await (const chunk of streamAssist(
        {
          op: "rewrite",
          text,
          instruction,
        },
        controller.signal,
      )) {
        if (controller.signal.aborted) return;
        accumulated += chunk;
        setStreamingText(accumulated);
      }

      if (controller.signal.aborted) return;

      const vars = parseBrandVars(accumulated);
      if (!vars) {
        setError("AI 返回的内容无法解析为设计文案，请重试");
        return;
      }
      const html = renderBrandTemplate(designType, vars);
      setRenderedHtml(html);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        setError("设计请求超时或被取消");
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      clearTimeout(timeout);
      setLoading(false);
    }
  }, [designType, note, docTitle, markdown]);

  const handleCopy = useCallback(() => {
    void navigator.clipboard.writeText(renderedHtml).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [renderedHtml]);

  // B6-T5: export rendered HTML as PNG via a hidden DOM node (can't screenshot iframe srcDoc)
  const handleExportPng = useCallback(
    async (mode: "insert" | "download") => {
      const node = exportContainerRef.current;
      if (!node || !renderedHtml) return;
      setExporting(true);
      try {
        const dataUrl = await toPng(node, {
          pixelRatio,
          cacheBust: true,
        });
        if (mode === "download") {
          const link = document.createElement("a");
          link.download = `${docTitle ?? "品牌设计"}.png`;
          link.href = dataUrl;
          link.click();
        } else {
          // Insert into document: dataURL → Blob → upload → insertImage
          const blob = await (await fetch(dataUrl)).blob();
          const file = new File([blob], `${docTitle ?? "design"}.png`, {
            type: "image/png",
          });
          const result = await uploadImage(file);
          onInsertImage(result.url, "品牌设计图");
        }
      } catch {
        toast.error("图片导出失败，请重试");
      } finally {
        setExporting(false);
      }
    },
    [renderedHtml, pixelRatio, docTitle, onInsertImage],
  );

  return (
    <div className="flex h-full flex-col gap-2 p-3">
      {/* Design type */}
      <div>
        <label className="mb-1 block text-xs text-[var(--muted-foreground)]">
          设计类型
        </label>
        <div className="flex flex-col gap-1">
          {BRAND_TYPES.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setDesignType(opt.value)}
              className={cn(
                "rounded-md px-2.5 py-1.5 text-left text-xs transition-colors",
                designType === opt.value
                  ? "bg-[var(--primary)]/15 text-[var(--primary)]"
                  : "bg-[var(--secondary)]/30 text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Note */}
      <div>
        <label className="mb-1 block text-xs text-[var(--muted-foreground)]">
          补充说明
        </label>
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="风格、配色、品牌等..."
          className="w-full rounded-md border border-[var(--border)] bg-[var(--secondary)]/50 px-2.5 py-1.5 text-xs text-[var(--foreground)] focus:outline-none"
        />
      </div>

      {/* Start button */}
      <Button size="sm" onClick={handleStart} disabled={loading}>
        {loading ? (
          <>
            <Loader2 size={13} className="animate-spin" />
            设计中...
          </>
        ) : (
          <>
            <Palette size={13} />
            启动设计
          </>
        )}
      </Button>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-1.5 text-xs text-rose-500">
          <AlertCircle size={12} />
          {error}
        </div>
      )}

      {/* Streaming progress (raw LLM text while loading) */}
      {loading && streamingText && (
        <div className="space-y-1">
          <div className="text-xs text-[var(--muted-foreground)]">
            正在生成文案... ({streamingText.length} 字)
          </div>
          <pre className="max-h-32 overflow-y-auto rounded-md border border-[var(--border)] bg-[var(--secondary)]/30 p-2 text-[10px] text-[var(--muted-foreground)]">
            {streamingText.slice(-500)}
          </pre>
        </div>
      )}

      {/* Rendered preview */}
      {renderedHtml && (
        <div className="space-y-1.5">
          <div className="text-xs text-[var(--muted-foreground)]">设计预览</div>
          <iframe
            srcDoc={renderedHtml}
            sandbox=""
            className="w-full rounded-md border border-[var(--border)]"
            style={{ minHeight: "120px" }}
            title="设计预览"
          />
          {/* B6-T5: hidden DOM node for html-to-image (can't screenshot iframe srcDoc).
              Security: renderedHtml is sanitized via DOMPurify before injection —
              unlike the sandboxed iframe preview, this div runs in the main document
              context where <img loading="lazy" onerror>/<svg onload> would execute. */}
          <div
            ref={exportContainerRef}
            style={{ position: "absolute", left: "-9999px", top: 0 }}
            dangerouslySetInnerHTML={{
              __html: sanitizedRenderedHtml,
            }}
          />
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={handleCopy}
              className="flex flex-1 items-center justify-center gap-1 rounded-md border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"
            >
              {copied ? <Check size={11} /> : <Clipboard size={11} />}
              {copied ? "已复制" : "复制 HTML"}
            </button>
            {/* B6-T5: PNG export with resolution selector */}
            <select
              value={pixelRatio}
              onChange={(e) => setPixelRatio(Number(e.target.value))}
              className="rounded-md border border-[var(--border)] px-1 py-1 text-xs text-[var(--muted-foreground)]"
              title="PNG 清晰度"
            >
              <option value={1}>普通</option>
              <option value={2}>高清</option>
              <option value={3}>超清</option>
            </select>
            <button
              type="button"
              onClick={() => void handleExportPng("insert")}
              disabled={exporting}
              className="flex items-center justify-center gap-1 rounded-md border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)] disabled:opacity-50"
            >
              <ImageIcon size={11} />
              插入文档
            </button>
            <button
              type="button"
              onClick={() => void handleExportPng("download")}
              disabled={exporting}
              className="flex items-center justify-center gap-1 rounded-md border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)] disabled:opacity-50"
            >
              {exporting ? (
                <Loader2 size={11} className="animate-spin" />
              ) : (
                <Download size={11} />
              )}
              下载 PNG
            </button>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!loading && !renderedHtml && !error && (
        <div className="flex flex-1 items-center justify-center text-xs text-[var(--muted-foreground)]">
          选择设计类型后启动
        </div>
      )}
    </div>
  );
}
