/**
 * 2026-09-28 edits 修复批（计划 §四 F1/F3/F4）：
 * - T9  voice/speed 变化保存后三选弹层（含帧数成本文案）；仅样式/BGM → 不弹；
 *       draft → 不弹（走 B5）。
 * - T11 config 外部变化且非 dirty → 表单同步（bgm 0-1↔0-100 换算）；dirty → 不覆盖。
 * - T14 voice_id 三分态提交：未动不发 / 选默认发 null / 选定发值。
 *
 * 仓库无 @testing-library/react：沿用 video-gen-detail-cost.test.tsx 的
 * createRoot + act + happy-dom 手写 harness。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  configMut: {
    mutate: vi.fn(),
    isPending: false,
  },
  regenAllMut: { mutate: vi.fn(), isPending: false },
  recomposeMut: { mutate: vi.fn(), isPending: false },
  rerenderMut: { mutate: vi.fn(), isPending: false },
  voices: [
    { id: "preset:146", name: "女声一", gender: "女" },
    { id: "preset:200", name: "男声一", gender: "男" },
  ],
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

vi.mock("@/core/video-gen/hooks", () => ({
  useVideoVoices: () => ({ data: h.voices }),
  useUpdateStoryboardConfig: () => h.configMut,
  useRegenerateAllAudio: () => h.regenAllMut,
  useRecomposeTask: () => h.recomposeMut,
  useReRenderTask: () => h.rerenderMut,
  getStallWarning: () => null,
  STORYBOARD_KEY: "video-storyboard",
  VIDEO_KEY: "video-task",
}));

// review 收尾（2026-09-28）：mock 返回最小 storyboard——返回 undefined 会触发
// TanStack「Query data cannot be undefined」控制台告警（watch 轮询期间），干扰排查。
vi.mock("@/core/video-gen/api", () => ({
  getStoryboard: vi.fn().mockResolvedValue({ config: {}, frames: [] }),
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ taskId: "vg_1" }),
  useRouter: () => ({ back: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

import { AudioSettingsSection } from "@/components/workspace/video-gen/video-gen-detail";
import type { StoryboardData } from "@/core/video-gen/types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function makeStoryboard(cfg: Record<string, unknown> = {}): StoryboardData {
  return {
    title: "t",
    config: {
      voice_id: "preset:146",
      tts_speed: 1.0,
      bgm_volume: 0.15,
      subtitle_style: "default",
      ...cfg,
    },
    frames: [
      { index: 0, narration: "a" },
      { index: 1, narration: "b" },
    ],
    total_duration: 4,
  } as unknown as StoryboardData;
}

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function render(el: ReactElement) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  act(() => {
    root!.render(<QueryClientProvider client={qc}>{el}</QueryClientProvider>);
  });
}

function q(sel: string): HTMLElement {
  const el = container!.querySelector(sel);
  if (!el) throw new Error(`not found: ${sel}`);
  return el as HTMLElement;
}

async function expand() {
  await act(async () => {
    q('[title="展开音频设置"]').click();
  });
}

function setSelect(sel: string, value: string) {
  act(() => {
    const el = q(sel) as unknown as HTMLSelectElement;
    el.value = value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function clickSave() {
  act(() => {
    const btns = Array.from(container!.querySelectorAll("button"));
    const save = btns.find((b) => b.textContent?.includes("保存音频设置"));
    if (!save) throw new Error("save button not found");
    save.click();
  });
}

function saveAndResolve() {
  clickSave();
  const call = h.configMut.mutate.mock.calls.at(-1);
  expect(call).toBeTruthy();
  act(() => {
    call![1]?.onSuccess?.();
  });
  return call![0] as Record<string, unknown>;
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
});

describe("T14 voice_id 三分态提交（F4）", () => {
  it("未动 = 键不发（保存按钮在无 dirty 时禁用）", async () => {
    render(
      <AudioSettingsSection
        taskId="vg_1"
        storyboard={makeStoryboard()}
        taskStatus="ready"
      />,
    );
    await expand();
    const btns = Array.from(container!.querySelectorAll("button"));
    const save = btns.find((b) => b.textContent?.includes("保存音频设置"));
    expect(save?.hasAttribute("disabled")).toBe(true);
  });

  it("选「默认/跟随项目」= 显式 null", async () => {
    render(
      <AudioSettingsSection
        taskId="vg_1"
        storyboard={makeStoryboard()}
        taskStatus="ready"
      />,
    );
    await expand();
    setSelect('[data-testid="voice-select"]', "");
    const patch = saveAndResolve();
    expect(patch).toEqual({ voice_id: null });
  });

  it("选定音色 = 值", async () => {
    render(
      <AudioSettingsSection
        taskId="vg_1"
        storyboard={makeStoryboard()}
        taskStatus="ready"
      />,
    );
    await expand();
    setSelect('[data-testid="voice-select"]', "preset:200");
    const patch = saveAndResolve();
    expect(patch).toEqual({ voice_id: "preset:200" });
  });
});

describe("T9 保存后三选弹层（F1）", () => {
  it("voice 变化 + ready → 弹层出现（含帧数与成本文案）；不 toast 保存成功", async () => {
    render(
      <AudioSettingsSection
        taskId="vg_1"
        storyboard={makeStoryboard()}
        taskStatus="ready"
      />,
    );
    await expand();
    setSelect('[data-testid="voice-select"]', "preset:200");
    saveAndResolve();
    // AlertDialog(Radix) 弹层 portal 到 document.body——断言走 body
    expect(document.body.textContent).toContain("将为 2 帧重新配音");
    expect(document.body.textContent).toContain("付费 TTS");
    expect(document.body.textContent).toContain("手动修改的字幕文本将被覆盖");
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("仅样式变化 → 不弹层，toast 引导「重新合成」", async () => {
    render(
      <AudioSettingsSection
        taskId="vg_1"
        storyboard={makeStoryboard()}
        taskStatus="ready"
      />,
    );
    await expand();
    setSelect('[data-testid="subtitle-style-select"]', "outline");
    saveAndResolve();
    expect(document.body.textContent).not.toContain("将为 2 帧重新配音");
    expect(toast.success).toHaveBeenCalled();
  });

  it("draft 态 voice 变化 → 不弹层（B5 链路文案）", async () => {
    render(
      <AudioSettingsSection
        taskId="vg_1"
        storyboard={makeStoryboard()}
        taskStatus="draft"
      />,
    );
    await expand();
    setSelect('[data-testid="voice-select"]', "preset:200");
    saveAndResolve();
    expect(document.body.textContent).not.toContain("将为 2 帧重新配音");
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining("准备音频"),
    );
  });

  it("弹层主按钮 → 批量重生发起，进度行出现", async () => {
    render(
      <AudioSettingsSection
        taskId="vg_1"
        storyboard={makeStoryboard()}
        taskStatus="ready"
      />,
    );
    await expand();
    setSelect('[data-testid="voice-select"]', "preset:200");
    saveAndResolve();
    const btn = document.body.querySelector(
      '[data-testid="regen-all-audio-btn"]',
    );
    if (!btn) throw new Error("regen-all-audio-btn not in body portal");
    act(() => {
      (btn as HTMLElement).click();
    });
    expect(h.regenAllMut.mutate).toHaveBeenCalledTimes(1);
    act(() => {
      h.regenAllMut.mutate.mock.calls.at(-1)![1]?.onSuccess?.({
        status: "started",
        frames: [0, 1],
      });
    });
    expect(q('[data-testid="audio-regen-progress"]').textContent).toContain(
      "2 帧",
    );
  });
});

describe("T11 config 外部同步（F3）", () => {
  it("非 dirty → 同步（bgm_volume 0-1 float ↔ 0-100 int 换算）", async () => {
    const { rerenderWith } = (() => {
      let client: QueryClient | null = null;
      return {
        rerenderWith: (sb: StoryboardData, status: string) => {
          if (!root) throw new Error("no root");
          act(() => {
            root!.render(
              <QueryClientProvider
                client={client ?? (client = new QueryClient())}
              >
                <AudioSettingsSection
                  taskId="vg_1"
                  storyboard={sb}
                  taskStatus={status}
                />
              </QueryClientProvider>,
            );
          });
        },
      };
    })();

    render(
      <AudioSettingsSection
        taskId="vg_1"
        storyboard={makeStoryboard()}
        taskStatus="ready"
      />,
    );
    await expand();
    // 服务端外部变化：bgm 0.45 → 滑杆 45；音色 preset:200
    rerenderWith(
      makeStoryboard({ bgm_volume: 0.45, voice_id: "preset:200" }),
      "ready",
    );
    const sliders = Array.from(
      container!.querySelectorAll('input[type="range"]'),
    ) as unknown as HTMLInputElement[];
    expect(sliders[1]!.value).toBe("45");
    expect(
      (q('[data-testid="voice-select"]') as unknown as HTMLSelectElement).value,
    ).toBe("preset:200");
  });

  it("dirty → 不覆盖用户输入（经 select 制造 dirty——range 合成事件在 happy-dom 不可靠）", async () => {
    let rootRef: Root | null = null;
    let cont = null as HTMLDivElement | null;
    let qc: QueryClient | null = null;
    const renderIt = (sb: StoryboardData) => {
      if (!rootRef) {
        cont = document.createElement("div");
        document.body.appendChild(cont);
        rootRef = createRoot(cont);
        qc = new QueryClient();
      }
      act(() => {
        rootRef!.render(
          <QueryClientProvider client={qc!}>
            <AudioSettingsSection
              taskId="vg_1"
              storyboard={sb}
              taskStatus="ready"
            />
          </QueryClientProvider>,
        );
      });
    };
    try {
      renderIt(makeStoryboard());
      const toggle =
        cont!.querySelector<HTMLElement>('[title="展开音频设置"]')!;
      await act(async () => {
        toggle.click();
      });
      // 用户改音色（dirty）
      act(() => {
        const sel = cont!.querySelector(
          '[data-testid="voice-select"]',
        ) as unknown as HTMLSelectElement;
        sel.value = "preset:200";
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      });
      // 服务端外部变化（音色回退 + bgm 0.45）——dirty 期间不得覆盖
      renderIt(makeStoryboard({ voice_id: "preset:146", bgm_volume: 0.45 }));
      const sel = cont!.querySelector(
        '[data-testid="voice-select"]',
      ) as unknown as HTMLSelectElement;
      expect(sel.value).toBe("preset:200");
      const sliders = Array.from(
        cont!.querySelectorAll('input[type="range"]'),
      ) as unknown as HTMLInputElement[];
      expect(sliders[1]!.value).toBe("15");
    } finally {
      act(() => rootRef?.unmount());
      cont?.remove();
    }
  });
});
