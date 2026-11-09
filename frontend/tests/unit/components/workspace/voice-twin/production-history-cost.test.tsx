/**
 * 批次 C Phase D (2026-08-13)：production-history 副行花费渲染。
 *
 * 复用 console.colCost i18n 标签；有值显示金额、null 显示「—」（区别于免费）。
 * renderToStaticMarkup 单遍渲染（无 effect），mock hooks/glass/i18n/sonner。
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      voiceTwin: {
        statusReady: "已完成",
        statusFailed: "失败",
        deleteTaskConfirm: "确定删除这个任务吗？",
        taskDeleted: "任务已删除",
        productionHistory: "制作历史",
      },
      console: { colCost: "花费" },
    },
  }),
}));

vi.mock("@/core/theme/glass", () => ({
  useGlassStyle: () => ({ glassStyle: () => ({}) }),
}));

const { tasksFixture } = vi.hoisted(() => ({
  tasksFixture: {
    data: [] as import("@/core/voice-twin/production-types").ProductionTask[],
  },
}));

vi.mock("@/core/voice-twin/production-hooks", () => ({
  useProductionTasks: () => ({ data: tasksFixture.data, isLoading: false }),
  useDeleteProductionTask: () => ({ mutate: vi.fn() }),
  useResumePptToVideo: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { DialogProvider } from "@/components/workspace/dialog-provider";
import { ProductionHistory } from "@/components/workspace/voice-twin/production-history";
import type { ProductionTask } from "@/core/voice-twin/production-types";

function makeTask(
  cost: number | null,
  currency: string | null,
  status: ProductionTask["status"] = "ready",
): ProductionTask {
  return {
    id: "pt_1",
    task_type: "script_to_audio",
    status,
    progress: 100,
    voice_name: "测试音色",
    title: "测试任务",
    file_size: 1024,
    cost,
    currency,
  };
}

test("production-history 副行显示花费金额（cost 有值）", () => {
  tasksFixture.data = [makeTask(12.5, "CNY")];
  const html = renderToStaticMarkup(
    createElement(DialogProvider, null, createElement(ProductionHistory)),
  );
  expect(html).toContain("花费");
  expect(html).toContain("¥12.50");
});

test("production-history 副行 cost 为 null 显示「—」", () => {
  tasksFixture.data = [makeTask(null, null)];
  const html = renderToStaticMarkup(
    createElement(DialogProvider, null, createElement(ProductionHistory)),
  );
  expect(html).toContain("花费 —");
});

test("production-history 进行中任务不显示花费段（D5，避免恒显示「花费 —」）", () => {
  tasksFixture.data = [makeTask(12.5, "CNY", "synthesizing")];
  const html = renderToStaticMarkup(
    createElement(DialogProvider, null, createElement(ProductionHistory)),
  );
  expect(html).not.toContain("花费");
});
