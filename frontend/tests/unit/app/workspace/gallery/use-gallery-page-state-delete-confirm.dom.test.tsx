/**
 * 弹层统一（OBS-3，2026-10-09）：灵感库删除确认从 window.confirm 换 useConfirm 弹层
 * （2026-10-06 拆分后调用点在 use-gallery-page-state hook 的 handleDeleteCase）。
 *
 * 契约（两分支）：
 * - handleDeleteCase → 弹层出现（「确定删除这条灵感吗？删除后不可恢复。」）；
 *   取消 → deleteCase API 未被调用；
 * - 确认 → deleteCase 以 selectedCase.id 调用，成功后缓存失效回写。
 *
 * harness：宿主组件捕获 hook 返回值（参照 recent-chat-list-delete-confirm 手写 harness）。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  deleteCase: vi.fn(async () => undefined),
  setQueryData: vi.fn(),
  hook: null as {
    setSelectedCase: (c: unknown) => void;
    handleDeleteCase: () => Promise<void>;
  } | null,
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: { cases: [] }, isLoading: false }),
  useQueryClient: () => ({ setQueryData: h.setQueryData }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "light" }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/core/gallery/api", () => ({
  caseHasFileVars: () => false,
  compileTemplate: vi.fn(),
  deleteCase: h.deleteCase as unknown as (...args: unknown[]) => Promise<void>,
  generateThumbnail: vi.fn(),
  instantiateCase: vi.fn(),
  loadGalleryCases: vi.fn(),
  toggleLikeCase: vi.fn(),
  updateCase: vi.fn(),
  validateVarsWithConfigs: () => ({}),
}));

vi.mock("@/core/gallery/file-vars-submit", () => ({
  submitFileVarsCase: vi.fn(),
}));
vi.mock("@/core/gallery/stream-rejoin", () => ({
  seedStreamRejoinKey: vi.fn(),
}));
vi.mock("@/core/uploads", () => ({
  uploadFilesWithProgress: vi.fn(),
}));
vi.mock("@/core/theme/glass", () => ({
  useGlassStyle: () => ({ glassStyle: () => ({}) }),
}));

import { useGalleryPageState } from "@/app/workspace/gallery/use-gallery-page-state";
import { DialogProvider } from "@/components/workspace/dialog-provider";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const caseFixture = { id: "case_1", title: "灵感一" };

function Host() {
  h.hook = useGalleryPageState() as unknown as {
    setSelectedCase: (c: unknown) => void;
    handleDeleteCase: () => Promise<void>;
  };
  return null;
}

function buttonsWithText(text: string): HTMLButtonElement[] {
  return Array.from(document.querySelectorAll("button")).filter(
    (b) => b.textContent?.trim() === text,
  );
}

async function clickRadix(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("灵感库删除确认弹层（OBS-3，use-gallery-page-state）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.deleteCase.mockClear();
    h.setQueryData.mockClear();
    h.hook = null;
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

  async function renderHost() {
    await act(async () => {
      root.render(
        <DialogProvider>
          <Host />
        </DialogProvider>,
      );
    });
  }

  it(
    "handleDeleteCase → 弹层出现；取消 → deleteCase 未被调用",
    { timeout: 15000 },
    async () => {
      await renderHost();
      await act(async () => {
        h.hook!.setSelectedCase(caseFixture);
      });

      let pending: Promise<void> | null = null;
      await act(async () => {
        pending = h.hook!.handleDeleteCase();
      });
      expect(document.body.textContent).toContain(
        "确定删除这条灵感吗？删除后不可恢复。",
      );
      expect(h.deleteCase).not.toHaveBeenCalled();

      const cancel = buttonsWithText("取消")[0];
      expect(cancel).toBeTruthy();
      await clickRadix(cancel!);
      await act(async () => {
        await pending;
      });
      expect(h.deleteCase).not.toHaveBeenCalled();
    },
  );

  it(
    "handleDeleteCase → 确认 → deleteCase 以 case id 调用并回写缓存",
    { timeout: 15000 },
    async () => {
      await renderHost();
      await act(async () => {
        h.hook!.setSelectedCase(caseFixture);
      });

      let pending: Promise<void> | null = null;
      await act(async () => {
        pending = h.hook!.handleDeleteCase();
      });

      const confirm = buttonsWithText("确定")[0];
      expect(confirm).toBeTruthy();
      await clickRadix(confirm!);
      await act(async () => {
        await pending;
      });
      expect(h.deleteCase).toHaveBeenCalledTimes(1);
      expect(h.deleteCase).toHaveBeenCalledWith("case_1");
      // 成功后两个分页缓存键都做本地删除回写
      expect(h.setQueryData).toHaveBeenCalledTimes(2);
    },
  );
});
