/**
 * Task 6 前端（2026-08-01 反馈 8）：生成模型下拉的取值逻辑。
 *
 * 可选值**由后端 GET /api/video/providers 动态给出**，不前端硬编码 ——
 * provider 可用性取决于 key 是否配置（未配 dashscope/kling 就不该出现在下拉里）。
 *
 * E5（2026-08-21 360 主力切换）：label 仍只给模型名（决策 6/7 对 label 依旧成立）；
 * 能力/风险标注走后端 annotations 元数据的 note 字段（决策 7/D5 新口径：
 * 实测结论 / 「未检测」，不推断），与 label 分离渲染。
 */

import { describe, expect, it } from "vitest";

import {
  buildProviderOptions,
  DEFAULT_PROVIDER_VALUE,
  formatProviderNote,
  providerFieldForTaskType,
  providerValueToPayload,
} from "@/core/video-gen/provider-options";
import type { ProviderInfo } from "@/core/video-gen/types";

const P = (
  name: string,
  available: boolean,
  is_default = false,
  label?: string,
  annotations?: ProviderInfo["annotations"],
): ProviderInfo => ({
  name,
  label: label ?? name,
  available,
  is_default,
  annotations,
});

describe("buildProviderOptions", () => {
  it("只保留 available 的项（未配 key 的自动消失）", () => {
    const opts = buildProviderOptions([
      P("seedream", true, true),
      P("dashscope", false),
      P("nano-banana", true),
    ]);
    // 默认项的 value 是哨兵（见下面「哨兵」用例说明）
    expect(opts.map((o) => o.value)).toEqual([
      DEFAULT_PROVIDER_VALUE,
      "nano-banana",
    ]);
  });

  it("默认项 label 追加「（默认）」后缀", () => {
    const opts = buildProviderOptions([
      P("seedream", true, true, "Seedream 5.0 Pro（字节直连）"),
      P("nano-banana", true, false, "Gemini 3 Pro Image"),
    ]);
    expect(opts[0]!.label).toBe("Seedream 5.0 Pro（字节直连）（默认）");
    expect(opts[1]!.label).toBe("Gemini 3 Pro Image");
  });

  it("默认项排在最前（用户最可能选它）", () => {
    const opts = buildProviderOptions([
      P("nano-banana", true),
      P("qihu", true),
      P("seedream", true, true),
    ]);
    expect(opts[0]!.value).toBe(DEFAULT_PROVIDER_VALUE);
  });

  it("不渲染任何模型限制/警告文案（决策 6/7）", () => {
    const opts = buildProviderOptions([
      P("seedance-ark", true, true, "Seedance 2.0（字节直连）"),
      P("gpt-image-2", true, false, "GPT Image 2"),
    ]);
    const blob = opts.map((o) => o.label).join(" ");
    for (const banned of ["真人", "拦", "漂移", "不支持", "限制", "警告"]) {
      expect(blob, `不应出现限制文案「${banned}」`).not.toContain(banned);
    }
  });

  it("空列表 / 全不可用 → 返回空数组（调用方据此不渲染下拉）", () => {
    expect(buildProviderOptions([])).toEqual([]);
    expect(buildProviderOptions([P("dashscope", false)])).toEqual([]);
  });

  it("undefined（接口未返回/加载中）不炸", () => {
    expect(buildProviderOptions(undefined)).toEqual([]);
  });
});

describe("providerFieldForTaskType", () => {
  it("ai_image → image_provider；ai_video → video_provider", () => {
    expect(providerFieldForTaskType("ai_image")).toBe("image");
    expect(providerFieldForTaskType("ai_video")).toBe("video");
  });

  it("其他类型无模型选择（remotion 不走后端；vimax 自带引擎链）", () => {
    expect(providerFieldForTaskType("remotion")).toBeNull();
    expect(providerFieldForTaskType("vimax")).toBeNull();
  });
});

describe("formatProviderNote（E5 能力/风险标注，D5 口径）", () => {
  it("实测项：tested 结论 + notes 用「·」拼接", () => {
    const note = formatProviderNote({
      reference_images: true,
      tested: "已实测（2026-08-21：10 路并发全成零限流；比例/水印/关音轨生效）",
      notes: "主力通道",
    });
    expect(note).toContain("已实测");
    expect(note).toContain("主力通道");
    expect(note).toContain("·");
  });

  it("未检测项：tested=未检测 原样呈现，不推断", () => {
    const note = formatProviderNote({
      reference_images: true,
      tested: "未检测",
      notes: "仅 480p/720p，低成本",
    });
    expect(note.startsWith("未检测")).toBe(true);
    expect(note).toContain("仅 480p/720p");
  });

  it("空 notes 不留尾部分隔符；无 annotations（旧后端）返回空串", () => {
    expect(
      formatProviderNote({
        reference_images: true,
        tested: "未检测",
        notes: "",
      }),
    ).toBe("未检测");
    expect(formatProviderNote(undefined)).toBe("");
  });

  it("buildProviderOptions 把 note 带进选项（label 保持纯模型名）", () => {
    const opts = buildProviderOptions([
      P("seedance-fast", true, false, "Seedance 2.0 Fast（360 通道）", {
        reference_images: true,
        tested: "未检测",
        notes: "仅 480p/720p，低成本",
      }),
    ]);
    expect(opts[0]!.label).toBe("Seedance 2.0 Fast（360 通道）");
    expect(opts[0]!.note).toBe("未检测 · 仅 480p/720p，低成本");
  });
});

describe("默认项用哨兵 value（避免悄悄关掉降级链）", () => {
  it("默认项 value 是哨兵，不是模型真名", () => {
    const opts = buildProviderOptions([
      P("seedream", true, true, "Seedream 5.0 Pro"),
      P("nano-banana", true),
    ]);
    expect(opts[0]!.value).toBe(DEFAULT_PROVIDER_VALUE);
    expect(opts[0]!.value).not.toBe("seedream");
  });

  it("providerValueToPayload：哨兵/空 → null，真名原样透传", () => {
    // 后端语义：不传 = 跟随默认且**保留降级链**；传名字 = **禁用降级**。
    // 若默认项用真名，用户选「（默认）」那项就会悄悄关掉降级，
    // 与他什么都不选的行为不同，但 UI 上看着是同一件事。
    expect(providerValueToPayload(DEFAULT_PROVIDER_VALUE)).toBeNull();
    expect(providerValueToPayload(null)).toBeNull();
    expect(providerValueToPayload("")).toBeNull();
    expect(providerValueToPayload("nano-banana")).toBe("nano-banana");
  });
});
