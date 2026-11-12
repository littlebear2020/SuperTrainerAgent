import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { AudioLines, MessageSquare } from "lucide-react";
import { describe, expect, it } from "vitest";

import {
  AGENT_APPEARANCE,
  AGENT_APPEARANCE_FALLBACK,
  agentAppearance,
  agentBadge,
} from "@/core/agents/appearance";

describe("podcast agent appearance", () => {
  it("pins a distinct podcast icon and colors without changing fallback", () => {
    expect(AGENT_APPEARANCE["podcast-studio"]).toEqual({
      icon: AudioLines,
      bg: "bg-cyan-100 dark:bg-cyan-500/10",
      text: "text-cyan-700 dark:text-cyan-300",
    });
    expect(agentAppearance("podcast-studio").icon).toBe(AudioLines);
    expect(agentBadge("podcast-studio")).toBe(
      "bg-cyan-100 dark:bg-cyan-500/10 text-cyan-700 dark:text-cyan-300",
    );
    expect(agentAppearance("unknown-agent").icon).toBe(MessageSquare);
    expect(AGENT_APPEARANCE_FALLBACK.icon).toBe(MessageSquare);
  });

  it("ships a VP8 960×540 podcast cover", () => {
    const path = resolve(
      process.cwd(),
      "public/course-samples/podcast-work.webp",
    );
    expect(existsSync(path)).toBe(true);
    if (!existsSync(path)) return;
    const data = readFileSync(path);
    expect(data.toString("ascii", 0, 4)).toBe("RIFF");
    expect(data.toString("ascii", 8, 16)).toBe("WEBPVP8 ");
    expect(data.readUIntBE(23, 3)).toBe(0x9d012a);
    expect({
      width: data.readUInt16LE(26) & 0x3fff,
      height: data.readUInt16LE(28) & 0x3fff,
    }).toEqual({ width: 960, height: 540 });
  });
});
