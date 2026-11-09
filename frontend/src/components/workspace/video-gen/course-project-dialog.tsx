"use client";

import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  useCourseProjects,
  useCreateCourseProject,
  useDeleteCourseProject,
} from "@/core/video-gen/hooks";
// Task 1（2026-08-01）：原先此处与 course-project-detail.tsx 各抄一份
// STYLE_PRESETS，且 value 是英文字面量（'3D render' 等）会被原样拼进生图 prompt。
// 现统一引用单一来源，value 改语言无关 key，由后端查表翻译成中文 prefix。
import { projectStyleOptions } from "@/core/video-gen/style-presets";
import type { CourseProject, CourseProjectInput } from "@/core/video-gen/types";

export function CourseProjectDialog({
  children,
  onSelect,
}: {
  children?: React.ReactNode;
  onSelect?: (project: CourseProject) => void;
}) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [stylePreset, setStylePreset] = useState("");
  const projectsQ = useCourseProjects();
  const createMut = useCreateCourseProject();
  const deleteMut = useDeleteCourseProject();

  const handleCreate = async () => {
    if (!title.trim()) return;
    const input: CourseProjectInput = {
      title: title.trim(),
      visual_style_preset: stylePreset || undefined,
    };
    try {
      const project = await createMut.mutateAsync(input);
      setTitle("");
      setStylePreset("");
      if (onSelect) {
        onSelect(project);
        setOpen(false);
      }
    } catch {
      // onError 已 toast；此处仅阻止 unhandled rejection 与误关对话框
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {children ?? (
          <Button variant="outline" size="sm">
            <Plus className="mr-1 h-4 w-4" />
            课程项目
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>课程项目管理</DialogTitle>
        </DialogHeader>

        {/* Existing projects */}
        {projectsQ.data && projectsQ.data.length > 0 && (
          <div className="max-h-48 space-y-2 overflow-y-auto">
            {projectsQ.data.map((p) => (
              <div
                key={p.id}
                className="flex items-center justify-between rounded-md border px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{p.title}</p>
                  {p.visual_style_preset && (
                    <p className="text-muted-foreground text-xs">
                      {p.visual_style_preset}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => {
                      onSelect?.(p);
                      setOpen(false);
                    }}
                  >
                    选择
                  </Button>
                  {/* B12：删除不可撤销，必须二次确认（复用 detail 页同款 AlertDialog） */}
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-muted-foreground hover:text-destructive h-7 w-7 p-0"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>确认删除</AlertDialogTitle>
                        <AlertDialogDescription>
                          确定要删除项目「{p.title}
                          」吗？关联的视频任务不会被删除，但会失去项目关联。此操作无法撤销。
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>取消</AlertDialogCancel>
                        <AlertDialogAction
                          onClick={() => deleteMut.mutate(p.id)}
                          className="bg-red-600 hover:bg-red-700"
                        >
                          删除
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Create form */}
        <div className="space-y-3 border-t pt-3">
          <div className="space-y-1">
            <label htmlFor="cp-title" className="text-xs font-medium">
              项目名称
            </label>
            <Input
              id="cp-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="如：领导力培训系列"
              className="h-9"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="cp-style" className="text-xs font-medium">
              视觉风格预设
            </label>
            <select
              id="cp-style"
              value={stylePreset}
              onChange={(e) => setStylePreset(e.target.value)}
              className="border-input bg-background flex h-9 w-full rounded-md border px-3 text-sm"
            >
              {projectStyleOptions(stylePreset).map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <DialogFooter>
          <Button
            onClick={handleCreate}
            disabled={!title.trim() || createMut.isPending}
          >
            {createMut.isPending ? "创建中..." : "创建项目"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
