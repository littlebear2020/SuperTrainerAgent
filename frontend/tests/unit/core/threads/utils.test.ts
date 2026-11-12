import { expect, test } from "vitest";

import { agentNameOfThread, pathOfThread } from "@/core/threads/utils";

test("agentNameOfThread prefers context then agent metadata then legacy project type", () => {
  expect(
    agentNameOfThread({
      thread_id: "priority",
      context: { agent_name: "course-ppt" },
      metadata: {
        agent_name: "creative-studio",
        project_type: "podcast-studio",
      },
    }),
  ).toBe("ppt-master");
  expect(
    agentNameOfThread({
      thread_id: "metadata",
      metadata: { agent_name: "course-ppt", project_type: "podcast-studio" },
    }),
  ).toBe("ppt-master");
  expect(
    agentNameOfThread({
      thread_id: "legacy",
      metadata: { project_type: "course-ppt" },
    }),
  ).toBe("ppt-master");
});

test.each([
  "card-poster",
  "case-article",
  "design-specialist",
  "knowledge-extraction",
  "video-clip",
])("agentNameOfThread canonicalizes retired creative agent %s", (agentName) => {
  expect(
    agentNameOfThread({
      thread_id: agentName,
      metadata: { project_type: agentName },
    }),
  ).toBe("creative-studio");
});

test("agentNameOfThread never invents an agent for unknown or missing metadata", () => {
  for (const agentName of ["lead_agent", "co-writer", "unknown-agent"]) {
    expect(
      agentNameOfThread({
        thread_id: agentName,
        metadata: { agent_name: agentName },
      }),
    ).toBe(agentName);
  }
  expect(
    agentNameOfThread({ thread_id: "plain", metadata: {} }),
  ).toBeUndefined();
});

test("pathOfThread reuses legacy project metadata and canonical agent aliases", () => {
  expect(
    pathOfThread({
      thread_id: "legacy-ppt",
      metadata: { project_type: "course-ppt" },
    }),
  ).toBe("/workspace/agents/ppt-master/chats/legacy-ppt");
});

test("uses standard chat route when thread has no agent context", () => {
  expect(pathOfThread("thread-123")).toBe("/workspace/chats/thread-123");
  expect(
    pathOfThread({
      thread_id: "thread-123",
    }),
  ).toBe("/workspace/chats/thread-123");
});

test("uses agent chat route when thread context has agent_name", () => {
  expect(
    pathOfThread({
      thread_id: "thread-123",
      context: { agent_name: "researcher" },
    }),
  ).toBe("/workspace/agents/researcher/chats/thread-123");
});

test("uses provided context when pathOfThread is called with a thread id", () => {
  expect(pathOfThread("thread-123", { agent_name: "ops agent" })).toBe(
    "/workspace/agents/ops%20agent/chats/thread-123",
  );
});

test("uses agent chat route when thread metadata has agent_name", () => {
  expect(
    pathOfThread({
      thread_id: "thread-456",
      metadata: { agent_name: "coder" },
    }),
  ).toBe("/workspace/agents/coder/chats/thread-456");
});

test("prefers context.agent_name over metadata.agent_name", () => {
  expect(
    pathOfThread({
      thread_id: "thread-789",
      context: { agent_name: "from-context" },
      metadata: { agent_name: "from-metadata" },
    }),
  ).toBe("/workspace/agents/from-context/chats/thread-789");
});
