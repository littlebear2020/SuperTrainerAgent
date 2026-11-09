/**
 * OBS-1（D1，2026-10-07）：技能设置页 Legacy tab。
 * 修前 tabs 只有 public/custom——P1-S3 起旧全局 custom 技能以 LEGACY 只读
 * 兜底进 load_skills 合并视图，但 UI 无入口，「技能不见了」无从解释。
 * 本用例钉扎：三 tab 渲染、过滤逻辑、legacy 空态（无创建 CTA + 专属文案）。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SkillSettingsPage } from "@/components/workspace/settings/skill-settings-page";
import { useIsConsoleAdmin } from "@/core/console/hooks";
import { I18nProvider } from "@/core/i18n/context";
import { useSkills } from "@/core/skills/hooks";
import type { Skill } from "@/core/skills/type";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));
vi.mock("@/env", () => ({
  env: { NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false" },
}));
// 组件只消费 useSkills / useEnableSkill 两个导出，整模块替换即可（无需
// importOriginal 保留其余导出）。
vi.mock("@/core/skills/hooks", () => ({
  useSkills: vi.fn(),
  useEnableSkill: () => ({ mutate: vi.fn() }),
}));
// admin 判定走 useIsConsoleAdmin（复核红线：不得硬编码角色字符串比较）
vi.mock("@/core/console/hooks", () => ({
  useIsConsoleAdmin: vi.fn(),
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mockUseSkills = vi.mocked(useSkills);
const mockUseIsConsoleAdmin = vi.mocked(useIsConsoleAdmin);

const SKILLS: Skill[] = [
  {
    name: "ppt-master",
    description: "public skill",
    category: "public",
    license: null as unknown as string,
    enabled: true,
  },
  {
    name: "my-skill",
    description: "custom skill",
    category: "custom",
    license: null as unknown as string,
    enabled: false,
  },
  {
    name: "course-video",
    description: "legacy skill",
    category: "legacy",
    license: null as unknown as string,
    enabled: true,
  },
];

describe("OBS-1: 技能设置页 Legacy tab", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    document.cookie = "locale=zh-CN; path=/";
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

  async function renderPage(skills: Skill[], isAdmin = true) {
    mockUseSkills.mockReturnValue({
      skills,
      isLoading: false,
      error: null,
    });
    mockUseIsConsoleAdmin.mockReturnValue(isAdmin);
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <I18nProvider initialLocale="zh-CN">
            <SkillSettingsPage />
          </I18nProvider>
        </QueryClientProvider>,
      );
    });
  }

  function tabs(): string[] {
    return Array.from(container.querySelectorAll('[role="tab"]')).map(
      (el) => el.textContent ?? "",
    );
  }

  it("渲染三个 tab：公共 / 自定义 / 旧版", async () => {
    await renderPage(SKILLS);
    expect(tabs()).toEqual(["公共", "自定义", "旧版"]);
  });

  it("默认公共 tab 只显示 public 技能", async () => {
    await renderPage(SKILLS);
    const text = container.textContent ?? "";
    expect(text).toContain("ppt-master");
    expect(text).not.toContain("my-skill");
    expect(text).not.toContain("legacy skill");
  });

  // Radix TabsTrigger 在 mousedown（button 0）激活，click 不触发——
  // element.click() 只发 click 事件（@radix-ui/react-tabs TabsTrigger 源码口径）。
  function mousedownTab(label: string) {
    const tab = Array.from(
      container.querySelectorAll<HTMLElement>('[role="tab"]'),
    ).find((el) => el.textContent === label);
    expect(tab, `tab ${label} 应存在`).toBeTruthy();
    tab!.dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true, button: 0 }),
    );
  }

  it("切到旧版 tab 只显示 legacy 技能（过滤逻辑）", async () => {
    await renderPage(SKILLS);
    expect(container.querySelector('[role="tab"]:nth-child(3)')).toBeTruthy();

    await act(async () => {
      mousedownTab("旧版");
      await new Promise((r) => setTimeout(r, 0));
    });

    const text = container.textContent ?? "";
    expect(text).toContain("course-video");
    expect(text).toContain("legacy skill");
    expect(text).not.toContain("ppt-master");
    expect(text).not.toContain("my-skill");
  });

  it("旧版 tab 空态：专属文案且无「创建你的第一个技能」CTA", async () => {
    await renderPage(SKILLS.filter((s) => s.category !== "legacy"));

    await act(async () => {
      mousedownTab("旧版");
      await new Promise((r) => setTimeout(r, 0));
    });

    const text = container.textContent ?? "";
    expect(text).toContain("没有旧版技能");
    expect(text).toContain("只读展示");
    expect(text).not.toContain("创建你的第一个技能");
  });

  it("自定义 tab 空态保留创建 CTA（既有行为不回归）", async () => {
    await renderPage(SKILLS.filter((s) => s.category === "public"));

    await act(async () => {
      mousedownTab("自定义");
      await new Promise((r) => setTimeout(r, 0));
    });

    const text = container.textContent ?? "";
    expect(text).toContain("还没有技能");
    expect(text).toContain("创建你的第一个技能");
    // OBS-1 空态文案修正：不再指引 /skills/custom 根目录（P1-S3 后失效）
    expect(text).not.toContain("/skills/custom");
  });

  // ── 收尾复核批（2026-10-07）：非 admin 的 legacy 开关必然 403，必须禁用 ──
  // 后端 _check_skill_ownership（SEC-M22）对 legacy 类技能要求 admin——
  // 普通用户可点开关 = 必然失败的误导 UI。

  function switches(): HTMLButtonElement[] {
    return Array.from(container.querySelectorAll('button[role="switch"]'));
  }

  it("非 admin 在 legacy tab：Switch 禁用 + 行内提示", async () => {
    await renderPage(SKILLS, /* isAdmin */ false);

    await act(async () => {
      mousedownTab("旧版");
      await new Promise((r) => setTimeout(r, 0));
    });

    const legacySwitches = switches();
    expect(legacySwitches.length).toBe(1);
    expect(legacySwitches[0]!.disabled).toBe(true);
    expect(container.textContent).toContain("旧版技能仅管理员可启停");
  });

  it("非 admin 在非 legacy tab：Switch 仍可点（行为不变）", async () => {
    await renderPage(SKILLS, /* isAdmin */ false);

    const publicSwitches = switches();
    expect(publicSwitches.length).toBe(1);
    expect(publicSwitches[0]!.disabled).toBe(false);
    expect(container.textContent).not.toContain("旧版技能仅管理员可启停");
  });

  it("admin 在 legacy tab：Switch 可点（行为不变）", async () => {
    await renderPage(SKILLS, /* isAdmin */ true);

    await act(async () => {
      mousedownTab("旧版");
      await new Promise((r) => setTimeout(r, 0));
    });

    const legacySwitches = switches();
    expect(legacySwitches.length).toBe(1);
    expect(legacySwitches[0]!.disabled).toBe(false);
  });
});
