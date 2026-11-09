/**
 * 资产中心 API client 测试（Phase 3.5）：mock fetcher 验证 URL/方法/载荷与
 * 响应解包（CSRF 由共享 fetcher 承担，不在本测范围）。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchMock = vi.fn();

vi.mock("@/core/api/fetcher", () => ({
  fetch: (...args: unknown[]) => fetchMock(...args),
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

import {
  applyPptTemplateToProject,
  deletePptTemplate,
  listPptTemplates,
  updatePptTemplate,
  uploadPptTemplate,
} from "@/core/assets/api";

function okJson(body: unknown) {
  return { ok: true, status: 200, json: async () => body };
}

beforeEach(() => {
  fetchMock.mockReset();
});

describe("assets api", () => {
  it("uploadPptTemplate 以 multipart POST 并解包 asset", async () => {
    fetchMock.mockResolvedValue(okJson({ asset: { id: "as_1", name: "N" } }));
    const file = new File(["PK"], "deck.pptx", {
      type: "application/octet-stream",
    });
    const asset = await uploadPptTemplate(file, "品牌A");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8001/api/assets/ppt-templates");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect(asset.id).toBe("as_1");
  });

  it("listPptTemplates 解包 assets 数组", async () => {
    fetchMock.mockResolvedValue(okJson({ assets: [{ id: "as_2" }] }));
    const items = await listPptTemplates();
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/api/assets/ppt-templates");
    expect(items).toHaveLength(1);
  });

  it("updatePptTemplate 走 PUT 且 JSON 载荷含 brand_overrides", async () => {
    fetchMock.mockResolvedValue(okJson({ asset: { id: "as_3" } }));
    await updatePptTemplate("as_3", {
      name: "正式",
      brand_overrides: { remotion_theme: "warm" },
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith("/ppt-templates/as_3")).toBe(true);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({
      name: "正式",
      brand_overrides: { remotion_theme: "warm" },
    });
  });

  it("deletePptTemplate 走 DELETE；apply 走 POST 且 body 含 project_id", async () => {
    fetchMock.mockResolvedValue(okJson({ deleted: "x" }));
    await deletePptTemplate("as_4");
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("DELETE");

    fetchMock.mockResolvedValue(
      okJson({ applied_fields: ["font_family"], logo_note: null }),
    );
    const res = await applyPptTemplateToProject("as_4", "cp_9");
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url.endsWith("/ppt-templates/as_4/apply-to-project")).toBe(true);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ project_id: "cp_9" });
    expect(res.applied_fields).toEqual(["font_family"]);
  });

  it("非 2xx 抛后端 detail", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ detail: "不是有效的 PPTX 文件" }),
    });
    await expect(listPptTemplates()).rejects.toThrow("不是有效的 PPTX 文件");
  });
});
