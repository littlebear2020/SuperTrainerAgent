import { useQueryClient } from "@tanstack/react-query";
import {
  Code2Icon,
  CopyIcon,
  DownloadIcon,
  EyeIcon,
  FileTextIcon,
  LoaderIcon,
  PackageIcon,
  PencilIcon,
  PencilOffIcon,
  RotateCcwIcon,
  SaveIcon,
  SquareArrowOutUpRightIcon,
  XIcon,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  Artifact,
  ArtifactAction,
  ArtifactActions,
  ArtifactContent,
  ArtifactHeader,
  ArtifactTitle,
} from "@/components/ai-elements/artifact";
import { Select, SelectItem } from "@/components/ui/select";
import {
  SelectContent,
  SelectGroup,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  buildConfirmOptions,
  useConfirm,
} from "@/components/workspace/dialog-provider";
import {
  ArtifactRequestError,
  updateArtifactContent,
} from "@/core/artifacts/api";
import {
  canEditOpenedArtifact,
  createArtifactDraft,
  reconcileArtifactDraft,
} from "@/core/artifacts/editing";
import { useArtifactContent } from "@/core/artifacts/hooks";
import {
  appendHtmlPreviewBaseHref,
  getTabularDelimiter,
} from "@/core/artifacts/preview";
import { urlOfArtifact } from "@/core/artifacts/utils";
import { useI18n } from "@/core/i18n/hooks";
import { installSkill } from "@/core/skills/api";
import { streamdownPlugins } from "@/core/streamdown";
import {
  SafeStreamdown,
  toStreamdownComponents,
} from "@/core/streamdown/components";
import {
  checkCodeFile,
  getFileExtensionDisplayName,
  getFileName,
  isBinaryFile,
} from "@/core/utils/files";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import { ArtifactLink } from "../citations/artifact-link";
import { useThread } from "../messages/context";
import { Tooltip } from "../tooltip";

import { ArtifactTablePreview } from "./artifact-table-preview";
import { useArtifacts } from "./context";

// 2026-10-06 性能批：CodeMirror 重库动态导入——仅在真正打开代码文件时加载
const CodeEditor = dynamic(
  () => import("@/components/workspace/code-editor").then((m) => m.CodeEditor),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-40 items-center justify-center text-xs text-[var(--muted-foreground)]">
        加载编辑器...
      </div>
    ),
  },
);

export function ArtifactFileDetail({
  className,
  filepath: filepathFromProps,
  threadId,
}: {
  className?: string;
  filepath: string;
  threadId: string;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const {
    artifacts,
    setOpen,
    select,
    drafts,
    setDrafts,
    editingPath,
    setEditingPath,
  } = useArtifacts();
  const { thread } = useThread();
  const isWriteFile = useMemo(() => {
    return filepathFromProps.startsWith("write-file:");
  }, [filepathFromProps]);
  const filepath = useMemo(() => {
    if (isWriteFile) {
      const url = new URL(filepathFromProps);
      return decodeURIComponent(url.pathname);
    }
    return filepathFromProps;
  }, [filepathFromProps, isWriteFile]);
  const isSkillFile = useMemo(() => {
    return filepath.endsWith(".skill");
  }, [filepath]);
  const { isCodeFile, language } = useMemo(() => {
    if (isWriteFile) {
      let language = checkCodeFile(filepath).language;
      language ??= "text";
      return { isCodeFile: true, language };
    }
    // Treat .skill files as markdown (they contain SKILL.md)
    if (isSkillFile) {
      return { isCodeFile: true, language: "markdown" };
    }
    return checkCodeFile(filepath);
  }, [filepath, isWriteFile, isSkillFile]);
  // PR-3 5.2：CSV 表格化预览——isTabular 时预览态可用且为默认视图。
  // 评审 important 订正①：pending write_file 的表格预览压回 source 态（上游
  // 不变量 "Pending write_file content stays in source mode until success" 的
  // 本地等价——本地加载链无 toolResult OK 信号，取 isWriteFile 全期不自动
  // 预览，保存后重开即恢复）。
  const isTabular = getTabularDelimiter(language) !== null;
  const tabularPreviewReady = isTabular && !isWriteFile;
  const isSupportPreview = useMemo(() => {
    return (
      language === "html" || language === "markdown" || tabularPreviewReady
    );
  }, [language, tabularPreviewReady]);
  const {
    content,
    url,
    sha256,
    isLoading: contentLoading,
  } = useArtifactContent({
    threadId,
    filepath: filepathFromProps,
    enabled: isCodeFile && !isWriteFile,
  });

  const displayContent = content ?? "";

  const [isSaving, setIsSaving] = useState(false);
  const activeDraft = drafts[filepath] ?? createArtifactDraft(filepath);
  const isDirty = activeDraft.draftContent !== activeDraft.baselineContent;
  const hasUnsavedDrafts = Object.values(drafts).some(
    (draft) => draft.draftContent !== draft.baselineContent,
  );
  const isEditing = editingPath === filepath;
  const canEdit = canEditOpenedArtifact({
    filepath,
    isCodeFile,
    isWriteFile,
    isSkillFile,
    hasRevision: typeof sha256 === "string",
    isStaticWebsite: env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true",
  });
  const editorContent = isDirty ? activeDraft.draftContent : displayContent;

  // Adopt refreshed server content into the draft while it is clean; a remote
  // change underneath a dirty draft is surfaced as a save conflict instead.
  useEffect(() => {
    if (content === undefined || sha256 === undefined || isWriteFile) {
      return;
    }
    setDrafts((current) => {
      const existing = current[filepath] ?? createArtifactDraft(filepath);
      const next = reconcileArtifactDraft(existing, { content, sha256 });
      if (next === existing) {
        return current;
      }
      return { ...current, [filepath]: next };
    });
  }, [content, filepath, isWriteFile, setDrafts, sha256]);

  const [viewMode, setViewMode] = useState<"code" | "preview">("code");
  const [isInstalling, setIsInstalling] = useState(false);
  useEffect(() => {
    if (isSupportPreview) {
      setViewMode("preview");
    } else {
      setViewMode("code");
    }
  }, [isSupportPreview]);

  const confirm = useConfirm();
  const confirmDiscard = useCallback(async () => {
    return (
      !isDirty ||
      (await confirm(
        buildConfirmOptions(t.artifactEditing.discardChanges, {
          variant: "destructive",
        }),
      ))
    );
  }, [confirm, isDirty, t.artifactEditing.discardChanges]);

  const discardDraft = useCallback(() => {
    const latestContent = content ?? activeDraft.baselineContent;
    const latestSha256 = sha256 ?? activeDraft.baselineSha256;
    setDrafts((current) => ({
      ...current,
      [filepath]: {
        ...activeDraft,
        baselineContent: latestContent,
        baselineSha256: latestSha256,
        draftContent: latestContent,
        conflict: false,
      },
    }));
    setEditingPath(null);
  }, [activeDraft, content, filepath, setDrafts, setEditingPath, sha256]);

  const handleSave = useCallback(async () => {
    if (
      !canEdit ||
      !isDirty ||
      isSaving ||
      thread.isLoading ||
      activeDraft.conflict ||
      activeDraft.baselineSha256 === null
    ) {
      return;
    }

    setIsSaving(true);
    try {
      const result = await updateArtifactContent({
        threadId,
        filepath,
        content: activeDraft.draftContent,
        expectedSha256: activeDraft.baselineSha256,
      });
      const savedContent = activeDraft.draftContent;
      setDrafts((current) => ({
        ...current,
        [filepath]: {
          filepath,
          baselineContent: savedContent,
          baselineSha256: result.sha256,
          draftContent: savedContent,
          conflict: false,
        },
      }));
      queryClient.setQueryData(
        ["artifact", filepathFromProps, threadId],
        (
          current:
            | { content?: string; url?: string; sha256?: string }
            | undefined,
        ) => ({
          ...current,
          content: savedContent,
          sha256: result.sha256,
        }),
      );
      toast.success(t.artifactEditing.saved);
    } catch (error) {
      if (error instanceof ArtifactRequestError && error.status === 412) {
        setDrafts((current) => ({
          ...current,
          [filepath]: { ...(current[filepath] ?? activeDraft), conflict: true },
        }));
        void queryClient.invalidateQueries({
          queryKey: ["artifact", filepathFromProps, threadId],
        });
        toast.error(t.artifactEditing.conflict);
      } else if (
        error instanceof ArtifactRequestError &&
        error.status === 409
      ) {
        toast.error(t.artifactEditing.runInProgress);
      } else {
        toast.error(
          error instanceof Error ? error.message : t.artifactEditing.saveFailed,
        );
      }
    } finally {
      setIsSaving(false);
    }
  }, [
    activeDraft,
    canEdit,
    filepath,
    filepathFromProps,
    isDirty,
    isSaving,
    queryClient,
    setDrafts,
    t.artifactEditing,
    thread.isLoading,
    threadId,
  ]);

  const handleInstallSkill = useCallback(async () => {
    if (isInstalling) return;

    setIsInstalling(true);
    try {
      const result = await installSkill({
        thread_id: threadId,
        path: filepath,
      });
      if (result.success) {
        toast.success(result.message);
      } else {
        toast.error(result.message ?? "Failed to install skill");
      }
    } catch (error) {
      console.error("Failed to install skill:", error);
      toast.error("Failed to install skill");
    } finally {
      setIsInstalling(false);
    }
  }, [threadId, filepath, isInstalling]);
  return (
    <Artifact className={cn(className)}>
      <ArtifactHeader className="px-2">
        <div className="flex items-center gap-2">
          <ArtifactTitle>
            {isWriteFile ? (
              <div className="px-2">{getFileName(filepath)}</div>
            ) : (
              <Select
                value={filepath}
                onValueChange={async (nextFilepath) => {
                  if (await confirmDiscard()) {
                    if (isDirty) {
                      discardDraft();
                    }
                    select(nextFilepath);
                  }
                }}
              >
                <SelectTrigger className="border-none bg-transparent! shadow-none select-none focus:outline-0 active:outline-0">
                  <SelectValue placeholder="Select a file" />
                </SelectTrigger>
                <SelectContent className="select-none">
                  <SelectGroup>
                    {(artifacts ?? []).map((filepath) => (
                      <SelectItem key={filepath} value={filepath}>
                        {getFileName(filepath)}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            )}
          </ArtifactTitle>
        </div>
        <div className="flex min-w-0 grow items-center justify-center gap-2">
          {isSupportPreview && (
            <ToggleGroup
              className="mx-auto"
              type="single"
              variant="outline"
              size="sm"
              value={viewMode}
              onValueChange={(value) => {
                if (value) {
                  setViewMode(value as "code" | "preview");
                }
              }}
            >
              <ToggleGroupItem value="code">
                <Code2Icon />
              </ToggleGroupItem>
              <ToggleGroupItem value="preview">
                <EyeIcon />
              </ToggleGroupItem>
            </ToggleGroup>
          )}
          {(isSaving || isDirty || activeDraft.conflict) && (
            <span
              className={cn(
                "text-muted-foreground max-w-32 truncate text-xs",
                activeDraft.conflict && "text-destructive",
              )}
              aria-live="polite"
            >
              {isSaving
                ? t.artifactEditing.saving
                : activeDraft.conflict
                  ? t.artifactEditing.conflictShort
                  : t.artifactEditing.unsaved}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <ArtifactActions>
            {canEdit && !isEditing && (
              <ArtifactAction
                icon={PencilIcon}
                label={t.common.edit}
                tooltip={t.common.edit}
                disabled={thread.isLoading}
                onClick={() => {
                  setViewMode("code");
                  setEditingPath(filepath);
                }}
              />
            )}
            {canEdit && isEditing && (
              <>
                <ArtifactAction
                  className={cn(
                    isDirty && !activeDraft.conflict && "text-primary",
                  )}
                  icon={isSaving ? LoaderIcon : SaveIcon}
                  label={t.common.save}
                  tooltip={
                    thread.isLoading
                      ? t.artifactEditing.runInProgress
                      : activeDraft.conflict
                        ? t.artifactEditing.conflict
                        : t.common.save
                  }
                  disabled={
                    !isDirty ||
                    isSaving ||
                    thread.isLoading ||
                    activeDraft.conflict
                  }
                  onClick={() => void handleSave()}
                />
                <ArtifactAction
                  icon={PencilOffIcon}
                  label={t.artifactEditing.exit}
                  tooltip={t.artifactEditing.exit}
                  disabled={isSaving}
                  onClick={() => setEditingPath(null)}
                />
                <ArtifactAction
                  icon={RotateCcwIcon}
                  label={t.artifactEditing.discard}
                  tooltip={t.artifactEditing.discard}
                  disabled={isSaving}
                  onClick={async () => {
                    if (await confirmDiscard()) {
                      discardDraft();
                    }
                  }}
                />
              </>
            )}
            {!isEditing && !isWriteFile && filepath.endsWith(".skill") && (
              <Tooltip content={t.toolCalls.skillInstallTooltip}>
                <ArtifactAction
                  icon={isInstalling ? LoaderIcon : PackageIcon}
                  label={t.common.install}
                  tooltip={t.common.install}
                  disabled={
                    isInstalling ||
                    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"
                  }
                  onClick={handleInstallSkill}
                />
              </Tooltip>
            )}
            {!isEditing && !isWriteFile && (
              <ArtifactAction
                icon={SquareArrowOutUpRightIcon}
                label={t.common.openInNewWindow}
                tooltip={t.common.openInNewWindow}
                onClick={() => {
                  const w = window.open(
                    urlOfArtifact({ filepath, threadId }),
                    "_blank",
                    "noopener,noreferrer",
                  );
                  if (w) w.opener = null;
                }}
              />
            )}
            {!isEditing && isCodeFile && (
              <ArtifactAction
                icon={CopyIcon}
                label={t.clipboard.copyToClipboard}
                disabled={!content}
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(editorContent ?? "");
                    toast.success(t.clipboard.copiedToClipboard);
                  } catch (error) {
                    toast.error("Failed to copy to clipboard");
                    console.error(error);
                  }
                }}
                tooltip={t.clipboard.copyToClipboard}
              />
            )}
            {!isEditing && !isWriteFile && (
              <ArtifactAction
                icon={DownloadIcon}
                label={t.common.download}
                tooltip={t.common.download}
                onClick={() => {
                  const w = window.open(
                    urlOfArtifact({
                      filepath,
                      threadId,
                      download: true,
                    }),
                    "_blank",
                    "noopener,noreferrer",
                  );
                  if (w) w.opener = null;
                }}
              />
            )}
            <ArtifactAction
              icon={XIcon}
              label={t.common.close}
              onClick={async () => {
                if (
                  !hasUnsavedDrafts ||
                  (await confirm(
                    buildConfirmOptions(t.artifactEditing.discardChanges, {
                      variant: "destructive",
                    }),
                  ))
                ) {
                  setDrafts({});
                  setEditingPath(null);
                  setOpen(false);
                }
              }}
              tooltip={t.common.close}
            />
          </ArtifactActions>
        </div>
      </ArtifactHeader>
      <ArtifactContent className="p-0">
        {isSupportPreview &&
          viewMode === "preview" &&
          isTabular &&
          getTabularDelimiter(language) && (
            <ArtifactTablePreview
              content={editorContent}
              delimiter={getTabularDelimiter(language)!}
              truncated={false}
              identity={filepath}
              // 评审 important 订正②：content 尚在 fetch 时挂载但不激活——
              // hook 不跑空串（否则先闪"文件为空"终态文案，fetch 完才翻表格）。
              active={!contentLoading}
            />
          )}
        {isSupportPreview &&
          viewMode === "preview" &&
          (language === "markdown" || language === "html") && (
            <ArtifactFilePreview
              content={editorContent}
              language={language ?? "text"}
              url={url}
            />
          )}
        {isCodeFile && viewMode === "code" && (
          <CodeEditor
            className="size-full resize-none rounded-none border-none"
            value={editorContent ?? ""}
            readonly={!isEditing}
            disabled={thread.isLoading || isSaving}
            autoFocus={isEditing}
            onChange={(nextContent) => {
              setDrafts((current) => ({
                ...current,
                [filepath]: {
                  ...(current[filepath] ?? activeDraft),
                  draftContent: nextContent,
                },
              }));
            }}
            onSave={() => void handleSave()}
          />
        )}
        {!isCodeFile && isBinaryFile(filepath) && (
          <div className="text-muted-foreground flex size-full flex-col items-center justify-center gap-4">
            <FileTextIcon className="size-16 opacity-40" />
            <p className="text-sm">
              {getFileExtensionDisplayName(filepath)}{" "}
              {t.common.previewNotSupported}
            </p>
            <button
              type="button"
              className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium"
              onClick={() => {
                const w = window.open(
                  urlOfArtifact({ filepath, threadId, download: true }),
                  "_blank",
                  "noopener,noreferrer",
                );
                if (w) w.opener = null;
              }}
            >
              <DownloadIcon className="size-4" />
              {t.common.download}
            </button>
          </div>
        )}
        {!isCodeFile && !isBinaryFile(filepath) && (
          <iframe
            className="size-full"
            // SEC-H10: this branch only serves svg/media/unknown-non-binary
            // previews — scripts are never needed, so a fully restrictive
            // sandbox blocks SVG-embedded XSS without affecting playback.
            sandbox=""
            src={urlOfArtifact({ filepath, threadId })}
          />
        )}
      </ArtifactContent>
    </Artifact>
  );
}

export function ArtifactFilePreview({
  content,
  language,
  url,
}: {
  content: string;
  language: string;
  url?: string;
}) {
  const [htmlPreviewUrl, setHtmlPreviewUrl] = useState<string>();

  useEffect(() => {
    if (language !== "html") {
      setHtmlPreviewUrl(undefined);
      return;
    }

    // A blob: document has an opaque path, so relative img/css/js URLs cannot
    // resolve. Give them the artifact's own directory as their base.
    const previewContent = appendHtmlPreviewBaseHref(content ?? "", url);
    const blob = new Blob([previewContent], { type: "text/html" });
    const objectUrl = URL.createObjectURL(blob);
    setHtmlPreviewUrl(objectUrl);

    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [content, language, url]);

  if (language === "markdown") {
    return (
      <div className="size-full px-4">
        {/* PR-2 3.6：换 SafeStreamdown 系（防崩安全壳 + 深嵌套封顶预处理）。 */}
        <SafeStreamdown
          className="size-full"
          {...streamdownPlugins}
          components={toStreamdownComponents({ a: ArtifactLink })}
        >
          {content ?? ""}
        </SafeStreamdown>
      </div>
    );
  }
  if (language === "html") {
    return (
      <iframe
        className="size-full"
        title="Artifact preview"
        sandbox="allow-scripts allow-forms"
        src={htmlPreviewUrl}
      />
    );
  }
  return null;
}
