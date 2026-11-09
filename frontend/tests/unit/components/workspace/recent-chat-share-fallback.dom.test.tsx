/**
 * DEF-4（2026-10-07）：分享链接复制被拒时的兜底。
 *
 * 契约：
 * - navigator.clipboard.writeText 被拒（非安全上下文）→ 弹可选中复制的链接
 *   文本框（不再只 toast 失败）；
 * - 链接用当前 origin 拼接（VERCEL_URL 硬编码回退已删）；
 * - 「复制到剪贴板」按钮走 execCommand 回退，成功 toast + 关弹层；
 * - 菜单项文案为「复制链接」（与发布灵感库的「分享」消歧）。
 *
 * harness 沿用 recent-chat-list-delete-confirm.test.tsx 的既定约定。
 */

import { readFileSync } from "fs";
import { join } from "path";

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  threads: [
    { thread_id: "th_1", title: "会话一", agent_name: undefined, values: {} },
  ],
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/workspace/chats/th_1",
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/core/threads/hooks", () => ({
  useThreads: () => ({ data: h.threads }),
  useDeleteThread: () => ({ mutate: vi.fn() }),
  useRenameThread: () => ({ mutate: vi.fn() }),
  usePinThread: () => ({ mutate: vi.fn() }),
}));

vi.mock("@/core/api", () => ({ getAPIClient: () => ({}) }));

vi.mock("@/env", () => ({
  env: { NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false" },
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: zhCNRef.value }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

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

const zhCNRef = { value: zhCN as unknown as Record<string, unknown> };

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

async function clickRadix(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("DEF-4：分享复制被拒 → 兜底弹层", () => {
  let container: HTMLDivElement;
  let root: Root;
  let execCommandMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    vi.clearAllMocks();
    execCommandMock = vi.fn(() => true);
    document.execCommand =
      execCommandMock as unknown as typeof document.execCommand;
    // 剪贴板被拒（非安全上下文的典型表现）
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
      configurable: true,
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    document.body.innerHTML = "";
  });

  async function openShareDialog() {
    await act(async () => {
      root.render(<RecentChatList />);
    });
    const trigger = container.querySelector("button");
    expect(trigger).toBeTruthy();
    await clickRadix(trigger!);

    const item = Array.from(
      document.querySelectorAll('[role="menuitem"]'),
    ).find((el) => el.textContent?.trim() === zhCN.clipboard.copyLink);
    expect(item, "菜单项应显示「复制链接」").toBeTruthy();
    await clickRadix(item as HTMLElement);

    // 兜底弹层出现（Radix Dialog portal 在 body）
    expect(document.body.textContent).toContain(zhCN.chats.shareFallbackTitle);
    const input = document.querySelector<HTMLInputElement>("input[readonly]");
    expect(input, "链接文本框应渲染").toBeTruthy();
    return input!;
  }

  it("剪贴板被拒 → 弹层展示当前 origin 拼接的链接（可选中只读文本框）", async () => {
    const input = await openShareDialog();

    expect(input.value).toContain(window.location.origin);
    expect(input.value).toContain("th_1");
    // 只读但可选中：不以 disabled 形态渲染（disabled 不可选中）
    expect(input.hasAttribute("disabled")).toBe(false);
    // 修前路径：catch 只 toast.error，无弹层
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("「复制到剪贴板」走 execCommand 回退：成功 toast + 关弹层", async () => {
    await openShareDialog();

    const copyBtn = Array.from(document.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === zhCN.clipboard.copyToClipboard,
    );
    expect(copyBtn).toBeTruthy();
    await clickRadix(copyBtn!);

    expect(execCommandMock).toHaveBeenCalledWith("copy");
    expect(toast.success).toHaveBeenCalledWith(zhCN.clipboard.linkCopied);
    expect(document.body.textContent).not.toContain(
      zhCN.chats.shareFallbackTitle,
    );
  });

  it("execCommand 也失败时弹层保留（用户可手动选中复制）", async () => {
    await openShareDialog();

    execCommandMock.mockReturnValue(false);
    const copyBtn = Array.from(document.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === zhCN.clipboard.copyToClipboard,
    );
    await clickRadix(copyBtn!);

    expect(document.body.textContent).toContain(zhCN.chats.shareFallbackTitle);
    expect(toast.success).not.toHaveBeenCalled();
  });
});

describe("DEF-4 静态守卫", () => {
  const src = readFileSync(
    join(process.cwd(), "src/components/workspace/recent-chat-list.tsx"),
    "utf-8",
  );

  it("VERCEL_URL 硬编码回退已删；菜单项消歧为 copyLink", () => {
    // 注：组件注释里保留「VERCEL_URL」字样说明删因，守卫钉的是代码形态——
    // 无上游公网域名常量、无 VERCEL_URL 声明/赋值。
    expect(src).not.toContain("vercel.app");
    expect(src).not.toMatch(/const\s+VERCEL_URL/);
    expect(src).toContain("t.clipboard.copyLink");
    // 分享菜单项不再用 t.common.share（与灵感库「分享」同名易误导）
    expect(src).not.toMatch(/<span>\{t\.common\.share\}<\/span>/);
  });
});
