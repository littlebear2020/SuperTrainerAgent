/**
 * 资产中心 API client（Phase 3.5，2026-09-11）。
 *
 * 平台级资产注册表；首个类型 `ppt_template`（上传 PPT → 蒸馏 → 预览 → 确认 →
 * 应用到课程项目）。全部走 CSRF-aware fetcher（写操作）。
 */

import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

const BASE = `${getBackendBaseURL()}/api/assets`;

export interface TrainerBrand {
  schema_version: number;
  distiller: string;
  palette: { primary: string | null; colors: Record<string, string> };
  typography: { title_font?: string; body_font?: string };
  font_family: string | null;
  logo: {
    filename: string | null;
    source_part: string | null;
    candidates: {
      part: string;
      refs: number;
      bytes: number;
      displayable: boolean;
    }[];
  };
  style_tendency: { remotion_theme: string; reason: string };
  layouts: string[];
}

export interface PptTemplateAsset {
  id: string;
  type: "ppt_template";
  name: string;
  status: string;
  source_filename?: string | null;
  brand?: TrainerBrand | null;
  created_at?: string;
  updated_at?: string;
  logo_url?: string;
}

export interface PptTemplateSummary {
  id: string;
  type: "ppt_template";
  name: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  palette: TrainerBrand["palette"] | null;
  font_family: string | null;
  remotion_theme: string | null;
  has_logo: boolean;
}

export interface BrandOverrides {
  font_family?: string;
  primary?: string;
  remotion_theme?: "dark-blue" | "light" | "dark-green" | "warm";
}

/**
 * PPT 模板上传上限（条目 23，2026-09-12 缺陷批）：与后端
 * backend/app/gateway/routers/assets.py MAX_PPTX_BYTES 同值（50→100MB 用户拍板）。
 * 改任一侧必须同步另一侧 + nginx dedicated location（110M）。
 */
export const MAX_PPTX_BYTES = 100 * 1024 * 1024;

async function readError(res: Response, fallback: string): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: string };
    return data.detail ?? fallback;
  } catch {
    return fallback;
  }
}

export async function uploadPptTemplate(
  file: File,
  name: string,
): Promise<PptTemplateAsset> {
  const form = new FormData();
  form.append("file", file);
  form.append("name", name);
  const res = await fetch(`${BASE}/ppt-templates`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await readError(res, "上传失败"));
  return ((await res.json()) as { asset: PptTemplateAsset }).asset;
}

export async function listPptTemplates(): Promise<PptTemplateSummary[]> {
  const res = await fetch(`${BASE}/ppt-templates`);
  if (!res.ok) throw new Error(await readError(res, "模板列表加载失败"));
  return ((await res.json()) as { assets: PptTemplateSummary[] }).assets;
}

export async function updatePptTemplate(
  id: string,
  body: { name?: string; brand_overrides?: BrandOverrides },
): Promise<PptTemplateAsset> {
  const res = await fetch(`${BASE}/ppt-templates/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readError(res, "保存失败"));
  return ((await res.json()) as { asset: PptTemplateAsset }).asset;
}

export async function deletePptTemplate(id: string): Promise<void> {
  const res = await fetch(`${BASE}/ppt-templates/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await readError(res, "删除失败"));
}

/**
 * 重新蒸馏（E1/E2，2026-09-23 批 2）：source.pptx 重跑确定性蒸馏，逐字段合并
 * ——`_confirmed`（主色/字体/主题）保留用户确认值，其余刷新。
 * `brand_diff.legacy_overwrite_warning` 非空 = 存量资产无标记，全量刷新。
 */
export interface RedistillResult {
  asset: PptTemplateAsset;
  brand_diff: {
    changed: string[];
    before: Record<string, unknown>;
    after: Record<string, unknown>;
    preserved_user_confirmed?: string[];
    legacy_overwrite_warning?: string;
  };
}

export async function redistillPptTemplate(
  id: string,
): Promise<RedistillResult> {
  const res = await fetch(
    `${BASE}/ppt-templates/${encodeURIComponent(id)}/redistill`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(await readError(res, "重新蒸馏失败"));
  return await res.json();
}

export async function applyPptTemplateToProject(
  id: string,
  projectId: string,
): Promise<{ applied_fields: string[]; logo_note: string | null }> {
  const res = await fetch(
    `${BASE}/ppt-templates/${encodeURIComponent(id)}/apply-to-project`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId }),
    },
  );
  if (!res.ok) throw new Error(await readError(res, "应用失败"));
  return await res.json();
}

export function pptTemplateLogoUrl(id: string): string {
  return `${BASE}/ppt-templates/${encodeURIComponent(id)}/logo`;
}
