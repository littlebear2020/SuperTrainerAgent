/**
 * remotion_course 独立管线前端契约测试（2026-09-10 计划 T10/T12/T13）：
 * - 路由分流：按 task_type 选组件（含 loading 骨架）
 * - 创建页：话题模式移除（supportsGenerate=false）/解析方式与暂存 id 进 payload/
 *   extract with_images 请求形状
 * - FrameOpsPanel：remotion_course 分流到专属形态；ai_image 形态不变（回归）
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildCreatePayload } from "./create-payload";
import { FrameOpsPanel } from "./video-gen-frame-ops-panel";
import { TYPE_CONFIG } from "./video-gen-page";

// ── 路由分流（T10）────────────────────────────────────────────────────────

vi.mock("@/core/video-gen/hooks", () => ({
  useVideoTask: vi.fn(),
  useStoryboardWithAudioPolling: vi.fn(() => ({ data: undefined })),
  useUpdateStoryboardFrame: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
    reset: vi.fn(),
    data: undefined,
  })),
  useUpdateFrameLayout: vi.fn(() => ({ mutate: vi.fn() })),
  useUploadFrameVisual: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useRegenerateFrame: vi.fn(() => ({ mutate: vi.fn() })),
}));
vi.mock("@/core/video-gen/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/core/video-gen/api")>();
  return {
    ...actual,
    regenerateFrame: vi.fn(),
    getFrameVisualUrl: vi.fn(() => "/api/video/x/visual"),
    // 脏测试修（2026-09-11 审查）：StillPreview 真发 fetch（403 噪音靠 catch-all 兜底）
    fetchStillPreview: vi.fn(async () => null),
    stillPreview: vi.fn(() => "/api/video/x/still"),
  };
});
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const { useVideoTask } = await import("@/core/video-gen/hooks");
const { StoryboardRouteSplit } = await import("./storyboard-route-split");

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("StoryboardRouteSplit（T10 路由分流）", () => {
  let container: HTMLDivElement;
  let root: Root;

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
    vi.clearAllMocks();
  });

  it("loading 态渲染骨架屏（不闪现旧编辑器）", async () => {
    vi.mocked(useVideoTask).mockReturnValue({
      data: undefined,
      isLoading: true,
    } as never);
    await act(async () => {
      root.render(
        <StoryboardRouteSplit
          taskId="t1"
          renderCourse={() => <div data-testid="course" />}
          renderDefault={() => <div data-testid="default" />}
        />,
      );
    });
    expect(container.querySelector("[data-testid='course']")).toBeNull();
    expect(container.querySelector("[data-testid='default']")).toBeNull();
    // M6（2026-09-11 审查）：未拿到 task_type 前轮询开（默认 enabled）
    expect(useVideoTask).toHaveBeenLastCalledWith("t1", { enabled: true });
  });

  it("m11：task 拉取失败 → 错误提示 + 立即重试（不再永久骨架屏）", async () => {
    vi.mocked(useVideoTask).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(async () => ({ data: undefined }) as never),
    } as never);
    await act(async () => {
      root.render(
        <StoryboardRouteSplit
          taskId="t-err"
          renderCourse={() => <div data-testid="course" />}
          renderDefault={() => <div data-testid="default" />}
        />,
      );
    });
    expect(container.textContent).toContain("无法加载任务信息");
    expect(container.textContent).toContain("立即重试");
    expect(container.querySelector("[data-testid='course']")).toBeNull();
  });

  it("remotion_course → 课程编辑器；ai_image → 既有编辑器", async () => {
    vi.mocked(useVideoTask).mockReturnValue({
      data: { task_type: "remotion_course" },
      isLoading: false,
    } as never);
    await act(async () => {
      root.render(
        <StoryboardRouteSplit
          taskId="t1"
          renderCourse={() => <div data-testid="course" />}
          renderDefault={() => <div data-testid="default" />}
        />,
      );
    });
    expect(container.querySelector("[data-testid='course']")).not.toBeNull();

    vi.mocked(useVideoTask).mockReturnValue({
      data: { task_type: "ai_image" },
      isLoading: false,
    } as never);
    await act(async () => {
      root.render(
        <StoryboardRouteSplit
          key="t2" // resolvedType state 随实例走——换类型须重挂载（M6 有状态化后）
          taskId="t2"
          renderCourse={() => <div data-testid="course" />}
          renderDefault={() => <div data-testid="default" />}
        />,
      );
    });
    expect(container.querySelector("[data-testid='default']")).not.toBeNull();
    expect(container.querySelector("[data-testid='course']")).toBeNull();
    // M6（2026-09-11 审查）：task_type 拿到即停轮询（draft 态 2s 永久轮询坑）
    expect(useVideoTask).toHaveBeenLastCalledWith("t2", { enabled: false });
  });
});

// ── 创建页契约（T12）──────────────────────────────────────────────────────

describe("创建页 remotion_course 契约（T12）", () => {
  afterEach(() => {
    // stubGlobal 清理兜底：移出测试体，中途断言失败也不泄漏全局 stub
    vi.unstubAllGlobals();
  });

  const courseCfg = TYPE_CONFIG.remotion_course!;

  it("话题模式移除：supportsGenerate=false（决策 12）", () => {
    expect(courseCfg.supportsGenerate).toBe(false);
    // 既有类型不受影响（回归）
    expect(TYPE_CONFIG.ai_image?.supportsGenerate).toBe(true);
    expect(TYPE_CONFIG.ai_video?.supportsGenerate).toBe(true);
  });

  it("副标题与脚本 placeholder 更新（四轮修订）", () => {
    expect(courseCfg.desc).toContain("把课程脚本变成有版式、有配音的课程视频");
    expect(courseCfg.placeholder).toContain("按章节与知识点组织成版式分镜");
  });

  it("payload 携带 course_parse_mode（rewrite）/script_extract_id；faithful 不下发", () => {
    const base = {
      taskType: "remotion_course" as const,
      mode: "fixed" as const,
      script: "s",
      topic: "",
      lang: "zh",
      aspect: "16:9" as const,
      style: "default",
      subtitleStyle: "default" as const,
      bgm: null,
      voiceId: null,
      outputFormat: "mp4" as const,
      quality: "standard" as const,
      ttsSpeed: null,
      bgmVolume: 0.15,
      bgmDucking: true,
      engine: "auto",
    };
    const rewrite = buildCreatePayload({
      ...base,
      courseParseMode: "rewrite",
      scriptExtractId: "EXTRACT01ULID",
    });
    expect(rewrite.course_parse_mode).toBe("rewrite");
    expect(rewrite.script_extract_id).toBe("EXTRACT01ULID");

    const faithful = buildCreatePayload({ ...base });
    expect(faithful.course_parse_mode).toBeUndefined();
    expect(faithful.script_extract_id).toBeUndefined();
  });

  it("aspect 恒 16:9 下发（创建页隐藏比例但 state 默认 9:16——Minor 2026-09-11）", () => {
    const payload = buildCreatePayload({
      taskType: "remotion_course",
      mode: "fixed",
      script: "s",
      topic: "",
      lang: "zh",
      aspect: "9:16", // state 默认值原样传入
      style: "default",
      subtitleStyle: "default",
      bgm: null,
      voiceId: null,
      outputFormat: "mp4",
      quality: "standard",
      ttsSpeed: null,
      bgmVolume: 0.15,
      bgmDucking: true,
    });
    expect(payload.aspect).toBe("16:9");
  });

  it("extractScript with_images=true → ?with_images=1 请求", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        text: "x",
        images: [],
        ignored_images: 0,
        extract_id: "E",
        expires_at: "t",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const { extractScript } = await import("@/core/video-gen/api");
    await extractScript(new File([], "a.docx"), true);
    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("/script/extract?with_images=1");
    await extractScript(new File([], "a.docx"));
    const url2 = fetchMock.mock.calls[1]?.[0] as string;
    expect(url2).not.toContain("with_images");
  });
});

// ── FrameOpsPanel 分流（T13）──────────────────────────────────────────────

describe("FrameOpsPanel remotion_course 形态（T13，决策 14）", () => {
  let container: HTMLDivElement;
  let root: Root;

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
  });

  const frame = {
    index: 0,
    narration: "旁白",
    layout: "ConceptPage",
  };

  it("remotion_course → 渲染版式切换 + 静帧预览（专属形态）", async () => {
    await act(async () => {
      root.render(
        <FrameOpsPanel
          taskId="t1"
          frame={frame}
          taskStatus="ready"
          onClose={() => undefined}
          videoType="remotion_course"
        />,
      );
    });
    expect(container.textContent).toContain("画面版式");
  });

  it("不传 videoType（ai_image 任务）→ AI 管线形态不变（回归）", async () => {
    await act(async () => {
      root.render(
        <FrameOpsPanel
          taskId="t1"
          frame={{ ...frame, layout: undefined }}
          taskStatus="ready"
          onClose={() => undefined}
        />,
      );
    });
    expect(container.textContent).toContain("重生画面");
    expect(container.textContent).toContain("视觉来源");
  });

  it("B2：AI 本地化帧（visuals/img_）显示「重新生成配图」", async () => {
    // AI 生图已本地化（首轮渲染后的常态）——修前 !!local_visual_path 误判成嵌入/上传
    await act(async () => {
      root.render(
        <FrameOpsPanel
          taskId="t1"
          frame={{
            ...frame,
            layout: "ImageTextLeft",
            visual_source: "ai_image",
            local_visual_path: "visuals/img_0003.png",
          }}
          taskStatus="ready"
          onClose={() => undefined}
          videoType="remotion_course"
        />,
      );
    });
    expect(container.textContent).toContain("重新生成配图");
    expect(container.textContent).not.toContain("不支持重新生成");
  });

  it("B2：嵌入帧（visuals/embedded/）只提示可替换、无重生入口", async () => {
    await act(async () => {
      root.render(
        <FrameOpsPanel
          taskId="t2"
          frame={{
            ...frame,
            layout: "ImageTextLeft",
            local_visual_path: "visuals/embedded/img_1.png",
          }}
          taskStatus="ready"
          onClose={() => undefined}
          videoType="remotion_course"
        />,
      );
    });
    expect(container.textContent).toContain(
      "嵌入/上传素材：不支持「重新生成」，可替换",
    );
    expect(container.textContent).not.toContain("重新生成配图");
  });

  it("B2：顺延标注（§3.6）image_note 展示", async () => {
    await act(async () => {
      root.render(
        <FrameOpsPanel
          taskId="t3"
          frame={{
            ...frame,
            layout: "ImageTextLeft",
            local_visual_path: "visuals/embedded/img_2.png",
            image_note: "来自邻近单元的文档配图",
          }}
          taskStatus="ready"
          onClose={() => undefined}
          videoType="remotion_course"
        />,
      );
    });
    expect(container.textContent).toContain("来自邻近单元的文档配图");
  });
});
