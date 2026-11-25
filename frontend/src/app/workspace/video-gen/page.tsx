import { Suspense } from "react";

import { VideoGenPage } from "@/components/workspace/video-gen";

function VideoGenPageFallback() {
  return (
    <div
      className="scrollbar-none flex size-full flex-col p-4 sm:p-6"
      aria-busy="true"
    >
      <div className="mx-auto w-full max-w-6xl animate-pulse space-y-6">
        <div className="space-y-2">
          <div className="bg-muted h-7 w-28 rounded-md" />
          <div className="bg-muted h-5 w-80 max-w-full rounded-md" />
        </div>
        <div className="bg-muted/50 h-11 rounded-lg" />
        <div className="flex flex-col gap-3 sm:flex-row">
          <div className="bg-muted h-9 flex-1 rounded-md" />
          <div className="bg-muted h-9 w-24 rounded-md" />
        </div>
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((item) => (
            <div key={item} className="overflow-hidden rounded-2xl border">
              <div className="bg-muted aspect-video" />
              <div className="space-y-3 p-4">
                <div className="bg-muted h-4 w-24 rounded" />
                <div className="bg-muted h-5 w-full rounded" />
                <div className="bg-muted h-12 w-full rounded-lg" />
              </div>
            </div>
          ))}
        </div>
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
