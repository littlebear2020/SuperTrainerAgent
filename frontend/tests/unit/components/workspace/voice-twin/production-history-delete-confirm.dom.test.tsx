/**
 * 弹层统一（OBS-3，2026-10-09）：production-history 删除任务确认从 window.confirm
 * 换 useConfirm 弹层（PPT 视频任务跑数分钟、删除不可恢复——确认语义必须保持）。
 *
 * 契约（两分支）：
 * - 点删除 → 弹层出现（deleteTaskConfirm 文案）；取消 → deleteTask.mutate 未被调用；
 * - 确认 → mutate 恰被调用一次、参数为 taskId。
 *
 * harness 参照 recent-chat-list-delete-confirm.test.tsx；任务 fixture 参照
 * production-history-cost.test.tsx 的 makeTask。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  deleteTask: vi.fn(),
  tBase: {} as Record<string, unknown>,
  task: {
    id: "pt_1",
    task_type: "ppt_to_video",
    status: "ready",
    progress: 100,
    voice_name: "测试音色",
    title: "测试任务",
    file_size: 1024,
    cost: null,
    currency: null,
  },
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: h.tBase }),
}));

vi.mock("@/core/theme/glass", () => ({
  useGlassStyle: () => ({ glassStyle: () => ({}) }),
}));

vi.mock("@/core/voice-twin/production-hooks", () => ({
  useProductionTasks: () => ({ data: [h.task], isLoading: false }),
  useDeleteProductionTask: () => ({ mutate: h.deleteTask }),
  useResumePptToVideo: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { DialogProvider } from "@/components/workspace/dialog-provider";
import { ProductionHistory } from "@/components/workspace/voice-twin/production-history";
import { zhCN } from "@/core/i18n/locales/zh-CN";

h.tBase = zhCN as unknown as Record<string, unknown>;

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const DELETE_CONFIRM_TEXT =
  zhCN.voiceTwin.deleteTaskConfirm ?? "确定删除这个任务吗？";

function buttonsWithText(text: string): HTMLButtonElement[] {
  return Array.from(document.querySelectorAll("button")).filter(
    (b) => b.textContent?.trim() === text,
  );
}

/** 删除按钮是 Trash2 图标按钮（无文字）——按 lucide svg class 定位。 */
function trashButtons(): HTMLButtonElement[] {
  return Array.from(document.querySelectorAll("button")).filter((b) =>
    b.innerHTML.includes("trash2"),
  );
}

async function clickRadix(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("production-history 删除任务确认弹层（OBS-3）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.deleteTask.mockClear();
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

  async function renderHistory() {
    await act(async () => {
      root.render(
        <DialogProvider>
          <ProductionHistory />
        </DialogProvider>,
      );
    });
  }

  it("点删除 → 弹层出现；取消 → deleteTask 未被调用", async () => {
    await renderHistory();
    const trash = trashButtons()[0];
    expect(trash).toBeTruthy();
    await clickRadix(trash!);

    expect(document.body.textContent).toContain(DELETE_CONFIRM_TEXT);
    expect(h.deleteTask).not.toHaveBeenCalled();

    const cancel = buttonsWithText("取消")[0];
    expect(cancel).toBeTruthy();
    await clickRadix(cancel!);
    expect(h.deleteTask).not.toHaveBeenCalled();
  });

  it("点删除 → 确认 → deleteTask 恰被调用一次（参数 = taskId）", async () => {
    await renderHistory();
    await clickRadix(trashButtons()[0]!);

    const confirm = buttonsWithText("确定")[0];
    expect(confirm).toBeTruthy();
    await clickRadix(confirm!);
    expect(h.deleteTask).toHaveBeenCalledTimes(1);
    expect(h.deleteTask).toHaveBeenCalledWith("pt_1", expect.anything());
  });
});
