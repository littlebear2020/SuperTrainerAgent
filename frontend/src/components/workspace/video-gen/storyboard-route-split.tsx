"use client";

/**
 * T10（2026-09-10 独立管线计划）：分镜页路由分流。
 *
 * 拉一次轻量 GET /tasks/{id} 取 video_type（loading 骨架屏避免错渲染窗口；
 * 不向下注入数据——VideoGenStoryboard 零修改边界优先，它内部自拉 storyboard
 * 的成本保留）：remotion_course → RemotionCourseStoryboard；其他 → 现有
 * VideoGenStoryboard。渲染回调注入便于单测（不挂真实编辑器）。
 */

import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useVideoTask } from "@/core/video-gen/hooks";

interface StoryboardRouteSplitProps {
  taskId: string;
  renderCourse: (taskId: string) => ReactNode;
  renderDefault: (taskId: string) => ReactNode;
}

export function StoryboardRouteSplit({
  taskId,
  renderCourse,
  renderDefault,
}: StoryboardRouteSplitProps) {
  // M6（2026-09-11 审查）：路由判定后立即停轮询——useVideoTask 默认 draft 态
  // 每 2s 永久轮询（video-gen-storyboard 传 {enabled: !storyboardLoaded} 的同一
  // 个坑；分流层曾给所有类型的分镜页重新引入）。task_type 路由后永不变，拿到即关；
  // resolvedType 落定后 enabled=false，缓存数据仍可读（useQuery 行为）。
  const [resolvedType, setResolvedType] = useState<string | null>(null);
  const {
    data: task,
    isLoading,
    isError,
    refetch,
  } = useVideoTask(taskId, {
    enabled: !resolvedType,
  });
  const videoType: string | undefined = resolvedType ?? task?.task_type;
  useEffect(() => {
    if (!resolvedType && task?.task_type) {
      setResolvedType(task.task_type);
    }
  }, [task, resolvedType]);

  // m11（2026-09-11 二轮审查）：task 拉取 404/失败 → isLoading=false 且
  // videoType=undefined，修前骨架屏永久。错误态渲染可重试提示；低频轮询保持
  // （resolvedType 未落定 → enabled 仍开）网关恢复后自愈跳转。
  if (!isLoading && !videoType && isError) {
    return (
      <div className="mx-auto max-w-3xl space-y-3 p-8 text-center">
        <p className="text-muted-foreground text-sm">
          无法加载任务信息——任务可能已删除，或网关正在重启（恢复后将自动重试）。
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void refetch()}
        >
          立即重试
        </Button>
      </div>
    );
  }

  if (isLoading || !videoType) {
    // loading 骨架屏：避免旧编辑器闪现（错渲染窗口）
    return (
      <div className="mx-auto max-w-5xl space-y-4 p-4">
        <Skeleton className="h-9 w-64" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-64 w-full" />
          ))}
        </div>
      </div>
    );
  }
  if (videoType === "remotion_course") {
    return <>{renderCourse(taskId)}</>;
  }
  return <>{renderDefault(taskId)}</>;
}
