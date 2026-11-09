"use client";

import {
  CheckIcon,
  ClapperboardIcon,
  Loader2Icon,
  PencilIcon,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  useRenderFromDraft,
  useStoryboard,
  useUpdateStoryboardFrame,
  useUpdateStory,
  useVideoTask,
} from "@/core/video-gen/hooks";
import { cn } from "@/lib/utils";

// NOTE: UI strings are literals for now; add a `video.storyboardConfirm` namespace
// to i18n locales (zh-CN.ts / en-US.ts) when consolidating translations.

/**
 * Chat-embedded storyboard confirmation card (plan §3.2 / §6.3 step 3).
 *
 * Renders as a special message group when the course-video agent calls
 * `plan_storyboard`. Shows the draft blueprint (frames: index / narration /
 * duration), lets the user edit narration inline, and a "确认渲染" button that
 * kicks off rendering directly via the REST `renderFromDraft` API (does NOT
 * route back through the agent — changelog 续章·二, simplest path chosen).
 *
 * Data flows entirely through the standard video-gen API (useStoryboard /
 * useUpdateStoryboardFrame / useRenderFromDraft) — no sandbox path involvement,
 * which fixes the present_files sandbox/storage seam exposed in chat-path
 * verification.
 */
export function StoryboardConfirmCard({ taskId }: { taskId: string }) {
  const { data: task } = useVideoTask(taskId);
  const { data: storyboard, isLoading } = useStoryboard(taskId);
  const updateFrame = useUpdateStoryboardFrame(taskId);
  const updateStory = useUpdateStory(taskId);
  const render = useRenderFromDraft(taskId);

  const config = storyboard?.config;
  const frames = storyboard?.frames ?? [];

  // M3.1: idea2video pauses at STORY_READY with an expanded story in result.story.
  // Show the story for confirmation instead of the (nonexistent) storyboard frames.
  // story2video/remotion/stock keep the frames-table form.
  const story = (task?.result?.story as string | undefined) ?? null;
  const isStoryMode = story != null;

  return (
    <Card className="w-full overflow-hidden">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <ClapperboardIcon className="size-4" />
          <span>
            {isStoryMode ? "故事已扩写，请确认" : "视频蓝图已生成，请确认"}
          </span>
          {config?.video_type && (
            <span className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-xs">
              {config.video_type}
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {isStoryMode ? (
          <StoryForm
            story={story}
            saving={updateStory.isPending}
            rendering={render.isPending}
            onSave={(edited) => updateStory.mutate(edited)}
            onConfirm={() => {
              // persist the edited story (if changed) then render
              updateStory.mutate(story, {
                onSuccess: () => render.mutate(undefined),
              });
            }}
          />
        ) : isLoading ? (
          <div className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2Icon className="size-4 animate-spin" />
            <span>加载蓝图…</span>
          </div>
        ) : frames.length === 0 ? (
          <p className="text-muted-foreground text-sm">蓝图为空</p>
        ) : (
          <div className="space-y-2">
            {frames.map((frame) => (
              <FrameRow
                key={frame.index}
                frame={frame}
                onSave={(narration) =>
                  updateFrame.mutate({
                    frameIndex: frame.index,
                    updates: { narration },
                  })
                }
                saving={updateFrame.isPending}
              />
            ))}
          </div>
        )}

        {storyboard?.total_duration != null && (
          <p className="text-muted-foreground text-xs">
            总时长：{Math.round(storyboard.total_duration)}s
            {config?.style ? ` · ${config.style}` : ""}
          </p>
        )}

        <div className="flex items-center justify-end gap-2 pt-1">
          <Button
            size="sm"
            onClick={() => render.mutate(undefined)}
            disabled={render.isPending || frames.length === 0}
          >
            {render.isPending ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <CheckIcon className="size-4" />
            )}
            确认渲染
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

type FrameLike = {
  index: number;
  narration?: string | null;
  duration?: number | null;
};

function FrameRow({
  frame,
  onSave,
  saving,
}: {
  frame: FrameLike;
  onSave: (narration: string) => void;
  saving: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(frame.narration ?? "");

  // keep local draft in sync when the server value changes (e.g. optimistic update settles)
  useEffect(() => {
    if (!editing) setDraft(frame.narration ?? "");
  }, [frame.narration, editing]);

  const commit = () => {
    const trimmed = draft.trim();
    if (trimmed && trimmed !== (frame.narration ?? "")) {
      onSave(trimmed);
    }
    setEditing(false);
  };

  return (
    <div className="bg-card rounded-md border p-2.5">
      <div className="text-muted-foreground mb-1 flex items-center gap-2 text-xs">
        <span className="font-mono">#{frame.index}</span>
        {frame.duration != null && (
          <span>{Math.round(frame.duration * 10) / 10}s</span>
        )}
      </div>
      {editing ? (
        <div className="space-y-1.5">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={2}
            className="resize-none text-sm"
            autoFocus
          />
          <div className="flex justify-end gap-1.5">
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              取消
            </Button>
            <Button size="sm" onClick={commit} disabled={saving}>
              保存
            </Button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setEditing(true)}
          className={cn(
            "group flex w-full items-start gap-1.5 text-left text-sm",
            "min-h-[1.5rem]",
          )}
        >
          <span className="flex-1 break-words whitespace-pre-wrap">
            {frame.narration ?? (
              <span className="text-muted-foreground italic">
                （无旁白，点击编辑）
              </span>
            )}
          </span>
          <PencilIcon className="mt-0.5 size-3.5 shrink-0 opacity-0 transition-opacity group-hover:opacity-60" />
        </button>
      )}
    </div>
  );
}

/**
 * StoryForm (M3.1 idea2video): shows the ViMax-expanded story as editable text so
 * the user can review/refine before confirming. Save → PUT /tasks/{id}/story
 * (persists the edit to story.txt so render reuses it). Confirm → save then
 * renderFromDraft.
 */
function StoryForm({
  story,
  saving,
  rendering,
  onSave,
  onConfirm,
}: {
  story: string;
  saving: boolean;
  rendering: boolean;
  onSave: (edited: string) => void;
  onConfirm: () => void;
}) {
  const [draft, setDraft] = useState(story);

  useEffect(() => {
    setDraft(story);
  }, [story]);

  const dirty = draft.trim() !== story.trim();

  return (
    <div className="space-y-2.5">
      <p className="text-muted-foreground text-xs">
        ViMax
        已根据你的想法扩写为完整故事，请确认无误后渲染（故事越具体，画面一致性越好）。
      </p>
      <Textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={8}
        className="resize-y text-sm leading-relaxed"
        placeholder="故事内容…"
      />
      <div className="flex items-center justify-end gap-2 pt-1">
        {dirty && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onSave(draft)}
            disabled={saving}
          >
            {saving ? <Loader2Icon className="size-4 animate-spin" /> : null}
            保存故事
          </Button>
        )}
        <Button size="sm" onClick={onConfirm} disabled={rendering || saving}>
          {rendering ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            <CheckIcon className="size-4" />
          )}
          确认故事并渲染
        </Button>
      </div>
    </div>
  );
}
