import { describe, expect, it, vi } from "vitest";

import type { AgentThread } from "@/core/threads/types";
import type { VideoTaskBrief } from "@/core/video-gen/types";
import {
  TASK_TYPE_CAPABILITY,
  WORK_CAPABILITY_SOURCES,
} from "@/core/works/capability-sources";
import {
  buildAgentWorks,
  buildTaskWorks,
  chunkThreadIds,
  selectAndFilterWorks,
} from "@/core/works/model";
import type {
  AgentDeliveryState,
  ArtifactFileSummary,
  ThreadArtifactSummary,
} from "@/core/works/types";
import { WORK_CAPABILITY_IDS } from "@/core/workspace/video-assets-route";

vi.mock("@/core/config", () => ({ getBackendBaseURL: () => "http://backend" }));

function makeTask(
  id: string,
  overrides: Partial<VideoTaskBrief> = {},
): VideoTaskBrief {
  return {
    id,
    user_id: "user-1",
    domain: "video_gen",
    task_type: "ai_video",
    status: "ready",
    progress: 100,
    title: `Title ${id}`,
    error_message: null,
    retry_count: 0,
    dirty_frames: [],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
    thumbnail_url: null,
    artifact_missing: false,
    cost: null,
    currency: null,
    ...overrides,
  };
}

function makeThread(
  id: string,
  overrides: {
    agentName?: string;
    projectType?: string;
    title?: string;
    createdAt?: string | null;
    updatedAt?: string | null;
  } = {},
): AgentThread {
  return {
    thread_id: id,
    created_at:
      overrides.createdAt === undefined
        ? "2026-01-01T00:00:00Z"
        : overrides.createdAt,
    updated_at:
      overrides.updatedAt === undefined
        ? "2026-01-02T00:00:00Z"
        : overrides.updatedAt,
    metadata: overrides.projectType
      ? { project_type: overrides.projectType }
      : { agent_name: overrides.agentName ?? "course-video" },
    status: "idle",
    values: {
      title: overrides.title ?? `Thread ${id}`,
      messages: [],
      artifacts: [],
    },
    interrupts: {},
  } as unknown as AgentThread;
}

function makeFile(
  path: string,
  overrides: Partial<ArtifactFileSummary> = {},
): ArtifactFileSummary {
  return {
    path,
    name: path.split("/").pop() ?? path,
    media_kind: "other",
    size: 10,
    modified_at: "2026-01-03T00:00:00Z",
    exists: true,
    ...overrides,
  };
}

function makeSummary(
  threadId: string,
  deliveryState: AgentDeliveryState = "not_delivered",
  files: ArtifactFileSummary[] = [],
): ThreadArtifactSummary {
  return { thread_id: threadId, delivery_state: deliveryState, files };
}

describe("capability sources", () => {
  it("covers all six capabilities and every task type explicitly", () => {
    expect(Object.keys(WORK_CAPABILITY_SOURCES)).toEqual([
      ...WORK_CAPABILITY_IDS,
    ]);
    expect(WORK_CAPABILITY_SOURCES).toEqual({
      "course-video": {
        agentNames: ["course-video"],
        taskTypes: ["remotion_course", "remotion"],
      },
      ai_image: { agentNames: [], taskTypes: ["ai_image", "ai_visual"] },
      ai_video: { agentNames: [], taskTypes: ["ai_video", "vimax"] },
      "creative-studio": { agentNames: ["creative-studio"], taskTypes: [] },
      "ppt-master": { agentNames: ["ppt-master"], taskTypes: [] },
      "podcast-studio": { agentNames: ["podcast-studio"], taskTypes: [] },
    });
    expect(TASK_TYPE_CAPABILITY).toEqual({
      ai_image: "ai_image",
      ai_visual: "ai_image",
      ai_video: "ai_video",
      vimax: "ai_video",
      remotion_course: "course-video",
      remotion: "course-video",
    });
  });

  it.each([
    ["remotion_course", "course-video"],
    ["remotion", "course-video"],
    ["ai_image", "ai_image"],
    ["ai_visual", "ai_image"],
    ["ai_video", "ai_video"],
    ["vimax", "ai_video"],
  ] as const)("maps %s only into %s", (taskType, capability) => {
    const tasks = [makeTask("same-task", { task_type: taskType })];

    for (const candidate of WORK_CAPABILITY_IDS) {
      expect(buildTaskWorks(tasks, candidate)).toHaveLength(
        candidate === capability ? 1 : 0,
      );
    }
  });

  it("combines Agent and Remotion sources without cross-source ID collisions", () => {
    const agentWorks = buildAgentWorks(
      [makeThread("same-id")],
      [makeSummary("same-id")],
      "course-video",
    );
    const taskWorks = buildTaskWorks(
      [makeTask("same-id", { task_type: "remotion_course" })],
      "course-video",
    );

    expect([...agentWorks, ...taskWorks].map((work) => work.id)).toEqual([
      "thread:same-id",
      "task:same-id",
    ]);
  });
});

describe("buildAgentWorks", () => {
  it("filters exactly, supports legacy aliases, dedupes, and preserves blank titles", () => {
    const threads = [
      makeThread("b", { title: "  " }),
      makeThread("a", { projectType: "course-video", agentName: undefined }),
      makeThread("b", { title: "duplicate" }),
      makeThread("lead", { agentName: "lead_agent" }),
      makeThread("unknown", { agentName: "unknown-agent" }),
    ];

    const works = buildAgentWorks(
      threads,
      [makeSummary("a"), makeSummary("b")],
      "course-video",
    );

    expect(works.map((work) => work.id)).toEqual(["thread:b", "thread:a"]);
    expect(works[0]?.title).toBe("");
    expect(works[0]?.sortAt).toBe("2026-01-02T00:00:00Z");
    expect(works[1]?.sortAt).toBe("2026-01-02T00:00:00Z");
  });

  it.each([
    "delivered",
    "not_delivered",
    "partial",
    "missing",
    "unknown",
  ] as const)("preserves Agent delivery state %s", (state) => {
    const [work] = buildAgentWorks(
      [makeThread("thread-1")],
      [makeSummary("thread-1", state)],
      "course-video",
    );

    expect(work?.agentDeliveryState).toBe(state);
  });

  it("selects the first readable file and creates safe preview/download URLs", () => {
    const files = [
      makeFile("/mnt/user-data/outputs/missing.mp4", {
        media_kind: "video",
        exists: false,
        size: null,
        modified_at: null,
      }),
      makeFile("/mnt/user-data/outputs/deck.pptx", {
        media_kind: "document",
      }),
      makeFile("/mnt/user-data/outputs/海报 final.png", {
        media_kind: "image",
      }),
    ];

    const [work] = buildAgentWorks(
      [makeThread("thread-1")],
      [makeSummary("thread-1", "partial", files)],
      "course-video",
    );

    expect(work?.files.map((file) => file.id)).toEqual([
      "thread:thread-1:/mnt/user-data/outputs/missing.mp4",
      "thread:thread-1:/mnt/user-data/outputs/deck.pptx",
      "thread:thread-1:/mnt/user-data/outputs/海报 final.png",
    ]);
    expect(work?.files[0]?.previewUrl).toBeNull();
    expect(work?.files[0]?.downloadUrl).toBeNull();
    expect(work?.files[1]?.previewUrl).toBeNull();
    expect(work?.files[1]?.downloadUrl).toContain("?download=true");
    expect(work?.files[2]?.previewUrl).toBe(
      "http://backend/api/threads/thread-1/artifacts/mnt/user-data/outputs/%E6%B5%B7%E6%8A%A5%20final.png",
    );
    expect(work?.primaryFile?.name).toBe("deck.pptx");
  });

  it("uses unknown rather than fabricating not_delivered when a summary is absent", () => {
    const [work] = buildAgentWorks(
      [makeThread("thread-1")],
      [],
      "course-video",
    );

    expect(work?.agentDeliveryState).toBe("unknown");
    expect(work?.files).toEqual([]);
    expect(work?.primaryFile).toBeNull();
  });
});

describe("buildTaskWorks", () => {
  it("keeps old backends usable with an unnamed current output", () => {
    const [work] = buildTaskWorks(
      [makeTask("task-1", { delivery: undefined })],
      "ai_video",
    );

    expect(work?.files).toHaveLength(1);
    expect(work?.primaryFile).toMatchObject({
      id: "task:task-1:output",
      name: null,
      mediaKindGuessed: true,
      mediaKind: "video",
      size: null,
      modifiedAt: null,
      exists: true,
      previewUrl: "http://backend/api/video/tasks/task-1/download",
      downloadUrl: "http://backend/api/video/tasks/task-1/download",
    });
  });

  it("uses safe delivery metadata without adding any host path", () => {
    const [work] = buildTaskWorks(
      [
        makeTask("task-1", {
          delivery: {
            file_name: "lesson.mp4",
            file_size: 0,
            file_updated_at: "2026-02-01T00:00:00Z",
            media_kind: "video",
            exists: true,
          },
        }),
      ],
      "ai_video",
    );

    expect(work?.primaryFile).toMatchObject({
      name: "lesson.mp4",
      size: 0,
      modifiedAt: "2026-02-01T00:00:00Z",
      exists: true,
    });
    expect(JSON.stringify(work)).not.toContain("output_path");
    expect(JSON.stringify(work)).not.toContain("/private/");
  });

  it.each([
    { status: "draft", artifact_missing: false },
    { status: "failed", artifact_missing: false },
    { status: "ready", artifact_missing: true },
  ])("gates media for $status and missing=$artifact_missing", (overrides) => {
    const [work] = buildTaskWorks([makeTask("task-1", overrides)], "ai_video");

    expect(work?.primaryFile).toBeNull();
    expect(work?.files.every((file) => file.downloadUrl === null)).toBe(true);
    expect(work?.files.every((file) => file.previewUrl === null)).toBe(true);
  });

  it("retains missing file facts from a new backend without enabling actions", () => {
    const [work] = buildTaskWorks(
      [
        makeTask("task-1", {
          artifact_missing: true,
          delivery: {
            file_name: "removed.mp4",
            file_size: null,
            file_updated_at: null,
            media_kind: "video",
            exists: false,
          },
        }),
      ],
      "ai_video",
    );

    expect(work?.files).toHaveLength(1);
    expect(work?.files[0]).toMatchObject({
      name: "removed.mp4",
      exists: false,
      previewUrl: null,
      downloadUrl: null,
    });
  });
});

describe("selectAndFilterWorks", () => {
  it("sorts by updated-or-created time, puts invalid dates last, and uses IDs for ties", () => {
    const tasks = [
      makeTask("b", { created_at: "2026-01-01T00:00:00Z" }),
      makeTask("a", { created_at: "2026-01-01T00:00:00Z" }),
      makeTask("updated", {
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-03-01T00:00:00Z",
      }),
      makeTask("created", {
        created_at: "2026-02-01T00:00:00Z",
        updated_at: null,
      }),
      makeTask("bad", { created_at: "not-a-date" }),
      makeTask("empty", { created_at: null }),
    ];
    const input = buildTaskWorks(tasks, "ai_video");
    const originalOrder = input.map((work) => work.id);

    const result = selectAndFilterWorks(input, "");

    expect(result.map((work) => work.id)).toEqual([
      "task:updated",
      "task:created",
      "task:a",
      "task:b",
      "task:bad",
      "task:empty",
    ]);
    expect(input.map((work) => work.id)).toEqual(originalOrder);
  });

  it("searches trimmed case-insensitive titles and file names after normalization", () => {
    const works = [
      ...buildTaskWorks(
        [
          makeTask("title", { title: "  Advanced COURSE Design " }),
          makeTask("blank", { title: null }),
        ],
        "ai_video",
      ),
      ...buildAgentWorks(
        [makeThread("file", { title: "Unrelated" })],
        [
          makeSummary("file", "delivered", [
            makeFile("/mnt/user-data/outputs/Final Lesson.MP4", {
              media_kind: "video",
            }),
          ]),
        ],
        "course-video",
      ),
    ];

    expect(
      selectAndFilterWorks(works, "  course design ").map((work) => work.id),
    ).toEqual(["task:title"]);
    expect(
      selectAndFilterWorks(works, "final lesson").map((work) => work.id),
    ).toEqual(["thread:file"]);
    expect(selectAndFilterWorks(works, "no match")).toEqual([]);
  });

  it("does not reintroduce the old 100-item cap", () => {
    const works = buildTaskWorks(
      Array.from({ length: 103 }, (_, index) => makeTask(`task-${index}`)),
      "ai_video",
    );

    expect(selectAndFilterWorks(works, "")).toHaveLength(103);
  });
});

describe("chunkThreadIds", () => {
  it("sorts and deduplicates before cutting 50-item batches", () => {
    const ids = [
      ...Array.from(
        { length: 101 },
        (_, index) => `thread-${String(index).padStart(3, "0")}`,
      ),
      "thread-000",
    ].reverse();

    const batches = chunkThreadIds(ids);

    expect(batches.map((batch) => batch.length)).toEqual([50, 50, 1]);
    expect(batches[0]?.[0]).toBe("thread-000");
    expect(batches[0]).toHaveLength(new Set(batches[0]).size);
    expect(batches.flat()).toHaveLength(101);
    expect(chunkThreadIds([])).toEqual([]);
  });
});
