import { describe, expect, it } from "vitest";

import {
  computeRecordedSeconds,
  extFromMimeType,
} from "@/components/workspace/voice-twin/voice-recorder";

describe("extFromMimeType", () => {
  it("maps Safari audio/mp4 to m4a (backend rejects mp4)", () => {
    expect(extFromMimeType("audio/mp4")).toBe("m4a");
  });
  it("keeps webm/ogg", () => {
    expect(extFromMimeType("audio/webm;codecs=opus")).toBe("webm");
    expect(extFromMimeType("audio/ogg;codecs=opus")).toBe("ogg");
  });
});

describe("computeRecordedSeconds", () => {
  it("no pause: duration equals wall clock", () => {
    expect(computeRecordedSeconds(100_000, 115_000, 0, 0)).toBe(15);
  });

  it("pause→resume→stop: settled pause deducted", () => {
    // 15s 墙钟，3s 已结算暂停 → 12s
    expect(computeRecordedSeconds(1_000, 16_000, 3_000, 0)).toBe(12);
  });

  it("pause→stop (unsettled): open pause segment settled at stop", () => {
    // 15s 墙钟，12s 处按下暂停未恢复，16s 直接停止 → 11s（曾把 4s 暂停段计入）
    expect(computeRecordedSeconds(1_000, 16_000, 0, 12_000)).toBe(11);
  });

  it("multiple pauses: settled + open segments both deducted", () => {
    // 15s 墙钟，已结算 5s + 未结算 2s（14s 暂停，16s 停止）→ 8s
    expect(computeRecordedSeconds(1_000, 16_000, 5_000, 14_000)).toBe(8);
  });

  it("clamps at 0 when pauses exceed wall clock", () => {
    expect(computeRecordedSeconds(1_000, 2_000, 5_000, 0)).toBe(0);
  });
});
