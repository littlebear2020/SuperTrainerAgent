"use client";

/**
 * 2026-10-06 拆分批：灵感库页状态/逻辑抽为 hook（数据/详情/编辑/上传/删除
 * 全部状态与 handler 上提，GalleryPage 只剩 JSX 编排）。
 * 2026-10-07 文件级拆分批：自 gallery/page.tsx 逐字上提为本文件。
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  buildConfirmOptions,
  useConfirm,
} from "@/components/workspace/dialog-provider";
import { buildLoginUrl } from "@/core/auth/types";
import {
  caseHasFileVars,
  compileTemplate,
  deleteCase,
  generateThumbnail,
  instantiateCase,
  loadGalleryCases,
  toggleLikeCase,
  updateCase,
  validateVarsWithConfigs,
  type GalleryCase,
  type Recipe,
} from "@/core/gallery/api";
import { submitFileVarsCase } from "@/core/gallery/file-vars-submit";
import { seedStreamRejoinKey } from "@/core/gallery/stream-rejoin";
import { useGlassStyle } from "@/core/theme/glass";
import { uploadFilesWithProgress } from "@/core/uploads";
import { PENDING_PROMPT_KEY } from "@/lib/pending-prompt";

export function useGalleryPageState() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  const isDark = mounted ? resolvedTheme === "dark" : false;
  const { glassStyle } = useGlassStyle();

  // Data state
  const [search, setSearch] = useState("");
  const [activeCategory, setActiveCategory] = useState("全部灵感");
  const [likedOnly, setLikedOnly] = useState(false);

  // Detail modal state
  const [selectedCase, setSelectedCase] = useState<GalleryCase | null>(null);
  const [formVars, setFormVars] = useState<Record<string, string>>({});
  // 弹窗内文件变量（2026-07-31）：type === "file" 的选中文件，与 formVars 并列
  const [formFiles, setFormFiles] = useState<Record<string, File[]>>({});

  // 作者删改（2026-07-27，editable_by_me 守卫）：编辑表单本地状态
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editPrompt, setEditPrompt] = useState("");
  const [editCategory, setEditCategory] = useState("");
  const [editRecipe, setEditRecipe] = useState<Recipe | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // 编辑态缩略图（2026-07-28 T2：发布后忘传图可补传/AI 重生成）
  const [editThumbnail, setEditThumbnail] = useState<string | null>(null);
  const [isGeneratingThumb, setIsGeneratingThumb] = useState(false);

  // Load gallery cases from API via react-query
  const { data: casesData, isLoading: loading } = useQuery({
    queryKey: ["gallery-cases", likedOnly],
    queryFn: () => loadGalleryCases(undefined, undefined, undefined, likedOnly),
  });
  // useMemo 钉住引用：裸 `?? []` 每次渲染新数组，下游 useMemo 的 deps 全部失稳
  const cases = useMemo(() => casesData?.cases ?? [], [casesData]);

  // Derived data
  const categories = useMemo(() => {
    const cats = new Set(cases.map((c) => c.category).filter(Boolean));
    return ["全部灵感", ...Array.from(cats)];
  }, [cases]);

  const featuredCases = useMemo(() => cases.filter((c) => c.featured), [cases]);

  const filteredCases = useMemo(() => {
    const q = search.toLowerCase();
    return cases.filter((c) => {
      const matchSearch =
        !q ||
        c.title.toLowerCase().includes(q) ||
        c.category.toLowerCase().includes(q) ||
        c.skill_description.toLowerCase().includes(q) ||
        c.prompt.toLowerCase().includes(q);
      const matchCategory =
        activeCategory === "全部灵感" || c.category === activeCategory;
      return matchSearch && matchCategory;
    });
  }, [cases, search, activeCategory]);

  // Detail modal handlers
  const openDetail = useCallback((c: GalleryCase) => {
    setSelectedCase(c);
    setFormVars(c.default_vars ?? {});
    setFormFiles({});
    setEditing(false);
  }, []);

  const closeDetail = useCallback(() => {
    setSelectedCase(null);
    setFormVars({});
    setFormFiles({});
    setEditing(false);
  }, []);

  // Like handler with toast
  const handleLike = useCallback(
    async (caseId: string) => {
      try {
        const result = await toggleLikeCase(caseId);
        // Update BOTH gallery-cases view caches (全部 = likedOnly:false / 我的点赞 = likedOnly:true),
        // not just the current view — otherwise un-liking in the liked-only view
        // leaves a stale card until refresh, and switching views flashes old state.
        // (setQueriesData's updater only receives `old`, not the query key, so we
        // update the two view keys explicitly to apply view-specific filtering.)
        const updateLikedCase = (c: GalleryCase) =>
          c.id === caseId
            ? { ...c, likes: result.likes, liked_by_me: result.liked_by_me }
            : c;
        queryClient.setQueryData<{ cases: GalleryCase[] }>(
          ["gallery-cases", false],
          (old) => (old ? { cases: old.cases.map(updateLikedCase) } : old),
        );
        queryClient.setQueryData<{ cases: GalleryCase[] }>(
          ["gallery-cases", true],
          (old) =>
            old
              ? {
                  cases: old.cases
                    .map(updateLikedCase)
                    // In the liked-only view, drop the card as soon as it's un-liked.
                    .filter((c) => !(c.id === caseId && !result.liked_by_me)),
                }
              : old,
        );
        setSelectedCase((prev) => {
          if (prev?.id === caseId) {
            return {
              ...prev,
              likes: result.likes,
              liked_by_me: result.liked_by_me,
            };
          }
          return prev;
        });
        if (result.liked_by_me) {
          toast.success("已添加到我的点赞列表！", {
            action: { label: "查看", onClick: () => setLikedOnly(true) },
          });
        } else {
          toast("已移出点赞列表");
        }
      } catch (err) {
        const status = (err as { status?: number }).status;
        if (status === 401) {
          toast.error("请登录后点赞", {
            action: {
              label: "去登录",
              onClick: () => router.push(buildLoginUrl("/workspace/gallery")),
            },
          });
        } else if (typeof status === "number") {
          toast.error(err instanceof Error ? err.message : "点赞失败，请重试");
        } else {
          toast.error("网络异常，请重试");
        }
      }
    },
    [queryClient, router],
  );

  // Make same — SEC-H11 后全量走 sessionStorage JSON（唯一可自动发送通道；
  // URL ?msg= 已不承载发送语义）
  const handleMakeSame = useCallback(
    (prompt: string) => {
      closeDetail();
      const targetPath = "/workspace/chats/new";
      // 含文件变量的灵感走两步流（2026-07-27）：预填不发送，
      // 用户在对话页用回形针上传文件后手动发。
      const hasFileVars = selectedCase ? caseHasFileVars(selectedCase) : false;
      if (hasFileVars) {
        sessionStorage.setItem(
          PENDING_PROMPT_KEY,
          JSON.stringify({ prompt, autoSend: false }),
        );
        toast.info(
          "该灵感需要先上传文件：请在对话页用回形针 📎 上传后，再点击发送。",
        );
        router.push(targetPath);
        return;
      }
      // SEC-H11（2026-09-04）：prompt 一律走 sessionStorage（consume-on-read、
      // 标签页隔离），不再走 URL ?msg=&autosend=1 一键自动发送通道——书签/
      // 分享链接最多预填，不能凭 URL 直接触发发送。
      // sessionStorage 而非 localStorage：避免跨标签页幽灵发送
      // （原 localStorage 被其他标签页的 PromptInputProvider mount 时误读并发送）。
      sessionStorage.setItem(
        PENDING_PROMPT_KEY,
        JSON.stringify({ prompt, autoSend: true }),
      );
      router.push(targetPath);
    },
    [router, closeDetail, selectedCase],
  );

  const [fileSubmitting, setFileSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);

  // 含文件变量的灵感：弹窗内直接上传（D2）——建线程→上传→自动发送→跳转
  const handleFileVarsSubmit = useCallback(async () => {
    if (!selectedCase) return;
    setFileSubmitting(true);
    setUploadProgress(null);
    try {
      await submitFileVarsCase(selectedCase.id, formVars, formFiles, {
        instantiate: (caseId, vars) =>
          instantiateCase(caseId, { vars, defer: true }),
        upload: (threadId, files) =>
          uploadFilesWithProgress(threadId, files, (loaded, total) =>
            setUploadProgress(Math.round((loaded / total) * 100)),
          ),
        seedPrompt: (payload) =>
          sessionStorage.setItem(PENDING_PROMPT_KEY, JSON.stringify(payload)),
        navigate: (threadId) => {
          closeDetail();
          router.push(`/workspace/chats/${threadId}`);
        },
      });
    } catch (err) {
      const status = (err as { status?: number }).status;
      if (status === 401) {
        toast.error("请登录后复刻");
      } else {
        toast.error(err instanceof Error ? err.message : "上传失败，请重试");
      }
    } finally {
      setFileSubmitting(false);
      setUploadProgress(null);
    }
  }, [selectedCase, formVars, formFiles, closeDetail, router]);

  // Template submit
  const handleTemplateSubmit = useCallback(() => {
    if (!selectedCase?.prompt_template || !selectedCase?.vars_config) return;
    const missingFields = validateVarsWithConfigs(
      selectedCase.vars_config,
      formVars,
      formFiles,
    );
    if (missingFields.length > 0) {
      toast.error(`请填写必填项：${missingFields.join("、")}`);
      return;
    }
    if (caseHasFileVars(selectedCase)) {
      void handleFileVarsSubmit();
      return;
    }
    const compiled = compileTemplate(selectedCase.prompt_template, formVars);
    handleMakeSame(compiled);
  }, [selectedCase, formVars, formFiles, handleMakeSame, handleFileVarsSubmit]);

  // Recipe 复刻：调用后端 instantiate，注入配方的 context_blocks + 首步模板，
  // 跳转到新创建的 thread（而不是把裸 prompt 塞进 ?msg=）。
  // 失败降级：instantiate 依赖 checkpointer/thread_store，若后端异常（500/网络），
  // 降级为本地编译首步模板走 handleMakeSame，保证复刻主路径可用（牺牲 context_blocks）。
  const handleRecipeInstantiate = useCallback(
    async (caseId: string) => {
      const formStep = selectedCase?.recipe?.steps.find((s) => s.template);
      try {
        closeDetail();
        const { thread_id, run_id } = await instantiateCase(caseId, {
          vars: formVars,
        });
        // 服务端已启动的 run：写重连键，聊天页 mount 时接续（否则页面静止=「假死」）
        seedStreamRejoinKey(thread_id, run_id);
        router.push(`/workspace/chats/${thread_id}`);
      } catch (err) {
        const status = (err as { status?: number }).status;
        if (status === 401) {
          toast.error("请登录后复刻");
          return;
        }
        // 非 401（500/网络等）：降级为本地编译发送，不让用户卡死。
        toast.error("在线复刻暂不可用，已切换为本地发送");
        const compiled = formStep
          ? compileTemplate(formStep.template, formVars)
          : (selectedCase?.prompt ?? "");
        if (compiled) handleMakeSame(compiled);
      }
    },
    [closeDetail, router, formVars, selectedCase, handleMakeSame],
  );

  // Recipe submit — 走后端 instantiate（保留 context_blocks 与方法论），不再丢配方
  const handleRecipeSubmit = useCallback(() => {
    if (!selectedCase?.recipe) return;
    const formStep = selectedCase.recipe.steps.find((s) => s.template);
    if (!formStep) {
      handleMakeSame(selectedCase.prompt);
      return;
    }
    const missingFields = validateVarsWithConfigs(
      formStep.vars ?? {},
      formVars,
      formFiles,
    );
    if (missingFields.length > 0) {
      toast.error(`请填写必填项：${missingFields.join("、")}`);
      return;
    }
    if (caseHasFileVars(selectedCase)) {
      void handleFileVarsSubmit();
      return;
    }
    // 复刻带配方的案例：交给后端实例化（含 context_blocks 系统设定 + 首步模板）。
    void handleRecipeInstantiate(selectedCase.id);
  }, [
    selectedCase,
    formVars,
    formFiles,
    handleMakeSame,
    handleRecipeInstantiate,
    handleFileVarsSubmit,
  ]);

  // ── 作者删改（editable_by_me 守卫；错误必须 toast 不静默） ─────────────
  const startEdit = useCallback(() => {
    if (!selectedCase) return;
    setEditTitle(selectedCase.title);
    setEditPrompt(selectedCase.prompt);
    setEditCategory(selectedCase.category);
    setEditRecipe(selectedCase.recipe ?? null);
    setEditThumbnail(selectedCase.thumbnail ?? null);
    setEditing(true);
  }, [selectedCase]);

  const handleSaveEdit = useCallback(async () => {
    if (!selectedCase) return;
    setSavingEdit(true);
    try {
      const updated = await updateCase(selectedCase.id, {
        title: editTitle.trim() || undefined,
        prompt: editPrompt.trim() || undefined,
        category: editCategory || undefined,
        recipe: editRecipe ?? undefined,
        // 仅在缩略图发生变化时携带（None 语义=不修改；不支持清空）
        ...(editThumbnail && editThumbnail !== selectedCase.thumbnail
          ? { thumbnail: editThumbnail }
          : {}),
      });
      toast.success("灵感已更新");
      setEditing(false);
      setSelectedCase(updated);
      // 两个视图缓存（全部/我的点赞）同步更新，避免切视图见到旧数据
      for (const key of [false, true]) {
        queryClient.setQueryData<{ cases: GalleryCase[] }>(
          ["gallery-cases", key],
          (old) =>
            old
              ? {
                  cases: old.cases.map((c) =>
                    c.id === updated.id ? updated : c,
                  ),
                }
              : old,
        );
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "更新失败，请重试");
    } finally {
      setSavingEdit(false);
    }
  }, [
    selectedCase,
    editTitle,
    editPrompt,
    editCategory,
    editRecipe,
    editThumbnail,
    queryClient,
  ]);

  const handleEditGenerateThumbnail = useCallback(async () => {
    if (!editTitle.trim()) {
      toast.error("请先填写标题再生成封面");
      return;
    }
    setIsGeneratingThumb(true);
    try {
      // 与内容感知封面一致：分类视觉方向 + 内容摘要注入后端 prompt
      const { thumbnail: url } = await generateThumbnail({
        title: editTitle,
        category: editCategory,
        prompt: editPrompt.slice(0, 480) || undefined,
      });
      setEditThumbnail(url);
      toast.success("缩略图已生成");
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "缩略图生成失败，可改用上传",
      );
    } finally {
      setIsGeneratingThumb(false);
    }
  }, [editTitle, editCategory, editPrompt]);

  const handleDeleteCase = useCallback(async () => {
    if (!selectedCase) return;
    const confirmed = await confirm(
      buildConfirmOptions("确定删除这条灵感吗？删除后不可恢复。", {
        variant: "destructive",
      }),
    );
    if (!confirmed) return;
    setDeleting(true);
    const removedId = selectedCase.id;
    try {
      await deleteCase(removedId);
      toast.success("灵感已删除");
      closeDetail();
      for (const key of [false, true]) {
        queryClient.setQueryData<{ cases: GalleryCase[] }>(
          ["gallery-cases", key],
          (old) =>
            old ? { cases: old.cases.filter((c) => c.id !== removedId) } : old,
        );
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败，请重试");
    } finally {
      setDeleting(false);
    }
  }, [confirm, selectedCase, closeDetail, queryClient]);

  return {
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
  };
}
