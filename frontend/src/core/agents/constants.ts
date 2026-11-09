/**
 * 历史常量（2026-09-22 工作台恢复后当前零消费，保留备查；删除属另一决策）。
 *
 * 沿革：09-07 /workspace 曾 307 重定向到默认 agent 新会话页，09-12（条目 30，
 * f143bbf83）将重定向目标与 workspace-header 高亮前缀抽到此单一事实源。
 * 2026-09-22 工作台恢复后 /workspace 直接渲染九宫格首页、header 高亮回退
 * 精确匹配，三个导出不再被引用。course-assistant 作为现役 agent 常驻卡片
 * 网格，其 landing/其他引用方均为各自硬编码，与本文件无关。
 */
export const DEFAULT_AGENT_NAME = "course-assistant";

/** （历史）/workspace 重定向目标（默认 agent 的新会话页）。 */
export const WORKSPACE_DEFAULT_REDIRECT = `/workspace/agents/${DEFAULT_AGENT_NAME}/chats/new`;

/** （历史）Dashboard 高亮命中的默认 agent 路由前缀。 */
export const DEFAULT_AGENT_ROUTE_PREFIX = `/workspace/agents/${DEFAULT_AGENT_NAME}`;
