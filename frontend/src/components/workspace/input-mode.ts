/**
 * 输入模式纯函数（2026-08-27 缺陷 1 解耦）：「模型思考能力」只门控「思考」档，
 * Pro/Ultra（agent 行为能力）对所有模型开放。
 *
 * 旧 getResolvedMode 把不支持思考的模型上的一切非 flash 档（含 pro/ultra）全部
 * 弹回 flash——Pro 的 TodoList 规划、Ultra 的子代理协作与模型是否支持 thinking
 * 无关，被一并误伤。新 resolveMode 只保留唯一一处弹回：显式选「思考」档但模型
 * 不支持时落 flash。
 *
 * 后端契约（不改）：hooks.ts 把 mode 翻译成 thinking_enabled/is_plan_mode/
 * subagent_enabled；agent.py 对不支持思考的模型强制关 thinking_enabled（warning），
 * factory.py pop 掉不支持的 reasoning_effort——解耦后行为安全。
 */

export type InputMode = "flash" | "thinking" | "pro" | "ultra";

export type ReasoningEffort = "minimal" | "low" | "medium" | "high";

export function resolveMode(
  mode: InputMode | undefined,
  supportsThinking: boolean,
): InputMode {
  // 唯一保留的弹回：显式「思考」档 + 模型不支持 → flash（T25 置灰行为的语义来源）
  if (mode === "thinking" && !supportsThinking) {
    return "flash";
  }
  if (mode) {
    // flash/pro/ultra 原样返回——pro/ultra 不再被 supportsThinking 门控（缺陷 1 修复）
    return mode;
  }
  // 无持久化 mode：维持现有默认（支持思考 → Pro，否则闪速）
  return supportsThinking ? "pro" : "flash";
}

export function reasoningEffortForMode(mode: InputMode): ReasoningEffort {
  switch (mode) {
    case "ultra":
      return "high";
    case "pro":
      return "medium";
    case "thinking":
      return "low";
    case "flash":
      return "minimal";
  }
}
