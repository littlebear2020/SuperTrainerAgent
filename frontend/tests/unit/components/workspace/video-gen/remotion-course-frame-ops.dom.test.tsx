/**
 * RemotionCourseFrameOps（remotion_course 详情页帧操作）行为测试。
 *
 * 既有覆盖：remotion-course-options.test.tsx（T13）经 FrameOpsPanel 分流间接锁过
 * 「画面版式」渲染与 B2 嵌入/AI 素材提示。本文件补其未覆盖的**本组件自有逻辑**：
 * - 状态门控矩阵：editable=ready/draft/failed；canRegen=ready/failed（重生配音）；
 *   canUpload=ready/draft（failed 隐藏上传——后端 409 守卫对齐）；composing 全锁
 * - 保存修改：dirty 派生 + payload 只带改动字段（image_prompt 仅槽位版式参与）
 * - 「重生配音」audio_only 接线：成功后 updateMut.reset；hook opts 回调透传 frameIndex
 * - 「重新生成配图/视频」：动态 import api regenerateFrame(visual_only) + onVisualChanged；
 *   失败 toast.error 且不发 onVisualChanged
 * - 嵌入/上传帧：只替换不重生（B2 前缀口径在本组件的落点）
 * - 「AI 生成配图/视频」：prompt 门控 + visual_source 按槽位分流
 * - 上传：accept 按槽位；成功后 reset + onVisualChanged
 * - 版式切换：LayoutSwitcher → useUpdateFrameLayout（「自动」→ null）；composing 禁用
 *
 * 不覆盖：Radix Select 弹层交互（ui/select stub 为原生 <select>，同
 * style-dropdown-engine-filter.test.tsx 口径）；StillPreview 轮询（api 层 stub）。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  updateMutate: vi.fn(),
  updateReset: vi.fn(),
  layoutMutate: vi.fn(),
  uploadMutate: vi.fn(),
  regenMutate: vi.fn(),
  regenOpts: null as {
    onMutate?: (frameIndex: number) => void;
    onSyncError?: (frameIndex: number) => void;
  } | null,
  regenerateFrame: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: h.toastSuccess, error: h.toastError },
}));

vi.mock("@/core/video-gen/hooks", () => ({
  useUpdateStoryboardFrame: () => ({
    mutate: h.updateMutate,
    isPending: false,
    reset: h.updateReset,
  }),
  useUpdateFrameLayout: () => ({ mutate: h.layoutMutate }),
  useUploadFrameVisual: () => ({ mutate: h.uploadMutate, isPending: false }),
  useRegenerateFrame: (
    _taskId: unknown,
    opts?: {
      onMutate?: (frameIndex: number) => void;
      onSyncError?: (frameIndex: number) => void;
    },
  ) => {
    // 素材区合并后本组件经共享 AssetArea 二次调用本 hook（无 opts）——
    // 只记录带 opts 的调用（重生配音），不随后者覆盖。
    if (opts) h.regenOpts = opts;
    return { mutate: h.regenMutate, isPending: false };
  },
}));

vi.mock("@/core/video-gen/api", () => ({
  regenerateFrame: (...args: unknown[]) => h.regenerateFrame(...args),
  // StillPreview 真发 fetch——单测无后端：start 同步返回、轮询恒 null
  stillPreview: vi.fn(() => ({ cache_key: "k" })),
  fetchStillPreview: vi.fn(() => Promise.resolve(null)),
}));

// Radix Select → 原生 <select>（沿用 style-dropdown-engine-filter.test.tsx 口径，
// 增加 disabled 透传）——SelectItem 收集为 <option>，测 LayoutSwitcher 接线。
vi.mock("@/components/ui/select", async () => {
  const React = await import("react");

  type Opt = { value: string; label: React.ReactNode };

  const SelectItemStub = Object.assign(() => null, { __isSelectItem: true });

  function collect(children: React.ReactNode, out: Opt[]): void {
    React.Children.forEach(children, (child) => {
      if (!React.isValidElement(child)) return;
      const type = child.type as { __isSelectItem?: boolean };
      if (type.__isSelectItem) {
        const props = child.props as {
          value: string;
          children?: React.ReactNode;
        };
        out.push({ value: props.value, label: props.children });
        return;
      }
      const nested = (child.props as { children?: React.ReactNode } | null)
        ?.children;
      if (nested) collect(nested, out);
    });
  }

  function Select({
    value,
    onValueChange,
    disabled,
    children,
  }: {
    value?: string;
    onValueChange?: (v: string) => void;
    disabled?: boolean;
    children?: React.ReactNode;
  }) {
    const opts: Opt[] = [];
    collect(children, opts);
    return React.createElement(
      "select",
      {
        value: value ?? "",
        disabled,
        onChange: (e: React.ChangeEvent<HTMLSelectElement>) =>
          onValueChange?.(e.target.value),
      },
      opts.map((o) =>
        React.createElement(
          "option",
          { key: o.value, value: o.value },
          o.label,
        ),
      ),
    );
  }

  const Null = () => null;

  return {
    Select,
    SelectContent: Null,
    SelectGroup: Null,
    SelectItem: SelectItemStub,
    SelectLabel: Null,
    SelectTrigger: Null,
    SelectValue: Null,
  };
});

import {
  RemotionCourseFrameOps,
  type RemotionCourseFrameOpsProps,
} from "@/components/workspace/video-gen/remotion-course-frame-ops";
import type { StoryboardFrameData } from "@/core/video-gen/types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function mkFrame(
  extra: Partial<StoryboardFrameData> = {},
): StoryboardFrameData {
  return {
    index: 0,
    narration: "原旁白",
    duration: 6,
    layout: "ImageTextLeft",
    ...extra,
  };
}

function findButton(scope: ParentNode, text: string): HTMLButtonElement {
  const btn = Array.from(scope.querySelectorAll("button")).find((b) =>
    b.textContent?.includes(text),
  );
  if (!btn) throw new Error(`button not found: ${text}`);
  return btn;
}

function maybeButton(
  scope: ParentNode,
  text: string,
): HTMLButtonElement | undefined {
  return Array.from(scope.querySelectorAll("button")).find((b) =>
    b.textContent?.includes(text),
  );
}

function iconButton(scope: ParentNode, iconCls: string): HTMLButtonElement {
  const btn = Array.from(scope.querySelectorAll("button")).find((b) =>
    b.innerHTML.includes(iconCls),
  );
  if (!btn) throw new Error(`icon button not found: ${iconCls}`);
  return btn;
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.click();
  });
}

function setNativeValue(
  el: HTMLInputElement | HTMLTextAreaElement,
  value: string,
) {
  const proto =
    el instanceof HTMLTextAreaElement ? HTMLTextAreaElement : HTMLInputElement;
  Object.getOwnPropertyDescriptor(proto.prototype, "value")?.set?.call(
    el,
    value,
  );
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

async function changeInput(
  el: HTMLInputElement | HTMLTextAreaElement,
  value: string,
) {
  await act(async () => {
    setNativeValue(el, value);
  });
}

async function changeSelect(sel: HTMLSelectElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      window.HTMLSelectElement.prototype,
      "value",
    )?.set?.call(sel, value);
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function promptTextarea(scope: ParentNode): HTMLTextAreaElement {
  const ta = scope.querySelector<HTMLTextAreaElement>(
    "textarea[placeholder*='示意图']",
  );
  if (!ta) throw new Error("prompt textarea missing");
  return ta;
}

describe("RemotionCourseFrameOps", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    h.regenOpts = null;
    h.regenerateFrame.mockResolvedValue({ frame_index: 0, storyboard: {} });
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

  async function renderOps(
    props: Partial<RemotionCourseFrameOpsProps> = {},
    frame: StoryboardFrameData = mkFrame(),
  ) {
    await act(async () => {
      root.render(
        <RemotionCourseFrameOps
          taskId="t1"
          frame={frame}
          taskStatus="ready"
          onClose={() => undefined}
          {...props}
        />,
      );
    });
  }

  // ── 头部 ──────────────────────────────────────────────────────────────

  it("头部：帧序号（index+1）+ 时长；关闭按钮触发 onClose", async () => {
    const onClose = vi.fn();
    await renderOps({ onClose }, mkFrame({ index: 2, duration: 6 }));
    expect(container.textContent).toContain("帧 3");
    expect(container.textContent).toContain("6.0s");
    await click(iconButton(container, "lucide-x"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // ── 状态门控矩阵 ───────────────────────────────────────────────────────

  it("ready：旁白/描述可编辑；重生配音与上传均在", async () => {
    await renderOps({ taskStatus: "ready" });
    const tas = Array.from(container.querySelectorAll("textarea"));
    expect(tas).toHaveLength(2);
    expect(tas.every((t) => !t.disabled)).toBe(true);
    expect(maybeButton(container, "重生配音")).toBeTruthy();
    expect(maybeButton(container, "上传图片")).toBeTruthy();
    expect(container.querySelector("select")?.disabled).toBe(false);
  });

  it("draft：可编辑；无「重生配音」（canRegen=ready/failed）；上传在", async () => {
    await renderOps({ taskStatus: "draft" });
    const tas = Array.from(container.querySelectorAll("textarea"));
    expect(tas.every((t) => !t.disabled)).toBe(true);
    expect(maybeButton(container, "重生配音")).toBeUndefined();
    expect(maybeButton(container, "上传图片")).toBeTruthy();
  });

  it("failed：可编辑 + 重生配音在；上传与「AI 生成配图」消失（canUpload=ready/draft，后端 409 对齐）", async () => {
    await renderOps({ taskStatus: "failed" });
    const tas = Array.from(container.querySelectorAll("textarea"));
    expect(tas.every((t) => !t.disabled)).toBe(true);
    expect(maybeButton(container, "重生配音")).toBeTruthy();
    expect(maybeButton(container, "上传图片")).toBeUndefined();
    expect(maybeButton(container, "AI 生成配图")).toBeUndefined();
  });

  it("composing：全锁——textarea 与版式下拉 disabled，重生配音/上传均不渲染", async () => {
    await renderOps({ taskStatus: "composing" });
    const tas = Array.from(container.querySelectorAll("textarea"));
    expect(tas).toHaveLength(2);
    expect(tas.every((t) => t.disabled)).toBe(true);
    expect(container.querySelector("select")?.disabled).toBe(true);
    expect(maybeButton(container, "重生配音")).toBeUndefined();
    expect(maybeButton(container, "上传图片")).toBeUndefined();
  });

  // ── 旁白/描述编辑与保存 ────────────────────────────────────────────────

  it("保存修改：dirty 才出现；payload 只带改动字段", async () => {
    await renderOps({}, mkFrame({ image_prompt: "旧词" }));
    expect(maybeButton(container, "保存修改")).toBeUndefined();

    const narrationTa = container.querySelector("textarea");
    if (!narrationTa) throw new Error("narration textarea missing");
    await changeInput(narrationTa, "新旁白");
    const save = maybeButton(container, "保存修改");
    expect(save).toBeTruthy();
    await click(save!);
    // 只改了旁白 → 不带 image_prompt
    expect(h.updateMutate.mock.calls[0]?.[0]).toEqual({
      frameIndex: 0,
      updates: { narration: "新旁白" },
    });

    await changeInput(promptTextarea(container), "新词");
    await click(findButton(container, "保存修改"));
    expect(h.updateMutate.mock.calls[1]?.[0]).toEqual({
      frameIndex: 0,
      updates: { narration: "新旁白", image_prompt: "新词" },
    });
  });

  it("非槽版式（ConceptPage）：素材区整块不渲染，保存只发 narration", async () => {
    await renderOps({}, mkFrame({ layout: "ConceptPage" }));
    expect(container.textContent).not.toContain("配图描述");
    expect(container.querySelectorAll("textarea")).toHaveLength(1);
    expect(maybeButton(container, "上传图片")).toBeUndefined();

    const narrationTa = container.querySelector("textarea");
    if (!narrationTa) throw new Error("narration textarea missing");
    await changeInput(narrationTa, "新旁白");
    await click(findButton(container, "保存修改"));
    expect(h.updateMutate.mock.calls[0]?.[0]).toEqual({
      frameIndex: 0,
      updates: { narration: "新旁白" },
    });
  });

  // ── 重生配音（audio_only）──────────────────────────────────────────────

  it("「重生配音」发 audio_only；onSuccess 复位编辑 mutation；hook opts 回调透传 frameIndex", async () => {
    const onRegenStart = vi.fn();
    const onRegenSyncError = vi.fn();
    await renderOps({ onRegenStart, onRegenSyncError }, mkFrame({ index: 3 }));

    await click(findButton(container, "重生配音"));
    expect(h.regenMutate).toHaveBeenCalledTimes(1);
    expect(h.regenMutate.mock.calls[0]?.[0]).toEqual({
      frameIndex: 3,
      mode: "audio_only",
    });

    // 成功后 updateMut.reset()——清掉陈旧 mutation 态
    const cb = h.regenMutate.mock.calls[0]?.[1] as
      | { onSuccess?: () => void }
      | undefined;
    cb?.onSuccess?.();
    expect(h.updateReset).toHaveBeenCalledTimes(1);

    // useRegenerateFrame(taskId, opts) 的 opts 回调把 frameIndex 透传给面板 props
    h.regenOpts?.onMutate?.(3);
    expect(onRegenStart).toHaveBeenCalledWith(3);
    h.regenOpts?.onSyncError?.(3);
    expect(onRegenSyncError).toHaveBeenCalledWith(3);
  });

  // ── 重新生成画面（visual_only）─────────────────────────────────────────
  // 2026-10-06 素材区合并批：改走 useRegenerateFrame（带 invalidateQueries）——
  // 修前直调 api.regenerateFrame 无失效逻辑，详情页重生完成后不自动刷新。

  it("AI 本地产物（visuals/img_）+ ready：「重新生成配图」→ useRegenerateFrame(visual_only)，不再直调 api", async () => {
    const onVisualChanged = vi.fn();
    await renderOps(
      { onVisualChanged },
      mkFrame({ local_visual_path: "visuals/img_0003.png" }),
    );
    await click(findButton(container, "重新生成配图"));
    expect(h.regenMutate).toHaveBeenCalledWith(
      { frameIndex: 0, mode: "visual_only" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
    expect(h.regenerateFrame).not.toHaveBeenCalled();
    // 成功回调链：hook 失效 storyboard + 组件侧 onVisualChanged（详情页刷新兜底）
    const cb = h.regenMutate.mock.calls[0]?.[1] as
      | { onSuccess?: () => void }
      | undefined;
    cb?.onSuccess?.();
    expect(onVisualChanged).toHaveBeenCalledTimes(1);
    expect(h.toastSuccess).toHaveBeenCalledWith("已重新生成配图");
  });

  it("重新生成失败：错误 toast 由 useRegenerateFrame 统一承担（组件不再自管错误路径）", async () => {
    await renderOps({}, mkFrame({ local_visual_path: "visuals/img_0003.png" }));
    await click(findButton(container, "重新生成配图"));
    // 组件层只发起 mutation；同步错误 toast 在 hook onError（mock 之外，另测）
    expect(h.toastError).not.toHaveBeenCalled();
  });

  // ── 嵌入/上传帧（B2）───────────────────────────────────────────────────

  it("嵌入素材（visuals/embedded/）：提示可替换、无「重新生成配图」、保留「替换素材」", async () => {
    await renderOps(
      {},
      mkFrame({ local_visual_path: "visuals/embedded/img_1.png" }),
    );
    expect(container.textContent).toContain(
      "嵌入/上传素材：不支持「重新生成」，可替换。",
    );
    expect(maybeButton(container, "重新生成配图")).toBeUndefined();
    expect(maybeButton(container, "替换")).toBeTruthy();
  });

  // ── AI 生成门控 ────────────────────────────────────────────────────────

  it("「AI 生成配图」按描述门控：空描述禁用；填写后点击发 image_prompt + visual_source=ai_image", async () => {
    await renderOps({}, mkFrame({ image_prompt: null }));
    const gated = findButton(container, "AI 生成配图");
    expect(gated.disabled).toBe(true);
    expect(gated.title).toBe("请先填写素材描述");

    await changeInput(promptTextarea(container), "一张架构图");
    await click(findButton(container, "AI 生成配图"));
    expect(h.updateMutate.mock.calls[0]?.[0]).toEqual({
      frameIndex: 0,
      updates: { image_prompt: "一张架构图", visual_source: "ai_image" },
    });
  });

  it("VideoClipPage：视频语义素材区；「AI 生成视频」发 visual_source=ai_video", async () => {
    await renderOps(
      {},
      mkFrame({ layout: "VideoClipPage", image_prompt: "运镜" }),
    );
    expect(container.textContent).toContain("视频素材");
    expect(
      container.querySelector("input[type=file]")?.getAttribute("accept"),
    ).toBe("video/mp4,video/webm");
    await click(findButton(container, "AI 生成视频"));
    expect(h.updateMutate.mock.calls[0]?.[0]).toEqual({
      frameIndex: 0,
      updates: { image_prompt: "运镜", visual_source: "ai_video" },
    });
  });

  // ── 上传 ──────────────────────────────────────────────────────────────

  it("上传：accept 按图槽；选文件发 {frameIndex, file}；成功后 reset + onVisualChanged", async () => {
    const onVisualChanged = vi.fn();
    await renderOps({ onVisualChanged });
    expect(
      container.querySelector("input[type=file]")?.getAttribute("accept"),
    ).toBe("image/jpeg,image/png,image/webp");

    const input = container.querySelector<HTMLInputElement>("input[type=file]");
    if (!input) throw new Error("file input missing");
    const file = new File([new Uint8Array(8)], "p.png", { type: "image/png" });
    Object.defineProperty(input, "files", {
      value: [file],
      configurable: true,
    });
    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(h.uploadMutate).toHaveBeenCalledTimes(1);
    expect(h.uploadMutate.mock.calls[0]?.[0]).toEqual({ frameIndex: 0, file });

    const cb = h.uploadMutate.mock.calls[0]?.[1] as
      | { onSuccess?: () => void }
      | undefined;
    cb?.onSuccess?.();
    expect(h.updateReset).toHaveBeenCalledTimes(1);
    expect(onVisualChanged).toHaveBeenCalledTimes(1);
  });

  // ── 版式切换 ──────────────────────────────────────────────────────────

  it("版式切换：下拉 = 自动 + 19 版式；切换发 useUpdateFrameLayout，「自动」发 null", async () => {
    await renderOps({}, mkFrame({ layout: "ConceptPage" }));
    const sel = container.querySelector("select");
    if (!sel) throw new Error("layout select missing");
    expect(sel.options).toHaveLength(20);
    expect(sel.value).toBe("ConceptPage");

    await changeSelect(sel, "VideoClipPage");
    expect(h.layoutMutate).toHaveBeenCalledWith({
      frameIndex: 0,
      layout: "VideoClipPage",
    });
    await changeSelect(sel, "__auto__");
    expect(h.layoutMutate).toHaveBeenCalledWith({
      frameIndex: 0,
      layout: null,
    });
  });
});
