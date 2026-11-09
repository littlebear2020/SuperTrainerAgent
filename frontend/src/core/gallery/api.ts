import { fetch } from "@/core/api/fetcher";
import { readFastApiErrorDetail } from "@/core/api/read-fastapi-error";
import { getBackendBaseURL } from "@/core/config";

export interface VarConfig {
  label: string;
  type: "string" | "select" | "file";
  required: boolean;
  placeholder?: string;
  options?: string[];
  accept?: string;
  multiple?: boolean;
}

export interface RecipeStepVar {
  label: string;
  type: "string" | "select" | "file";
  required: boolean;
  placeholder?: string;
  options?: string[];
  accept?: string;
  multiple?: boolean;
}

export interface RecipeStep {
  instruction: string;
  template: string;
  example?: string | null;
  tip?: string | null;
  vars?: Record<string, RecipeStepVar> | null;
}

export interface ShowcaseItem {
  url: string;
  caption?: string | null;
}

export interface ShowcaseConfig {
  type: "image" | "gallery" | "video" | "document";
  items: ShowcaseItem[];
}

export interface Recipe {
  title: string;
  goal: string;
  approach?: string | null;
  difficulty: "beginner" | "intermediate" | "advanced";
  est_steps: number;
  context_blocks: string[];
  steps: RecipeStep[];
  pitfalls: string[];
  showcase?: ShowcaseConfig | null;
  source?: Record<string, unknown> | null;
}

export interface GalleryCase {
  id: string;
  title: string;
  skill_name: string;
  skill_description: string;
  category: string;
  thumbnail: string | null;
  prompt: string;
  featured: boolean;
  prompt_template: string | null;
  default_vars: Record<string, string> | null;
  vars_config: Record<string, VarConfig> | null;

  // UGC & Social Extension Fields
  source_type: "official" | "ugc";
  author_name?: string | null;
  likes: number;
  liked_by_me: boolean;
  author_id?: string | null;
  editable_by_me?: boolean;
  created_at?: string | null;

  // Recipe & Showcase (Phase 1 additions)
  recipe?: Recipe | null;
  showcase?: ShowcaseConfig | null;
}

export interface GalleryListResponse {
  cases: GalleryCase[];
  total: number;
}

export interface GallerySettings {
  enabled: boolean;
  ugc_enabled: boolean;
}

export async function loadGallerySettings(): Promise<GallerySettings> {
  const url = `${getBackendBaseURL()}/api/gallery/settings`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to load gallery settings: ${res.statusText}`);
  }
  return res.json();
}

export async function loadGalleryCategories(): Promise<string[]> {
  const url = `${getBackendBaseURL()}/api/gallery/categories`;
  const res = await fetch(url);
  if (!res.ok) {
    return [];
  }
  const data = await res.json();
  return Array.isArray(data?.categories) ? data.categories : [];
}

export async function loadGalleryCases(
  category?: string,
  skillName?: string,
  featured?: boolean,
  likedOnly?: boolean,
): Promise<GalleryListResponse> {
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  if (skillName) params.set("skill_name", skillName);
  if (featured !== undefined) params.set("featured", String(featured));
  if (likedOnly) params.set("liked_only", "true");
  const query = params.toString();
  const url = `${getBackendBaseURL()}/api/gallery/cases${query ? `?${query}` : ""}`;

  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to load gallery cases: ${res.statusText}`);
  }
  return res.json();
}

export async function shareCase(payload: {
  title: string;
  prompt: string;
  category: string;
  skill_name?: string;
  thumbnail?: string | null;
  recipe?: Recipe | null;
}): Promise<GalleryCase> {
  const url = `${getBackendBaseURL()}/api/gallery/cases`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw galleryError(
      await readFastApiErrorDetail(res, "分享失败"),
      res.status,
    );
  }
  return res.json();
}

export async function uploadThumbnail(
  file: File,
): Promise<{ thumbnail: string }> {
  const url = `${getBackendBaseURL()}/api/gallery/upload-thumbnail`;
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(url, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    throw galleryError(
      await readFastApiErrorDetail(res, "缩略图上传失败"),
      res.status,
    );
  }
  return res.json();
}

export async function generateThumbnail(params: {
  title: string;
  category: string;
  prompt?: string;
}): Promise<{ thumbnail: string }> {
  const url = `${getBackendBaseURL()}/api/gallery/generate-thumbnail`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    throw galleryError(
      await readFastApiErrorDetail(res, "缩略图生成失败"),
      res.status,
    );
  }
  return res.json();
}

export async function toggleLikeCase(caseId: string): Promise<{
  likes: number;
  liked_by_me: boolean;
}> {
  const url = `${getBackendBaseURL()}/api/gallery/cases/${caseId}/like`;
  const res = await fetch(url, {
    method: "POST",
  });
  if (!res.ok) {
    throw galleryError(
      await readFastApiErrorDetail(res, "点赞失败"),
      res.status,
    );
  }
  return res.json();
}

export async function updateCase(
  caseId: string,
  patch: {
    title?: string;
    prompt?: string;
    category?: string;
    recipe?: GalleryCase["recipe"];
    thumbnail?: string;
  },
): Promise<GalleryCase> {
  const url = `${getBackendBaseURL()}/api/gallery/cases/${encodeURIComponent(caseId)}`;
  const res = await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    throw galleryError(
      await readFastApiErrorDetail(res, "更新灵感失败"),
      res.status,
    );
  }
  return res.json();
}

export async function deleteCase(
  caseId: string,
): Promise<{ deleted: boolean }> {
  const url = `${getBackendBaseURL()}/api/gallery/cases/${encodeURIComponent(caseId)}`;
  const res = await fetch(url, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw galleryError(
      await readFastApiErrorDetail(res, "删除灵感失败"),
      res.status,
    );
  }
  return res.json();
}

export function compileTemplate(
  template: string,
  vars: Record<string, string>,
): string {
  return template.replace(/\{(\w+)\}/g, (match, key) => vars[key] ?? match);
}

export interface GalleryError extends Error {
  status: number;
}

function galleryError(message: string, status: number): GalleryError {
  const err = new Error(message) as GalleryError;
  err.status = status;
  return err;
}

export async function extractRecipe(
  threadId: string,
  title: string,
  category: string,
): Promise<{ recipe: Recipe }> {
  const url = `${getBackendBaseURL()}/api/gallery/extract-recipe`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId, title, category }),
  });
  if (!res.ok) {
    throw galleryError(
      await readFastApiErrorDetail(res, "配方提取失败"),
      res.status,
    );
  }
  return res.json();
}

export async function instantiateCase(
  caseId: string,
  opts: { vars: Record<string, string>; defer?: boolean },
): Promise<{
  thread_id: string;
  compiled_prompt?: string;
  /** 非 defer 分支才有：服务端已启动的 run，前端据此写 useStream 重连键。 */
  run_id?: string;
}> {
  const url = `${getBackendBaseURL()}/api/gallery/cases/${caseId}/instantiate`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vars: opts.vars, defer: opts.defer ?? false }),
  });
  if (!res.ok) {
    throw galleryError(
      await readFastApiErrorDetail(res, "启动会话失败"),
      res.status,
    );
  }
  return res.json();
}

/**
 * 案例是否含文件类变量（T11 type="file"）——顶层 vars_config 与
 * recipe.steps[].vars 两处挂载点都查。含文件变量的灵感走「先上传再发送」
 * 两步流（defer instantiate + 预填不自动发送，2026-07-27）。
 */
export function caseHasFileVars(c: GalleryCase): boolean {
  const configs = [
    ...Object.values(c.vars_config ?? {}),
    ...(c.recipe?.steps ?? []).flatMap((s) => Object.values(s.vars ?? {})),
  ];
  return configs.some((v) => v?.type === "file");
}

/**
 * 统一校验模板/配方变量：文本类查 vars，文件类查 files（2026-07-31 弹窗内上传）。
 * 返回缺失必填项的 label 列表，空数组 = 通过。
 */
export function validateVarsWithConfigs(
  configs: Record<string, VarConfig | RecipeStepVar>,
  vars: Record<string, string>,
  files: Record<string, File[]>,
): string[] {
  const missing: string[] = [];
  for (const [key, cfg] of Object.entries(configs)) {
    if (!cfg.required) continue;
    if (cfg.type === "file") {
      if (!files[key]?.length) missing.push(cfg.label);
    } else if (!vars[key]?.trim()) {
      missing.push(cfg.label);
    }
  }
  return missing;
}
