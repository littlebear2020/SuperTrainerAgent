"use client";

import { use } from "react";

import { WriterPage } from "@/components/workspace/writer";

export default function Page({
  params,
}: {
  params: Promise<{ doc_id: string }>;
}) {
  // Next.js 16: params is a Promise
  return <WriterPageWithParams params={params} />;
}

function WriterPageWithParams({
  params,
}: {
  params: Promise<{ doc_id: string }>;
}) {
  const { doc_id } = use(params);
  return <WriterPage docId={doc_id} />;
}
