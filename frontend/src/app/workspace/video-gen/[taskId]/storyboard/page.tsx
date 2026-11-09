"use client";

import { use } from "react";

import { RemotionCourseStoryboard } from "@/components/workspace/video-gen/remotion-course-storyboard";
import { StoryboardRouteSplit } from "@/components/workspace/video-gen/storyboard-route-split";
import { VideoGenStoryboard } from "@/components/workspace/video-gen/video-gen-storyboard";

export default function Page({
  params,
}: {
  params: Promise<{ taskId: string }>;
}) {
  // Next.js 16 async params — unwrap with React.use() in client component
  return <StoryboardPageWrapper params={params} />;
}

function StoryboardPageWrapper({
  params,
}: {
  params: Promise<{ taskId: string }>;
}) {
  const { taskId } = use(params);
  // P0-1: scoped scroll container (matches chats page pattern). SidebarInset is
  // flex-1 inside h-screen SidebarProvider, so h-full = viewport height →
  // overflow-y-auto scrolls long storyboard. Does NOT touch global SidebarInset
  // (which wraps all workspace pages → avoids cross-page regression, reviewer #9).
  return (
    <div className="scrollbar-none h-full overflow-y-auto">
      <StoryboardRouteSplit
        taskId={taskId}
        renderCourse={(id) => <RemotionCourseStoryboard taskId={id} />}
        renderDefault={(id) => <VideoGenStoryboard taskId={id} />}
      />
    </div>
  );
}
