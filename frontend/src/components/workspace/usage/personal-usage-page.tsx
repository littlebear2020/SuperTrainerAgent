"use client";

import { localTzOffsetMinutes } from "@/core/console/format";
import { usePersonalUsage } from "@/core/usage/hooks";

export function PersonalUsagePage() {
  const query = usePersonalUsage({
    days: 7,
    capability: "all",
    offset: 0,
    limit: 20,
    tzOffsetMinutes: localTzOffsetMinutes(),
  });

  return (
    <main
      data-testid="personal-usage-page"
      className="size-full overflow-y-auto p-6"
    >
      <h1 className="text-lg font-semibold">我的用量</h1>
      {query.isLoading ? <p>正在加载用量…</p> : null}
      {query.isError ? <p>用量加载失败，请稍后重试。</p> : null}
      {query.data ? <p>共 {query.data.total_tasks} 项任务</p> : null}
    </main>
  );
}
