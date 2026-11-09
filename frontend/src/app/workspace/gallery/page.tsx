"use client";

import { ChevronRight, Heart, Search, Sparkles } from "lucide-react";

import { EmptyState } from "@/components/workspace/empty-state";
import { GalleryDetailModal } from "@/components/workspace/gallery/gallery-detail-modal";
import { SourceBadge } from "@/components/workspace/gallery/source-badge";
import { cn } from "@/lib/utils";

import { useGalleryPageState } from "./use-gallery-page-state";

export default function GalleryPage() {
  const {
    router,
    queryClient,
    resolvedTheme,
    mounted,
    setMounted,
    isDark,
    glassStyle,
    search,
    setSearch,
    activeCategory,
    setActiveCategory,
    likedOnly,
    setLikedOnly,
    selectedCase,
    setSelectedCase,
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
    setSavingEdit,
    deleting,
    setDeleting,
    editThumbnail,
    setEditThumbnail,
    isGeneratingThumb,
    setIsGeneratingThumb,
    casesData,
    loading,
    cases,
    categories,
    featuredCases,
    filteredCases,
    openDetail,
    closeDetail,
    handleLike,
    handleMakeSame,
    fileSubmitting,
    setFileSubmitting,
    uploadProgress,
    setUploadProgress,
    handleFileVarsSubmit,
    handleTemplateSubmit,
    handleRecipeInstantiate,
    handleRecipeSubmit,
    startEdit,
    handleSaveEdit,
    handleEditGenerateThumbnail,
    handleDeleteCase,
  } = useGalleryPageState();

  return (
    <div className="scrollbar-none flex size-full flex-col overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-6xl space-y-8">
        {/* Header */}
        <div>
          <h1
            className={cn(
              "flex items-center gap-2 text-lg font-extrabold",
              isDark ? "text-white" : "text-slate-800",
            )}
          >
            <Sparkles className="h-5 w-5 text-indigo-500" />
            创意灵感库 (Gallery)
          </h1>
          <p className="mt-1 text-[10px] font-semibold text-slate-400">
            浏览社区与官方精选的优质交付模板，一键加载 Prompt 同款制作
          </p>
        </div>

        {/* Search Input */}
        <div className="relative">
          <Search
            className={cn(
              "absolute top-1/2 left-4 h-4 w-4 -translate-y-1/2",
              isDark ? "text-slate-500" : "text-slate-400",
            )}
          />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索灵感 (例如：科技风PPT, 数据可视化...)"
            style={glassStyle()}
            className={cn(
              "w-full rounded-2xl py-3.5 pr-6 pl-11 text-xs shadow-xl transition-all duration-300 outline-none",
              isDark
                ? "text-white placeholder-slate-500"
                : "text-slate-800 placeholder-slate-400",
            )}
          />
        </div>

        {/* Featured Section — only show when no search/filter active */}
        {featuredCases.length > 0 &&
          !search &&
          activeCategory === "全部灵感" &&
          !likedOnly && (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-amber-500" />
                <h2
                  className={cn(
                    "text-xs font-extrabold",
                    isDark ? "text-slate-300" : "text-slate-600",
                  )}
                >
                  精选推荐
                </h2>
              </div>
              <div className="scrollbar-hide flex gap-4 overflow-x-auto pb-2">
                {featuredCases.map((c, i) => (
                  <div
                    key={c.id}
                    onClick={() => openDetail(c)}
                    style={glassStyle()}
                    className="interactive-glass group w-64 shrink-0 cursor-pointer overflow-hidden rounded-2xl transition-transform hover:scale-[1.02]"
                  >
                    {/* Thumbnail or gradient fallback */}
                    <div
                      className={cn(
                        "relative flex h-32 items-center justify-center overflow-hidden",
                        isDark ? "bg-white/5" : "bg-indigo-500/5",
                      )}
                    >
                      {c.thumbnail ? (
                        <img
                          loading="lazy"
                          src={c.thumbnail}
                          alt={c.title}
                          onError={(e) => {
                            (
                              e.currentTarget as HTMLImageElement
                            ).style.display = "none";
                          }}
                          className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110"
                        />
                      ) : (
                        <span className="text-3xl opacity-40">✨</span>
                      )}
                      {/* Floating like */}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleLike(c.id);
                        }}
                        className={cn(
                          "absolute top-2 right-2 flex items-center gap-1 rounded-full px-2 py-0.5 text-[9px] font-bold backdrop-blur-md transition-all hover:scale-105",
                          isDark
                            ? "border border-white/10 bg-black/40 text-slate-300"
                            : "border border-slate-200/50 bg-white/70 text-slate-600",
                        )}
                      >
                        <Heart
                          className={cn(
                            "h-3 w-3",
                            c.liked_by_me ? "fill-rose-500 text-rose-500" : "",
                          )}
                        />
                        <span>{c.likes}</span>
                      </button>
                    </div>
                    <div className="p-3.5">
                      <div className="flex items-center gap-1.5">
                        <span
                          className={cn(
                            "rounded-md px-1.5 py-0.5 text-[8px] font-bold",
                            isDark
                              ? "bg-white/5 text-slate-400"
                              : "bg-indigo-500/5 text-indigo-600",
                          )}
                        >
                          {c.category}
                        </span>
                        <SourceBadge
                          sourceType={c.source_type}
                          isDark={isDark}
                        />
                      </div>
                      <h3
                        className={cn(
                          "mt-1.5 line-clamp-1 text-[11px] font-bold",
                          isDark ? "text-white" : "text-slate-800",
                        )}
                      >
                        {c.title}
                      </h3>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

        {/* Category Filter Buttons */}
        <div className="flex flex-wrap gap-2">
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() => {
                // P3-1/BUG-001（2026-09-14）：视图互斥——点类目清 likedOnly，
                // 与数据查询语义一致（likedOnly 时类目不参与过滤）。
                setActiveCategory(cat);
                setLikedOnly(false);
              }}
              className={cn(
                "rounded-full px-4 py-1.5 text-[10px] font-bold transition-all",
                // P3-1/BUG-001：likedOnly 激活时类目 pill 一律不高亮（修前双高亮）
                !likedOnly && activeCategory === cat
                  ? isDark
                    ? "border border-white/20 bg-white/15 text-white shadow-sm"
                    : "border border-indigo-200 bg-indigo-500 text-white shadow-sm"
                  : isDark
                    ? "border border-white/5 bg-white/5 text-slate-400 hover:bg-white/10 hover:text-white"
                    : "border border-slate-200 bg-slate-50 text-slate-500 hover:bg-slate-100 hover:text-indigo-600",
              )}
            >
              {cat}
            </button>
          ))}
          <button
            onClick={() => setLikedOnly((v) => !v)}
            className={cn(
              "flex items-center gap-1 rounded-full px-4 py-1.5 text-[10px] font-bold transition-all",
              likedOnly
                ? isDark
                  ? "border border-rose-300/30 bg-rose-500/20 text-rose-200 shadow-sm"
                  : "border border-rose-200 bg-rose-500 text-white shadow-sm"
                : isDark
                  ? "border border-white/5 bg-white/5 text-slate-400 hover:bg-white/10 hover:text-white"
                  : "border border-slate-200 bg-slate-50 text-slate-500 hover:bg-rose-50 hover:text-rose-600",
            )}
          >
            <Heart className={cn("h-3 w-3", likedOnly && "fill-current")} />
            我的点赞
          </button>
        </div>

        {/* Gallery Grid */}
        {loading ? (
          <div className="flex h-48 items-center justify-center">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
          </div>
        ) : filteredCases.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {filteredCases.map((c) => (
              <div
                key={c.id}
                onClick={() => openDetail(c)}
                style={glassStyle()}
                className="interactive-glass group cursor-pointer overflow-hidden rounded-2xl transition-transform hover:scale-[1.02]"
              >
                {/* Thumbnail */}
                <div
                  className={cn(
                    "relative flex h-36 items-center justify-center overflow-hidden",
                    isDark ? "bg-white/5" : "bg-indigo-500/5",
                  )}
                >
                  {c.thumbnail ? (
                    <img
                      loading="lazy"
                      src={c.thumbnail}
                      alt={c.title}
                      onError={(e) => {
                        (e.currentTarget as HTMLImageElement).style.display =
                          "none";
                      }}
                      className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110"
                    />
                  ) : (
                    <span className="text-4xl opacity-30">📄</span>
                  )}
                  {/* Floating like */}
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleLike(c.id);
                    }}
                    className={cn(
                      "absolute top-3 right-3 flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[9px] font-bold backdrop-blur-md transition-all hover:scale-105",
                      isDark
                        ? "border border-white/10 bg-black/40 text-slate-300"
                        : "border border-slate-200/50 bg-white/70 text-slate-600",
                    )}
                  >
                    <Heart
                      className={cn(
                        "h-3.5 w-3.5",
                        c.liked_by_me ? "fill-rose-500 text-rose-500" : "",
                      )}
                    />
                    <span>{c.likes}</span>
                  </button>
                </div>

                {/* Content */}
                <div className="p-4">
                  <div className="flex items-center gap-1.5">
                    <span
                      className={cn(
                        "rounded-md px-1.5 py-0.5 text-[8px] font-bold",
                        isDark
                          ? "bg-white/5 text-slate-400"
                          : "bg-indigo-500/5 text-indigo-600",
                      )}
                    >
                      {c.category}
                    </span>
                    <SourceBadge sourceType={c.source_type} isDark={isDark} />
                  </div>
                  <h3
                    className={cn(
                      "mt-2 text-xs font-bold",
                      isDark ? "text-white" : "text-slate-800",
                    )}
                  >
                    {c.title}
                  </h3>
                  <p
                    className={cn(
                      "mt-1 line-clamp-2 text-[9px] leading-relaxed font-semibold",
                      isDark ? "text-slate-400" : "text-slate-500",
                    )}
                  >
                    {c.skill_description}
                  </p>
                  <div className="mt-3 flex items-center justify-between">
                    <span
                      className={cn(
                        "inline-flex items-center gap-1 text-[9px]",
                        isDark ? "text-indigo-400" : "text-indigo-600",
                      )}
                    >
                      查看详情
                      <ChevronRight className="h-3 w-3" />
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          /* 空态三件套统一（2026-09-14 D 级收敛）：EmptyState */
          <EmptyState
            icon={Sparkles}
            title={
              search
                ? `没有找到"${search}"相关的灵感`
                : likedOnly
                  ? "还没有点赞的灵感，去发现喜欢的案例吧"
                  : "暂无灵感案例，敬请期待"
            }
            action={
              search ? (
                <button
                  onClick={() => setSearch("")}
                  className={cn(
                    "rounded-full px-5 py-2 text-xs font-bold transition-all",
                    isDark
                      ? "border border-white/10 bg-white/5 text-white hover:bg-white/10"
                      : "border border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100",
                  )}
                >
                  查看全部灵感
                </button>
              ) : undefined
            }
          />
        )}
      </div>

      {/* ==================== Detail Modal ==================== */}
      <GalleryDetailModal
        isDark={isDark}
        selectedCase={selectedCase}
        formVars={formVars}
        setFormVars={setFormVars}
        formFiles={formFiles}
        setFormFiles={setFormFiles}
        editing={editing}
        setEditing={setEditing}
        editTitle={editTitle}
        setEditTitle={setEditTitle}
        editPrompt={editPrompt}
        setEditPrompt={setEditPrompt}
        editCategory={editCategory}
        setEditCategory={setEditCategory}
        editRecipe={editRecipe}
        setEditRecipe={setEditRecipe}
        savingEdit={savingEdit}
        deleting={deleting}
        editThumbnail={editThumbnail}
        setEditThumbnail={setEditThumbnail}
        isGeneratingThumb={isGeneratingThumb}
        loading={loading}
        categories={categories}
        closeDetail={closeDetail}
        handleLike={handleLike}
        handleMakeSame={handleMakeSame}
        fileSubmitting={fileSubmitting}
        uploadProgress={uploadProgress}
        handleTemplateSubmit={handleTemplateSubmit}
        handleRecipeSubmit={handleRecipeSubmit}
        startEdit={startEdit}
        handleSaveEdit={handleSaveEdit}
        handleEditGenerateThumbnail={handleEditGenerateThumbnail}
        handleDeleteCase={handleDeleteCase}
      />
    </div>
  );
}
