/** graphic-native-text（2026-08-17）：选项集 = ai_image/ai_video；graphic/remotion 仅存量展示。 */
import { expect, test } from "vitest";

import { visualSourceOptions } from "@/components/workspace/video-gen/visual-source-switcher";

test("默认两选：AI 生图 / AI 生视频（graphic 已随卡片渲染退役）", () => {
  const opts = visualSourceOptions(undefined);
  expect(opts.map((o) => o.value)).toEqual(["ai_image", "ai_video"]);
  expect(opts.map((o) => o.label)).toEqual(["AI 生图", "AI 生视频"]);
});

test("存量值（graphic/remotion）仅追加只读展示项，不提供新选", () => {
  const g = visualSourceOptions("graphic");
  expect(g.map((o) => o.value)).toEqual(["graphic", "ai_image", "ai_video"]);
  expect(g[0]).toEqual({ value: "graphic", label: "图文镜（图内文字）" });
  const r = visualSourceOptions("remotion");
  expect(r[0]).toEqual({ value: "remotion", label: "文字动效（旧）" });
});

test("普通当前值不追加存量项", () => {
  expect(visualSourceOptions("ai_image")).toHaveLength(2);
});
