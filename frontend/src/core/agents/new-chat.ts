export const AGENT_NEW_CHAT_GUIDE_IDS = [
  "course-video",
  "ppt-master",
  "creative-studio",
  "co-writer",
] as const;

export type AgentNewChatGuideId = (typeof AGENT_NEW_CHAT_GUIDE_IDS)[number];

export function isAgentNewChatGuideId(
  value: string,
): value is AgentNewChatGuideId {
  return AGENT_NEW_CHAT_GUIDE_IDS.includes(value as AgentNewChatGuideId);
}
