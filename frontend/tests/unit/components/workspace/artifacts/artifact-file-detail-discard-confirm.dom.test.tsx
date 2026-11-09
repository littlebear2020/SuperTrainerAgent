/**
 * 弹层统一（OBS-3，2026-10-09）：artifact 编辑的放弃修改/关闭确认从 window.confirm
 * 换 useConfirm 弹层（confirmDiscard 异步化 + 关闭按钮守卫）。
 *
 * 契约（两分支）：
 * - 脏草稿点「放弃修改」→ 弹层出现（discardChanges 文案）；取消 → setDrafts 未被调用；
 * - 确认 → setDrafts 被调用（草稿重置回基线）；
 * - 关闭按钮在 hasUnsavedDrafts 时同样走弹层，确认后整份草稿清空 + 关闭面板。
 *
 * harness 参照 recent-chat-list-delete-confirm.test.tsx（无 @testing-library/react）。
 */

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  setDrafts: vi.fn(),
  setEditingPath: vi.fn(),
  setOpen: vi.fn(),
  select: vi.fn(),
  tBase: {} as Record<string, unknown>,
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ getQueryData: vi.fn(), setQueryData: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: h.tBase }),
}));

vi.mock("@/env", () => ({
  env: { NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false" },
}));

vi.mock("next/dynamic", () => ({
  default: () => () => null,
}));

vi.mock("streamdown", () => ({
  Streamdown: () => null,
}));

vi.mock("@/core/streamdown", () => ({
  streamdownPlugins: [],
}));

vi.mock("@/core/artifacts/hooks", () => ({
  useArtifactContent: () => ({
    content: "baseline",
    url: "/api/files/a.py",
    sha256: "sha1",
    isLoading: false,
  }),
}));

vi.mock("@/core/skills/api", () => ({
  installSkill: vi.fn(),
}));

// artifacts 面板上下文——drafts/editingPath 状态注入点（受控）
vi.mock("@/components/workspace/artifacts/context", () => ({
  useArtifacts: () => ({
    artifacts: ["mnt/user-data/outputs/a.py"],
    setOpen: h.setOpen,
    select: h.select,
    drafts: {
      "mnt/user-data/outputs/a.py": {
        baselineContent: "baseline",
        draftContent: "edited",
        baselineSha256: "sha1",
        conflict: false,
      },
    },
    setDrafts: h.setDrafts,
    editingPath: "mnt/user-data/outputs/a.py",
    setEditingPath: h.setEditingPath,
  }),
}));

vi.mock("@/components/workspace/messages/context", () => ({
  useThread: () => ({ thread: { isLoading: false } }),
}));

vi.mock("@/components/workspace/tooltip", () => ({
  Tooltip: (p: Record<string, unknown>) => <div>{p.children as ReactNode}</div>,
}));

vi.mock("@/components/workspace/citations/artifact-link", () => ({
  ArtifactLink: () => null,
}));

// ai-elements Artifact 原语摊平（测试只关心按钮与弹层）
vi.mock("@/components/ai-elements/artifact", () => ({
  Artifact: (p: Record<string, unknown>) => <div {...p} />,
  ArtifactHeader: (p: Record<string, unknown>) => <div {...p} />,
  ArtifactTitle: (p: Record<string, unknown>) => <div {...p} />,
  ArtifactActions: (p: Record<string, unknown>) => <div {...p} />,
  ArtifactAction: (p: Record<string, unknown>) => (
    <button
      type="button"
      className={p.className as string | undefined}
      disabled={p.disabled as boolean | undefined}
      onClick={p.onClick as (() => void) | undefined}
      data-action={p.label as string}
    >
      {p.label as string}
    </button>
  ),
  ArtifactContent: (p: Record<string, unknown>) => <div {...p} />,
}));

vi.mock("@/components/ui/select", () => ({
  Select: () => <div data-testid="artifact-select" />,
  SelectContent: () => null,
  SelectGroup: () => null,
  SelectItem: () => null,
  SelectTrigger: () => null,
  SelectValue: () => null,
}));

vi.mock("@/components/ui/toggle-group", () => ({
  ToggleGroup: (p: Record<string, unknown>) => <div {...p} />,
  ToggleGroupItem: () => null,
}));

import { ArtifactFileDetail } from "@/components/workspace/artifacts/artifact-file-detail";
import { DialogProvider } from "@/components/workspace/dialog-provider";
import { zhCN } from "@/core/i18n/locales/zh-CN";

h.tBase = zhCN as unknown as Record<string, unknown>;

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function actionButton(label: string): HTMLButtonElement | undefined {
  return Array.from(document.querySelectorAll("button")).find(
    (b) => b.getAttribute("data-action") === label,
  );
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

describe("artifact 放弃修改/关闭确认弹层（OBS-3）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.setDrafts.mockClear();
    h.setEditingPath.mockClear();
    h.setOpen.mockClear();
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

  async function renderDetail() {
    await act(async () => {
      root.render(
        <DialogProvider>
          <ArtifactFileDetail
            filepath="mnt/user-data/outputs/a.py"
            threadId="th_1"
          />
        </DialogProvider>,
      );
    });
  }

  it("脏草稿点「放弃修改」→ 弹层出现；取消 → 草稿未被重置", async () => {
    await renderDetail();
    const discard = actionButton(zhCN.artifactEditing.discard);
    expect(discard).toBeTruthy();
    await clickRadix(discard!);

    expect(document.body.textContent).toContain(
      zhCN.artifactEditing.discardChanges,
    );

    const cancel = buttonsWithText("取消")[0];
    expect(cancel).toBeTruthy();
    await clickRadix(cancel!);
    // discardDraft 的标志 = 退出编辑（mount 时的 adopt effect 也会调 setDrafts(updater)，
    // 不能以 setDrafts 计数判定——用 setEditingPath(null) 作 discard 发生标记）
    expect(h.setEditingPath).not.toHaveBeenCalledWith(null);
  });

  it("脏草稿点「放弃修改」→ 确认 → 草稿重置并退出编辑（setDrafts 收到重置 updater）", async () => {
    await renderDetail();
    await clickRadix(actionButton(zhCN.artifactEditing.discard)!);

    const confirm = buttonsWithText("确定")[0];
    expect(confirm).toBeTruthy();
    await clickRadix(confirm!);
    expect(h.setEditingPath).toHaveBeenCalledWith(null);
    // discardDraft 以 updater 形式重置当前文件草稿
    const updaterCalls = h.setDrafts.mock.calls.filter(
      (args) => typeof args[0] === "function",
    );
    expect(updaterCalls.length).toBeGreaterThan(0);
    const reset = updaterCalls[updaterCalls.length - 1]![0] as (
      current: Record<string, unknown>,
    ) => Record<string, unknown>;
    const next = reset({
      "mnt/user-data/outputs/a.py": {
        baselineContent: "b",
        draftContent: "d",
        baselineSha256: "s",
        conflict: false,
      },
    });
    const resetDraft = next["mnt/user-data/outputs/a.py"] as {
      baselineContent: string;
      draftContent: string;
    };
    expect(resetDraft.draftContent).toBe("baseline");
  });

  it("关闭按钮（有未保存草稿）→ 确认 → 草稿清空 + 退出编辑 + 关闭面板", async () => {
    await renderDetail();
    const close = actionButton(zhCN.common.close);
    expect(close).toBeTruthy();
    await clickRadix(close!);

    expect(document.body.textContent).toContain(
      zhCN.artifactEditing.discardChanges,
    );
    const confirm = buttonsWithText("确定")[0];
    expect(confirm).toBeTruthy();
    await clickRadix(confirm!);
    expect(h.setDrafts).toHaveBeenCalledWith({});
    expect(h.setEditingPath).toHaveBeenCalledWith(null);
    expect(h.setOpen).toHaveBeenCalledWith(false);
  });
});
