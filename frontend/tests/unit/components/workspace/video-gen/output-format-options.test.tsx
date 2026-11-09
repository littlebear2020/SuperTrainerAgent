/**
 * Task 3（2026-08-01 反馈 6）：输出格式去掉 WebM。
 *
 * WebM 在后端**确有完整实现**（`_ffmpeg_helpers` libvpx-vp9 + libopus + CRF 映射），
 * 不是死代码。但：用途对用户不明、无人使用、且 VP9 重编码显著慢于 mp4
 * （mp4 出口只做 faststart 不重编码）。故**只删 UI 选项**。
 *
 * ⚠️ **schema `Literal` 与 worker 实现都保留** —— `CreateVideoRequest.output_format`
 * 若删掉 webm，存量草稿 payload 带 `output_format="webm"` 会 422 拒绝，
 * 用户视角是「我的草稿突然打不开」。后端保留是零成本的。
 *
 * GIF 按用户要求保留，但实读发现两个真实缺陷必须在 UI 披露（属已知取舍）：
 *   1. **GIF 无音频** —— gif 分支只做 palettegen/paletteuse，音轨被丢弃，
 *      而配音/BGM/字幕全流程照跑照烧钱；
 *   2. **GIF 无法在详情页播放** —— 详情页用 <video src>，<video> 标签不能渲染 GIF，
 *      选 GIF 的任务成功后详情页是空播放器（download 端点 MIME 是对的）。
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { OUTPUT_FORMAT_OPTIONS } from "@/core/video-gen/output-formats";

describe("Task 3: 输出格式选项", () => {
  it("只暴露 mp4 与 gif 两项，不含 webm", () => {
    const values = OUTPUT_FORMAT_OPTIONS.map((o) => o.value);
    expect(values).toEqual(["mp4", "gif"]);
    expect(values).not.toContain("webm");
  });

  it("GIF 项必须披露「无声」与「不支持在线播放」两个已知限制", () => {
    const gif = OUTPUT_FORMAT_OPTIONS.find((o) => o.value === "gif");
    expect(gif, "应保留 GIF 选项（用户要求）").toBeTruthy();
    // 两个缺陷都得说，否则用户会白烧一次配音钱再发现没声音
    expect(gif!.label, `GIF label 应披露无声：${gif!.label}`).toMatch(
      /无声|无音频/,
    );
    expect(gif!.label, `GIF label 应披露不能在线播放：${gif!.label}`).toMatch(
      /仅下载|不支持在线播放|无法播放/,
    );
  });

  it("MP4 是默认项且 label 不带警告文案", () => {
    const mp4 = OUTPUT_FORMAT_OPTIONS.find((o) => o.value === "mp4");
    expect(mp4, "应保留 MP4 选项").toBeTruthy();
    expect(OUTPUT_FORMAT_OPTIONS[0]?.value, "MP4 应排在首位（默认）").toBe(
      "mp4",
    );
    expect(mp4!.label).not.toMatch(/无声|仅下载/);
  });

  it("渲染成 markup 后含 MP4 与 GIF、不含 WebM", () => {
    const html = renderToStaticMarkup(
      <select>
        {OUTPUT_FORMAT_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>,
    );
    expect(html).toMatch(/MP4/);
    expect(html).toMatch(/GIF/);
    expect(html).not.toMatch(/WebM/i);
  });
});
