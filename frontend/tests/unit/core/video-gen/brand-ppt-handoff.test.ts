/**
 * 课程项目品牌 → ppt-master handoff 测试（Phase 3.5c）：
 * 同字段同源映射（remotion_theme/primary/font/logo → prompt 指令）与跳转通道。
 */
import { describe, expect, it, vi } from "vitest";

import {
  BRAND_THEME_LABELS,
  PPT_MASTER_CHAT_TARGET,
  composeBrandPptPrompt,
  jumpToBrandPptChat,
} from "@/components/workspace/video-gen/brand-ppt-handoff";

describe("composeBrandPptPrompt", () => {
  it("全字段：主题/主色/字体/LOGO 全部进提示词（同源字段名可追溯）", () => {
    const prompt = composeBrandPptPrompt({
      projectTitle: "新员工培训",
      remotionTheme: "warm",
      primaryColor: "#E01B22",
      fontFamily: "思源黑体",
      hasLogo: true,
    });
    expect(prompt).toContain("新员工培训");
    expect(prompt).toContain(BRAND_THEME_LABELS.warm);
    expect(prompt).toContain("warm");
    expect(prompt).toContain("#E01B22");
    expect(prompt).toContain("思源黑体");
    expect(prompt).toContain("LOGO");
    expect(prompt).toContain("design spec");
  });

  it("缺字段：只输出存在的行，不出现 null 字样", () => {
    const prompt = composeBrandPptPrompt({
      projectTitle: "空品牌项目",
      remotionTheme: null,
      primaryColor: null,
      fontFamily: null,
      hasLogo: false,
    });
    expect(prompt).toContain("空品牌项目");
    expect(prompt).not.toContain("null");
    expect(prompt).not.toContain("主题：");
    expect(prompt).not.toContain("主色");
    expect(prompt).not.toContain("LOGO");
  });
});

describe("jumpToBrandPptChat", () => {
  it("短提示词走 ?msg= URL", () => {
    const push = vi.fn();
    const path = jumpToBrandPptChat("短提示", { push });
    expect(path).toBe("url");
    expect(push).toHaveBeenCalledWith(
      `${PPT_MASTER_CHAT_TARGET}?msg=${encodeURIComponent("短提示")}`,
    );
  });

  it("超长提示词走 sessionStorage（带 storage 时）", () => {
    const push = vi.fn();
    const long = "长".repeat(5000);
    const setItem = vi.fn();
    const storage = {
      setItem,
      getItem: vi.fn(),
      removeItem: vi.fn(),
    } as unknown as Storage;
    const path = jumpToBrandPptChat(long, { push }, storage);
    expect(path).toBe("sessionStorage");
    expect(setItem).toHaveBeenCalled();
    expect(push).toHaveBeenCalledWith(PPT_MASTER_CHAT_TARGET);
  });

  it("超长提示词且 storage 不可用：截断到 URL 上限跳转，不整段丢失", () => {
    const push = vi.fn();
    const long = "长".repeat(5000);
    const path = jumpToBrandPptChat(long, { push }, null);
    // 修前：裸跳丢提示词却仍返回 "url"
    expect(path).toBe("url");
    expect(push).toHaveBeenCalledTimes(1);
    const url = push.mock.calls[0]?.[0] ?? "";
    expect(url.startsWith(`${PPT_MASTER_CHAT_TARGET}?msg=`)).toBe(true);
    // 截断后编码长度必须落在 URL 上限内，且非空
    const msg = decodeURIComponent(url.split("?msg=")[1] ?? "");
    expect(msg.length).toBeGreaterThan(0);
    expect(encodeURIComponent(msg).length).toBeLessThanOrEqual(4000);
  });
});
