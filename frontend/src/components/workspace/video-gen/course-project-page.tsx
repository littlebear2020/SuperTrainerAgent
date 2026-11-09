"use client";

import { FolderOpen, Plus } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useCourseProjects } from "@/core/video-gen/hooks";

import { CourseProjectDetailPage } from "./course-project-detail";
import { CourseProjectDialog } from "./course-project-dialog";

export function CourseProjectPage({
  onOpenTask,
}: {
  onOpenTask?: (taskId: string) => void;
} = {}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const projectsQ = useCourseProjects();
  const projects = projectsQ.data ?? [];

  if (selectedId) {
    return (
      <CourseProjectDetailPage
        projectId={selectedId}
        onBack={() => setSelectedId(null)}
        onOpenTask={onOpenTask}
      />
    );
  }

  return (
    <div className="scrollbar-none mx-auto w-full max-w-4xl space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-bold">
            <FolderOpen className="h-5 w-5 text-indigo-500" />
            课程项目
          </h1>
          <p className="text-muted-foreground mt-1 text-xs">
            管理系列课程的视觉风格、色彩主题和关联视频
          </p>
        </div>
        <CourseProjectDialog>
          <Button size="sm">
            <Plus className="mr-1 h-4 w-4" />
            新建项目
          </Button>
        </CourseProjectDialog>
      </div>

      {/* Project grid */}
      {projectsQ.isLoading ? (
        <p className="text-muted-foreground py-8 text-center text-sm">
          加载中...
        </p>
      ) : projects.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-12">
          <FolderOpen className="text-muted-foreground/50 h-12 w-12" />
          <p className="text-muted-foreground text-sm">
            暂无课程项目，点击「新建项目」开始
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => {
            const primary = (p.color_palette?.primary as string) ?? undefined;
            return (
              <button
                key={p.id}
                onClick={() => setSelectedId(p.id)}
                className="group space-y-3 rounded-lg border p-4 text-left transition-colors hover:border-indigo-400 dark:hover:border-indigo-600"
              >
                {/* Color strip */}
                {primary && (
                  <div
                    className="h-1.5 w-full rounded-full"
                    style={{ backgroundColor: primary }}
                  />
                )}
                <div className="space-y-1">
                  <h3 className="truncate text-sm font-semibold group-hover:text-indigo-600 dark:group-hover:text-indigo-400">
                    {p.title}
                  </h3>
                  {p.visual_style_preset && (
                    <p className="text-muted-foreground text-xs">
                      {p.visual_style_preset}
                    </p>
                  )}
                </div>
                <div className="text-muted-foreground text-xs">
                  点击编辑详情
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
