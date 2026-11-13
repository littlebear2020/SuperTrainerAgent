import { Suspense } from "react";

import { VideoGenPage } from "@/components/workspace/video-gen";

function VideoGenPageFallback() {
  return (
    <div
      className="scrollbar-none flex size-full flex-col p-4 sm:p-6"
      aria-busy="true"
    >
      <div className="mx-auto w-full max-w-6xl animate-pulse space-y-6">
        <div className="bg-muted h-7 w-28 rounded-md" />
        <div className="bg-muted/50 h-64 rounded-2xl" />
      </div>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<VideoGenPageFallback />}>
      <VideoGenPage />
    </Suspense>
  );
}
