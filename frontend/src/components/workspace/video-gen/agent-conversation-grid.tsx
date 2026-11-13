"use client";

import {
  AudioLines,
  Image as ImageIcon,
  Presentation,
  Type,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { type ReactNode, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { HomeCourseItem } from "@/components/workspace/home-course-catalog";
import { useI18n } from "@/core/i18n/hooks";
import { useThreads } from "@/core/threads/hooks";
import type { AgentThread } from "@/core/threads/types";
import {
  agentNameOfThread,
  pathOfThread,
  titleOfThread,
} from "@/core/threads/utils";
import { formatDateTime, parseTimestamp } from "@/core/utils/datetime";

const THREAD_PAGE_SIZE = 6;
const THREAD_SEARCH_PARAMS = {
  sortBy: "updated_at",
  sortOrder: "desc",
  select: ["thread_id", "created_at", "updated_at", "values", "metadata"],
} satisfies NonNullable<Parameters<typeof useThreads>[0]>;

type WorkAgentName = Extract<HomeCourseItem, { kind: "agent" }>["agentName"];

export function userTurnCount(thread: AgentThread): number | null {
  const messages = thread.values?.messages;
  if (!Array.isArray(messages)) return null;
  const count = messages.filter((message) => message.type === "human").length;
  return count > 0 ? count : null;
}

function ConversationMotif({ agentName }: { agentName: WorkAgentName }) {
  let content: ReactNode;
  if (agentName === "course-video") {
    content = (
      <div className="bg-background w-full max-w-52 space-y-3 rounded-xl border p-4 shadow-sm">
        <Type className="h-6 w-6 text-indigo-500" />
        <div className="h-2 w-full rounded-full bg-indigo-200 dark:bg-indigo-800" />
        <div className="h-2 w-4/5 rounded-full bg-neutral-200 dark:bg-neutral-700" />
        <div className="h-2 w-3/5 rounded-full bg-neutral-200 dark:bg-neutral-700" />
      </div>
    );
  } else if (agentName === "creative-studio") {
    content = (
      <div className="bg-background flex h-4/5 w-2/5 flex-col items-center justify-center gap-3 rounded-lg border shadow-sm">
        <ImageIcon className="h-8 w-8 text-indigo-500" />
        <div className="h-2 w-1/2 rounded-full bg-indigo-200 dark:bg-indigo-800" />
        <div className="h-2 w-1/3 rounded-full bg-neutral-200 dark:bg-neutral-700" />
      </div>
    );
  } else if (agentName === "ppt-master") {
    content = (
      <div className="bg-background w-4/5 space-y-3 rounded-lg border p-4 shadow-sm">
        <div className="flex items-center gap-2">
          <Presentation className="h-5 w-5 text-indigo-500" />
          <div className="h-2 w-1/3 rounded-full bg-indigo-200 dark:bg-indigo-800" />
        </div>
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-md bg-indigo-100 dark:bg-indigo-900/40" />
          <div className="col-span-2 space-y-2">
            <div className="h-2 rounded-full bg-neutral-200 dark:bg-neutral-700" />
            <div className="h-2 w-4/5 rounded-full bg-neutral-200 dark:bg-neutral-700" />
          </div>
        </div>
      </div>
    );
  } else {
    content = (
      <div className="flex items-center gap-2">
        <AudioLines className="mr-2 h-8 w-8 text-indigo-500" />
        {["h-8", "h-14", "h-20", "h-12", "h-24", "h-16", "h-10"].map(
          (height) => (
            <span
              key={height}
              className={`w-1.5 rounded-full bg-indigo-400 ${height}`}
            />
          ),
        )}
      </div>
    );
  }

  return (
    <div
      aria-hidden="true"
      data-motif={agentName}
      className="flex aspect-video w-full shrink-0 items-center justify-center overflow-hidden bg-neutral-100 p-5 dark:bg-neutral-900"
    >
      {content}
    </div>
  );
}

function conversationTime(thread: AgentThread): number {
  const value = thread.updated_at ?? thread.created_at;
  return value ? parseTimestamp(value).getTime() : Number.NaN;
}

function AgentConversationGridContent({
  agentName,
  label,
}: {
  agentName: WorkAgentName;
  label: string;
}) {
  const router = useRouter();
  const { t } = useI18n();
  const threadsQuery = useThreads(THREAD_SEARCH_PARAMS);
  const [query, setQuery] = useState("");
  const [visibleCount, setVisibleCount] = useState(THREAD_PAGE_SIZE);

  const agentThreads = useMemo(() => {
    const unique = new Map<string, AgentThread>();
    for (const thread of threadsQuery.data ?? []) {
      if (
        agentNameOfThread(thread) === agentName &&
        !unique.has(thread.thread_id)
      ) {
        unique.set(thread.thread_id, thread);
      }
    }
    return Array.from(unique.values()).sort((left, right) => {
      const leftTime = conversationTime(left);
      const rightTime = conversationTime(right);
      const leftValid = Number.isFinite(leftTime);
      const rightValid = Number.isFinite(rightTime);
      if (leftValid && rightValid && leftTime !== rightTime) {
        return rightTime - leftTime;
      }
      if (leftValid !== rightValid) return leftValid ? -1 : 1;
      return left.thread_id.localeCompare(right.thread_id);
    });
  }, [agentName, threadsQuery.data]);

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const matchingThreads = agentThreads.filter((thread) =>
    titleOfThread(thread).trim().toLocaleLowerCase().includes(normalizedQuery),
  );

  let content;
  if (threadsQuery.isLoading) {
    content = (
      <div
        role="status"
        className="rounded-2xl border border-dashed p-10 text-center"
      >
        {t.workspace.works.loading}
      </div>
    );
  } else if (threadsQuery.isError) {
    content = (
      <div
        role="alert"
        className="rounded-2xl border border-dashed p-10 text-center"
      >
        <p>{t.workspace.works.loadError}</p>
        <Button
          variant="outline"
          className="mt-4"
          onClick={() => void threadsQuery.refetch()}
        >
          {t.workspace.works.retry}
        </Button>
      </div>
    );
  } else if (agentThreads.length === 0) {
    content = (
      <div
        role="status"
        className="rounded-2xl border border-dashed p-10 text-center"
      >
        {t.workspace.works.empty(label)}
      </div>
    );
  } else if (matchingThreads.length === 0) {
    content = (
      <div
        role="status"
        className="rounded-2xl border border-dashed p-10 text-center"
      >
        {t.workspace.works.noResults}
      </div>
    );
  } else {
    content = (
      <>
        <div className="grid grid-cols-1 justify-start gap-5 sm:grid-cols-2 xl:grid-cols-3">
          {matchingThreads.slice(0, visibleCount).map((thread) => {
            const updatedAt = thread.updated_at ?? thread.created_at;
            const validUpdatedAt =
              updatedAt && Number.isFinite(parseTimestamp(updatedAt).getTime())
                ? updatedAt
                : null;
            const turns = userTurnCount(thread);
            return (
              <article
                key={thread.thread_id}
                data-testid="agent-conversation-card"
                data-thread-id={thread.thread_id}
                className="bg-background flex h-full min-w-0 flex-col overflow-hidden rounded-2xl border shadow-sm"
              >
                <ConversationMotif agentName={agentName} />
                <div className="flex flex-1 flex-col space-y-3 p-5">
                  <h3 className="font-semibold break-words">
                    {titleOfThread(thread)}
                  </h3>
                  <p className="text-muted-foreground text-sm">{label}</p>
                  <p className="text-muted-foreground text-xs">
                    {validUpdatedAt
                      ? `${t.workspace.works.updated} ${formatDateTime(validUpdatedAt)}`
                      : t.workspace.works.updatedUnknown}
                  </p>
                  {turns !== null ? (
                    <p className="text-muted-foreground text-xs">
                      {t.workspace.works.turns(turns)}
                    </p>
                  ) : null}
                  <div className="mt-auto">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => router.push(pathOfThread(thread))}
                    >
                      {t.workspace.works.open}
                    </Button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
        {visibleCount < matchingThreads.length ? (
          <div className="mt-6 text-center">
            <Button
              variant="outline"
              onClick={() =>
                setVisibleCount((count) => count + THREAD_PAGE_SIZE)
              }
            >
              {t.common.loadMore}
            </Button>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <section
      aria-label={`${label}${t.workspace.works.history}`}
      className="space-y-5"
    >
      <Input
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        aria-label={t.workspace.works.searchLabel}
        placeholder={t.workspace.works.searchLabel}
        className="max-w-sm"
      />
      {content}
    </section>
  );
}

export function AgentConversationGrid(props: {
  agentName: WorkAgentName;
  label: string;
}) {
  return <AgentConversationGridContent key={props.agentName} {...props} />;
}
