/** 退役 agent 别名（2026-09-09 创意工作合并）：旧 5 壳名 → creative-studio。
 *  2026-09-11 ppt-master 6.3.0 批次：course-ppt 完全退役 → ppt-master
 *  （快出课件=quick-generate；精品/存量改造=default/roundtrip）。
 *  旧线程/旧 URL 的唯一续跑通路；随 0.6 IA 定案后清理。 */
export const RETIRED_AGENT_ALIAS: Record<string, string> = {
  "card-poster": "creative-studio",
  "case-article": "creative-studio",
  "design-specialist": "creative-studio",
  "knowledge-extraction": "creative-studio",
  "video-clip": "creative-studio",
  "course-ppt": "ppt-master",
};

export function resolveAgentName(name: string): string {
  return RETIRED_AGENT_ALIAS[name] ?? name;
}
