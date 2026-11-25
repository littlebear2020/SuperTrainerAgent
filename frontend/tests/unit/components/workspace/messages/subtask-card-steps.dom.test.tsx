/**
 * PR-3 5.1 行为级断言：子任务运行中卡片的步骤时间线逐步增长——走真实链路
 * mapTaskEvent（steps 单条 delta）→ applySubtaskPatch（mergeSteps 合并）→
 * store setTasks → SubtaskCard 渲染，每追加一条 task_running 事件 DOM 行数 +1。
 */

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

// streamdown mock：SafeStreamdown 系渲染原文本（AI 步骤文本可断言）
vi.mock("streamdown", () => ({
  Streamdown: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      subtasks: {
        in_progress: "子任务运行中",
        completed: "子任务已完成",
        failed: "子任务失败",
        cancelled: "子任务已取消",
        executing: (n: number) => `执行 ${n} 个子任务`,
      },
      toolCalls: { useTool: (n: string) => n },
    },
  }),
}));

import { SubtaskCard } from "@/components/workspace/messages/subtask-card";
import {
  applySubtaskPatch,
  SubtasksProvider,
  useSubtaskContext,
  type SubtaskContextValue,
} from "@/core/tasks/context";
import { mapTaskEvent } from "@/core/tasks/task-event";
import type { Subtask } from "@/core/tasks/types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
// react-hooks 编译器规则禁组件内重赋值组件外变量——effect 期写入容器对象
const ctx: { current: SubtaskContextValue | null } = { current: null };

function ContextProbe() {
  const value = useSubtaskContext();
  useEffect(() => {
    ctx.current = value;
  });
  return null;
}

function taskRunning(taskId: string, index: number, message: unknown) {
  return {
    type: "task_running",
    task_id: taskId,
    message,
    message_index: index,
  };
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  document.body.innerHTML = "";
});

test("运行中卡片：每条 task_running 事件后步骤行逐步增长（真实链路合并）", async () => {
  const taskId = "t-steps";
  let task: Subtask | undefined;

  await act(async () => {
    root.render(
      <SubtasksProvider>
        <ContextProbe />
        <SubtaskCard taskId={taskId} isLoading={true} />
      </SubtasksProvider>,
    );
  });
  expect(container.textContent).not.toContain("web_search");

  // 事件 1：tool 消息（search）
  task = applySubtaskPatch(
    task,
    mapTaskEvent(
      taskRunning(taskId, 1, {
        type: "tool",
        name: "web_search",
        content: "ok",
      }),
    )!,
  );
  await act(async () => {
    ctx.current!.setTasks({ [taskId]: task! });
  });
  // 卡片默认折叠（collapsed=true）——点头部按钮展开后时间线才可见
  await act(async () => {
    container.querySelector("button")?.click();
  });
  expect(container.textContent).toContain("web_search");

  // 事件 2：AI 推理文本
  task = applySubtaskPatch(
    task,
    mapTaskEvent(
      taskRunning(taskId, 2, { type: "ai", content: "正在检索相关资料" }),
    )!,
  );
  await act(async () => {
    ctx.current!.setTasks({ [taskId]: task! });
  });
  expect(container.textContent).toContain("正在检索相关资料");
  expect(container.textContent).toContain("web_search");

  // 事件 3：再一个 tool（write）——行数继续增长且旧步骤保留（禁 spread 的
  // 直接证据：事件 3 的 delta 只带 message_index=3，若 spread 会抹掉前两条）
  task = applySubtaskPatch(
    task,
    mapTaskEvent(
      taskRunning(taskId, 3, {
        type: "tool",
        name: "write_file",
        content: "done",
      }),
    )!,
  );
  await act(async () => {
    ctx.current!.setTasks({ [taskId]: task });
  });
  expect(container.textContent).toContain("write_file");
  expect(container.textContent).toContain("正在检索相关资料");
  expect(container.textContent).toContain("web_search");

  // store 状态断言：steps 按 message_index 有序 3 条
  expect(task?.steps?.map((s) => s.message_index)).toEqual([1, 2, 3]);
  expect(task?.steps?.map((s) => s.kind)).toEqual(["tool", "ai", "tool"]);
});

test("终态卡片：cancelled 显示已取消而不是失败；failed 没有错误详情时显示状态级文案（PR3）", async () => {
  const base = {
    subagent_type: "general-purpose",
    description: "整理资料",
    prompt: "p",
  };
  await act(async () => {
    root.render(
      <SubtasksProvider>
        <ContextProbe />
        <SubtaskCard taskId="t-cancel" isLoading={false} />
        <SubtaskCard taskId="t-fail" isLoading={false} />
      </SubtasksProvider>,
    );
  });
  await act(async () => {
    ctx.current!.setTasks({
      "t-cancel": { ...base, id: "t-cancel", status: "cancelled" },
      // 所属 run 是 error：runs API 没有 error 字段，卡片上没有可显示的错误详情
      "t-fail": { ...base, id: "t-fail", status: "failed" },
    });
  });
  const [cancelCard, failCard] = [...container.children];
  expect(cancelCard?.textContent).toContain("子任务已取消");
  expect(cancelCard?.textContent).not.toContain("子任务失败");

  // 展开两张卡：折叠时头部显示状态标签，展开后头部标签让位给终态行
  for (const button of [...container.querySelectorAll("button")]) {
    await act(async () => {
      button.click();
    });
  }
  expect(cancelCard?.textContent).toContain("子任务已取消");
  // 不虚构错误详情，但要有状态级文案——不能是一行空的红字
  expect(failCard?.textContent).toContain("子任务失败");
});
