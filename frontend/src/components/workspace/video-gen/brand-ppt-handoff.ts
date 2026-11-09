/**
 * 课程项目品牌 → ppt-master chat handoff（Phase 3.5c，2026-09-11）。
 *
 * 消费侧反向打通：把 CourseProject 品牌包字段（remotion_theme/主色/字体/LOGO）
 * 组装成自然语言提示词，跳转 ppt-master 新会话——agent 按提示词把品牌写进
 * design spec（与 remotion_course 的品牌解析同字段同源：一套品牌，PPT/视频
 * 双产物）。纯函数便于单测；跳转复用既有 `?msg=`/sessionStorage 预填通路。
 */

import { PENDING_PROMPT_KEY } from "@/lib/pending-prompt";

export const URL_MSG_MAX_ENCODED = 4000;

export const PPT_MASTER_CHAT_TARGET = "/workspace/agents/ppt-master/chats/new";

export const BRAND_THEME_LABELS: Record<string, string> = {
  "dark-blue": "深空蓝",
  light: "明亮浅色",
  "dark-green": "墨绿沉稳",
  warm: "暖色纸感",
};

export type BrandPptPromptInput = {
  projectTitle: string;
  remotionTheme: string | null;
  primaryColor: string | null;
  fontFamily: string | null;
  hasLogo: boolean;
};

/** Compose the brand-instruction prompt handed to the ppt-master agent. */
export function composeBrandPptPrompt(input: BrandPptPromptInput): string {
  const lines: string[] = [
    "请用以下课程项目品牌生成 PPT（品牌字段与课程视频管线同源，请在 Strategist 阶段把它们写入 design spec 的 colors/typography 约定，并全程遵守）：",
    "",
    "【课程项目品牌】",
    `- 项目：${input.projectTitle}`,
  ];
  if (input.remotionTheme) {
    const label =
      BRAND_THEME_LABELS[input.remotionTheme] ?? input.remotionTheme;
    lines.push(
      `- 视觉主题：${label}（${input.remotionTheme}）——整体明暗与氛围向该主题对齐`,
    );
  }
  if (input.primaryColor) {
    lines.push(
      `- 品牌主色：${input.primaryColor}（标题强调/关键图形使用，配色围绕它展开）`,
    );
  }
  if (input.fontFamily) {
    lines.push(`- 品牌字体：${input.fontFamily}（标题与正文沿用该字族）`);
  }
  if (input.hasLogo) {
    lines.push(
      "- 品牌 LOGO：课程项目已配置（封面与页脚角标位置使用；若我未随消息上传 logo 文件，请提醒我先上传）",
    );
  }
  lines.push(
    "",
    "请先确认内容大纲（两段式确认），再逐页手写 SVG 生成原生可编辑 PPTX。",
  );
  return lines.join("\n");
}

export type RouterLike = { push: (url: string) => void };

/**
 * Jump to the ppt-master chat with the composed prompt (url or sessionStorage).
 *
 * 短提示词走 `?msg=`（仅预填）；超长走 sessionStorage（消费端 readPendingPrompt）。
 * storage 不可用（禁用/隐私模式）时截断到 URL 上限跳转——截断残句仍优于整段
 * 静默丢失（修前裸跳却仍返回 "url"，返回值与实际行为不符）。
 */
export function jumpToBrandPptChat(
  prompt: string,
  router: RouterLike,
  storage: Storage | null = null,
): "url" | "sessionStorage" {
  if (encodeURIComponent(prompt).length <= URL_MSG_MAX_ENCODED) {
    router.push(`${PPT_MASTER_CHAT_TARGET}?msg=${encodeURIComponent(prompt)}`);
    return "url";
  }
  if (storage) {
    storage.setItem(
      PENDING_PROMPT_KEY,
      JSON.stringify({ prompt, autoSend: false }),
    );
    router.push(PPT_MASTER_CHAT_TARGET);
    return "sessionStorage";
  }
  let payload = prompt.slice(0, URL_MSG_MAX_ENCODED); // ASCII 上界；CJK 逐轮收缩
  while (payload && encodeURIComponent(payload).length > URL_MSG_MAX_ENCODED) {
    payload = payload.slice(0, Math.floor(payload.length * 0.8));
  }
  router.push(`${PPT_MASTER_CHAT_TARGET}?msg=${encodeURIComponent(payload)}`);
  return "url";
}
