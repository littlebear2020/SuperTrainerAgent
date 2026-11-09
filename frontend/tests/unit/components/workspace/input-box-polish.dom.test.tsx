/**
 * P1-11：通用聊天输入框的「一键润色」。
 *
 * writer 早就能润色（selection-popover / full-draft-modal 走
 * `POST /api/ai/assist`），通用输入框却没有入口——用户想把随手打的一句话
 * 改清楚，只能自己重写。后端 `/api/ai/assist` 与 writer 无耦合
 * （`ai_assist.py:23-31`），所以这是纯前端接线，后端零改动。
 *
 * 这里锁住四件事，缺一都会让功能「看起来有、实际不可用」：
 *   1. 按钮存在，且文案走 i18n（不是硬编码中文）；
 *   2. 结果必须**流式**回填 composer——不能等整段回来再一次性替换；
 *   3. 空输入 / 请求进行中不得重复发起；
 *   4. 失败要可见（toast）**且回滚原文**——半截润色 + 原文丢失是最糟的结局。
 *      仓库有过 mutation 漏 onError 静默吞错的历史，这条专门防它。
 *
 * 仓库无 @testing-library/react：沿用 bgm-picker-clickable.test.tsx 的
 * createRoot + act + happy-dom 手写 harness。i18n 用真的 I18nProvider
 * （不 mock useI18n），这样漏加语言包 key 时测试会直接红。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  streamAssist: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "light" }),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
  getLangGraphBaseURL: () => "",
}));

vi.mock("@/core/models/hooks", () => ({
  useModels: () => ({
    models: [
      {
        name: "m1",
        display_name: "模型一",
        model: "m1",
        supports_thinking: false,
      },
    ],
    tokenUsageEnabled: false,
    isLoading: false,
  }),
}));

vi.mock("@/core/skills/hooks", () => ({
  useSkills: () => ({ skills: [], isLoading: false }),
}));

vi.mock("sonner", () => ({
  toast: { error: h.toastError, success: vi.fn(), info: vi.fn() },
}));

// 只 mock streamAssist —— 复用的就是它，断言它被以 op:"polish" 调用即证明
// 走的是既有 /api/ai/assist SSE 通道，而不是新写一条。
vi.mock("@/core/writer/api", () => ({
  streamAssist: h.streamAssist,
}));

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { InputBox } from "@/components/workspace/input-box";
import { ThreadContext } from "@/components/workspace/messages/context";
import { I18nProvider } from "@/core/i18n/context";
import type { Locale } from "@/core/i18n/locale";
import { enUS, zhCN } from "@/core/i18n/locales";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const fakeThread = { messages: [] } as never;

let container: HTMLDivElement;
let root: Root;

/**
 * useI18n 的初始化 effect 会用 cookie / 浏览器语言覆盖 initialLocale，
 * happy-dom 的 navigator.language 是 en-US——所以语言必须靠 cookie 指定，
 * 否则测试断言的语言和实际渲染的语言对不上。且该 effect 每个 consumer 只跑
 * 一次，切语言必须重新挂载一棵新树。
 */
async function renderInputBox(initialInput: string, locale: Locale = "en-US") {
  // 每次都换一棵新树：切语言必须重新触发那个只跑一次的 init effect。
  await act(async () => root.unmount());
  root = createRoot(container);
  document.cookie = `locale=${locale}; path=/`;
  await act(async () => {
    root.render(
      <I18nProvider initialLocale={locale}>
        <ThreadContext.Provider value={{ thread: fakeThread }}>
          <PromptInputProvider initialInput={initialInput}>
            <InputBox
              threadId="t1"
              context={{ model_name: "m1", mode: "flash" }}
            />
          </PromptInputProvider>
        </ThreadContext.Provider>
      </I18nProvider>,
    );
  });
}

function polishButton(label = enUS.inputBox.polish): HTMLButtonElement | null {
  return container.querySelector<HTMLButtonElement>(
    `button[aria-label="${label}"]`,
  );
}

function textarea(): HTMLTextAreaElement {
  const el = container.querySelector<HTMLTextAreaElement>(
    "textarea[name='message']",
  );
  if (!el) throw new Error("composer textarea not rendered");
  return el;
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

/** 可外部推进的假 SSE 流，用来断言「边收边回填」而非一次性替换。 */
function controllableStream() {
  const queue: string[] = [];
  let done = false;
  let wake: (() => void) | null = null;

  const push = async (chunk: string) => {
    queue.push(chunk);
    await act(async () => {
      wake?.();
      wake = null;
      await Promise.resolve();
    });
  };
  const finish = async () => {
    done = true;
    await act(async () => {
      wake?.();
      wake = null;
      await Promise.resolve();
    });
  };

  async function* gen() {
    while (true) {
      while (queue.length > 0) {
        yield queue.shift()!;
      }
      if (done) return;
      await new Promise<void>((resolve) => {
        wake = resolve;
      });
    }
  }

  return { gen, push, finish };
}

/**
 * P2-8 草稿持久化会把 composer 文本写进 `draft:<threadId>`（本文件统一用
 * t1），同一测试文件共享同一个 happy-dom window——不清掉的话，上一个测试
 * 收尾时的文本会在下一个测试挂载时被草稿恢复 effect 盖过 initialInput
 * （「输入为空」用例按钮没禁用、回滚用例读到上一个测试的流式终稿，根因
 * 都是这个串台，而不是 PromptInputProvider 的 useState 重置失效）。
 *
 * 是否泄漏取决于 window.localStorage 落到哪个实现：Node ≤24 没有内置
 * localStorage，vitest 把 happy-dom 的真 Storage 装上全局（泄漏成立，
 * CI 的 Node 22 红）；Node 25+ 的全局 localStorage 是 Node 内置 stub
 * （未配 --localstorage-file 时读写抛错，被 InputBox 隐私模式的
 * try/catch 吞掉，持久化天然失效，本地绿）。stub 上 clear 也会抛，兜一层即可。
 */
function clearDraftStorage() {
  try {
    window.localStorage.clear();
  } catch {
    // Node 内置 localStorage stub：持久化天然失效，无需清理。
  }
}

beforeEach(() => {
  clearDraftStorage();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  // 卸载 cleanup 会同步把最终文本落盘（防抖的 400ms 也可能跨测试触发）——
  // 在下一个 beforeEach 之外再清一次，堵住所有写入时机的串台路径。
  clearDraftStorage();
  vi.clearAllMocks();
});

describe("P1-11 通用输入框一键润色", () => {
  it("渲染润色按钮，文案随语言切换（不是硬编码）", async () => {
    await renderInputBox("帮我写个脚本", "en-US");
    expect(polishButton(enUS.inputBox.polish)).not.toBeNull();

    // zh-CN 下必须换成中文标签——硬编码任一语言都会让另一语言的用户看到错的文案
    await renderInputBox("帮我写个脚本", "zh-CN");
    expect(polishButton(zhCN.inputBox.polish)).not.toBeNull();
    expect(zhCN.inputBox.polish).not.toBe(enUS.inputBox.polish);
  });

  it("点击后复用 /api/ai/assist 的 streamAssist，以 op:polish 发送当前输入", async () => {
    const s = controllableStream();
    h.streamAssist.mockReturnValue(s.gen());

    await renderInputBox("帮我写个脚本");
    await click(polishButton()!);

    expect(h.streamAssist).toHaveBeenCalledTimes(1);
    expect(h.streamAssist.mock.calls[0]?.[0]).toMatchObject({
      op: "polish",
      text: "帮我写个脚本",
    });
    // 必须带 AbortSignal，否则卸载/重复点击时流没法掐断
    expect(h.streamAssist.mock.calls[0]?.[1]).toBeInstanceOf(AbortSignal);

    await s.finish();
  });

  it("流式回填：每来一块就更新输入框，而不是等结束再整段替换", async () => {
    const s = controllableStream();
    h.streamAssist.mockReturnValue(s.gen());

    await renderInputBox("帮我写个脚本");
    await click(polishButton()!);

    await s.push("请帮我编写一个");
    expect(textarea().value).toBe("请帮我编写一个");

    await s.push("用于批量重命名文件的脚本。");
    expect(textarea().value).toBe("请帮我编写一个用于批量重命名文件的脚本。");

    await s.finish();
    expect(textarea().value).toBe("请帮我编写一个用于批量重命名文件的脚本。");
  });

  it("输入为空时按钮禁用，不会发请求", async () => {
    await renderInputBox("   ");
    const btn = polishButton();
    expect(btn).not.toBeNull();
    expect(btn!.disabled).toBe(true);

    await click(btn!);
    expect(h.streamAssist).not.toHaveBeenCalled();
  });

  it("请求进行中再次点击不会并发第二次润色", async () => {
    const s = controllableStream();
    h.streamAssist.mockReturnValue(s.gen());

    await renderInputBox("帮我写个脚本");
    await click(polishButton()!);
    expect(polishButton()!.disabled).toBe(true);

    await click(polishButton()!);
    expect(h.streamAssist).toHaveBeenCalledTimes(1);

    await s.finish();
  });

  it("失败时报错可见，并把输入回滚成原文（不留半截润色）", async () => {
    h.streamAssist.mockImplementation(async function* () {
      yield "请帮我编写";
      await Promise.resolve();
      throw new Error("assist 服务不可用");
    });

    await renderInputBox("帮我写个脚本");
    await click(polishButton()!);
    await act(async () => {
      await Promise.resolve();
    });

    expect(h.toastError).toHaveBeenCalled();
    expect(String(h.toastError.mock.calls[0]?.[0])).toContain(
      "assist 服务不可用",
    );
    expect(String(h.toastError.mock.calls[0]?.[0])).toContain(
      enUS.inputBox.polishFailed,
    );
    expect(textarea().value).toBe("帮我写个脚本");
    expect(polishButton()!.disabled).toBe(false);
  });

  it("返回空结果时不清空用户原文", async () => {
    const s = controllableStream();
    h.streamAssist.mockReturnValue(s.gen());

    await renderInputBox("帮我写个脚本");
    await click(polishButton()!);
    await s.finish();

    expect(textarea().value).toBe("帮我写个脚本");
    expect(h.toastError).toHaveBeenCalled();
  });
});
