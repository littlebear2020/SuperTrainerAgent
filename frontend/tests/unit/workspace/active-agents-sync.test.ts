import { readdirSync } from "fs";
import { join } from "path";

import { describe, expect, it } from "vitest";

import { WORKFLOWS } from "@/components/workspace/workflow-grid";
import { ROUTING_RULES } from "@/components/workspace/workspace-home";

/**
 * R1（僵尸卡）自动守卫（review 2026-09-22 五-1）：工作台的卡片与关键词路由
 * 是硬编码清单，历史上仅靠人工核对 + e2e 六卡断言防漂移——下次 agent 合并/
 * 退役时会静默漂移。本测试直读 backend/seed/agents 目录（跨仓源码级守卫，
 * 模式沿用 tests/unit/core/video-gen/detail-warning-fields.test.ts 先例），
 * 把「清单与后端现役集一致」固化为断言。
 */
const SEED_AGENTS_DIR = join(__dirname, "../../../..", "backend/seed/agents");

function listSeedAgents(): string[] {
  return readdirSync(SEED_AGENTS_DIR, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort();
}

describe("工作台卡片/路由与 seed 现役 agent 同步（R1 跨仓守卫）", () => {
  it("WORKFLOWS 卡片类型集合 === backend/seed/agents 目录集合", () => {
    const cardTypes = WORKFLOWS.map((wf) => wf.type).sort();

    expect(cardTypes).toEqual(listSeedAgents());
  });

  it("ROUTING_RULES 目标与兜底 agent 均在现役集合内", () => {
    const active = listSeedAgents();

    for (const rule of ROUTING_RULES) {
      expect(active).toContain(rule.agent);
    }
    // startFree 的兜底硬编码 course-assistant（非 ROUTING_RULES 成员），
    // 同样必须在役。
    expect(active).toContain("course-assistant");
  });
});
