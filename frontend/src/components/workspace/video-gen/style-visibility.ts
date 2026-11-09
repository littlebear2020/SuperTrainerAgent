/**
 * 风格下拉的可见性判定（2026-08-03 Task 6）。
 *
 * 风格在后端是「生成提示词的中文前缀」（style_presets.py STYLE_PREFIXES），
 * 只对走 AI 生成的类型有意义。remotion 由 agent 直接写 React 代码渲染，
 * 无生图/生视频步骤 —— orchestration/storyboard_planner.py 对 remotion 直接 return，
 * 风格前缀无处注入。继续展示只会让用户选一个静默失效的旋钮。
 *
 * T1（2026-08-18）：返回值升级为**类型谓词**——true 分支同时把 taskType 收窄成
 * 「排除 remotion」的联合，调用方（任务级风格下拉）可直接把收窄结果传给
 * styleKeysForTaskType；「remotion 不进分派函数」由本层在类型层一并排除。
 *
 * 2026-09-09：chat-handoff 的 "remotion" 类型已归一删除、并入 "remotion_course"
 * （同为模板渲染管线、无生图步骤），谓词同步排除两者——此前只排 "remotion"，
 * 导致 remotion_course 下风格下拉残留（静默失效旋钮，与上方设计意图相悖）。
 */
import type { VideoTaskType } from "@/core/video-gen/types";

export function shouldShowStyleSelector(
  taskType: string,
): taskType is Exclude<VideoTaskType, "remotion" | "remotion_course"> {
  return taskType !== "remotion" && taskType !== "remotion_course";
}
