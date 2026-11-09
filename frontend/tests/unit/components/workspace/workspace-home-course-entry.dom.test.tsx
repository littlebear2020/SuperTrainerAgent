import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  push: vi.fn(),
  dialogProps: [] as Array<Record<string, unknown>>,
  projectThreads: [] as unknown[],
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: h.push }),
}));

vi.mock("@/core/i18n/hooks", async () => {
  const { translations } = await import("@/core/i18n/translations");
  return { useI18n: () => ({ t: translations["zh-CN"] }) };
});

vi.mock("@/core/threads/hooks", () => ({
  useThreads: () => ({
    data: [
      {
        thread_id: "real-thread",
        values: { title: "真实近期项目" },
        metadata: { project_type: "course-video" },
        updated_at: "2026-11-09T08:00:00Z",
      },
    ],
  }),
}));

vi.mock("@/components/workspace/project-list", () => ({
  ProjectList: ({ threads }: { threads: unknown[] }) => {
    h.projectThreads = threads;
    return <div data-testid="project-list">真实近期项目</div>;
  },
}));

vi.mock("@/components/workspace/home-course-entry", async () => {
  const { HOME_COURSE_ITEMS } =
    await import("@/components/workspace/home-course-catalog");
  return {
    HomeCourseEntry: ({
      onSelect,
    }: {
      onSelect: (item: (typeof HOME_COURSE_ITEMS)[number]) => void;
    }) => (
      <div>
        {HOME_COURSE_ITEMS.map((item) => (
          <button key={item.id} onClick={() => onSelect(item)}>
            {item.id}
          </button>
        ))}
      </div>
    ),
  };
});

vi.mock("@/components/workspace/video-gen/video-gen-create-dialog", () => ({
  CreateTaskDialog: (props: Record<string, unknown>) => {
    h.dialogProps.push(props);
    return (
      <div data-testid="create-task-dialog">
        <button
          onClick={() => (props.onOpenChange as (open: boolean) => void)(false)}
        >
          关闭创建框
        </button>
      </div>
    );
  },
}));

import WorkspaceHome from "@/components/workspace/workspace-home";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function entryButton(id: string): HTMLButtonElement {
  const result = Array.from(document.querySelectorAll("button")).find(
    (item) => item.textContent === id,
  );
  if (!result) throw new Error(`entry not found: ${id}`);
  return result;
}

describe("WorkspaceHome course entries", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.push.mockClear();
    h.dialogProps.length = 0;
    h.projectThreads = [];
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    document.body.innerHTML = "";
  });

  beforeEach(async () => {
    await act(async () => root.render(<WorkspaceHome />));
  });

  it("agent cards navigate to existing new-chat routes", async () => {
    for (const agent of [
      "course-video",
      "creative-studio",
      "ppt-master",
      "co-writer",
    ]) {
      await act(async () => entryButton(agent).click());
      expect(h.push).toHaveBeenLastCalledWith(
        `/workspace/agents/${agent}/chats/new`,
      );
    }
  });

  it("AI dialog is conditionally unmounted between alternating entries", async () => {
    expect(
      document.querySelector("[data-testid='create-task-dialog']"),
    ).toBeNull();
    await act(async () => entryButton("ai_image").click());
    expect(h.dialogProps.at(-1)?.initialTaskType).toBe("ai_image");
    await act(async () => entryButton("关闭创建框").click());
    expect(
      document.querySelector("[data-testid='create-task-dialog']"),
    ).toBeNull();

    await act(async () => entryButton("ai_video").click());
    expect(h.dialogProps.at(-1)?.initialTaskType).toBe("ai_video");
    await act(async () => entryButton("关闭创建框").click());
    await act(async () => entryButton("ai_image").click());
    expect(h.dialogProps.at(-1)?.initialTaskType).toBe("ai_image");
    expect(h.dialogProps.map((props) => props.initialTaskType)).toEqual([
      "ai_image",
      "ai_video",
      "ai_image",
    ]);
  });

  it("routes view all recent projects to the existing chats search page", async () => {
    await act(async () => entryButton("查看全部 →").click());
    expect(h.push).toHaveBeenCalledWith("/workspace/chats");
  });

  it("keeps real recent project list", () => {
    expect(h.projectThreads).toHaveLength(1);
    expect(h.projectThreads[0]).toMatchObject({
      thread_id: "real-thread",
      values: { title: "真实近期项目" },
    });
    expect(document.body.textContent).toContain("真实近期项目");
    expect(document.body.textContent).not.toContain("新员工入职培训");
  });
});
