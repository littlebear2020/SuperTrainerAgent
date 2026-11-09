import { ArrowLeft, FileText } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { cache } from "react";

import { Button } from "@/components/ui/button";
import { MarkdownPreview } from "@/components/workspace/writer/markdown-preview";
import type { SharedWriterDoc } from "@/core/writer/types";

/**
 * 2026-10-06 性能批：RSC 化——去 "use client"，服务端取数 + generateMetadata
 * 社交卡片（分享是外部入口）。服务端 fetch 走内部网关绝对地址（同
 * next.config.js rewrites 的 DEER_FLOW_INTERNAL_GATEWAY_BASE_URL 口径）；
 * 客户端 getSharedDoc 的相对 URL 在 RSC 不可用。
 * 同日评估回馈修复：React cache() 去重 metadata+page 双重取数（单请求单回源）；
 * 非 2xx 时透传后端 detail 文案（修前客户端 getSharedDoc 行为，避免错误
 * 诊断信息被通用文案吞掉）。
 */

type SharedDocResult =
  | { doc: SharedWriterDoc; error?: undefined }
  | { doc?: undefined; error: string | null };

const fetchSharedDoc = cache(
  async (token: string): Promise<SharedDocResult> => {
    const base =
      process.env.DEER_FLOW_INTERNAL_GATEWAY_BASE_URL ??
      "http://127.0.0.1:8001";
    try {
      const res = await fetch(
        `${base}/api/writer/share/${encodeURIComponent(token)}`,
        { cache: "no-store" },
      );
      if (res.ok) return { doc: (await res.json()) as SharedWriterDoc };
      // 透传 FastAPI {detail}（getSharedDoc 客户端版同口径）；非 JSON 落 null
      const body = (await res.json().catch(() => null)) as {
        detail?: unknown;
      } | null;
      const detail =
        typeof body?.detail === "string" && body.detail.trim()
          ? body.detail
          : null;
      return { error: detail };
    } catch {
      return { error: "无法连接服务器，请稍后重试" };
    }
  },
);

function SharedDocNotFound({ reason }: { reason: string | null }) {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4">
      <FileText size={48} className="text-[var(--muted-foreground)]/40" />
      <p className="text-lg font-medium text-[var(--foreground)]">
        文档不存在或已过期
      </p>
      <p className="text-sm text-[var(--muted-foreground)]">
        {reason ?? "请检查分享链接是否正确"}
      </p>
      <Link href="/">
        <Button variant="outline" size="sm">
          <ArrowLeft className="mr-1 h-3.5 w-3.5" />
          返回首页
        </Button>
      </Link>
    </div>
  );
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ token: string }>;
}): Promise<Metadata> {
  const { token } = await params;
  const { doc } = await fetchSharedDoc(token);
  const title = doc ? `${doc.title} — Trainer AI` : "文档分享 — Trainer AI";
  const description = doc
    ? "阅读这份由 Trainer AI 创作的分享文档"
    : "文档不存在或已过期";
  return {
    title,
    description,
    openGraph: { title, description, type: "article" },
    twitter: { card: "summary", title, description },
  };
}

export default async function SharedWriterPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  const { doc, error } = await fetchSharedDoc(token);

  if (!doc) {
    return <SharedDocNotFound reason={error ?? null} />;
  }

  return (
    <div className="flex h-screen flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-[var(--border)] px-6 py-3">
        <FileText size={18} className="text-[var(--muted-foreground)]" />
        <div>
          <div className="text-sm font-semibold text-[var(--foreground)]">
            {doc.title}
          </div>
          <div className="text-xs text-[var(--muted-foreground)]">
            分享文档 · 只读
          </div>
        </div>
      </header>
      <main className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-3xl">
          <MarkdownPreview content={doc.content_json} />
        </div>
      </main>
    </div>
  );
}
