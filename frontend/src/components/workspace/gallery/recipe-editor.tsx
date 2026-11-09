"use client";

/**
 * 灵感库 Recipe 编辑器（2026-10-06 近重复合并批，单一事实源）。
 *
 * 分享弹窗（messages/share-button）与灵感库编辑表单（gallery/page）原本
 * 各持一份 goal/steps 受控输入。收敛为共享组件；分享版额外展示
 * 方法论行、步骤 tip 与避坑指南（showMethodologyAndPitfalls），
 * 库内编辑版为精简输入形态。
 */

import type { Recipe } from "@/core/gallery/api";

export interface RecipeEditorProps {
  recipe: Recipe;
  onChange: (recipe: Recipe) => void;
  /** 分享弹窗形态：展示方法论/步骤 tip/避坑指南；库内编辑默认精简。 */
  showMethodologyAndPitfalls?: boolean;
}

export function RecipeEditor({
  recipe,
  onChange,
  showMethodologyAndPitfalls = false,
}: RecipeEditorProps) {
  return (
    <div className="rounded-lg border border-indigo-200/50 bg-indigo-50/50 p-3 text-xs dark:border-indigo-500/20 dark:bg-indigo-500/5">
      <input
        type="text"
        value={recipe.goal}
        onChange={(e) => onChange({ ...recipe, goal: e.target.value })}
        className="w-full rounded border border-transparent bg-transparent font-medium text-indigo-700 focus:border-indigo-300 focus:outline-none dark:text-indigo-300"
        placeholder="一句话目标"
      />
      {showMethodologyAndPitfalls && (
        <p className="text-muted-foreground mt-1">
          方法论：{recipe.approach ?? "通用"} · {recipe.est_steps} 步
        </p>
      )}
      {recipe.steps.map((step, i) => (
        <div
          key={i}
          className="mt-2 border-t border-indigo-100 pt-2 dark:border-indigo-500/10"
        >
          <input
            type="text"
            value={step.instruction}
            onChange={(e) => {
              const next = [...recipe.steps];
              next[i] = { ...step, instruction: e.target.value };
              onChange({ ...recipe, steps: next });
            }}
            className="w-full rounded border border-transparent bg-transparent font-medium focus:border-indigo-300 focus:outline-none"
          />
          {showMethodologyAndPitfalls && step.tip && (
            <p className="text-muted-foreground mt-0.5">💡 {step.tip}</p>
          )}
        </div>
      ))}
      {showMethodologyAndPitfalls && recipe.pitfalls.length > 0 && (
        <div className="mt-2 border-t border-indigo-100 pt-2 dark:border-indigo-500/10">
          <p className="font-medium">⚠️ 避坑指南</p>
          {recipe.pitfalls.map((p, i) => (
            <p key={i} className="text-muted-foreground">
              · {p}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
