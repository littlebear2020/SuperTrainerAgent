"use client";

import { ChatProviders } from "@/components/workspace/chats/chat-providers";

export default function AgentChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Prefill moved to the agent PAGE's useEffect (方案 A). The layout must NOT call
  // readPendingPrompt — it consumes sessionStorage on every render, and on a
  // layout-persistence nav the consumed value is thrown away (useState ignores the new
  // initialInput), starving the page useEffect. Provider stays for the controller context.
  return <ChatProviders>{children}</ChatProviders>;
}
