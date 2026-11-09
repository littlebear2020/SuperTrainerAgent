/**
 * 字幕编辑校验 + dirty 比较 + 样式常量（2026-07-31 T7-T12）。
 *
 * 校验口径必须与后端 routers/video_gen.py 的 _srt_ts_to_seconds 一致：
 * 格式 HH:MM:SS,mmm + mm/ss < 60 + start < end。
 */
import { describe, expect, it } from "vitest";

import {
  isValidSubtitleTs,
  subtitleCueIssues,
  subtitleCuesEqual,
  subtitleCuesHaveIssues,
  subtitleTsToSeconds,
} from "@/components/workspace/video-gen/video-gen-detail";
import {
  SUBTITLE_STYLES,
  isSubtitleStyle,
  subtitleStyleLabel,
  subtitleStylePreviewClass,
} from "@/core/video-gen/subtitle-styles";

describe("isValidSubtitleTs", () => {
  it("accepts well-formed timestamps", () => {
    expect(isValidSubtitleTs("00:00:00,000")).toBe(true);
    expect(isValidSubtitleTs("01:59:59,999")).toBe(true);
  });

  it("rejects malformed or out-of-range timestamps (后端同口径)", () => {
    expect(isValidSubtitleTs("99:99:99,999")).toBe(false); // mm/ss ≥ 60
    expect(isValidSubtitleTs("00:00:00")).toBe(false); // 缺毫秒
    expect(isValidSubtitleTs("0:00:00,000")).toBe(false); // 位数不足
    expect(isValidSubtitleTs("00:60:00,000")).toBe(false);
    expect(isValidSubtitleTs("00:00:60,000")).toBe(false);
    expect(isValidSubtitleTs("")).toBe(false);
    expect(isValidSubtitleTs("abc")).toBe(false);
  });
});

describe("subtitleTsToSeconds", () => {
  it("parses to seconds", () => {
    expect(subtitleTsToSeconds("00:00:02,500")).toBe(2.5);
    expect(subtitleTsToSeconds("01:01:01,123")).toBeCloseTo(3661.123, 3);
  });

  it("returns null for invalid input", () => {
    expect(subtitleTsToSeconds("99:99:99,999")).toBeNull();
  });
});

describe("subtitleCueIssues", () => {
  it("flags nothing for a valid cue", () => {
    const iss = subtitleCueIssues({
      start: "00:00:00,000",
      end: "00:00:02,000",
      text: "你好",
    });
    expect(iss).toEqual({ start: false, end: false, text: false });
  });

  it("flags start >= end on both timestamp fields", () => {
    const iss = subtitleCueIssues({
      start: "00:00:03,000",
      end: "00:00:01,000",
      text: "你好",
    });
    expect(iss.start).toBe(true);
    expect(iss.end).toBe(true);
  });

  it("flags equal start/end (后端要求严格 start < end)", () => {
    const iss = subtitleCueIssues({
      start: "00:00:01,000",
      end: "00:00:01,000",
      text: "你好",
    });
    expect(iss.start).toBe(true);
  });

  it("flags empty / whitespace-only text", () => {
    expect(
      subtitleCueIssues({
        start: "00:00:00,000",
        end: "00:00:01,000",
        text: "",
      }).text,
    ).toBe(true);
    expect(
      subtitleCueIssues({
        start: "00:00:00,000",
        end: "00:00:01,000",
        text: "   ",
      }).text,
    ).toBe(true);
  });

  it("flags text over 200 chars (后端 max_length=200)", () => {
    expect(
      subtitleCueIssues({
        start: "00:00:00,000",
        end: "00:00:01,000",
        text: "x".repeat(201),
      }).text,
    ).toBe(true);
    expect(
      subtitleCueIssues({
        start: "00:00:00,000",
        end: "00:00:01,000",
        text: "x".repeat(200),
      }).text,
    ).toBe(false);
  });
});

describe("subtitleCuesHaveIssues", () => {
  const good = { start: "00:00:00,000", end: "00:00:01,000", text: "你好" };

  it("is false when every cue is valid", () => {
    expect(subtitleCuesHaveIssues([good, { ...good, text: "第二句" }])).toBe(
      false,
    );
  });

  it("is true when any cue is invalid (保存按钮据此禁用，避免 422 往返)", () => {
    expect(subtitleCuesHaveIssues([good, { ...good, text: "" }])).toBe(true);
    expect(
      subtitleCuesHaveIssues([good, { ...good, start: "99:99:99,999" }]),
    ).toBe(true);
  });

  it("is false for an empty list (按钮另有 cues.length === 0 门禁)", () => {
    expect(subtitleCuesHaveIssues([])).toBe(false);
  });
});

describe("subtitleCuesEqual (T11 dirty baseline)", () => {
  const a = [
    { start: "00:00:00,000", end: "00:00:01,000", text: "一" },
    { start: "00:00:01,000", end: "00:00:02,000", text: "二" },
  ];

  it("is true for identical content", () => {
    expect(
      subtitleCuesEqual(
        a,
        a.map((c) => ({ ...c })),
      ),
    ).toBe(true);
  });

  it("detects text edits", () => {
    const b = a.map((c) => ({ ...c }));
    b[1]!.text = "改过";
    expect(subtitleCuesEqual(a, b)).toBe(false);
  });

  it("detects timestamp edits (T9 让时间戳也可改)", () => {
    const b = a.map((c) => ({ ...c }));
    b[0]!.end = "00:00:01,500";
    expect(subtitleCuesEqual(a, b)).toBe(false);
  });

  it("detects length changes", () => {
    expect(subtitleCuesEqual(a, a.slice(0, 1))).toBe(false);
  });

  it("treats empty vs empty as equal (首渲染 baseline 空 → 不应恒 dirty)", () => {
    expect(subtitleCuesEqual([], [])).toBe(true);
  });
});

describe("subtitle style shared constants (T7 单一来源)", () => {
  it("exposes the five styles the backend accepts", () => {
    expect(SUBTITLE_STYLES.map((s) => s.value)).toEqual([
      "default",
      "bold",
      "minimal",
      "outline",
      "none",
    ]);
  });

  it("validates known values only", () => {
    expect(isSubtitleStyle("bold")).toBe(true);
    expect(isSubtitleStyle("none")).toBe(true);
    expect(isSubtitleStyle("nope")).toBe(false);
    expect(isSubtitleStyle(undefined)).toBe(false);
  });

  it("labels in Chinese instead of raw enum values", () => {
    expect(subtitleStyleLabel("default")).toBe("标准");
    expect(subtitleStyleLabel("none")).toBe("无字幕");
    expect(subtitleStyleLabel(undefined)).toBe("标准");
  });

  it("echoes unknown values rather than swallowing them", () => {
    expect(subtitleStyleLabel("future_style")).toBe("future_style");
  });

  it("provides a preview class for every style (T12)", () => {
    for (const s of SUBTITLE_STYLES) {
      expect(subtitleStylePreviewClass(s.value)).toBe(s.previewClassName);
      expect(s.previewClassName.length).toBeGreaterThan(0);
    }
    expect(subtitleStylePreviewClass("unknown")).toBe(
      SUBTITLE_STYLES[0].previewClassName,
    );
  });
});
