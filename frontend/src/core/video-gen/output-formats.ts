/**
 * 输出格式选项（Task 3，2026-08-01 反馈 6）。
 *
 * **WebM 已从 UI 移除**：后端确有完整实现（libvpx-vp9 + libopus），但用途对用户
 * 不明、无人使用，且 VP9 重编码显著慢于 mp4 出口（后者只做 faststart 不重编码）。
 *
 * ⚠️ **后端 schema 的 `Literal` 与 worker 实现都保留，不要跟着删** ——
 * `CreateVideoRequest.output_format` 若删掉 webm，存量草稿 payload 带
 * `output_format="webm"` 会被 422 拒绝，用户视角是「我的草稿突然打不开」。
 * 前端不再产生 webm 即可，后端保留是零成本的向后兼容。
 */

import type { CreateVideoRequest } from "./types";

/** UI 可选的输出格式（`types.ts` 的 Literal 仍含 webm 以兼容存量草稿） */
export type UiOutputFormat = "mp4" | "gif";

export type OutputFormatOption = {
  value: UiOutputFormat;
  label: string;
};

/**
 * GIF 保留（用户要求），但**必须披露两个实读确认的限制**，否则用户会先白烧一次
 * 配音/BGM 的钱、再发现成片没声音也放不出来：
 *   1. 无音频：gif 分支只做 palettegen/paletteuse，音轨被丢弃 ——
 *      而配音、BGM、字幕全流程照跑照计费；
 *   2. 不支持在线播放：详情页用 `<video src>`，`<video>` 标签渲染不了 GIF，
 *      成功后详情页是空播放器（download 端点的 MIME 是正确的 image/gif）。
 */
export const OUTPUT_FORMAT_OPTIONS: readonly OutputFormatOption[] = [
  { value: "mp4", label: "MP4 视频" },
  { value: "gif", label: "GIF 动图（无声，仅下载不支持在线播放）" },
] as const;

/** 类型护栏：UI 值必须是后端 schema 允许值的子集 */
type _AssertSubset =
  UiOutputFormat extends NonNullable<CreateVideoRequest["output_format"]>
    ? true
    : never;
const _assertSubset: _AssertSubset = true;
void _assertSubset;
