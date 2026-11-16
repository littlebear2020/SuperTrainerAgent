import { describe, expect, it } from "vitest";

import { matchAgent } from "@/components/workspace/workspace-home";

// 2026-11-16 收敛：creative-studio 只承接视觉生图关键词；剪辑与知识萃取
// 不再路由到它（无命中返回 null）。

describe("matchAgent — chat routing keywords", () => {
  it("routes story/narrative video requests to vimax-video (2026-07-15 split)", () => {
    // 故事/剧情/叙事 videos route to the vimax-video agent (ViMax backend engine);
    // course-video is now Remotion direct-write (讲解/科普, no story/character).
    expect(matchAgent("做个故事视频")).toBe("vimax-video");
    expect(matchAgent("帮我做一个剧情短片")).toBe("vimax-video");
    expect(matchAgent("我想生成一个叙事微电影")).toBe("vimax-video");
    expect(matchAgent("做个有角色的动画故事")).toBe("vimax-video");
  });

  it("still routes the original course-video (讲解/科普) keywords", () => {
    expect(matchAgent("制作视频课程")).toBe("course-video");
    expect(matchAgent("做个培训视频")).toBe("course-video");
  });

  it("routes PPT/课件 requests to ppt-master (course-ppt keywords absorbed, 09-11 retirement)", () => {
    expect(matchAgent("制作一个 PPT 课件")).toBe("ppt-master");
    expect(matchAgent("做一套幻灯片")).toBe("ppt-master");
    expect(matchAgent("帮我写演示文稿")).toBe("ppt-master");
  });

  it("no longer routes clip/editing or knowledge-extraction requests (2026-11-16 convergence)", () => {
    expect(matchAgent("剪辑视频")).toBeNull();
    expect(matchAgent("视频剪辑一下")).toBeNull();
    expect(matchAgent("提取精华片段")).toBeNull();
    expect(matchAgent("知识萃取这份访谈材料")).toBeNull();
    expect(matchAgent("经验提炼")).toBeNull();
  });

  it("routes visual requests to creative-studio (视觉生图入口)", () => {
    expect(matchAgent("设计一张知识卡片")).toBe("creative-studio");
    expect(matchAgent("做一张宣传海报")).toBe("creative-studio");
    expect(matchAgent("做一张信息图")).toBe("creative-studio");
  });

  it("routes case/article writing requests to co-writer", () => {
    expect(matchAgent("写一篇案例分析")).toBe("co-writer");
    expect(matchAgent("帮我润色这段文字")).toBe("co-writer");
  });

  it("多规则同时命中时按数组顺序取先（ROUTING_RULES 顺序即优先级）", () => {
    // 「剪辑培训视频」命中 course-video 的「培训视频」（规则序 1）；creative-studio
    // 已移除「剪辑」关键词，本用例继续钉扎顺序语义，防止未来重排引入静默行为变化。
    expect(matchAgent("剪辑培训视频")).toBe("course-video");
    // 「把这份案例文章做成课件」同时命中 ppt-master 的「课件」（规则序 3）
    // 与 co-writer 的「案例文章」（规则序 5）——取先者。
    expect(matchAgent("把这份案例文章做成课件")).toBe("ppt-master");
  });

  it("falls back to null (course-assistant retired 2026-11-16.2) for unmatched input", () => {
    expect(matchAgent("随便聊聊")).toBeNull();
  });

  it("never routes to retired agents (course-ppt / 五壳)", () => {
    expect(matchAgent("视频剪辑")).not.toBe("video-clip");
    expect(matchAgent("案例分析")).not.toBe("case-article");
    expect(matchAgent("知识萃取")).not.toBe("knowledge-extraction");
    expect(matchAgent("制作一个 PPT 课件")).not.toBe("course-ppt");
  });
});
