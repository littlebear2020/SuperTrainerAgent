"use client";

import {
  Briefcase,
  FileText,
  GraduationCap,
  Mic,
  Newspaper,
  Plus,
  Video,
} from "lucide-react";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DOC_TYPE_CONFIGS,
  DOC_TYPE_ORDER,
} from "@/core/writer/doc-type-config";
import { getStarterTemplate } from "@/core/writer/doc-type-templates";
import { useCreateWriterDoc } from "@/core/writer/hooks";
import type { DocType } from "@/core/writer/types";
import { cn } from "@/lib/utils";

const ICON_MAP: Record<string, typeof FileText> = {
  newspaper: Newspaper,
  briefcase: Briefcase,
  video: Video,
  mic: Mic,
  "graduation-cap": GraduationCap,
  "file-text": FileText,
};

interface NewDocDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (docId: string) => void;
}

export function NewDocDialog({
  open,
  onOpenChange,
  onCreated,
}: NewDocDialogProps) {
  const [selectedType, setSelectedType] = useState<DocType>("general");
  const createMut = useCreateWriterDoc();

  const handleSubmit = useCallback(() => {
    const config = DOC_TYPE_CONFIGS[selectedType];
    createMut.mutate(
      {
        title: config.defaultTitle,
        content_json: getStarterTemplate(selectedType, config.defaultTitle),
        doc_type: selectedType,
      },
      {
        onSuccess: (doc) => {
          // Guard against a malformed response so we never push to
          // /workspace/writer/undefined. The hook's global onError handles
          // thrown requests; this handles the rare 2xx-without-id case.
          if (!doc?.id) {
            toast.error("创建成功但返回数据异常，请刷新后重试");
            return;
          }
          onCreated(doc.id);
          onOpenChange(false);
          setSelectedType("general");
        },
      },
    );
  }, [createMut, selectedType, onCreated, onOpenChange]);

  const handleClose = useCallback(() => {
    onOpenChange(false);
    setSelectedType("general");
  }, [onOpenChange]);

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Plus className="h-5 w-5 text-indigo-500" />
            新建文档
          </DialogTitle>
          <DialogDescription>选择内容类型，开始创作</DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-3 gap-2.5 py-4">
          {DOC_TYPE_ORDER.map((type) => {
            const config = DOC_TYPE_CONFIGS[type];
            const Icon = ICON_MAP[config.icon] ?? FileText;
            return (
              <button
                key={type}
                onClick={() => setSelectedType(type)}
                className={cn(
                  "flex flex-col items-center gap-2 rounded-lg border-2 p-3 text-center transition-colors",
                  selectedType === type
                    ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950/30"
                    : "border-[var(--border)] hover:border-indigo-300 hover:bg-[var(--muted)]",
                )}
              >
                <Icon className="h-5 w-5 text-indigo-500" />
                <span className="text-xs font-medium">{config.label}</span>
                <span className="text-[10px] leading-tight text-[var(--muted-foreground)]">
                  {config.description}
                </span>
              </button>
            );
          })}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            取消
          </Button>
          <Button onClick={handleSubmit} disabled={createMut.isPending}>
            {createMut.isPending ? "创建中..." : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
