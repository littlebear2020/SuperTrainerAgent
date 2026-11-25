/**
 * PR3（2026-11-24 前端可靠性批）：会话里的恢复入口。断线且 run 可能仍在服务端运行时
 * 给「重新连接」，取消失败时给「重试停止」；两者都是用户显式触发，不自动重连。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      recovery: {
        disconnected: "连接已中断，任务可能仍在运行",
        reconnect: "重新连接",
        reconnecting: "连接中…",
        cancelFailed: "停止失败，任务可能仍在运行",
        retryCancel: "重试停止",
      },
    },
  }),
}));

import { ThreadContext } from "@/components/workspace/messages/context";
import { RecoveryBanner } from "@/components/workspace/messages/recovery-banner";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

const thread = { messages: [], values: {}, error: null, isLoading: false };

function recovery(overrides: Record<string, unknown> = {}) {
  return {
    isRecoverableDisconnect: false,
    isReconnecting: false,
    reconnect: vi.fn(async () => undefined),
    cancelFailed: false,
    isCancelling: false,
    retryCancel: vi.fn(async () => undefined),
    ...overrides,
  };
}

async function render(value: Record<string, unknown>) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await update(value);
}

async function update(value: Record<string, unknown>) {
  await act(async () => {
    root.render(
      <ThreadContext.Provider value={value as never}>
        <RecoveryBanner />
      </ThreadContext.Provider>,
    );
  });
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

test("没有恢复需求时不渲染", async () => {
  await render({ thread, recovery: recovery() });
  expect(container.textContent).toBe("");
});

test("没有 recovery（agents/new 等其他消费者）时不渲染", async () => {
  await render({ thread });
  expect(container.textContent).toBe("");
});

test("断线且 run 可能仍在运行：显示重新连接；点击调用 reconnect，重连中禁用", async () => {
  const reconnect = vi.fn(async () => undefined);
  await render({
    thread,
    recovery: recovery({ isRecoverableDisconnect: true, reconnect }),
  });
  expect(container.textContent).toContain("连接已中断，任务可能仍在运行");

  await act(async () => {
    container.querySelector<HTMLButtonElement>("button")?.click();
  });
  expect(reconnect).toHaveBeenCalledTimes(1);

  await update({
    thread,
    recovery: recovery({
      isRecoverableDisconnect: true,
      isReconnecting: true,
      reconnect,
    }),
  });
  const button = container.querySelector<HTMLButtonElement>("button");
  expect(button?.disabled).toBe(true);
  expect(container.textContent).toContain("连接中…");
});

test("取消失败：显示重试停止；点击调用 retryCancel；重试在途时禁用", async () => {
  const retryCancel = vi.fn(async () => undefined);
  const reconnect = vi.fn(async () => undefined);
  await render({
    thread,
    recovery: recovery({ cancelFailed: true, retryCancel, reconnect }),
  });
  expect(container.textContent).toContain("停止失败，任务可能仍在运行");

  await act(async () => {
    container.querySelector<HTMLButtonElement>("button")?.click();
  });
  expect(retryCancel).toHaveBeenCalledTimes(1);
  expect(reconnect).not.toHaveBeenCalled();

  await update({
    thread,
    recovery: recovery({
      cancelFailed: true,
      isCancelling: true,
      retryCancel,
      reconnect,
    }),
  });
  expect(container.querySelector<HTMLButtonElement>("button")?.disabled).toBe(
    true,
  );
});
