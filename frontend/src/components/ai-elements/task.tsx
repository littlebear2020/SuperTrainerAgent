"use client";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { glassStyle } from "@/core/theme/glass";
import { cn } from "@/lib/utils";
import { ChevronDownIcon, SearchIcon } from "lucide-react";
import type { ComponentProps } from "react";

export type TaskItemFileProps = ComponentProps<"div">;

export const TaskItemFile = ({
  children,
  className,
  ...props
}: TaskItemFileProps) => (
  <div
    className={cn(
      "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs",
      "border-indigo-500/15 bg-indigo-500/5 text-indigo-700 dark:border-indigo-400/20 dark:bg-indigo-500/10 dark:text-indigo-300",
      className,
    )}
    {...props}
  >
    {children}
  </div>
);

export type TaskItemProps = ComponentProps<"div">;

export const TaskItem = ({ children, className, ...props }: TaskItemProps) => (
  <div
    className={cn("text-sm text-slate-600 dark:text-slate-400", className)}
    {...props}
  >
    {children}
  </div>
);

export type TaskProps = ComponentProps<typeof Collapsible>;

export const Task = ({
  defaultOpen = true,
  className,
  ...props
}: TaskProps) => (
  <Collapsible
    style={glassStyle()}
    className={cn(
      "interactive-glass overflow-hidden rounded-xl px-4 py-3",
      className,
    )}
    defaultOpen={defaultOpen}
    {...props}
  />
);

export type TaskTriggerProps = ComponentProps<typeof CollapsibleTrigger> & {
  title: string;
};

export const TaskTrigger = ({
  children,
  className,
  title,
  ...props
}: TaskTriggerProps) => (
  <CollapsibleTrigger asChild className={cn("group", className)} {...props}>
    {children ?? (
      <div className="flex w-full cursor-pointer items-center gap-2 text-sm text-slate-600 transition-colors hover:text-indigo-600 dark:text-slate-400 dark:hover:text-indigo-400">
        <SearchIcon className="size-4 text-indigo-500" />
        <p className="text-sm font-medium">{title}</p>
        <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
      </div>
    )}
  </CollapsibleTrigger>
);

export type TaskContentProps = ComponentProps<typeof CollapsibleContent>;

export const TaskContent = ({
  children,
  className,
  ...props
}: TaskContentProps) => (
  <CollapsibleContent
    className={cn(
      "data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-2 data-[state=open]:slide-in-from-top-2 data-[state=closed]:animate-out data-[state=open]:animate-in outline-none",
      className,
    )}
    {...props}
  >
    <div className="mt-3 space-y-2 border-l-2 border-indigo-500/15 pl-4 dark:border-indigo-400/15">
      {children}
    </div>
  </CollapsibleContent>
);
