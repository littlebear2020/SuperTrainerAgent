/**
 * 回归（2026-07-24 白屏 P0）：subtask-card.tsx 的 `useSubtask(taskId)!` 非空断言
 * 在 store 尚未填充时抛 "Cannot read properties of undefined (reading 'status')"
 * ——store 两条写入路径（流式 custom 事件、useSyncSubtaskStatus 的 useEffect 反推）
 * 都晚于首次渲染。修复后：store 无对应 task 时渲染 null，写入后自动重渲染。
 *
 * 仓库无 @testing-library/react：沿用 detail-draft-audio-polling.test.tsx 的
 * createRoot + act + happy-dom 手写 harness。渲染期异常经 createRoot 的
 * onCaughtError/onUncaughtError 捕获（React 19 不再从 root rethrow），
 * 断言「无异常 + 渲染为空」双重锁定。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

// streamdown 在模块级 import katex CSS，vitest 无 CSS transform——本用例只走到
// null 渲染分支，stub 掉即可（同仓其他组件测试对重依赖的处置方式一致）。
vi.mock("streamdown", () => ({
  Streamdown: () => null,
}));

// useI18n 需要 I18nProvider；本用例不经过任何文案渲染路径，给最小 t 对象。
vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      subtasks: {
        in_progress: "执行中",
        completed: "已完成",
        failed: "失败",
        executing: (n: number) => `执行 ${n} 个子任务`,
      },
    },
  }),
}));

import { SubtaskCard } from "@/components/workspace/messages/subtask-card";
import { SubtasksProvider } from "@/core/tasks/context";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let caughtErrors: unknown[];

beforeEach(() => {
  caughtErrors = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container, {
    onCaughtError: (err) => caughtErrors.push(err),
    onUncaughtError: (err) => caughtErrors.push(err),
  });
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  document.body.innerHTML = "";
});

test("SubtaskCard renders null without crashing when task is absent from store", async () => {
  await act(async () => {
    root.render(
      <SubtasksProvider>
        <SubtaskCard taskId="missing-task-id" isLoading={false} />
      </SubtasksProvider>,
    );
  });
  expect(caughtErrors).toEqual([]);
  expect(container.firstChild).toBeNull();
});
