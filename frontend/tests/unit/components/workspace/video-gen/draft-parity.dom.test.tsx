/**
 * Task 19（前端 D2）：「保存草稿」必须与「生成视频」走同一套校验和同一份 payload。
 *
 * 修复前 `video-gen-page.tsx`：
 *   1. 2 万字上限守卫只写在 `handleSubmit` 里；草稿按钮 onClick 直接
 *      `submitMut.mutate(buildCreatePayload(...))` 绕过 → 超长脚本草稿必吃后端
 *      422（schemas.py script max_length=20000），用户还得等一个往返才知道。
 *   2. 草稿那份手抄的 payload **漏了 `secondsPerFrame`** → 同样的表单，存草稿和
 *      直接生成算出的帧时长不一样。
 *
 * 本测试锁两条不变量：超长脚本草稿也拦（不发请求）；正常脚本草稿的 payload
 * 必须带上 `secondsPerFrame`。
 *
 * 仓库无 @testing-library/react：沿用同目录 storyboard-no-auto-preview.test.tsx
 * 的 createRoot + act + happy-dom 手写 harness。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  submitMutate: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  useRouter: () => ({ back: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { error: h.toastError, success: vi.fn(), info: vi.fn() },
}));

const stubMut = () => ({ mutate: vi.fn(), isPending: false, reset: vi.fn() });

vi.mock("@/core/video-gen/hooks", () => ({
  useSubmitVideo: () => ({
    mutate: h.submitMutate,
    isPending: false,
    reset: vi.fn(),
  }),
  useBGMUpload: () => ({
    mutate: vi.fn(),
    isPending: false,
    isSuccess: false,
    data: undefined,
    reset: vi.fn(),
  }),
  // 真实 shape 是 {built_in, uploaded}，不是数组——给 [] 会让组件在
  // bgmQuery.data?.built_in.length 处抛 TypeError。
  // Task 6：生成模型下拉。空列表 → 不渲染，不影响 payload 断言。
  useProviders: () => ({ data: { image: [], video: [] } }),
  // 条目 39（2026-09-12 缺陷批）：video-gen-page 新增 useCourseProjects() 消费
  // （取消「跟随」取项目语速），mock 需补该导出。
  useCourseProjects: () => ({ data: [], isLoading: false }),
  useBGMList: () => ({
    data: { built_in: [], custom: [] },
    isLoading: false,
  }),
  useVideoVoices: () => ({ data: [], isLoading: false }),
  useDefaultVoice: () => ({ data: undefined }),
  useExtractScript: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteVideoTask: () => ({ mutate: vi.fn(), isPending: false }),
  useRecomposeTask: () => ({ mutate: vi.fn(), isPending: false }),
  useVideoTasks: () => ({ data: [], isLoading: false }),
  getStallWarning: () => null,
}));

vi.mock("@/components/workspace/video-gen/course-project-select", () => ({
  CourseProjectSelect: () => null,
}));
vi.mock("@/components/workspace/video-gen/course-project-page", () => ({
  CourseProjectPage: () => null,
}));

/**
 * 驱动受控输入：必须走原型上的 value setter。
 *
 * 直接 `el.value = x` 会被 React 的 value tracker 判定为「没变化」而**不触发
 * onChange** —— state 不动，只有 DOM 显示变了，断言就变成空的。
 */
function setNativeValue(
  el: HTMLInputElement | HTMLTextAreaElement,
  value: string,
) {
  const proto =
    el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const desc = Object.getOwnPropertyDescriptor(proto, "value");
  desc?.set?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("Task 19: 草稿路径与正式提交对齐（前端 D2）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.submitMutate.mockClear();
    h.toastError.mockClear();
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

  async function renderPage() {
    const { CreateTaskDialog: Page } =
      await import("@/components/workspace/video-gen/video-gen-page");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <Page open={true} onOpenChange={vi.fn()} />
        </QueryClientProvider>,
      );
    });
  }

  /** 受控 textarea 必须走原型 setter，否则 React 判定「没变」不触发 onChange。 */
  function typeInto(el: HTMLTextAreaElement, value: string) {
    setNativeValue(el, value);
  }

  function findButton(text: string): HTMLButtonElement {
    const btn = Array.from(document.querySelectorAll("button")).find((b) =>
      (b.textContent ?? "").includes(text),
    );
    if (!btn) throw new Error(`找不到按钮：${text}`);
    return btn;
  }

  /** mode 默认就是 fixed（固定脚本），直接填 textarea 即可。 */
  async function fillScript(text: string) {
    const ta = document.querySelector("textarea");
    if (!ta) throw new Error("找不到脚本 textarea");
    await act(async () => {
      typeInto(ta, text);
    });
  }

  it(
    "超过 20000 字时「保存草稿」也必须被拦住，不得发请求",
    { timeout: 20_000 },
    async () => {
      // 2026-10-06：单跑 ~2.6s，但全量套件并发负载下逼近默认 5s 边际（实测
      // 5.5s 偶发超时，stash 干净树复现=环境性而非回归）——加余量保门禁确定性。
      await renderPage();
      await fillScript("字".repeat(25000));

      await act(async () => {
        findButton("保存草稿").click();
      });

      expect(
        h.submitMutate,
        "草稿路径必须复用同一套校验，不能绕过 20000 字上限",
      ).not.toHaveBeenCalled();
      expect(h.toastError).toHaveBeenCalled();
    },
  );

  it("正常脚本的草稿 payload 必须包含 secondsPerFrame 字段", async () => {
    await renderPage();

    // secondsPerFrame 只在 mode==="generate" 下渲染（fixed 模式该控件不存在，
    // 两边恒为 undefined → toEqual 断言会变成空的）。切到「话题生成」并填主题。
    await act(async () => {
      findButton("话题生成").click();
    });
    // 话题控件是 Textarea（不是 input），且额外受 cfg.supportsGenerate 门控
    const topicInput = document.querySelector<HTMLTextAreaElement>(
      'textarea[placeholder*="输入一个话题"]',
    );
    if (!topicInput) throw new Error("找不到话题输入框");
    await act(async () => {
      typeInto(topicInput, "新员工入职培训");
    });

    const adv = Array.from(document.querySelectorAll("button")).find((b) =>
      (b.textContent ?? "").includes("高级设置"),
    );
    if (adv) {
      await act(async () => {
        adv.click();
      });
    }
    const spf = document.querySelector<HTMLInputElement>(
      'input[type="number"][placeholder="5"]',
    );
    if (!spf) throw new Error("找不到每图时长输入框（secondsPerFrame）");
    await act(async () => {
      setNativeValue(spf, "7");
    });

    await act(async () => {
      findButton("保存草稿").click();
    });

    expect(h.submitMutate, "草稿应正常发起").toHaveBeenCalled();
    const draftPayload = h.submitMutate.mock.calls[0]![0] as Record<
      string,
      unknown
    >;

    // 与「生成视频」按钮走同一路径后，两份 payload 必须逐键相同。
    // 只断言 seconds_per_frame 存在是不够的：漏传时该键仍会以 undefined 出现。
    h.submitMutate.mockClear();
    await act(async () => {
      findButton("生成视频").click();
    });
    const submitPayload = h.submitMutate.mock.calls[0]![0] as Record<
      string,
      unknown
    >;

    expect(
      draftPayload,
      "草稿与正式提交必须共用同一份 payload 构造（漏 secondsPerFrame 会导致帧时长不一致）",
    ).toEqual(submitPayload);
  });
});
