"use client";

import { FolderOpen } from "lucide-react";
import { useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { formatDateTime, parseTimestamp } from "@/core/utils/datetime";
import { styleAnchorImageUrl } from "@/core/video-gen/api";
import { useCourseProjects } from "@/core/video-gen/hooks";
import {
  carryLabelFor,
  resolveStyleValue,
  stylePreviewImage,
} from "@/core/video-gen/style-presets";
import type { CourseProject } from "@/core/video-gen/types";

import { CourseProjectDetailPage } from "./course-project-detail";

export function courseProjectCoverUrls(project: CourseProject): string[] {
  const preview = stylePreviewImage(
    resolveStyleValue(project.visual_style_preset),
  );
  return [
    ...(project.style_anchor_image ? [styleAnchorImageUrl(project.id)] : []),
    ...(preview ? [preview] : []),
  ];
}

export function courseProjectFallbackColor(project: CourseProject): string {
  const primary = project.color_palette?.primary;
  return typeof primary === "string" && primary.trim() ? primary : "#eef2ff";
}

export function courseProjectStyleLabel(project: CourseProject): string {
  if (!project.visual_style_preset) return "未设置风格";
  return carryLabelFor(resolveStyleValue(project.visual_style_preset));
}

function CourseProjectCover({ project }: { project: CourseProject }) {
  const candidates = courseProjectCoverUrls(project);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const src = candidates[candidateIndex];
  if (!src) {
    return (
      <div
        data-testid="course-project-cover-fallback"
        role="img"
        aria-label="项目封面色块"
        className="aspect-video w-full shrink-0"
        style={{ backgroundColor: courseProjectFallbackColor(project) }}
      />
    );
  }
  return (
    <img
      src={src}
      alt={
        candidateIndex === 0 && project.style_anchor_image
          ? "项目风格锚图"
          : "项目风格示意图"
      }
      className="aspect-video w-full shrink-0 object-cover"
      loading="lazy"
      onError={() => setCandidateIndex((index) => index + 1)}
    />
  );
}

function projectUpdatedAt(project: CourseProject): string {
  const value = project.updated_at ?? project.created_at;
  if (!value || !Number.isFinite(parseTimestamp(value).getTime())) {
    return "更新时间未知";
  }
  return `最近更新 ${formatDateTime(value)}`;
}

interface CourseProjectPageProps {
  selectedId: string | null;
  onSelect: (projectId: string) => void;
  onBack: () => void;
}

export function CourseProjectPage({
  selectedId,
  onSelect,
  onBack,
}: CourseProjectPageProps) {
  const { t } = useI18n();
  const projectsQ = useCourseProjects();
  const projects = [...(projectsQ.data ?? [])].sort((a, b) => {
    const aValue = a.updated_at ?? a.created_at;
    const bValue = b.updated_at ?? b.created_at;
    const aTime = aValue ? parseTimestamp(aValue).getTime() : Number.NaN;
    const bTime = bValue ? parseTimestamp(bValue).getTime() : Number.NaN;
    const aValid = Number.isFinite(aTime);
    const bValid = Number.isFinite(bTime);
    if (aValid && bValid && aTime !== bTime) return bTime - aTime;
    if (aValid !== bValid) return aValid ? -1 : 1;
    return a.id.localeCompare(b.id);
  });

  if (selectedId) {
    return <CourseProjectDetailPage projectId={selectedId} onBack={onBack} />;
  }

  return (
    <div className="w-full space-y-6">
      {projectsQ.isLoading ? (
        <p
          role="status"
          className="text-muted-foreground py-8 text-center text-sm"
        >
          正在加载资产…
        </p>
      ) : projectsQ.isError ? (
        <p role="alert" className="text-destructive py-8 text-center text-sm">
          资产加载失败，请稍后重试
        </p>
      ) : projects.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-12">
          <FolderOpen className="text-muted-foreground/50 h-12 w-12" />
          <p className="text-muted-foreground text-sm">
            暂无课程项目，点击「新建项目」开始
          </p>
        </div>
      ) : (
        <div
          data-testid="course-project-grid"
          className="grid grid-cols-1 justify-start gap-5 sm:grid-cols-2 xl:grid-cols-3"
        >
          {projects.map((project) => {
            const coverKey = [
              project.id,
              ...courseProjectCoverUrls(project),
            ].join(":");
            return (
              <button
                key={project.id}
                type="button"
                onClick={() => onSelect(project.id)}
                className="group bg-background flex h-full min-w-0 flex-col overflow-hidden rounded-2xl border text-left shadow-sm transition-shadow hover:shadow-md"
              >
                <CourseProjectCover key={coverKey} project={project} />
                <span className="flex flex-1 flex-col space-y-2 p-4">
                  <span className="block truncate font-semibold group-hover:text-indigo-600">
                    {project.title}
                  </span>
                  <span className="text-muted-foreground block text-sm">
                    {courseProjectStyleLabel(project)}
                  </span>
                  <span className="text-muted-foreground block text-xs">
                    {projectUpdatedAt(project)}
                  </span>
                  <span className="mt-auto block pt-1 text-sm font-medium text-indigo-600">
                    {t.workspace.works.openProject}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
