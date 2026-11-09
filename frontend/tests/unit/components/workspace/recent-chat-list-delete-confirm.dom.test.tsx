/**
 * P2-1 / BUG-003 / DSN-001：侧栏会话删除确认弹层。
 *
 * 修前：Delete 菜单项 onSelect 直调 handleDelete → deleteThread 立即执行并跳转，
 * 无任何确认（组件内只有 Rename 一个 Dialog）。
 *
 * 契约：
 * - More → Delete → 弹层出现（「确定要删除这个会话吗？此操作无法撤销。」）；
 * - 点取消 → deleteThread 未被调用（无副作用）；
 * - 点确认 → deleteThread 恰被调用一次。
 *
 * 静态守卫：Delete DropdownMenuItem 不再直接调用 handleDelete。
 *
 * 仓库无 @testing-library/react：沿用 createRoot + act + happy-dom 手写 harness。
 */

import { readFileSync } from "fs";
import { join } from "path";

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  deleteThread: vi.fn(),
  tBase: {} as Record<string, unknown>,
  threads: [
    { thread_id: "th_1", title: "会话一", agent_name: undefined, values: {} },
    { thread_id: "th_2", title: "会话二", agent_name: undefined, values: {} },
  ],
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/workspace/chats/th_1",
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/core/threads/hooks", () => ({
  useThreads: () => ({ data: h.threads }),
  useDeleteThread: () => ({ mutate: h.deleteThread }),
  useRenameThread: () => ({ mutate: vi.fn() }),
  usePinThread: () => ({ mutate: vi.fn() }),
}));

vi.mock("@/core/api", () => ({ getAPIClient: () => ({}) }));

vi.mock("@/env", () => ({
  env: { NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false" },
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: { ...h.tBase } }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

// Sidebar 原语需要 SidebarProvider 上下文——测试只关心菜单/弹层，直接摊平成 div
vi.mock("@/components/ui/sidebar", () => ({
  SidebarGroup: (p: Record<string, unknown>) => <div {...p} />,
  SidebarGroupContent: (p: Record<string, unknown>) => <div {...p} />,
  SidebarGroupLabel: (p: Record<string, unknown>) => <div {...p} />,
  SidebarMenu: (p: Record<string, unknown>) => <div {...p} />,
  SidebarMenuAction: (p: Record<string, unknown>) => <button {...p} />,
  SidebarMenuButton: (p: Record<string, unknown>) => <div {...p} />,
  SidebarMenuItem: (p: Record<string, unknown>) => <div {...p} />,
}));

import { RecentChatList } from "@/components/workspace/recent-chat-list";
import { zhCN } from "@/core/i18n/locales/zh-CN";

h.tBase = zhCN as unknown as Record<string, unknown>;

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

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

describe("侧栏会话删除确认（P2-1/BUG-003）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.deleteThread.mockClear();
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

  async function renderList() {
    await act(async () => {
      root.render(<RecentChatList />);
    });
  }

  async function openDeleteMenu() {
    // 第一个 MoreHorizontal 触发器（th_1 行——mock 的 SidebarMenuAction 摊平成 button）
    const trigger = container.querySelector("button");
    expect(trigger).toBeTruthy();
    await clickRadix(trigger!);
    // 下拉菜单渲染在 body portal——找「删除」菜单项
    const item = Array.from(
      document.querySelectorAll('[role="menuitem"]'),
    ).find((el) => el.textContent?.trim() === zhCN.common.delete);
    expect(item, "Delete 菜单项应出现").toBeTruthy();
    return item!;
  }

  it("Delete → 弹层出现；取消 → deleteThread 未被调用", async () => {
    await renderList();
    const item = await openDeleteMenu();
    await clickRadix(item as HTMLElement);

    // 弹层出现（Radix AlertDialog portal 在 body）
    expect(document.body.textContent).toContain("此操作无法撤销");
    expect(h.deleteThread).not.toHaveBeenCalled();

    const cancel = buttonsWithText(zhCN.common.cancel)[0];
    expect(cancel).toBeTruthy();
    await clickRadix(cancel!);
    expect(h.deleteThread).not.toHaveBeenCalled();
  });

  it("Delete → 确认 → deleteThread 恰被调用一次", async () => {
    await renderList();
    const item = await openDeleteMenu();
    await clickRadix(item as HTMLElement);

    const confirm = Array.from(document.querySelectorAll("button")).find((b) =>
      b.textContent?.includes(zhCN.chats.deleteChatConfirmAction ?? "删除"),
    );
    expect(confirm).toBeTruthy();
    await clickRadix(confirm!);
    expect(h.deleteThread).toHaveBeenCalledTimes(1);
  });
});

describe("静态守卫：Delete 菜单项不直调 handleDelete（P2-1）", () => {
  const src = readFileSync(
    join(process.cwd(), "src/components/workspace/recent-chat-list.tsx"),
    "utf-8",
  );

  it("存在删除确认 AlertDialog + Delete 菜单项只开弹层", () => {
    expect(src).toContain("AlertDialog");
    // Delete DropdownMenuItem 的 onSelect 打开弹层而非直接删
    expect(src).not.toMatch(/onSelect=\{\(\) => handleDelete\(/);
    expect(src).toContain("setPendingDelete");
  });
});
