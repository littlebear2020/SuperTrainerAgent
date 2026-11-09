/**
 * 灵感库「弹窗内上传文档」编排（2026-07-31，产品决策 D2）：
 *   1) instantiate(defer) 建线程拿 thread_id + compiled_prompt
 *   2) 上传全部文件变量到该线程（/api/threads/{id}/uploads，markitdown 自动转换）
 *   3) 预填 prompt 并标记 autoSend + existingThread（聊天页 mount 自动发送，
 *      UploadsMiddleware 会把已上传文件注入首轮 run）
 *   4) 跳转聊天页
 * 依赖注入以便 node 环境单测；组件层负责 toast/进度态。
 */
export interface FileVarsSubmitDeps {
  instantiate: (
    caseId: string,
    vars: Record<string, string>,
  ) => Promise<{ thread_id: string; compiled_prompt?: string }>;
  upload: (threadId: string, files: File[]) => Promise<unknown>;
  seedPrompt: (payload: {
    prompt: string;
    autoSend: boolean;
    existingThread: boolean;
  }) => void;
  navigate: (threadId: string) => void;
}

export async function submitFileVarsCase(
  caseId: string,
  vars: Record<string, string>,
  files: Record<string, File[]>,
  deps: FileVarsSubmitDeps,
): Promise<void> {
  const { thread_id, compiled_prompt } = await deps.instantiate(caseId, vars);
  const allFiles = Object.values(files).flat();
  if (allFiles.length > 0) {
    await deps.upload(thread_id, allFiles);
  }
  deps.seedPrompt({
    prompt: compiled_prompt ?? "",
    autoSend: true,
    existingThread: true,
  });
  deps.navigate(thread_id);
}
