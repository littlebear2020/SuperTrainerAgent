import { urlOfArtifact } from "@/core/artifacts/utils";
import type { AgentThread } from "@/core/threads/types";
import { agentNameOfThread } from "@/core/threads/utils";
import { getVideoDownloadUrl } from "@/core/video-gen/api";
import type { VideoTaskBrief } from "@/core/video-gen/types";
import type { WorkCapability } from "@/core/workspace/video-assets-route";

import {
  TASK_TYPE_CAPABILITY,
  WORK_CAPABILITY_SOURCES,
} from "./capability-sources";
import type {
  ArtifactFileSummary,
  ThreadArtifactSummary,
  WorkFile,
  WorkSummary,
} from "./types";

function buildAgentFile(threadId: string, file: ArtifactFileSummary): WorkFile {
  const previewUrl =
    file.exists && ["video", "image", "audio"].includes(file.media_kind)
      ? urlOfArtifact({ filepath: file.path, threadId })
      : null;
  return {
    id: `thread:${threadId}:${file.path}`,
    name: file.name,
    mediaKind: file.media_kind,
    size: file.size,
    modifiedAt: file.modified_at,
    exists: file.exists,
    previewUrl,
    downloadUrl: file.exists
      ? urlOfArtifact({ filepath: file.path, threadId, download: true })
      : null,
  };
}

export function buildAgentWorks(
  threads: readonly AgentThread[],
  summaries: readonly ThreadArtifactSummary[],
  capability: WorkCapability,
): WorkSummary[] {
  const summariesByThread = new Map(
    summaries.map((summary) => [summary.thread_id, summary]),
  );
  const seen = new Set<string>();
  const works: WorkSummary[] = [];

  for (const thread of threads) {
    const configuredAgentMatch = WORK_CAPABILITY_SOURCES[
      capability
    ].agentNames.some((name) => name === agentNameOfThread(thread));
    if (seen.has(thread.thread_id) || !configuredAgentMatch) {
      continue;
    }
    seen.add(thread.thread_id);
    const summary = summariesByThread.get(thread.thread_id);
    const files = (summary?.files ?? []).map((file) =>
      buildAgentFile(thread.thread_id, file),
    );
    works.push({
      id: `thread:${thread.thread_id}`,
      capability,
      title: (thread.values?.title ?? "").trim(),
      source: { kind: "agent", thread },
      agentDeliveryState: summary?.delivery_state ?? "unknown",
      sortAt: thread.updated_at ?? thread.created_at,
      files,
      primaryFile: files.find((file) => file.exists) ?? null,
    });
  }
  return works;
}

function buildTaskFile(task: VideoTaskBrief): WorkFile | null {
  if (task.status !== "ready") return null;

  if (task.delivery) {
    const exists = task.delivery.exists && !task.artifact_missing;
    const url = exists ? getVideoDownloadUrl(task.id) : null;
    return {
      id: `task:${task.id}:output`,
      name: task.delivery.file_name,
      mediaKind: task.delivery.media_kind,
      size: task.delivery.file_size,
      modifiedAt: task.delivery.file_updated_at,
      exists,
      previewUrl:
        url && ["video", "image", "audio"].includes(task.delivery.media_kind)
          ? url
          : null,
      downloadUrl: url,
    };
  }

  if (task.artifact_missing) return null;
  const url = getVideoDownloadUrl(task.id);
  return {
    id: `task:${task.id}:output`,
    name: null,
    mediaKind: "video",
    mediaKindGuessed: true,
    size: null,
    modifiedAt: null,
    exists: true,
    previewUrl: url,
    downloadUrl: url,
  };
}

export function buildTaskWorks(
  tasks: readonly VideoTaskBrief[],
  capability: WorkCapability,
): WorkSummary[] {
  const works: WorkSummary[] = [];
  for (const task of tasks) {
    if (TASK_TYPE_CAPABILITY[task.task_type] !== capability) continue;
    const file = buildTaskFile(task);
    works.push({
      id: `task:${task.id}`,
      capability,
      title: (task.title ?? "").trim(),
      source: { kind: "video_task", task },
      agentDeliveryState: null,
      sortAt: task.updated_at ?? task.created_at,
      files: file ? [file] : [],
      primaryFile: file?.exists ? file : null,
    });
  }
  return works;
}

/**
 * 作品卡面的真实图片封面：按后端现有文件顺序取首个可预览的光栅图。
 *
 * 只影响卡面，与主交付文件解耦——`primaryFile`、文件顺序、查看器默认文件与下载
 * 都不变。封面资格只看 `mediaKind === "image"`：SVG/HTML 被后端归为 `other`，
 * 即使夹具错误地带了 `previewUrl` 也不会被选中。
 */
export function selectWorkCoverFile(work: WorkSummary): WorkFile | null {
  return (
    work.files.find(
      (file) =>
        file.exists && file.mediaKind === "image" && file.previewUrl !== null,
    ) ?? null
  );
}

function compareIds(left: string, right: string): number {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function compareWorks(left: WorkSummary, right: WorkSummary): number {
  const leftTime = left.sortAt ? Date.parse(left.sortAt) : Number.NaN;
  const rightTime = right.sortAt ? Date.parse(right.sortAt) : Number.NaN;
  const leftValid = Number.isFinite(leftTime);
  const rightValid = Number.isFinite(rightTime);
  if (leftValid && rightValid && leftTime !== rightTime) {
    return rightTime - leftTime;
  }
  if (leftValid !== rightValid) return leftValid ? -1 : 1;
  return compareIds(left.id, right.id);
}

export function selectAndFilterWorks(
  works: readonly WorkSummary[],
  query: string,
): WorkSummary[] {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  return [...works].sort(compareWorks).filter((work) => {
    if (!normalizedQuery) return true;
    if (work.title.toLocaleLowerCase().includes(normalizedQuery)) return true;
    return work.files.some((file) =>
      file.name?.toLocaleLowerCase().includes(normalizedQuery),
    );
  });
}

export function chunkThreadIds(
  threadIds: readonly string[],
  batchSize = 50,
): string[][] {
  if (!Number.isInteger(batchSize) || batchSize <= 0) {
    throw new RangeError("batchSize must be a positive integer");
  }
  const sortedIds = [...new Set(threadIds)].sort();
  const batches: string[][] = [];
  for (let index = 0; index < sortedIds.length; index += batchSize) {
    batches.push(sortedIds.slice(index, index + batchSize));
  }
  return batches;
}
