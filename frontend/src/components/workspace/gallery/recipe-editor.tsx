"use client";

/**
 * 灵感库 Recipe 编辑器（2026-10-06 近重复合并批，单一事实源）。
 * 库内编辑使用 goal/steps 精简受控输入。
 */

import type { Recipe } from "@/core/gallery/api";

export interface RecipeEditorProps {
  recipe: Recipe;
  onChange: (recipe: Recipe) => void;
}

export function RecipeEditor({ recipe, onChange }: RecipeEditorProps) {
  return (
    <div className="rounded-lg border border-indigo-200/50 bg-indigo-50/50 p-3 text-xs dark:border-indigo-500/20 dark:bg-indigo-500/5">
      <input
        type="text"
        value={recipe.goal}
        onChange={(e) => onChange({ ...recipe, goal: e.target.value })}
        className="w-full rounded border border-transparent bg-transparent font-medium text-indigo-700 focus:border-indigo-300 focus:outline-none dark:text-indigo-300"
        placeholder="一句话目标"
      />
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
        </div>
      ))}
    </div>
  );
}
