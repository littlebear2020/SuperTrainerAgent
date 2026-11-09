/**
 * RemotionCourseStoryboard（remotion_course 独立分镜编辑器）行为测试。
 *
 * 覆盖 AGENTS.md 管线契约关心的行为面：
 * - 素材区门控：图槽版式/VideoClipPage 才渲染素材区；嵌入/上传帧（visuals/embedded/、
 *   visuals/frame_）只替换不重生；AI 本地产物（visuals/img_）保留「重新生成」入口（B2）
 * - 版式切换：LayoutSwitcher → useUpdateFrameLayout（「自动」映射 null）
 * - schema 驱动内容表单：dirty 派生（m14）+ 保存 payload（number 化 m13/空值剔除/全空→null）
 * - 旁白 trim 保存门控；试听 audio URL 携带 updated_at 缓存版本（m12）
 * - draft 重排：↑↓ 发 insert-before 语义（下移 index+2）+ 首尾/组边界禁用（M3）
 * - 渲染确认弹层：「将生成」计数与后端四条件逐字对齐（版式+源+prompt+无素材，m8）+
 *   占位帧警告（决策 16）
 * - 重新解析：选档 + 二次确认 → reparseCourseStoryboard + 限定 key 失效（m12）
 *
 * 不覆盖（happy-dom 做不到/不值得）：
 * - dnd-kit 拖拽手势本身——无真实指针与布局测量，DndContext/useSortable 全部 stub，
 *   重排逻辑改经 ↑↓ 按钮路径覆盖；
 * - Radix Select 弹层交互——ui/select stub 为原生 <select>（沿用
 *   style-dropdown-engine-filter.test.tsx 口径），测本组件接线而非 Radix 行为。
 */

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  useVideoTask: vi.fn(),
  updateFrameMutate: vi.fn(),
  updateLayoutMutate: vi.fn(),
  deleteFrameMutate: vi.fn(),
  reorderMutate: vi.fn(),
  addFrameMutate: vi.fn(),
  renderMutate: vi.fn(),
  regenMutate: vi.fn(),
  uploadMutate: vi.fn(),
  invalidateQueries: vi.fn(() => Promise.resolve()),
  reparseCourseStoryboard: vi.fn((..._args: unknown[]) =>
    Promise.resolve({ n_frames: 3 }),
  ),
  routerPush: vi.fn(),
  task: { data: undefined as Record<string, unknown> | undefined },
  storyboard: { data: undefined as Record<string, unknown> | undefined },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: h.routerPush }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: h.invalidateQueries }),
}));

vi.mock("@/core/video-gen/hooks", () => ({
  STORYBOARD_KEY: "videoStoryboard",
  VIDEO_KEY: "videoTasks",
  useVideoTask: (...args: unknown[]) => h.useVideoTask(...args),
  useStoryboardWithAudioPolling: () => ({ data: h.storyboard.data }),
  useUpdateStoryboardFrame: () => ({
    mutate: h.updateFrameMutate,
    isPending: false,
  }),
  useUpdateFrameLayout: () => ({ mutate: h.updateLayoutMutate }),
  useDeleteFrame: () => ({ mutate: h.deleteFrameMutate }),
  useReorderFrames: () => ({ mutate: h.reorderMutate }),
  useAddFrame: () => ({ mutate: h.addFrameMutate }),
  useRenderFromDraft: () => ({ mutate: h.renderMutate, isPending: false }),
  useRegenerateFrame: () => ({ mutate: h.regenMutate, isPending: false }),
  useUploadFrameVisual: () => ({ mutate: h.uploadMutate, isPending: false }),
  // B3（2026-10-07 遗留批）起 FrameAudioButton 消费此 hook——mock 缺它会整体炸（25/25）
  useGenerateAudio: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/core/video-gen/api", () => ({
  reparseCourseStoryboard: (...args: unknown[]) =>
    h.reparseCourseStoryboard(...args),
  // StillPreview 真发 fetch——单测无后端：start 同步返回、轮询恒 null（既不 ready
  // 也不 failed），setState 全在 effect 同步路径，无 act 警告、无轮询噪音。
  stillPreview: vi.fn(() => ({ cache_key: "k" })),
  fetchStillPreview: vi.fn(() => Promise.resolve(null)),
}));

// dnd-kit：拖拽手势在 happy-dom 无真实驱动路径——stub 掉上下文，
// ↑↓ 按钮重排不经过 dnd-kit，照常覆盖。
vi.mock("@dnd-kit/core", () => ({
  DndContext: ({ children }: { children?: ReactNode }) => children ?? null,
  PointerSensor: class {},
  closestCenter: () => null,
  useSensor: () => ({}),
  useSensors: () => [],
}));
vi.mock("@dnd-kit/sortable", () => ({
  SortableContext: ({ children }: { children?: ReactNode }) => children ?? null,
  useSortable: () => ({
    attributes: {},
    listeners: undefined,
    setNodeRef: () => undefined,
    transform: null,
    transition: undefined,
    isDragging: false,
  }),
  verticalListSortingStrategy: () => [],
}));
vi.mock("@dnd-kit/utilities", () => ({
  CSS: { Transform: { toString: () => undefined } },
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

import { RemotionCourseStoryboard } from "@/components/workspace/video-gen/remotion-course-storyboard";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

type Frame = Record<string, unknown>;

function mkFrame(index: number, extra: Frame = {}): Frame {
  return {
    index,
    narration: `第${index + 1}帧旁白`,
    duration: 3,
    layout: "ConceptPage",
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

/** 帧卡定位：内层卡片容器类名 bg-card，顺序与 frames 一致（平铺/分组均然）。 */
function cardOf(container: HTMLElement, oneBased: number): HTMLElement {
  const card = Array.from(container.querySelectorAll<HTMLElement>(".bg-card"))[
    oneBased - 1
  ];
  if (!card) throw new Error(`帧卡 ${oneBased} 未渲染`);
  return card;
}

/** 图标按钮定位（↑↓/删除无文字）：按 lucide svg 类名匹配。 */
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

describe("RemotionCourseStoryboard", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    h.task.data = {
      title: "测试课程",
      status: "draft",
      task_type: "remotion_course",
      updated_at: "v1",
    };
    h.useVideoTask.mockImplementation(() => ({ data: h.task.data }));
    h.storyboard.data = {
      title: "测试课程",
      status: "draft",
      frames: [],
      scene_groups: [],
    };
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

  function setFrames(frames: Frame[], extra: Record<string, unknown> = {}) {
    h.storyboard.data = {
      title: "测试课程",
      status: "draft",
      frames,
      scene_groups: [],
      ...extra,
    };
  }

  function setStatus(status: string) {
    h.task.data = { ...h.task.data, status };
  }

  async function renderStoryboard() {
    await act(async () => {
      root.render(<RemotionCourseStoryboard taskId="t1" />);
    });
  }

  // ── 头部与任务拉取 ────────────────────────────────────────────────────

  it("头部：标题/帧数/状态；拿到 task 后停止轮询（M4：enabled 翻 false）", async () => {
    setFrames([mkFrame(0), mkFrame(1)]);
    await renderStoryboard();
    expect(container.textContent).toContain("测试课程");
    expect(container.textContent).toContain("文字动效 · 2 帧 · 状态 draft");
    // M4：resolvedTask 落定后轮询关闭——修前 draft 主态每 2s 永久轮询
    expect(h.useVideoTask).toHaveBeenLastCalledWith("t1", { enabled: false });
  });

  it("「插入帧」→ useAddFrame.mutate({})", async () => {
    setFrames([mkFrame(0)]);
    await renderStoryboard();
    await click(findButton(container, "插入帧"));
    expect(h.addFrameMutate).toHaveBeenCalledWith({});
  });

  it("头部动作门控：非 draft 禁用「重新解析/开始渲染」", async () => {
    setStatus("ready");
    setFrames([mkFrame(0)]);
    await renderStoryboard();
    expect(findButton(container, "重新解析").disabled).toBe(true);
    expect(findButton(container, "开始渲染").disabled).toBe(true);
  });

  // ── 素材区门控（图槽/VideoClipPage）────────────────────────────────────

  it("素材区门控：图槽版式出配图区、VideoClipPage 出视频区、ConceptPage 无素材区；accept 按槽位分流", async () => {
    setStatus("ready");
    setFrames([
      mkFrame(0, { layout: "ImageTextLeft" }),
      mkFrame(1, { layout: "VideoClipPage" }),
      mkFrame(2, { layout: "ConceptPage" }),
    ]);
    await renderStoryboard();

    const imgCard = cardOf(container, 1);
    const videoCard = cardOf(container, 2);
    const conceptCard = cardOf(container, 3);
    expect(imgCard.textContent).toContain("配图素材");
    expect(videoCard.textContent).toContain("视频素材");
    expect(conceptCard.textContent).not.toContain("素材");
    expect(
      imgCard.querySelector("input[type=file]")?.getAttribute("accept"),
    ).toBe("image/jpeg,image/png,image/webp");
    expect(
      videoCard.querySelector("input[type=file]")?.getAttribute("accept"),
    ).toBe("video/mp4,video/webm");
  });

  it("嵌入/上传帧（visuals/embedded/、visuals/frame_）：只提供替换，不提供重新生成（P6/B2）", async () => {
    setStatus("ready");
    setFrames([
      mkFrame(0, {
        layout: "ImageTextLeft",
        local_visual_path: "visuals/embedded/img_1.png",
      }),
      mkFrame(1, {
        layout: "ImageTextLeft",
        local_visual_path: "visuals/frame_0007.png",
      }),
    ]);
    await renderStoryboard();
    for (const n of [1, 2]) {
      const card = cardOf(container, n);
      expect(card.textContent).toContain(
        "嵌入/上传素材：不支持「重新生成」，可替换。",
      );
      expect(
        Array.from(card.querySelectorAll("button")).some((b) =>
          b.textContent?.includes("重新生成配图"),
        ),
      ).toBe(false);
      findButton(card, "替换");
    }
  });

  it("AI 本地产物（visuals/img_）不是用户素材：ready 态保留「重新生成配图」→ visual_only", async () => {
    setStatus("ready");
    setFrames([
      mkFrame(0, {
        layout: "ImageTextLeft",
        local_visual_path: "visuals/img_0003.png",
        visual_source: "ai_image",
        image_prompt: "示意图",
      }),
    ]);
    await renderStoryboard();
    const card = cardOf(container, 1);
    expect(card.textContent).not.toContain("嵌入/上传素材");
    await click(findButton(card, "重新生成配图"));
    expect(h.regenMutate).toHaveBeenCalledTimes(1);
    expect(h.regenMutate.mock.calls[0]?.[0]).toEqual({
      frameIndex: 0,
      mode: "visual_only",
    });
  });

  it("「重新生成」入口仅 ready/failed：draft 态即使 AI 素材帧也不显示", async () => {
    setFrames([
      mkFrame(0, {
        layout: "ImageTextLeft",
        local_visual_path: "visuals/img_0003.png",
      }),
    ]);
    await renderStoryboard();
    expect(cardOf(container, 1).textContent).not.toContain("重新生成配图");
  });

  it("无素材帧：「生成配图」按 prompt 门控；点击开启 ai_image 源；已开启可取消（清源）", async () => {
    setFrames([
      mkFrame(0, { layout: "ImageTextLeft" }),
      mkFrame(1, { layout: "ImageTextLeft", image_prompt: "架构图" }),
      mkFrame(2, {
        layout: "ImageTextLeft",
        image_prompt: "架构图",
        visual_source: "ai_image",
      }),
    ]);
    await renderStoryboard();

    const gated = findButton(cardOf(container, 1), "生成配图");
    expect(gated.disabled).toBe(true);
    expect(gated.title).toBe("请先填写配图描述");

    await click(findButton(cardOf(container, 2), "生成配图"));
    // 素材区合并批：AI 开启 toast 统一挂 onSuccess（修前 fire-and-forget，
    // 开启失败也误报「已开启」）——调用带第二参 options。
    expect(h.updateFrameMutate).toHaveBeenCalledWith(
      {
        frameIndex: 1,
        updates: { visual_source: "ai_image" },
      },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );

    const enabledCard = cardOf(container, 3);
    expect(enabledCard.textContent).toContain("已开启 AI 生成（渲染时生成）");
    await click(findButton(enabledCard, "取消"));
    expect(h.updateFrameMutate).toHaveBeenCalledWith({
      frameIndex: 2,
      updates: { visual_source: null },
    });
  });

  it("VideoClipPage：「AI 生成视频」开启 ai_video 源", async () => {
    setFrames([mkFrame(0, { layout: "VideoClipPage", image_prompt: "运镜" })]);
    await renderStoryboard();
    await click(findButton(cardOf(container, 1), "AI 生成视频"));
    expect(h.updateFrameMutate).toHaveBeenCalledWith(
      {
        frameIndex: 0,
        updates: { visual_source: "ai_video" },
      },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("上传素材：选文件 → useUploadFrameVisual({frameIndex, file})", async () => {
    setFrames([mkFrame(0, { layout: "ImageTextLeft" })]);
    await renderStoryboard();
    const input = cardOf(container, 1).querySelector<HTMLInputElement>(
      "input[type=file]",
    );
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
  });

  it("配图描述失焦保存（M8：onChange 不逐键发 PUT）", async () => {
    setFrames([mkFrame(0, { layout: "ImageTextLeft", image_prompt: "旧词" })]);
    await renderStoryboard();
    const ta = cardOf(container, 1).querySelector<HTMLTextAreaElement>(
      "textarea[placeholder*='示意图']",
    );
    if (!ta) throw new Error("prompt textarea missing");
    await changeInput(ta, "新词");
    expect(h.updateFrameMutate).not.toHaveBeenCalled();
    await act(async () => {
      ta.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    });
    expect(h.updateFrameMutate).toHaveBeenCalledWith({
      frameIndex: 0,
      updates: { image_prompt: "新词" },
    });
  });

  // ── 版式切换 ──────────────────────────────────────────────────────────

  it("版式切换：下拉 = 自动 + 19 版式；切换发 useUpdateFrameLayout，「自动」发 null", async () => {
    setFrames([mkFrame(0, { layout: "ConceptPage" })]);
    await renderStoryboard();
    const sel = cardOf(container, 1).querySelector("select");
    if (!sel) throw new Error("layout select missing");
    expect(sel.options).toHaveLength(20);
    expect(sel.value).toBe("ConceptPage");

    await changeSelect(sel, "VideoClipPage");
    expect(h.updateLayoutMutate).toHaveBeenCalledWith({
      frameIndex: 0,
      layout: "VideoClipPage",
    });
    await changeSelect(sel, "__auto__");
    expect(h.updateLayoutMutate).toHaveBeenCalledWith({
      frameIndex: 0,
      layout: null,
    });
  });

  // ── 结构化内容表单（schema 驱动）───────────────────────────────────────

  it("schema 字段渲染 + label 关联（DSN-007）；编辑 → dirty → 保存 payload 剔除空值", async () => {
    setFrames([
      mkFrame(0, {
        layout: "ConceptPage",
        layout_content: { title: "旧标题", bullets: ["要点一", "要点二"] },
      }),
    ]);
    await renderStoryboard();
    const card = cardOf(container, 1);
    expect(
      card.querySelector('label[for="frame-content-title"]')?.textContent,
    ).toBe("标题");
    const title = card.querySelector<HTMLInputElement>("#frame-content-title");
    if (!title) throw new Error("title input missing");
    // m14：dirty 派生——未改时禁存
    expect(findButton(card, "保存画面内容").disabled).toBe(true);

    await changeInput(title, "新标题");
    expect(findButton(card, "保存画面内容").disabled).toBe(false);
    await click(findButton(card, "保存画面内容"));
    expect(h.updateFrameMutate).toHaveBeenCalledWith(
      {
        frameIndex: 0,
        // kicker/lead 未填 → 从 payload 剔除；已填字段整包写回
        updates: {
          layout_content: { title: "新标题", bullets: ["要点一", "要点二"] },
        },
      },
      expect.anything(),
    );
  });

  it("number 档：保存统一 Number 化；清空即「未设置」（m13，不落 NaN）", async () => {
    setFrames([
      mkFrame(0, {
        layout: "OutlinePage",
        layout_content: { title: "大纲", currentChapter: 0 },
      }),
    ]);
    await renderStoryboard();
    const card = cardOf(container, 1);
    const num = card.querySelector<HTMLInputElement>(
      "#frame-content-currentChapter",
    );
    if (!num) throw new Error("number input missing");
    expect(num.type).toBe("number");

    await changeInput(num, "2");
    await click(findButton(card, "保存画面内容"));
    const first = h.updateFrameMutate.mock.calls[0]?.[0] as {
      updates: { layout_content: Record<string, unknown> };
    };
    expect(first.updates.layout_content.currentChapter).toBe(2);

    await changeInput(num, "");
    await click(findButton(card, "保存画面内容"));
    const second = h.updateFrameMutate.mock.calls[1]?.[0] as {
      updates: { layout_content: Record<string, unknown> };
    };
    expect(second.updates.layout_content).toEqual({ title: "大纲" });
  });

  it("全空保存 → layout_content=null（回确定性推导）", async () => {
    setFrames([
      mkFrame(0, { layout: "ConceptPage", layout_content: { title: "x" } }),
    ]);
    await renderStoryboard();
    const card = cardOf(container, 1);
    const title = card.querySelector<HTMLInputElement>("#frame-content-title");
    if (!title) throw new Error("title input missing");
    await changeInput(title, "");
    await click(findButton(card, "保存画面内容"));
    expect(h.updateFrameMutate).toHaveBeenCalledWith(
      { frameIndex: 0, updates: { layout_content: null } },
      expect.anything(),
    );
  });

  it("list 档：每行一条（trim + 滤空行）", async () => {
    setFrames([
      mkFrame(0, {
        layout: "ConceptPage",
        layout_content: { title: "t", bullets: ["甲"] },
      }),
    ]);
    await renderStoryboard();
    const card = cardOf(container, 1);
    const ta = card.querySelector<HTMLTextAreaElement>(
      'textarea[placeholder="每行一个条目"]',
    );
    if (!ta) throw new Error("list textarea missing");
    expect(ta.value).toBe("甲");
    await changeInput(ta, "一\n\n 二 \n三");
    await click(findButton(card, "保存画面内容"));
    const payload = h.updateFrameMutate.mock.calls[0]?.[0] as {
      updates: { layout_content: Record<string, unknown> };
    };
    expect(payload.updates.layout_content.bullets).toEqual(["一", "二", "三"]);
  });

  it("复合行编辑器（options）：删除唯一行后保存，该字段从 payload 剔除", async () => {
    setFrames([
      mkFrame(0, {
        layout: "QuizPage",
        layout_content: {
          question: "Q？",
          options: [{ key: "A", text: "甲" }],
        },
      }),
    ]);
    await renderStoryboard();
    const card = cardOf(container, 1);
    expect(card.querySelector('input[placeholder="选项键"]')).toBeTruthy();
    const delRow = card.querySelector<HTMLButtonElement>(
      'button[aria-label="删除第1行"]',
    );
    if (!delRow) throw new Error("row delete button missing");
    await click(delRow);
    await click(findButton(card, "保存画面内容"));
    expect(h.updateFrameMutate.mock.calls[0]?.[0]).toEqual({
      frameIndex: 0,
      updates: { layout_content: { question: "Q？" } },
    });
  });

  // ── 旁白与试听 ────────────────────────────────────────────────────────

  it("旁白：未改/全空白禁存；保存发 trim 后的 narration", async () => {
    // CoverPage 无 textarea 型内容字段——卡内唯一 textarea 即旁白
    setFrames([mkFrame(0, { layout: "CoverPage", narration: "原始旁白" })]);
    await renderStoryboard();
    const card = cardOf(container, 1);
    const ta = card.querySelector("textarea");
    if (!ta) throw new Error("narration textarea missing");
    expect(findButton(card, "保存旁白").disabled).toBe(true);

    await changeInput(ta, "  新旁白  ");
    expect(findButton(card, "保存旁白").disabled).toBe(false);
    await click(findButton(card, "保存旁白"));
    expect(h.updateFrameMutate).toHaveBeenCalledWith(
      { frameIndex: 0, updates: { narration: "新旁白" } },
      expect.anything(),
    );

    await changeInput(ta, "   ");
    expect(findButton(card, "保存旁白").disabled).toBe(true);
  });

  it("试听按钮：audio URL 携带任务 updated_at 缓存版本（m12 防旧缓冲）", async () => {
    setFrames([mkFrame(0)]);
    await renderStoryboard();
    const audio = cardOf(container, 1).querySelector("audio");
    expect(audio?.getAttribute("src")).toBe(
      "/api/video/tasks/t1/audio?frame=0&v=v1",
    );
  });

  // ── draft 重排（D2/M3）────────────────────────────────────────────────

  it("draft 平铺：↑↓ 发 insert-before 语义（下移 index+2），首尾边界禁用", async () => {
    setFrames([mkFrame(0), mkFrame(1), mkFrame(2)]);
    await renderStoryboard();
    expect(
      container.querySelectorAll('span[title="拖拽调整帧顺序"]'),
    ).toHaveLength(3);
    expect(container.textContent).toContain("草稿态可拖拽");

    const firstCard = cardOf(container, 1);
    expect(iconButton(firstCard, "lucide-chevron-up").disabled).toBe(true);
    await click(iconButton(firstCard, "lucide-chevron-down"));
    // insert-before 语义：下移须发 index+2（发 index+1 会插回原位恒 no-op）
    expect(h.reorderMutate).toHaveBeenCalledWith({ fromIndex: 0, toIndex: 2 });

    await click(iconButton(cardOf(container, 2), "lucide-chevron-up"));
    expect(h.reorderMutate).toHaveBeenCalledWith({ fromIndex: 1, toIndex: 0 });

    expect(
      iconButton(cardOf(container, 3), "lucide-chevron-down").disabled,
    ).toBe(true);
  });

  it("ready 态：无拖拽手柄、无 ↑↓；删除帧仍可用", async () => {
    setStatus("ready");
    setFrames([mkFrame(0), mkFrame(1)]);
    await renderStoryboard();
    expect(
      container.querySelectorAll('span[title="拖拽调整帧顺序"]'),
    ).toHaveLength(0);
    expect(
      Array.from(container.querySelectorAll("button")).some((b) =>
        b.innerHTML.includes("lucide-chevron-up"),
      ),
    ).toBe(false);
    expect(container.textContent).not.toContain("草稿态可拖拽");

    await click(findButton(cardOf(container, 2), "删除帧"));
    expect(h.deleteFrameMutate).toHaveBeenCalledWith(1);
  });

  it("单帧 draft：不可重排（无手柄/↑↓）", async () => {
    setFrames([mkFrame(0)]);
    await renderStoryboard();
    expect(
      container.querySelectorAll('span[title="拖拽调整帧顺序"]'),
    ).toHaveLength(0);
    expect(
      Array.from(container.querySelectorAll("button")).some((b) =>
        b.innerHTML.includes("lucide-chevron-up"),
      ),
    ).toBe(false);
  });

  it("分组视图：组锚点导航 + 组边界禁用跨组 ↓（M3）+ 折叠组不渲染帧卡", async () => {
    setFrames([mkFrame(0), mkFrame(1), mkFrame(2), mkFrame(3)], {
      scene_groups: [
        { index: 0, title: "第一章", frame_indices: [0, 1] },
        { index: 1, title: "第二章", frame_indices: [2, 3] },
      ],
    });
    await renderStoryboard();

    expect(
      container.querySelector('a[href="#course-group-0"]')?.textContent,
    ).toBe("第一章");
    expect(
      container.querySelector('a[href="#course-group-1"]')?.textContent,
    ).toBe("第二章");
    // 默认只展开第一组
    expect(container.textContent).toContain("帧 1");
    expect(container.textContent).toContain("帧 2");
    expect(container.textContent).not.toContain("帧 3");
    // 分组模式不提供拖拽手柄（sortable 仅平铺 draft）
    expect(
      container.querySelectorAll('span[title="拖拽调整帧顺序"]'),
    ).toHaveLength(0);

    // 组边界：帧 2 的下一帧属第二章 → ↓ 禁用且带原因提示
    const boundaryDown = iconButton(
      cardOf(container, 2),
      "lucide-chevron-down",
    );
    expect(boundaryDown.disabled).toBe(true);
    expect(boundaryDown.title).toContain("下一帧属其他章节");
    // 组内可移：帧 1 ↓ 照常发 insert-before
    await click(iconButton(cardOf(container, 1), "lucide-chevron-down"));
    expect(h.reorderMutate).toHaveBeenCalledWith({ fromIndex: 0, toIndex: 2 });

    // 展开第二组 → 帧 3 渲染
    await click(findButton(container, "第二章"));
    expect(container.textContent).toContain("帧 3");
  });

  // ── 渲染确认弹层（决策 16 + m8 计数口径）───────────────────────────────

  it("渲染确认：「将生成」计数与后端四条件对齐；占位帧明示；确认 → renderFromDraft + 跳详情页", async () => {
    setFrames([
      // 计入配图：图槽 + ai_image + prompt + 无素材
      mkFrame(0, {
        layout: "ImageTextLeft",
        visual_source: "ai_image",
        image_prompt: "图",
      }),
      // 不计入：已有 AI 本地产物
      mkFrame(1, {
        layout: "ImageTextRight",
        visual_source: "ai_image",
        image_prompt: "图",
        local_visual_path: "visuals/img_0001.png",
      }),
      // 配图占位：有 prompt 但未开源 → 渲染不会生成（第 3 帧）
      mkFrame(2, { layout: "QuotePage", image_prompt: "图" }),
      // 计入视频：VideoClipPage + ai_video + prompt + 无素材
      mkFrame(3, {
        layout: "VideoClipPage",
        visual_source: "ai_video",
        image_prompt: "片",
      }),
      // 视频占位：残留 ai_image 源不匹配视频槽（第 5 帧）
      mkFrame(4, {
        layout: "VideoClipPage",
        visual_source: "ai_image",
        image_prompt: "片",
      }),
      // 非槽版式：不进任何统计
      mkFrame(5, { layout: "ConceptPage" }),
    ]);
    await renderStoryboard();

    await click(findButton(container, "开始渲染"));
    expect(h.renderMutate).not.toHaveBeenCalled(); // 确认前零副作用
    const body = document.body.textContent ?? "";
    expect(body).toContain("槽位配图生成（1 张）");
    expect(body).toContain("AI 视频生成（1 段，约 2 分钟）");
    expect(body).toMatch(/1 帧无配图（第\s*3\s*帧）/);
    expect(body).toMatch(/1 帧无视频（第\s*5\s*帧）/);

    h.renderMutate.mockImplementationOnce(
      (_v: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.(),
    );
    await click(findButton(document.body, "仍要渲染"));
    expect(h.renderMutate).toHaveBeenCalledTimes(1);
    expect(h.routerPush).toHaveBeenCalledWith("/workspace/video-gen/t1");
  });

  // ── 重新解析（决策 15 + m12 定向失效）──────────────────────────────────

  it("重新解析：选档 + 二次确认 → reparseCourseStoryboard(rewrite) + 限定 key 失效", async () => {
    setFrames([mkFrame(0)]);
    await renderStoryboard();

    await click(findButton(container, "重新解析"));
    expect(document.body.textContent).toContain("重新解析课程脚本");
    expect(h.reparseCourseStoryboard).not.toHaveBeenCalled();

    const radios = Array.from(
      document.querySelectorAll<HTMLInputElement>('input[name="reparse-mode"]'),
    );
    expect(radios).toHaveLength(2);
    await click(radios[1]!); // AI 改写
    await click(findButton(document.body, "下一步"));
    expect(document.body.textContent).toContain("将以「改写」档重新解析");

    await click(findButton(document.body, "确认重新解析"));
    expect(h.reparseCourseStoryboard).toHaveBeenCalledWith("t1", "rewrite");
    // m12：限定 query key——无参 invalidateQueries 全量失效的回归护栏
    expect(h.invalidateQueries).toHaveBeenCalledTimes(2);
    expect(h.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["videoStoryboard", "t1"],
    });
    expect(h.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["videoTasks", "t1"],
    });
  });
});
