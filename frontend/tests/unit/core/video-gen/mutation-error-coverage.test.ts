import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * 行为锁：每个 useMutation 都要有失败反馈。
 *
 * useGenerateAudio（生成配音）/ useDeleteVideoTask（删除任务）等失败时完全无提示，
 * 用户点了没反应，无法区分「没点上」「参数错」「服务器挂了」。
 *
 * useSubmitAIVisual 指向 POST /api/video/ai-visual —— 后端是稳定必坏的死端点
 * （硬编码 raise 410，引导到 /create），且全 UI 零调用者，删除比补 onError 更干净。
 *
 * 用源码文本锁而非行为测试：这些 hook 需要 QueryClient + toast provider 才能跑，
 * 而要锁的不变量（"每个 mutation 都带 onError"）本身就是源码结构属性。
 */
describe("video-gen mutation 错误反馈覆盖", () => {
  const hooksSrc = readFileSync(
    join(process.cwd(), "src/core/video-gen/hooks.ts"),
    "utf-8",
  );
  const apiSrc = readFileSync(
    join(process.cwd(), "src/core/video-gen/api.ts"),
    "utf-8",
  );

  /** 按顶层 `export function` 切块，取指定 hook 的函数体（不含后续 hook）。 */
  function hookBody(name: string): string {
    const blocks = hooksSrc.split(/(?=^export function )/m);
    const found = blocks.find((b) =>
      new RegExp(`^export function ${name}\\b`).test(b),
    );
    expect(found, `hooks.ts 里找不到 ${name}`).toBeTruthy();
    return found!;
  }

  /** 所有含 useMutation 的顶层 hook 名。 */
  function mutationHookNames(): string[] {
    return hooksSrc
      .split(/(?=^export function )/m)
      .filter((b) => b.includes("useMutation"))
      .map((b) => /^export function (\w+)/.exec(b)?.[1])
      .filter((n): n is string => !!n);
  }

  it.each([
    "useGenerateAudio",
    "useDeleteVideoTask",
    "useUpdateStory",
    "useBGMUpload",
    "useRedoVimaxShot",
  ])("%s 必须有 onError（失败无提示则用户以为没点上）", (name) => {
    expect(hookBody(name)).toContain("onError");
  });

  it("所有 mutation hook 都有 onError（防新增 hook 再次遗漏）", () => {
    const missing = mutationHookNames().filter(
      (name) => !hookBody(name).includes("onError"),
    );
    expect(missing).toEqual([]);
  });

  it("submitAIVisual 应已删除（指向 410 死端点、无 UI 调用者）", () => {
    expect(apiSrc).not.toContain("submitAIVisual");
    expect(hooksSrc).not.toContain("useSubmitAIVisual");
  });
});
