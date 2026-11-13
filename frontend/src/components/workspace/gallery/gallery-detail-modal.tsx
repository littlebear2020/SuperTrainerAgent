"use client";

/**
 * 2026-10-06 拆分批：灵感详情弹窗（查看 + 编辑表单 + 删除 + 复刻入口）。
 * 2026-10-07 文件级拆分批：自 gallery/page.tsx 逐字上提为本文件（模板变量
 * 表单组件 FormField 随迁，保持文件内私有不导出）。
 */

import { Heart, Sparkles, Wand2, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import type { useGalleryPageState } from "@/app/workspace/gallery/use-gallery-page-state";
import { type VarConfig } from "@/core/gallery/api";
import { cn } from "@/lib/utils";

import { RecipeEditor } from "./recipe-editor";
import { SourceBadge } from "./source-badge";
import { ThumbnailFields } from "./thumbnail-fields";

export function GalleryDetailModal(
  props: Pick<
    ReturnType<typeof useGalleryPageState>,
    | "isDark"
    | "selectedCase"
    | "formVars"
    | "setFormVars"
    | "formFiles"
    | "setFormFiles"
    | "editing"
    | "setEditing"
    | "editTitle"
    | "setEditTitle"
    | "editPrompt"
    | "setEditPrompt"
    | "editCategory"
    | "setEditCategory"
    | "editRecipe"
    | "setEditRecipe"
    | "savingEdit"
    | "deleting"
    | "editThumbnail"
    | "setEditThumbnail"
    | "isGeneratingThumb"
    | "loading"
    | "categories"
    | "closeDetail"
    | "handleLike"
    | "handleMakeSame"
    | "fileSubmitting"
    | "uploadProgress"
    | "handleTemplateSubmit"
    | "handleRecipeSubmit"
    | "startEdit"
    | "handleSaveEdit"
    | "handleEditGenerateThumbnail"
    | "handleDeleteCase"
  >,
) {
  const {
    isDark,
    selectedCase,
    formVars,
    setFormVars,
    formFiles,
    setFormFiles,
    editing,
    setEditing,
    editTitle,
    setEditTitle,
    editPrompt,
    setEditPrompt,
    editCategory,
    setEditCategory,
    editRecipe,
    setEditRecipe,
    savingEdit,
    deleting,
    editThumbnail,
    setEditThumbnail,
    isGeneratingThumb,
    loading,
    categories,
    closeDetail,
    handleLike,
    handleMakeSame,
    fileSubmitting,
    uploadProgress,
    handleTemplateSubmit,
    handleRecipeSubmit,
    startEdit,
    handleSaveEdit,
    handleEditGenerateThumbnail,
    handleDeleteCase,
  } = props;
  return (
    <>
      {/* ==================== Detail Modal ==================== */}
      <AnimatePresence>
        {selectedCase && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[100] flex items-center justify-center p-4 backdrop-blur-sm"
            style={{
              backgroundColor: isDark ? "rgba(0,0,0,0.7)" : "rgba(0,0,0,0.4)",
            }}
            onClick={closeDetail}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 20 }}
              transition={{ duration: 0.2 }}
              style={{
                background: isDark
                  ? "linear-gradient(135deg, rgba(15, 23, 42, 0.92) 0%, rgba(10, 18, 36, 0.88) 100%)"
                  : "linear-gradient(135deg, rgba(255, 255, 255, 0.92) 0%, rgba(243, 245, 255, 0.88) 100%)",
                backdropFilter: "blur(40px) saturate(200%)",
                WebkitBackdropFilter: "blur(40px) saturate(200%)",
                borderTop: "1.5px solid rgba(255, 255, 255, 0.4)",
                borderLeft: "1.5px solid rgba(255, 255, 255, 0.4)",
                borderBottom: `1.5px solid ${isDark ? "rgba(0,0,0,0.4)" : "rgba(99,102,241,0.12)"}`,
                borderRight: `1.5px solid ${isDark ? "rgba(0,0,0,0.4)" : "rgba(99,102,241,0.12)"}`,
                boxShadow: isDark
                  ? "0 25px 60px -12px rgba(0,0,0,0.6), 0 0 40px -8px rgba(99,102,241,0.12)"
                  : "0 25px 60px -12px rgba(99,102,241,0.08), 0 8px 24px rgba(0,0,0,0.06), inset 0 1px 0 rgba(255,255,255,0.8)",
              }}
              className={cn(
                "relative max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl shadow-2xl",
              )}
              onClick={(e) => e.stopPropagation()}
            >
              {/* Close button */}
              <button
                onClick={closeDetail}
                className={cn(
                  "absolute top-4 right-4 z-10 rounded-full p-2 transition-colors",
                  isDark
                    ? "bg-white/10 text-slate-300 hover:bg-white/20"
                    : "bg-slate-100 text-slate-500 hover:bg-slate-200",
                )}
              >
                <X className="h-4 w-4" />
              </button>

              {/* Thumbnail / Gradient Fallback */}
              <div
                className={cn(
                  "relative flex aspect-video items-center justify-center overflow-hidden rounded-t-2xl",
                  isDark ? "bg-white/5" : "bg-indigo-500/5",
                )}
              >
                {selectedCase.thumbnail ? (
                  <img
                    loading="lazy"
                    src={selectedCase.thumbnail}
                    alt={selectedCase.title}
                    onError={(e) => {
                      (e.currentTarget as HTMLImageElement).style.display =
                        "none";
                    }}
                    className="h-full w-full object-cover"
                  />
                ) : (
                  <div className="flex items-center gap-2 text-sm opacity-40">
                    <Sparkles className="h-5 w-5 animate-pulse text-indigo-400" />
                    <span>灵感库社区案例</span>
                  </div>
                )}
              </div>

              <div className="p-6">
                {/* Header */}
                <div className="mb-6">
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <span
                      className={cn(
                        "rounded-md px-2.5 py-1 text-[10px] font-bold",
                        isDark
                          ? "bg-white/10 text-slate-300"
                          : "bg-indigo-500/10 text-indigo-600",
                      )}
                    >
                      {selectedCase.category}
                    </span>
                    <SourceBadge
                      sourceType={selectedCase.source_type}
                      isDark={isDark}
                    />
                    {selectedCase.source_type === "ugc" &&
                      selectedCase.author_name && (
                        <span
                          className={cn(
                            "text-[10px] font-medium",
                            isDark ? "text-slate-400" : "text-slate-500",
                          )}
                        >
                          由 @{selectedCase.author_name} 创作
                        </span>
                      )}
                  </div>
                  <div className="flex items-center justify-between gap-4">
                    <h2
                      className={cn(
                        "text-xl font-bold",
                        isDark ? "text-white" : "text-slate-800",
                      )}
                    >
                      {selectedCase.title}
                    </h2>
                    {/* Like button */}
                    <button
                      type="button"
                      onClick={() => void handleLike(selectedCase.id)}
                      className={cn(
                        "flex shrink-0 items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-bold transition-all hover:scale-105",
                        isDark
                          ? "border border-white/10 bg-white/5 text-slate-300 hover:bg-white/10"
                          : "border border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100",
                      )}
                    >
                      <Heart
                        className={cn(
                          "h-4 w-4",
                          selectedCase.liked_by_me
                            ? "fill-rose-500 text-rose-500"
                            : "",
                        )}
                      />
                      <span>{selectedCase.likes} 个点赞</span>
                    </button>
                  </div>
                  <p
                    className={cn(
                      "mt-2 text-sm",
                      isDark ? "text-slate-400" : "text-slate-500",
                    )}
                  >
                    {selectedCase.skill_description}
                  </p>
                </div>

                {/* Recipe > Template > Prompt (3-level data-driven rendering) */}
                {editing ? (
                  /* —— 编辑模式（仅作者入口）：标题/分类/Prompt/配方可改 —— */
                  <div className="space-y-4">
                    <div>
                      <label
                        className={cn(
                          "mb-1 block text-[10px] font-extrabold tracking-widest uppercase",
                          isDark ? "text-slate-400" : "text-slate-500",
                        )}
                      >
                        标题
                      </label>
                      <input
                        type="text"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        className={cn(
                          "w-full rounded-xl border px-3 py-2 text-sm focus:border-indigo-400 focus:outline-none",
                          isDark
                            ? "border-white/10 bg-white/5 text-white"
                            : "border-slate-200 bg-white text-slate-800",
                        )}
                      />
                    </div>
                    <div>
                      <label
                        className={cn(
                          "mb-1 block text-[10px] font-extrabold tracking-widest uppercase",
                          isDark ? "text-slate-400" : "text-slate-500",
                        )}
                      >
                        分类
                      </label>
                      <select
                        value={editCategory}
                        onChange={(e) => setEditCategory(e.target.value)}
                        className={cn(
                          "w-full rounded-xl border px-3 py-2 text-sm focus:border-indigo-400 focus:outline-none",
                          isDark
                            ? "border-white/10 bg-white/5 text-white"
                            : "border-slate-200 bg-white text-slate-800",
                        )}
                      >
                        {categories
                          .filter((c) => c !== "全部灵感")
                          .map((c) => (
                            <option key={c} value={c}>
                              {c}
                            </option>
                          ))}
                      </select>
                    </div>
                    <div>
                      <label
                        className={cn(
                          "mb-1 block text-[10px] font-extrabold tracking-widest uppercase",
                          isDark ? "text-slate-400" : "text-slate-500",
                        )}
                      >
                        创作咒语 (Prompt)
                      </label>
                      <textarea
                        value={editPrompt}
                        onChange={(e) => setEditPrompt(e.target.value)}
                        rows={5}
                        className={cn(
                          "w-full rounded-xl border px-3 py-2 text-xs leading-relaxed focus:border-indigo-400 focus:outline-none",
                          isDark
                            ? "border-white/10 bg-white/5 text-slate-200"
                            : "border-slate-200 bg-white text-slate-700",
                        )}
                      />
                    </div>
                    <div>
                      <label
                        className={cn(
                          "mb-1 block text-[10px] font-extrabold tracking-widest uppercase",
                          isDark ? "text-slate-400" : "text-slate-500",
                        )}
                      >
                        缩略图
                      </label>
                      <ThumbnailFields
                        thumbnail={editThumbnail}
                        onThumbnailChange={setEditThumbnail}
                        isGenerating={isGeneratingThumb}
                        onGenerate={() => void handleEditGenerateThumbnail()}
                        disabled={!editTitle.trim()}
                        generateDisabledTitle={
                          editTitle.trim()
                            ? undefined
                            : "请先填写标题再生成封面"
                        }
                        originalThumbnail={selectedCase.thumbnail}
                        isDark={isDark}
                      />
                    </div>
                    {editRecipe && (
                      <RecipeEditor
                        recipe={editRecipe}
                        onChange={setEditRecipe}
                      />
                    )}
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => void handleSaveEdit()}
                        disabled={savingEdit}
                        className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 py-3 text-sm font-bold text-white transition-all hover:brightness-110 disabled:opacity-60"
                      >
                        {savingEdit ? "保存中…" : "保存修改"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditing(false)}
                        className={cn(
                          "rounded-xl border px-4 py-3 text-sm font-bold transition-colors",
                          isDark
                            ? "border-white/10 bg-white/5 text-slate-300 hover:bg-white/10"
                            : "border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100",
                        )}
                      >
                        取消
                      </button>
                    </div>
                  </div>
                ) : selectedCase.recipe ? (
                  <div>
                    {/* Recipe methodology card */}
                    <div className="mb-6 space-y-2 rounded-xl border border-indigo-200/50 bg-indigo-50/40 p-4 text-xs dark:border-indigo-500/20 dark:bg-indigo-500/5">
                      <p className="font-bold text-indigo-700 dark:text-indigo-300">
                        🎯 {selectedCase.recipe.goal}
                      </p>
                      <p className="text-muted-foreground">
                        方法论：{selectedCase.recipe.approach ?? "通用"} ·{" "}
                        {selectedCase.recipe.est_steps} 步
                      </p>
                      {selectedCase.recipe.steps.map((s, i) => (
                        <div
                          key={i}
                          className="border-t border-indigo-100 pt-2 dark:border-indigo-500/10"
                        >
                          <p className="font-medium">
                            步骤 {i + 1}：{s.instruction}
                          </p>
                          {s.tip && (
                            <p className="text-muted-foreground">💡 {s.tip}</p>
                          )}
                        </div>
                      ))}
                      {selectedCase.recipe.pitfalls.length > 0 && (
                        <div className="border-t border-indigo-100 pt-2 dark:border-indigo-500/10">
                          <p className="font-medium">⚠️ 避坑指南</p>
                          {selectedCase.recipe.pitfalls.map((p, i) => (
                            <p key={i} className="text-muted-foreground">
                              · {p}
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                    {(() => {
                      const formStep = selectedCase.recipe.steps.find(
                        (s) => s.template && s.vars,
                      );
                      if (formStep?.vars) {
                        return (
                          <>
                            <div className="mb-6 space-y-4">
                              {Object.entries(formStep.vars).map(
                                ([key, cfg]) => (
                                  <FormField
                                    key={key}
                                    varKey={key}
                                    config={cfg}
                                    value={formVars[key] ?? ""}
                                    onChange={(val) =>
                                      setFormVars((prev) => ({
                                        ...prev,
                                        [key]: val,
                                      }))
                                    }
                                    files={formFiles[key] ?? []}
                                    onFilesChange={(fs) =>
                                      setFormFiles((prev) => ({
                                        ...prev,
                                        [key]: fs,
                                      }))
                                    }
                                    isDark={isDark}
                                  />
                                ),
                              )}
                            </div>
                            <button
                              onClick={handleRecipeSubmit}
                              disabled={fileSubmitting}
                              className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 py-3.5 text-sm font-bold text-white transition-all hover:shadow-[0_0_20px_rgba(99,102,241,0.3)] hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              <Wand2 className="h-4 w-4" />
                              {fileSubmitting
                                ? uploadProgress !== null
                                  ? `上传中 ${uploadProgress}%`
                                  : "正在创建会话…"
                                : "开始生成"}
                            </button>
                          </>
                        );
                      }
                      return (
                        <button
                          onClick={handleRecipeSubmit}
                          className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 py-3.5 text-sm font-bold text-white transition-all hover:shadow-[0_0_20px_rgba(99,102,241,0.3)] hover:brightness-110"
                        >
                          <Wand2 className="h-4 w-4" />
                          制作同款
                        </button>
                      );
                    })()}
                  </div>
                ) : selectedCase.prompt_template && selectedCase.vars_config ? (
                  <div>
                    {/* Template variable form fields */}
                    <div className="mb-6 space-y-4">
                      {Object.entries(selectedCase.vars_config).map(
                        ([key, cfg]) => (
                          <FormField
                            key={key}
                            varKey={key}
                            config={cfg}
                            value={formVars[key] ?? ""}
                            onChange={(val) =>
                              setFormVars((prev) => ({ ...prev, [key]: val }))
                            }
                            files={formFiles[key] ?? []}
                            onFilesChange={(fs) =>
                              setFormFiles((prev) => ({ ...prev, [key]: fs }))
                            }
                            isDark={isDark}
                          />
                        ),
                      )}
                    </div>
                    <button
                      onClick={handleTemplateSubmit}
                      disabled={fileSubmitting}
                      className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 py-3.5 text-sm font-bold text-white transition-all hover:shadow-[0_0_20px_rgba(99,102,241,0.3)] hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <Wand2 className="h-4 w-4" />
                      {fileSubmitting
                        ? uploadProgress !== null
                          ? `上传中 ${uploadProgress}%`
                          : "正在创建会话…"
                        : "开始生成"}
                    </button>
                  </div>
                ) : (
                  /* No recipe/template — show prompt preview (简化版 for UGC) */
                  <div>
                    <div className="mb-6">
                      <h3
                        className={cn(
                          "mb-2 text-[10px] font-extrabold tracking-widest uppercase",
                          isDark ? "text-slate-400" : "text-slate-500",
                        )}
                      >
                        创作咒语 (Prompt)
                      </h3>
                      <div
                        className={cn(
                          "rounded-xl p-4 text-xs leading-relaxed whitespace-pre-wrap",
                          isDark
                            ? "bg-white/5 text-slate-300"
                            : "bg-slate-50 text-slate-600",
                        )}
                      >
                        {selectedCase.prompt}
                      </div>
                      {selectedCase.source_type === "ugc" && (
                        <p className="text-muted-foreground mt-1 text-[10px]">
                          简化版：未提取配方，仅以纯 Prompt 形式分享。
                        </p>
                      )}
                    </div>
                    <button
                      onClick={() => handleMakeSame(selectedCase.prompt)}
                      className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 py-3.5 text-sm font-bold text-white transition-all hover:shadow-[0_0_20px_rgba(99,102,241,0.3)] hover:brightness-110"
                    >
                      <Wand2 className="h-4 w-4" />
                      制作同款
                    </button>
                  </div>
                )}

                {/* 作者删改入口（仅自己的 UGC 案例；editable_by_me 由服务端计算） */}
                {selectedCase.source_type === "ugc" &&
                  selectedCase.editable_by_me &&
                  !editing && (
                    <div className="mt-4 flex justify-end gap-2">
                      <button
                        type="button"
                        onClick={startEdit}
                        className={cn(
                          "rounded-xl border px-3 py-1.5 text-xs font-bold transition-colors",
                          isDark
                            ? "border-white/10 bg-white/5 text-slate-300 hover:bg-white/10"
                            : "border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100",
                        )}
                      >
                        编辑
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDeleteCase()}
                        disabled={deleting}
                        className={cn(
                          "rounded-xl border px-3 py-1.5 text-xs font-bold transition-colors disabled:opacity-60",
                          isDark
                            ? "border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20"
                            : "border-red-200 bg-red-50 text-red-600 hover:bg-red-100",
                        )}
                      >
                        {deleting ? "删除中…" : "删除"}
                      </button>
                    </div>
                  )}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

/** Form field for a template variable. */
function FormField({
  varKey,
  config,
  value,
  onChange,
  files,
  onFilesChange,
  isDark,
}: {
  varKey: string;
  config: VarConfig;
  value: string;
  onChange: (val: string) => void;
  files?: File[];
  onFilesChange?: (files: File[]) => void;
  isDark: boolean;
}) {
  const id = `gallery-var-${varKey}`;
  if (config.type === "file") {
    return (
      <div>
        <label
          htmlFor={id}
          className={cn(
            "mb-1.5 block text-xs font-medium",
            isDark ? "text-slate-300" : "text-slate-600",
          )}
        >
          {config.label}
          {config.required && <span className="ml-1 text-rose-400">*</span>}
        </label>
        <input
          id={id}
          type="file"
          accept={config.accept}
          multiple={config.multiple}
          onChange={(e) => onFilesChange?.(Array.from(e.target.files ?? []))}
          className={cn(
            "w-full cursor-pointer rounded-lg px-3 py-2 text-xs transition-all file:mr-2 file:rounded-md file:border-0 file:px-2.5 file:py-1 file:text-xs",
            isDark
              ? "border border-white/10 bg-white/5 text-slate-300 file:bg-indigo-500/20 file:text-indigo-300"
              : "border border-slate-200 bg-slate-50 text-slate-600 file:bg-indigo-50 file:text-indigo-600",
          )}
        />
        {files?.length ? (
          <p
            className={cn(
              "mt-1 text-xs",
              isDark ? "text-slate-400" : "text-slate-500",
            )}
          >
            已选：{files.map((f) => f.name).join("、")}
          </p>
        ) : config.placeholder ? (
          <p
            className={cn(
              "mt-1 text-xs",
              isDark ? "text-slate-500" : "text-slate-400",
            )}
          >
            {config.placeholder}
          </p>
        ) : null}
      </div>
    );
  }
  if (config.type === "select" && config.options) {
    return (
      <div>
        <label
          htmlFor={id}
          className={cn(
            "mb-1.5 block text-xs font-medium",
            isDark ? "text-slate-300" : "text-slate-600",
          )}
        >
          {config.label}
          {config.required && <span className="ml-1 text-rose-400">*</span>}
        </label>
        <select
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={cn(
            "w-full cursor-pointer appearance-none rounded-lg px-3 py-2.5 text-xs transition-all",
            isDark
              ? "border border-white/10 bg-white/5 text-white"
              : "border border-slate-200 bg-slate-50 text-slate-800",
          )}
        >
          {config.options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </div>
    );
  }

  return (
    <div>
      <label
        htmlFor={id}
        className={cn(
          "mb-1.5 block text-xs font-medium",
          isDark ? "text-slate-300" : "text-slate-600",
        )}
      >
        {config.label}
        {config.required && <span className="ml-1 text-rose-400">*</span>}
      </label>
      <input
        id={id}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={config.placeholder ?? ""}
        className={cn(
          "w-full rounded-lg px-3 py-2.5 text-xs transition-all",
          isDark
            ? "border border-white/10 bg-white/5 text-white placeholder-slate-500"
            : "border border-slate-200 bg-slate-50 text-slate-800 placeholder-slate-400",
        )}
      />
    </div>
  );
}
