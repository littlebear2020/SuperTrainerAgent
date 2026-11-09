/**
 * 生成模型下拉的取值逻辑（Task 6，2026-08-01 反馈 8）。
 *
 * 可选值**由后端 `GET /api/video/providers` 动态给出**，不前端硬编码 ——
 * provider 可用性取决于 key 是否配置（`registry.py` 各分支均
 * `and config.xxx_api_key` 短路），未配 key 的（dashscope/kling）自动不出现。
 *
 * 依用户决策 6/7：下拉**只给模型名**，不渲染任何模型限制/警告文案。
 */

import type { ProviderAnnotations, ProviderInfo, VideoTaskType } from "./types";

export type ProviderOption = {
  value: string;
  label: string;
  /** E5（2026-08-21）：能力/风险标注（D5 口径：实测结论/未检测），与 label 分离渲染 */
  note: string;
};

/**
 * 「跟随后端默认」的哨兵值。
 *
 * Radix Select 不接受空串作为有效值，故用一个不会与真实 provider 名冲突的哨兵；
 * 提交时映射回 `null`（= 请求体不带 provider 字段）。
 */
export const DEFAULT_PROVIDER_VALUE = "__default__";

/**
 * 把后端返回的 provider 列表转成下拉选项。
 *
 * - 过滤掉 `available=false`（没配 key 的模型选了也只会失败）
 * - 空数组 = 调用方不应渲染下拉（没有可选模型）
 *
 * ⚠️ **默认项的 value 是哨兵而非模型名，这是有意的。**
 * 后端语义：不传 provider = 跟随 config 默认并**保留既有降级链**；
 * 显式传名字 = **禁用降级**（用户决策 4）。若默认项用真名做 value，
 * 用户「选了那个写着（默认）的项」就会悄悄关掉降级 —— 与他什么都不选的行为不同，
 * 而 UI 上看起来是同一件事。用哨兵可保证「每个模型只有一种含义」。
 */
export function buildProviderOptions(
  providers: readonly ProviderInfo[] | undefined,
): ProviderOption[] {
  if (!providers?.length) return [];
  const usable = providers.filter((p) => p.available);
  // 默认项排最前（用户最可能用它），其余保持后端给的顺序（已按推荐度排列）
  const sorted = [
    ...usable.filter((p) => p.is_default),
    ...usable.filter((p) => !p.is_default),
  ];
  return sorted.map((p) => ({
    value: p.is_default ? DEFAULT_PROVIDER_VALUE : p.name,
    label: p.is_default ? `${p.label}（默认）` : p.label,
    note: formatProviderNote(p.annotations),
  }));
}

/**
 * E5（2026-08-21）：把后端 annotations 标注拼成下拉二级文案。
 *
 * 口径（决策 D5）：实测结论 + notes 事实，用「·」分隔；tested 缺失按「未检测」
 * 兜底（不推断）；无 annotations（旧后端）返回空串——调用方据此不渲染二级行。
 */
export function formatProviderNote(
  annotations: ProviderAnnotations | undefined,
): string {
  if (!annotations) return "";
  const tested = (annotations.tested ?? "").trim() || "未检测";
  const notes = (annotations.notes ?? "").trim();
  return notes ? `${tested} · ${notes}` : tested;
}

/** 下拉选中值 → 请求体里的 provider 字段（哨兵映射回 null=不指定） */
export function providerValueToPayload(value: string | null): string | null {
  return !value || value === DEFAULT_PROVIDER_VALUE ? null : value;
}

/**
 * 该视频类型对应哪个 provider 字段；`null` = 不提供模型选择。
 *
 * - `remotion` 不走后端（前端直接 chat-handoff 给 course-video agent）；
 *   `remotion_course` 走后端任务管线（2026-09-08 T4），但其 provider 由
 *   config 默认链决定（与 vimax 同理不提供任务级覆盖）
 * - `vimax` 有自己的引擎链与内部 provider 路由，任务级覆盖会与之打架
 */
export function providerFieldForTaskType(
  taskType: VideoTaskType | string,
): "image" | "video" | null {
  if (taskType === "ai_image") return "image";
  if (taskType === "ai_video") return "video";
  return null;
}
