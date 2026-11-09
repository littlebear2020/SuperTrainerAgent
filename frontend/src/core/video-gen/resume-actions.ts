/**
 * 「重试合成/重新合成」按钮状态（详情页头部/分镜条 + 列表卡片 + 分镜编辑器共用）。
 *
 * 2026-07-22:failed 态豁免 dirty_frames 门控——渲染失败的合成从未完成,
 * dirty_frames 为空是因为用户还没编辑,不是没有工作要做(修前 failed+无编辑
 * → 按钮 disabled,任务只剩删除一条路,盘上已生成视觉全浪费,vg_f74c8fa2331a)。
 *
 * - failed:合成未完成 → 恒可点「重试合成」(recompose 复用盘面素材断点续跑);
 * - ready:有编辑才需要重跑 → dirty_frames 空时 disabled(旧行为);
 * - 其余状态(draft/running/composing/pending):不展示;
 * - vimax/remotion:非 segments 管线,recompose 不支持 → 不展示。
 */

export interface RecomposeAction {
  /** 是否展示按钮 */
  visible: boolean;
  /** 是否禁用 */
  disabled: boolean;
  /** 按钮文案 */
  label: string;
}

export function recomposeActionFor(
  task: {
    status: string;
    task_type: string;
    dirty_frames?: number[] | null;
  },
  opts?: { subtitleLocked?: boolean },
): RecomposeAction {
  const segments = task.task_type !== "vimax" && task.task_type !== "remotion";
  if (!segments || (task.status !== "failed" && task.status !== "ready")) {
    return { visible: false, disabled: true, label: "" };
  }
  if (task.status === "failed") {
    return { visible: true, disabled: false, label: "重试合成" };
  }
  const dirty = (task.dirty_frames ?? []).length;
  // 2026-07-28：字幕手改置 subtitle_locked 不产 dirty_frames——
  // 锁也是「有待合成变更」的信号，否则新字幕烧不进成片（T15 最后一环）。
  return {
    visible: true,
    disabled: dirty === 0 && !opts?.subtitleLocked,
    label: "重新合成",
  };
}
