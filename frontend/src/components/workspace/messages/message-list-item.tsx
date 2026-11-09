import type { Message } from "@langchain/langgraph-sdk";
import {
  FileIcon,
  Loader2Icon,
  ThumbsDownIcon,
  ThumbsUpIcon,
  TriangleAlert,
} from "lucide-react";
import {
  memo,
  useCallback,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
  type ImgHTMLAttributes,
} from "react";

import { Loader } from "@/components/ai-elements/loader";
import {
  Message as AIElementMessage,
  MessageContent as AIElementMessageContent,
  MessageToolbar,
} from "@/components/ai-elements/message";
import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { Task, TaskTrigger } from "@/components/ai-elements/task";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  deleteFeedback,
  upsertFeedback,
  type FeedbackData,
} from "@/core/api/feedback";
import { resolveArtifactURL } from "@/core/artifacts/utils";
import { useI18n } from "@/core/i18n/hooks";
import { submitThroughComposer } from "@/core/messages/composer";
import {
  extractContentFromMessage,
  extractReasoningContentFromMessage,
  parseUploadedFiles,
  stripInjectedReminderTags,
  stripInternalMarkers,
  stripUploadedFilesTag,
  type FileInMessage,
} from "@/core/messages/utils";
import { formatFileSize } from "@/core/utils/format";
import { cn } from "@/lib/utils";

import { CopyButton } from "../copy-button";

import { MarkdownContent } from "./markdown-content";
import { MessageTokenUsage } from "./message-token-usage";
import { ShareButton, ShareErrorBoundary } from "./share-button";

function FeedbackButtons({
  threadId,
  runId,
  initialFeedback,
}: {
  threadId: string;
  runId: string;
  initialFeedback: FeedbackData | null;
}) {
  const [feedback, setFeedback] = useState<FeedbackData | null>(
    initialFeedback,
  );
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleClick = useCallback(
    async (rating: number) => {
      if (isSubmitting) return;
      setIsSubmitting(true);
      try {
        if (feedback?.rating === rating) {
          await deleteFeedback(threadId, runId);
          setFeedback(null);
        } else {
          const result = await upsertFeedback(threadId, runId, rating);
          setFeedback(result);
        }
      } catch {
        // Revert on error — feedback state unchanged on catch
      } finally {
        setIsSubmitting(false);
      }
    },
    [threadId, runId, feedback, isSubmitting],
  );

  return (
    <div className="flex gap-1">
      <button
        type="button"
        className={cn(
          "text-muted-foreground hover:text-foreground rounded-md p-1 transition-colors",
          feedback?.rating === 1 && "text-foreground",
        )}
        onClick={() => handleClick(1)}
        disabled={isSubmitting}
      >
        <ThumbsUpIcon
          className={cn("size-4", feedback?.rating === 1 && "fill-current")}
        />
      </button>
      <button
        type="button"
        className={cn(
          "text-muted-foreground hover:text-foreground rounded-md p-1 transition-colors",
          feedback?.rating === -1 && "text-foreground",
        )}
        onClick={() => handleClick(-1)}
        disabled={isSubmitting}
      >
        <ThumbsDownIcon
          className={cn("size-4", feedback?.rating === -1 && "fill-current")}
        />
      </button>
    </div>
  );
}

function MessageListItemInner({
  className,
  threadId,
  message,
  isLoading,
  tokenUsageEnabled = false,
  feedback,
  runId,
  promptText,
  galleryEnabled = false,
}: {
  className?: string;
  message: Message;
  isLoading?: boolean;
  threadId: string;
  tokenUsageEnabled?: boolean;
  feedback?: FeedbackData | null;
  runId?: string;
  promptText?: string;
  galleryEnabled?: boolean;
}) {
  const isHuman = message.type === "human";
  return (
    <AIElementMessage
      className={cn("group/conversation-message relative w-full", className)}
      from={isHuman ? "user" : "assistant"}
    >
      <MessageContent
        className={isHuman ? "w-fit" : "w-full"}
        message={message}
        isLoading={isLoading}
        threadId={threadId}
        tokenUsageEnabled={tokenUsageEnabled}
      />
      {!isLoading && (
        <MessageToolbar
          className={cn(
            isHuman ? "-bottom-9 justify-end" : "-bottom-8",
            "absolute right-0 left-0 z-20",
          )}
        >
          <div className="flex gap-1">
            <CopyButton
              clipboardData={
                extractContentFromMessage(message) ??
                extractReasoningContentFromMessage(message) ??
                ""
              }
            />
            {feedback !== undefined && runId && threadId && (
              <FeedbackButtons
                threadId={threadId}
                runId={runId}
                initialFeedback={feedback}
              />
            )}
            {!isHuman && promptText && galleryEnabled && (
              <ShareErrorBoundary>
                <ShareButton promptText={promptText} threadId={threadId} />
              </ShareErrorBoundary>
            )}
          </div>
        </MessageToolbar>
      )}
    </AIElementMessage>
  );
}

/**
 * Custom image component that handles artifact URLs
 */
function MessageImage({
  src,
  alt,
  threadId,
  maxWidth = "90%",
  ...props
}: React.ImgHTMLAttributes<HTMLImageElement> & {
  threadId: string;
  maxWidth?: string;
}) {
  if (!src) return null;

  const imgClassName = cn("overflow-hidden rounded-lg", `max-w-[${maxWidth}]`);

  if (typeof src !== "string") {
    return (
      <img
        loading="lazy"
        className={imgClassName}
        src={src}
        alt={alt}
        {...props}
      />
    );
  }

  const url = src.startsWith("/mnt/") ? resolveArtifactURL(src, threadId) : src;

  return (
    <a href={url} target="_blank" rel="noopener noreferrer">
      <img
        loading="lazy"
        className={imgClassName}
        src={url}
        alt={alt}
        {...props}
      />
    </a>
  );
}

function MessageContent_({
  className,
  message,
  isLoading = false,
  threadId,
  tokenUsageEnabled = false,
}: {
  className?: string;
  message: Message;
  isLoading?: boolean;
  threadId: string;
  tokenUsageEnabled?: boolean;
}) {
  const isHuman = message.type === "human";
  const { t } = useI18n();
  // B2（2026-09-23 批 1）：后端 TerminalResponseMiddleware 空响应兜底产物带
  // deerflow_error_fallback 标记 → 渲染错误卡片（真实事件契约，见 plan A4/B2）。
  const isFallbackError =
    !isHuman && message.additional_kwargs?.deerflow_error_fallback === true;
  const components = useMemo(
    () => ({
      img: (props: ImgHTMLAttributes<HTMLImageElement>) => (
        <MessageImage {...props} threadId={threadId} maxWidth="90%" />
      ),
      a: ({ href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => {
        if (href?.startsWith("/mnt/")) {
          const url = resolveArtifactURL(href, threadId);
          return (
            <a
              {...props}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
            />
          );
        }
        return <a {...props} href={href} />;
      },
    }),
    [threadId],
  );

  const rawContent = extractContentFromMessage(message);
  const reasoningContent = extractReasoningContentFromMessage(message);

  const files = useMemo(() => {
    const files = message.additional_kwargs?.files;
    if (!Array.isArray(files) || files.length === 0) {
      if (rawContent.includes("<uploaded_files>")) {
        // If the content contains the <uploaded_files> tag, we return the parsed files from the content for backward compatibility.
        return parseUploadedFiles(rawContent);
      }
      return null;
    }
    return files as FileInMessage[];
  }, [message.additional_kwargs?.files, rawContent]);

  const contentToDisplay = useMemo(() => {
    // B4（2026-09-23 批 1 DEF-03）：渲染层兜底剥离内部标签，覆盖存量脏 thread
    // （注入消息早于 hide_from_ui 修复的 checkpoint）。useMemo 按 rawContent
    // （string 值相等）缓存——content 不变则剥离结果复用，不叠加 DEF-06 的
    // 每-token O(n) 热点。
    if (isHuman) {
      if (!rawContent) return "";
      // 人类消息只剥上传块与下划线变体提醒标签——不剥 <memory> 等，
      // 避免吃掉用户就系统内部机制提问的正当输入。
      return stripInjectedReminderTags(stripUploadedFilesTag(rawContent));
    }
    if (!rawContent) return "";
    return stripInternalMarkers(rawContent);
  }, [rawContent, isHuman]);

  const filesList =
    files && files.length > 0 ? (
      <RichFilesList files={files} threadId={threadId} />
    ) : null;

  // Uploading state: mock AI message shown while files upload
  if (message.additional_kwargs?.element === "task") {
    return (
      <AIElementMessageContent className={className}>
        <Task defaultOpen={false}>
          <TaskTrigger title="">
            <div className="text-muted-foreground flex w-full cursor-default items-center gap-2 text-sm select-none">
              <Loader className="size-4" />
              <span>{contentToDisplay}</span>
            </div>
          </TaskTrigger>
        </Task>
      </AIElementMessageContent>
    );
  }

  // Reasoning-only AI message (no main response content yet)
  if (!isHuman && reasoningContent && !rawContent) {
    return (
      <AIElementMessageContent className={className}>
        <Reasoning isStreaming={isLoading}>
          <ReasoningTrigger />
          <ReasoningContent>{reasoningContent}</ReasoningContent>
        </Reasoning>
        <MessageTokenUsage
          enabled={tokenUsageEnabled}
          isLoading={isLoading}
          message={message}
        />
      </AIElementMessageContent>
    );
  }

  // B2（2026-09-23 批 1）：空响应兜底错误卡片——中文说明（来自后端 A4/B1 兜底
  // 文案，已在 contentToDisplay）+「重试」按钮 = 发送「继续」从断点续作。
  if (isFallbackError) {
    return (
      <AIElementMessageContent className={className}>
        <div className="border-destructive/40 bg-destructive/5 my-3 w-full max-w-xl rounded-lg border p-4 text-sm">
          <div className="text-destructive flex items-center gap-2 font-medium">
            <TriangleAlert className="size-4 shrink-0" />
            {t.errorFallback.title}
          </div>
          <p className="text-muted-foreground mt-2 break-words whitespace-pre-wrap">
            {contentToDisplay}
          </p>
          <Button
            size="sm"
            variant="outline"
            className="mt-3"
            type="button"
            disabled={isLoading}
            onClick={() => submitThroughComposer("继续")}
          >
            {t.errorFallback.retry}
          </Button>
        </div>
        <MessageTokenUsage
          enabled={tokenUsageEnabled}
          isLoading={isLoading}
          message={message}
        />
      </AIElementMessageContent>
    );
  }

  if (isHuman) {
    // Plain text, deliberately: the composer sends plain text, so parsing it
    // back as Markdown only corrupted it — `__init__` lost its underscores,
    // `**CRITICAL**` lost its asterisks, a leading `#` became a heading,
    // `$5 … $9` was swallowed as math, and multi-line logs collapsed onto one
    // line. `whitespace-pre-wrap` preserves newlines and runs of spaces;
    // `break-words` keeps long unbroken tokens (URLs, stack frames) inside the
    // bubble. Trade-off accepted 2026-08-04: user messages no longer render
    // math. AI replies are unaffected and still render Markdown.
    const messageResponse = contentToDisplay ? (
      <div className="break-words whitespace-pre-wrap">{contentToDisplay}</div>
    ) : null;
    return (
      <div className={cn("ml-auto flex flex-col gap-2", className)}>
        {filesList}
        {messageResponse && (
          <AIElementMessageContent className="w-fit">
            {messageResponse}
          </AIElementMessageContent>
        )}
      </div>
    );
  }

  return (
    <AIElementMessageContent className={className}>
      {filesList}
      {/* PR-2 3.6：统一走 MarkdownContent——流式动画/平滑揭示/KaTeX/代码高亮
          全部内建（修前这里是手工拼 rehype 词动画 + katex 链）。 */}
      <MarkdownContent
        content={contentToDisplay}
        isLoading={isLoading}
        className="my-3"
        components={components}
      />
      <MessageTokenUsage
        enabled={tokenUsageEnabled}
        isLoading={isLoading}
        message={message}
      />
    </AIElementMessageContent>
  );
}

/**
 * Get file extension and check helpers
 */
const getFileExt = (filename: string) =>
  filename.split(".").pop()?.toLowerCase() ?? "";

const FILE_TYPE_MAP: Record<string, string> = {
  json: "JSON",
  csv: "CSV",
  txt: "TXT",
  md: "Markdown",
  py: "Python",
  js: "JavaScript",
  ts: "TypeScript",
  tsx: "TSX",
  jsx: "JSX",
  html: "HTML",
  css: "CSS",
  xml: "XML",
  yaml: "YAML",
  yml: "YAML",
  pdf: "PDF",
  png: "PNG",
  jpg: "JPG",
  jpeg: "JPEG",
  gif: "GIF",
  svg: "SVG",
  zip: "ZIP",
  tar: "TAR",
  gz: "GZ",
};

const IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"];

function getFileTypeLabel(filename: string): string {
  const ext = getFileExt(filename);
  return FILE_TYPE_MAP[ext] ?? (ext.toUpperCase() || "FILE");
}

function isImageFile(filename: string): boolean {
  return IMAGE_EXTENSIONS.includes(getFileExt(filename));
}

/**
 * Format bytes to human-readable size string
 */
// 0 字节 = 空文件标记「—」；非零走共享 formatFileSize
function formatBytes(bytes: number): string {
  return bytes === 0 ? "—" : formatFileSize(bytes);
}

/**
 * List of files from additional_kwargs.files (with optional upload status)
 */
function RichFilesList({
  files,
  threadId,
}: {
  files: FileInMessage[];
  threadId: string;
}) {
  if (files.length === 0) return null;
  return (
    <div className="mb-2 flex flex-wrap justify-end gap-2">
      {files.map((file, index) => (
        <RichFileCard
          key={`${file.filename}-${index}`}
          file={file}
          threadId={threadId}
        />
      ))}
    </div>
  );
}

/**
 * Single file card that handles FileInMessage (supports uploading state)
 */
function RichFileCard({
  file,
  threadId,
}: {
  file: FileInMessage;
  threadId: string;
}) {
  const { t } = useI18n();
  const isUploading = file.status === "uploading";
  const isImage = isImageFile(file.filename);

  if (isUploading) {
    return (
      <div className="bg-background border-border/40 flex max-w-50 min-w-30 flex-col gap-1 rounded-lg border p-3 opacity-60 shadow-sm">
        <div className="flex items-start gap-2">
          <Loader2Icon className="text-muted-foreground mt-0.5 size-4 shrink-0 animate-spin" />
          <span
            className="text-foreground truncate text-sm font-medium"
            title={file.filename}
          >
            {file.filename}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <Badge
            variant="secondary"
            className="rounded px-1.5 py-0.5 text-[10px] font-normal"
          >
            {getFileTypeLabel(file.filename)}
          </Badge>
          <span className="text-muted-foreground text-[10px]">
            {t.uploads.uploading}
          </span>
        </div>
      </div>
    );
  }

  if (!file.path) return null;

  const fileUrl = resolveArtifactURL(file.path, threadId);

  if (isImage) {
    return (
      <a
        href={fileUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="group border-border/40 relative block overflow-hidden rounded-lg border"
      >
        <img
          loading="lazy"
          src={fileUrl}
          alt={file.filename}
          className="h-32 w-auto max-w-60 object-cover transition-transform group-hover:scale-105"
        />
      </a>
    );
  }

  return (
    <div className="bg-background border-border/40 flex max-w-50 min-w-30 flex-col gap-1 rounded-lg border p-3 shadow-sm">
      <div className="flex items-start gap-2">
        <FileIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
        <span
          className="text-foreground truncate text-sm font-medium"
          title={file.filename}
        >
          {file.filename}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <Badge
          variant="secondary"
          className="rounded px-1.5 py-0.5 text-[10px] font-normal"
        >
          {getFileTypeLabel(file.filename)}
        </Badge>
        <span className="text-muted-foreground text-[10px]">
          {formatBytes(file.size)}
        </span>
      </div>
    </div>
  );
}

const MessageContent = memo(MessageContent_);

// 2026-10-06 性能批：memo 化（对齐 MessageGroup 既有模式）——流式期兄弟消息
// 重渲时 props 未变的项跳过。
export const MessageListItem = memo(MessageListItemInner);
