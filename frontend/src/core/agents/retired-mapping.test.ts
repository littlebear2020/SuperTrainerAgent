/**
 * retired-mapping 守卫（条目 25，2026-09-12 缺陷批）：修前 resolveAgentName/
 * RETIRED_AGENT_ALIAS 有 4 个产品文件引用（agents/[agent_name]/chats 页、
 * agent-card、threads/utils、project-list）+ 1 处桶式再导出，但全前端测试零引用。
 * 后端同款映射见 backend/app/gateway/_shared/retired_agents.py（跨仓一致性由
 * backend/tests/test_retired_agent_alias.py 解析本文件源码守护）。
 */
import { describe, expect, it } from "vitest";

import { RETIRED_AGENT_ALIAS, resolveAgentName } from "./retired-mapping";

describe("retired-mapping", () => {
  it("course-ppt → ppt-master（2026-09-11 ppt-master 6.3.0 批次退役承接）", () => {
    expect(RETIRED_AGENT_ALIAS["course-ppt"]).toBe("ppt-master");
    expect(resolveAgentName("course-ppt")).toBe("ppt-master");
  });

  it("五壳 → creative-studio（2026-09-09 创意工作合并）全键值", () => {
    for (const shell of [
      "card-poster",
      "case-article",
      "design-specialist",
      "knowledge-extraction",
      "video-clip",
    ]) {
      expect(RETIRED_AGENT_ALIAS[shell]).toBe("creative-studio");
      expect(resolveAgentName(shell)).toBe("creative-studio");
    }
  });

  it("未知名原样返回（现役 agent 与任意输入不受影响）", () => {
    expect(resolveAgentName("ppt-master")).toBe("ppt-master");
    expect(resolveAgentName("creative-studio")).toBe("creative-studio");
    expect(resolveAgentName("course-assistant")).toBe("course-assistant");
    expect(resolveAgentName("lead_agent")).toBe("lead_agent");
  });
});
