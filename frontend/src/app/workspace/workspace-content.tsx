import { cookies } from "next/headers";
import { Toaster } from "sonner";

import { QueryClientProvider } from "@/components/query-client-provider";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { CommandPalette } from "@/components/workspace/command-palette";
import { DialogProvider } from "@/components/workspace/dialog-provider";
import { FloatingBlobsBackground } from "@/components/workspace/floating-blobs-background";
import { SidebarFirstVisitCollapse } from "@/components/workspace/sidebar-first-visit-collapse";
import { WorkspaceSidebar } from "@/components/workspace/workspace-sidebar";

function parseSidebarOpenCookie(
  value: string | undefined,
): boolean | undefined {
  if (value === "true") return true;
  if (value === "false") return false;
  return undefined;
}

export async function WorkspaceContent({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const cookieStore = await cookies();
  const initialSidebarOpen = parseSidebarOpenCookie(
    cookieStore.get("sidebar_state")?.value,
  );

  return (
    <QueryClientProvider>
      {/* 弹层统一（OBS-3）：workspace 全域挂一次 DialogProvider，useConfirm 在
          gallery/video-gen/voice-twin/artifacts/console 各消费点可用；writer 页
          自带的内层 Provider 语义不受影响（就近 context 生效）。 */}
      <DialogProvider>
        <SidebarProvider
          className="h-screen overflow-hidden"
          defaultOpen={initialSidebarOpen}
        >
          <FloatingBlobsBackground />
          {/* DSN-015：首访窄屏（≤1024px）默认收起；cookie 已存在则尊重用户选择 */}
          <SidebarFirstVisitCollapse
            cookieWasSet={initialSidebarOpen !== undefined}
          />
          <WorkspaceSidebar className="ws-sidebar-glow relative z-10" />
          <SidebarInset className="relative z-10 min-w-0 bg-transparent">
            {children}
          </SidebarInset>
        </SidebarProvider>
        <CommandPalette />
        <Toaster position="top-center" />
      </DialogProvider>
    </QueryClientProvider>
  );
}
