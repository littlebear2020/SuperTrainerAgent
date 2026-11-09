"use client";

import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatDateTime } from "@/core/utils/datetime";
import { useCourseProjects } from "@/core/video-gen/hooks";
import type { CourseProject } from "@/core/video-gen/types";

import { CourseProjectDialog } from "./course-project-dialog";

/** DSN-013（2026-09-14）：同名项目区分——下拉项显示 创建日期 + id 尾 4 位。 */
export function projectOptionLabel(
  p: Pick<CourseProject, "id" | "title" | "created_at">,
): string {
  const tail = p.id.slice(-4);
  const mmdd = p.created_at ? formatDateTime(p.created_at) : null;
  return `${p.title}${mmdd ? ` · ${mmdd} 创建` : ""} · #${tail}`;
}

interface CourseProjectSelectProps {
  value?: string;
  onChange: (value: string | undefined) => void;
}

export function CourseProjectSelect({
  value,
  onChange,
}: CourseProjectSelectProps) {
  const projectsQ = useCourseProjects();
  const projects = projectsQ.data ?? [];

  // Detect if the currently selected project no longer exists in the list
  // (e.g. it was deleted). Show it as a greyed-out option to avoid crash.
  const selectedDeleted = !!value && !projects.some((p) => p.id === value);

  return (
    <div className="flex items-center gap-2">
      <Select
        value={value ?? "__none__"}
        onValueChange={(v) => onChange(v === "__none__" ? undefined : v)}
      >
        <SelectTrigger className="h-9 flex-1">
          <SelectValue placeholder="不关联项目" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__none__">不关联项目</SelectItem>
          {projects.map((p) => (
            <SelectItem key={p.id} value={p.id}>
              {projectOptionLabel(p)}
              {p.visual_style_preset ? ` · ${p.visual_style_preset}` : ""}
            </SelectItem>
          ))}
          {selectedDeleted && (
            <SelectItem value={value} disabled>
              已删除的项目
            </SelectItem>
          )}
        </SelectContent>
      </Select>
      <CourseProjectDialog>
        <Button variant="outline" size="sm" className="h-9 shrink-0">
          <Plus className="h-4 w-4" />
        </Button>
      </CourseProjectDialog>
    </div>
  );
}
