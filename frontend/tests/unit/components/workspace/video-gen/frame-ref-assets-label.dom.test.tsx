/** 二期 M5：分镜级「参考资产」标签（复用 sidecar ref_injection，决策 19/B15）。 */

import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test, vi } from "vitest";

vi.mock("@/core/video-gen/hooks", () => ({
  useRegenerateFrame: () => ({
    mutate: vi.fn(),
    isPending: false,
    variables: undefined,
  }),
  useUploadFrameVisual: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateStoryboardFrame: () => ({
    mutate: vi.fn(),
    isPending: false,
    data: undefined,
    reset: vi.fn(),
  }),
}));

import {
  FrameOpsPanel,
  refAssetsLabel,
} from "@/components/workspace/video-gen/video-gen-frame-ops-panel";
import type {
  RefInjectionRecord,
  StoryboardFrameData,
} from "@/core/video-gen/types";

const frame: StoryboardFrameData = {
  index: 3,
  narration: "旁白",
  image_prompt: "p",
  duration: 5,
};

test("refAssetsLabel：实体聚合计数 + style_anchor 归一为「锚图」", () => {
  const rec: RefInjectionRecord = {
    frame_index: 3,
    injected_refs: [
      { entity: "小林", path: "/a.png", view: "front" },
      { entity: "小林", path: "/b.png", view: "side" },
      { entity: "魔方", path: "/m.png", view: 0 },
      { entity: "style_anchor", path: "/anchor.png" },
    ],
  };
  expect(refAssetsLabel(rec)).toBe("小林 ×2 / 魔方 ×1 / 锚图 ×1");
});

test("refAssetsLabel：任务级 entity=ref 归一为「参考图」；无注入 → null", () => {
  expect(
    refAssetsLabel({
      frame_index: 0,
      injected_refs: [{ entity: "ref", path: "/x.png" }],
    }),
  ).toBe("参考图 ×1");
  expect(refAssetsLabel({ frame_index: 0, injected_refs: [] })).toBeNull();
  expect(refAssetsLabel(undefined)).toBeNull();
});

test("FrameOpsPanel：传 refRecord 渲染「参考：…」标签行", () => {
  const html = renderToStaticMarkup(
    createElement(FrameOpsPanel, {
      taskId: "t1",
      frame,
      taskStatus: "ready",
      onClose: vi.fn(),
      refRecord: {
        frame_index: 3,
        injected_refs: [
          { entity: "小林", path: "/a.png", view: "front" },
          { entity: "style_anchor", path: "/anchor.png" },
        ],
      },
    }),
  );
  expect(html).toContain("参考：小林 ×1 / 锚图 ×1");
});

test("FrameOpsPanel：无 refRecord 不渲染标签（既有任务零视觉变化）", () => {
  const html = renderToStaticMarkup(
    createElement(FrameOpsPanel, {
      taskId: "t1",
      frame,
      taskStatus: "ready",
      onClose: vi.fn(),
    }),
  );
  expect(html).not.toContain("参考：");
});

test("FrameOpsPanel：DOM 挂载路径带 data-testid 可被 E2E 锚定", async () => {
  const div = document.createElement("div");
  document.body.appendChild(div);
  const root = createRoot(div);
  await act(async () => {
    root.render(
      createElement(FrameOpsPanel, {
        taskId: "t1",
        frame,
        taskStatus: "ready",
        onClose: vi.fn(),
        refRecord: {
          frame_index: 3,
          injected_refs: [{ entity: "魔方", path: "/m.png" }],
        },
      }),
    );
  });
  expect(div.querySelector('[data-testid="frame-ref-assets"]')).not.toBeNull();
  expect(div.textContent).toContain("魔方 ×1");
  await act(async () => {
    root.unmount();
  });
  div.remove();
});
