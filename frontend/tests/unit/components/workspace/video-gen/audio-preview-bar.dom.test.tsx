/**
 * Plan B Task 4（2026-10-19）：AudioPreviewBar 的降级可见性。
 *
 * 契约（plan Review Focus「『已开始降级』和『实际 Edge 成功』不得共用同一成功提示」）：
 * - audioReady + degradationWarning → 琥珀色「已使用备用音色（请求 X，实际 Y）」。
 * - 仅 audioError（降级后失败）→ 显示错误文本，**不得**出现成功降级提示。
 * - audioReady 且无 warning → 无降级条。
 *
 * 仓库无 @testing-library/react：createRoot + act + happy-dom 手写 harness。
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { info: vi.fn(), error: vi.fn() } }));

vi.mock("@/core/video-gen/hooks", () => ({
  useGenerateAudio: () => ({
    mutate: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    isError: false,
  }),
}));

import { AudioPreviewBar } from "@/components/workspace/video-gen/audio-preview-bar";

function setup(props: Parameters<typeof AudioPreviewBar>[0]) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const qc = new QueryClient();
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <AudioPreviewBar {...props} />
      </QueryClientProvider>,
    );
  });
  return { container, root };
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("AudioPreviewBar 降级可见性", () => {
  it("audioReady + degradationWarning → 琥珀提示已使用备用音色", () => {
    const { container } = setup({
      taskId: "t1",
      audioReady: true,
      degradationWarning:
        "已使用备用音色（请求 doubao:zh_male_A，实际 zh-CN-YunjianNeural）",
    });
    expect(container.textContent).toContain("已使用备用音色");
    expect(container.textContent).toContain("doubao:zh_male_A");
    const amber = container.querySelector(
      "[data-testid='audio-degradation-warning']",
    );
    expect(amber).not.toBeNull();
    expect(amber!.className).toContain("amber");
  });

  it("audioError（降级后失败）→ 显示错误，无成功降级提示", () => {
    const { container } = setup({
      taskId: "t1",
      audioReady: false,
      audioError: "音频生成失败（配音合成），请重试",
    });
    expect(container.textContent).toContain("音频生成失败");
    expect(container.textContent).not.toContain("已使用备用音色");
    expect(
      container.querySelector("[data-testid='audio-degradation-warning']"),
    ).toBeNull();
  });

  it("audioReady 无 warning → 无降级条", () => {
    const { container } = setup({ taskId: "t1", audioReady: true });
    expect(
      container.querySelector("[data-testid='audio-degradation-warning']"),
    ).toBeNull();
  });
});
