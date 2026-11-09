/**
 * 2026-10-20 Task 4：分镜帧与 ViMax 渲染镜头分源展示。
 *
 * 盘面 storyboard.json（经服务端摘要 frame_count）是分镜帧事实源；
 * result.vimax_shots 只表示渲染后的镜头 manifest。未渲染完成时镜头数显示空态，
 * 不把 0 当成「已生成 0 镜」。
 */

export interface StoryboardCountTask {
  frame_count?: number | null;
  shot_count?: number | null;
  shots_ready?: boolean | null;
}

export interface StoryboardCountSource {
  frames?: readonly unknown[] | null;
}

export interface StoryboardCounts {
  frameCount: number | null;
  shotCount: number | null;
}

export function resolveStoryboardCounts(
  task?: StoryboardCountTask | null,
  storyboard?: StoryboardCountSource | null,
): StoryboardCounts {
  let frameCount: number | null = null;
  if (typeof task?.frame_count === "number") {
    frameCount = task.frame_count;
  } else if (storyboard?.frames) {
    frameCount = storyboard.frames.length;
  }
  const shotCount =
    task?.shots_ready === true &&
    typeof task.shot_count === "number" &&
    task.shot_count > 0
      ? task.shot_count
      : null;

  return {
    frameCount,
    shotCount,
  };
}

/** 有可用 manifest 即可逐镜重做；任务状态不参与（failed 也可重做已生成镜头）。 */
export function hasVimaxShotManifest(
  shots: unknown,
): shots is Record<string, unknown> {
  return (
    !!shots &&
    typeof shots === "object" &&
    !Array.isArray(shots) &&
    Object.keys(shots as Record<string, unknown>).length > 0
  );
}
