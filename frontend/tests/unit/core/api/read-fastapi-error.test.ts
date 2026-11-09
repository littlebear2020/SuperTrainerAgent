import { describe, expect, it } from "vitest";

import {
  formatFastApiErrorDetail,
  readFastApiErrorDetail,
} from "@/core/api/read-fastapi-error";

describe("formatFastApiErrorDetail", () => {
  it("string detail 原样返回", () => {
    expect(formatFastApiErrorDetail("task not found")).toBe("task not found");
  });

  it("Pydantic 422 数组：提取 msg 并 ; 连接", () => {
    const detail = [
      { loc: ["body", "title"], msg: "Field required", type: "missing" },
      { msg: "too long" },
    ];
    expect(formatFastApiErrorDetail(detail)).toBe("Field required; too long");
  });

  it("数组内无 msg 的对象退化为 JSON 序列化", () => {
    expect(formatFastApiErrorDetail([{ code: 42 }])).toBe('{"code":42}');
  });

  it("混排 string 与对象的数组全部保留", () => {
    expect(formatFastApiErrorDetail(["a", { msg: "b" }])).toBe("a; b");
  });

  it("对象 detail 序列化为 JSON", () => {
    expect(formatFastApiErrorDetail({ message: "x" })).toBe('{"message":"x"}');
  });

  it("原始值 detail 转 String，undefined/null 返回 null（空串原样，由 reader 的 truthy 守卫兜底）", () => {
    expect(formatFastApiErrorDetail(404)).toBe("404");
    expect(formatFastApiErrorDetail(null)).toBeNull();
    expect(formatFastApiErrorDetail(undefined)).toBeNull();
    expect(formatFastApiErrorDetail("")).toBe("");
  });
});

describe("readFastApiErrorDetail", () => {
  it("读取 {detail: string}", async () => {
    const res = new Response(JSON.stringify({ detail: "boom" }), {
      status: 409,
    });
    expect(await readFastApiErrorDetail(res, "fallback")).toBe("boom");
  });

  it("读取 422 数组 detail", async () => {
    const res = new Response(
      JSON.stringify({ detail: [{ msg: "Field required" }] }),
      { status: 422 },
    );
    expect(await readFastApiErrorDetail(res, "fallback")).toBe(
      "Field required",
    );
  });

  it("body 非 JSON → fallback 带 HTTP status（指明服务端错误）", async () => {
    const res = new Response("<html>proxy error</html>", { status: 502 });
    expect(await readFastApiErrorDetail(res, "Failed")).toBe(
      "Failed (HTTP 502)",
    );
  });

  it("body JSON 无 detail → fallback 带 HTTP status", async () => {
    const res = new Response(JSON.stringify({}), { status: 500 });
    expect(await readFastApiErrorDetail(res, "Failed")).toBe(
      "Failed (HTTP 500)",
    );
  });

  it("detail 为空串视同不可解析 → fallback", async () => {
    const res = new Response(JSON.stringify({ detail: "" }), { status: 400 });
    expect(await readFastApiErrorDetail(res, "Failed")).toBe(
      "Failed (HTTP 400)",
    );
  });
});
