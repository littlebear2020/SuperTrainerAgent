"use client";

import { useSearchParams } from "next/navigation";

import { ChatProviders } from "@/components/workspace/chats/chat-providers";

export default function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const searchParams = useSearchParams();
  const initialMessage = searchParams.get("msg");
  // SEC-H11：URL autosend 参数已移除——?msg= 只预填输入框，用户手动发送
  // （自动发送唯一通道是 sessionStorage pending prompt，见 chat page）。
  const initialInput = initialMessage ?? "";

  return <ChatProviders initialInput={initialInput}>{children}</ChatProviders>;
}
