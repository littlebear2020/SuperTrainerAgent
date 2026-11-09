"use client";

import { Video } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

export type TaskThumbnailProps = {
  url: string | null;
  artifactMissing?: boolean;
  className?: string;
};

export function TaskThumbnail({
  url,
  artifactMissing,
  className,
}: TaskThumbnailProps) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const err = url === failedUrl;
  if (artifactMissing) {
    return (
      <div
        data-testid="thumbnail-artifact-missing"
        className={cn(
          "flex h-9 w-16 shrink-0 items-center justify-center rounded-lg bg-amber-100 text-[10px] font-medium text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
          className,
        )}
      >
        产物已清理
      </div>
    );
  }
  if (url && !err) {
    return (
      <img
        loading="lazy"
        data-testid="thumbnail-img"
        src={url}
        alt=""
        onError={() => setFailedUrl(url)}
        className={cn(
          "h-9 w-16 shrink-0 rounded-lg object-cover",
          className,
        )}
      />
    );
  }
  return (
    <div
      data-testid="thumbnail-placeholder"
      className={cn(
        "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-indigo-100 dark:bg-indigo-900/30",
        className,
      )}
    >
      <Video className="h-4 w-4 text-indigo-600 dark:text-indigo-400" />
    </div>
  );
}
