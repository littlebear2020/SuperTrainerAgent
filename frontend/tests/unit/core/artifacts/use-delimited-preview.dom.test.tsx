/**
 * PR-3 5.2：useDelimitedPreview 的 1MiB 输入裁剪与 worker 生命周期钉子。
 * （上游同用例用 @testing-library renderHook；本地无该库——host 组件 +
 * createRoot + act 手写 harness，Worker 用 FakeWorker 全局桩替换。）
 */

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { useDelimitedPreview } from "@/core/artifacts/use-delimited-preview";

const sample = {
  rows: [["a"]],
  columnCount: 1,
  limited: false,
  unevenRows: false,
};

class FakeWorker {
  static instances: FakeWorker[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessageerror: (() => void) | null = null;
  postMessage = vi.fn();
  terminate = vi.fn();
  constructor() {
    FakeWorker.instances.push(this);
  }
  complete() {
    this.onmessage?.({ data: { result: sample } } as MessageEvent);
  }
}

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function HookHost({
  content,
  active,
  identity,
}: {
  content: string;
  active: boolean;
  identity: string;
}) {
  // host 组件持有 hook 结果供断言（renderHook 等价物）——effect 期写容器，
  // 组件内不重赋值模块级变量（react-hooks 编译器规则）
  const value = useDelimitedPreview({
    content,
    delimiter: ",",
    truncated: false,
    active,
    identity,
  });
  useEffect(() => {
    latest.current = value;
  });
  return null;
}
const latest: { current: ReturnType<typeof useDelimitedPreview> | undefined } =
  {
    current: undefined,
  };

beforeEach(() => {
  FakeWorker.instances = [];
  vi.stubGlobal("Worker", FakeWorker);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  vi.unstubAllGlobals();
});

test("仅 active 时起 worker；成功后结果就绪且复用", async () => {
  await act(async () => {
    root.render(<HookHost content="a" active={false} identity="f1" />);
  });
  expect(FakeWorker.instances).toHaveLength(0);

  await act(async () => {
    root.render(<HookHost content="a" active={true} identity="f1" />);
  });
  expect(FakeWorker.instances).toHaveLength(1);
  expect(latest.current?.status).toBe("loading");
  await act(async () => {
    FakeWorker.instances.at(-1)!.complete();
  });
  expect(latest.current?.status).toBe("ready");
  expect(latest.current?.result).toEqual(sample);

  // 无关重渲染（同 key）不另起 worker
  await act(async () => {
    root.render(<HookHost content="a" active={true} identity="f1" />);
  });
  expect(FakeWorker.instances).toHaveLength(1);
});

test("超 1MiB 输入：发送前裁到 DELIMITED_INPUT_LIMIT 且 truncated=true", async () => {
  const oversized = "a".repeat(1_048_576 + 100);
  await act(async () => {
    root.render(<HookHost content={oversized} active={true} identity="big" />);
  });
  const worker = FakeWorker.instances.at(-1) ?? new FakeWorker();
  expect(worker.postMessage).toHaveBeenCalledTimes(1);
  const payload = worker.postMessage.mock.calls[0]![0] as {
    content: string;
    truncated: boolean;
  };
  expect(payload.content.length).toBe(1_048_576);
  expect(payload.truncated).toBe(true);
});
