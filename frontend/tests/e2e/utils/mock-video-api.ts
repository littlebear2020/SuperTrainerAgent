import { type Page, type Route } from "@playwright/test";

export type MockVideoTaskStatus =
  | "pending"
  | "processing"
  | "running"
  | "draft"
  | "composing"
  | "qc_repair"
  | "ready"
  | "failed"
  | "cancelled";

export interface MockVideoTask {
  id: string;
  status: MockVideoTaskStatus;
  progress: number;
  task_type?: string;
  title?: string | null;
  created_at?: string;
  updated_at?: string;
  thumbnail_url?: string | null;
  artifact_missing?: boolean;
  error_message?: string | null;
  dirty_frames?: number[];
  course_project_id?: string | null;
  payload?: Record<string, unknown>;
  result?: Record<string, unknown>;
}

export interface MockCourseProject {
  id: string;
  title: string;
  visual_style_preset?: string | null;
  color_palette?: Record<string, string | number | undefined> | null;
  style_anchor_image?: string | null;
  remotion_theme?: string | null;
  font_family?: string | null;
  logo_path?: string | null;
  intro_outro?: {
    intro?: { headline?: string; subtitle?: string };
    outro?: { headline?: string; follow?: string };
  } | null;
  created_at?: string;
  updated_at?: string;
}

export interface MockStoryboardFrame {
  index: number;
  narration: string;
  image_prompt?: string | null;
  search_term?: string | null;
  duration?: number;
  visual_url?: string | null;
  local_visual_path?: string | null;
  visual_type?: string | null;
  visual_source?: string | null;
  preview_state?: "running" | "done" | "error";
  preview_error?: string | null;
}

export interface MockStoryboard {
  title?: string;
  config?: Record<string, unknown>;
  frames?: MockStoryboardFrame[];
  scene_groups?: Array<Record<string, unknown>>;
  total_duration?: number;
  audio_ready?: boolean;
  status?: string;
  progress?: number;
}

export interface RecordedRequest {
  method: string;
  url: string;
  body: unknown;
}

export interface MockVideoAPI {
  readonly createRequests: RecordedRequest[];
  readonly frameUpdateRequests: RecordedRequest[];
  readonly uploadVisualRequests: RecordedRequest[];
  readonly renderRequests: RecordedRequest[];
  readonly downloadRequests: RecordedRequest[];
  readonly retryRequests: RecordedRequest[];
  readonly deleteRequests: RecordedRequest[];
  readonly projectRequests: RecordedRequest[];
  readonly createdTaskId: string | undefined;
  seedTask(task: Partial<MockVideoTask> & { id: string }): void;
  clearTasks(): void;
  failNextList(status?: number): void;
  clearListFailure(): void;
  failNextRetry(status: 409): void;
  seedProject(project: MockCourseProject): MockCourseProject;
  project(id: string): MockCourseProject | undefined;
  setTaskStatus(
    taskId: string,
    status: MockVideoTaskStatus,
    options?: { progress?: number; errorMessage?: string | null },
  ): void;
  upsertStoryboard(
    taskId: string,
    storyboard: MockStoryboard | undefined,
  ): void;
  failNextCreate(status: 422 | 503, detail: unknown): void;
  task(taskId: string): MockVideoTask | undefined;
}

const CREATED_TASK_ID = "vg-created-e2e";

const VOICES = [
  {
    id: "zh-CN-YunjianNeural",
    name: "云健（男）",
    locale: "zh-CN",
    gender: "Male",
    provider: "edge-tts",
  },
  {
    id: "en-US-ChristopherNeural",
    name: "Christopher (Male)",
    locale: "en-US",
    gender: "Male",
    provider: "edge-tts",
  },
];

const BGM = {
  built_in: [
    {
      id: "morning_piano",
      name: "清晨钢琴",
      preview_url: "/api/video/bgm/morning_piano/preview",
      source: "built-in",
      tags: ["钢琴"],
      duration: 179,
    },
  ],
  custom: [],
};

function now(): string {
  return new Date().toISOString();
}

function brief(task: MockVideoTask) {
  return {
    id: task.id,
    user_id: "e2e-user",
    domain: "video_gen",
    task_type: task.task_type ?? "ai_image",
    status: task.status,
    progress: task.progress,
    title: task.title ?? null,
    error_message: task.error_message ?? null,
    retry_count: 0,
    dirty_frames: task.dirty_frames ?? [],
    created_at: task.created_at ?? null,
    updated_at: task.updated_at ?? null,
    thumbnail_url: task.thumbnail_url ?? null,
    artifact_missing: task.artifact_missing ?? false,
    cost: null,
    currency: null,
    course_project_id: task.course_project_id ?? null,
  };
}

function detail(task: MockVideoTask) {
  return {
    ...brief(task),
    payload: task.payload ?? {},
    result: task.result ?? {},
  };
}

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function parseBody(route: Route): unknown {
  const text = route.request().postData();
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

/**
 * Stateful video-gen API mock. All response shapes mirror the routes listed in
 * docs/plans/2026-10-16-frontend-e2e-main-flows-plan.md §1.3.
 */
export function mockVideoAPI(page: Page): MockVideoAPI {
  const tasks = new Map<string, MockVideoTask>();
  const projects = new Map<string, MockCourseProject>();
  const storyboards = new Map<string, MockStoryboard | undefined>();
  const createRequests: RecordedRequest[] = [];
  const frameUpdateRequests: RecordedRequest[] = [];
  const uploadVisualRequests: RecordedRequest[] = [];
  const renderRequests: RecordedRequest[] = [];
  const downloadRequests: RecordedRequest[] = [];
  const retryRequests: RecordedRequest[] = [];
  const deleteRequests: RecordedRequest[] = [];
  const projectRequests: RecordedRequest[] = [];
  let failCreate: { status: 422 | 503; detail: unknown } | undefined;
  let failListStatus: number | undefined;
  let failRetryStatus: 409 | undefined;
  let createdTaskId: string | undefined;
  let projectSequence = 0;

  function seedTask(input: Partial<MockVideoTask> & { id: string }) {
    const seededAt = now();
    const task: MockVideoTask = {
      status: "draft",
      progress: 0,
      task_type: "ai_image",
      error_message: null,
      dirty_frames: [],
      created_at: seededAt,
      updated_at: seededAt,
      ...input,
    };
    tasks.set(task.id, task);
    if (!storyboards.has(task.id)) storyboards.set(task.id, undefined);
    return task;
  }

  function clearTasks() {
    tasks.clear();
    storyboards.clear();
    createdTaskId = undefined;
  }

  function seedProject(input: MockCourseProject) {
    const seededAt = now();
    const project = {
      created_at: seededAt,
      updated_at: seededAt,
      ...input,
    };
    projects.set(project.id, project);
    return project;
  }

  function projectResponse(project: MockCourseProject) {
    return {
      user_id: "e2e-user",
      character_sheets: [],
      prop_assets: [],
      voice_id: "",
      tts_speed: null,
      subtitle_style: "default",
      ...project,
    };
  }

  function setTaskStatus(
    taskId: string,
    status: MockVideoTaskStatus,
    options?: { progress?: number; errorMessage?: string | null },
  ) {
    const task = tasks.get(taskId);
    if (!task) throw new Error(`task fixture must exist: ${taskId}`);
    task.status = status;
    task.progress = options?.progress ?? (status === "ready" ? 100 : 0);
    task.error_message = options?.errorMessage ?? null;
    const storyboard = storyboards.get(taskId);
    if (storyboard?.frames) {
      storyboard.status = status;
      storyboard.progress = task.progress;
    }
  }

  function upsertStoryboard(taskId: string, storyboard: MockStoryboard) {
    storyboards.set(taskId, storyboard);
  }

  seedTask({ id: CREATED_TASK_ID, status: "processing", progress: 10 });
  storyboards.set(CREATED_TASK_ID, { status: "generating", progress: 10 });

  void page.route("**/api/video/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (method === "POST" && path === "/api/video/create") {
      const body = parseBody(route);
      createRequests.push({ method, url: path, body });
      if (failCreate) {
        const failure = failCreate;
        failCreate = undefined;
        return json(route, failure.status, { detail: failure.detail });
      }
      const input = body as { video_type?: string; title?: string };
      const task = seedTask({
        id: CREATED_TASK_ID,
        status: "processing",
        progress: 10,
        task_type: input.video_type ?? "ai_image",
        title: input.title ?? null,
        payload: input as Record<string, unknown>,
      });
      storyboards.set(task.id, { status: "generating", progress: 10 });
      createdTaskId = task.id;
      return json(route, 200, [brief(task)]);
    }

    if (path === "/api/video/projects") {
      if (method === "GET") {
        return json(route, 200, [...projects.values()].map(projectResponse));
      }
      if (method === "POST") {
        const body = parseBody(route) as Partial<MockCourseProject>;
        projectRequests.push({ method, url: path, body });
        projectSequence += 1;
        const project = seedProject({
          id: `project-e2e-${projectSequence}`,
          title: body.title ?? "未命名项目",
          visual_style_preset: body.visual_style_preset ?? null,
          color_palette: body.color_palette ?? null,
        });
        return json(route, 200, projectResponse(project));
      }
    }

    const projectMatch = /^\/api\/video\/projects\/([^/]+)(?:\/(.*))?$/.exec(
      path,
    );
    if (projectMatch) {
      const projectId = decodeURIComponent(projectMatch[1] ?? "");
      const suffix = projectMatch[2] ?? "";
      const project = projects.get(projectId);
      if (!project) return json(route, 404, { detail: "Project not found" });
      if (suffix === "style-anchor-image" && method === "GET") {
        if (!project.style_anchor_image) {
          return json(route, 404, { detail: "Anchor image not found" });
        }
        return route.fulfill({
          status: 200,
          path: `${process.cwd()}/public/course-samples/knowledge-card.webp`,
          contentType: "image/webp",
        });
      }
      if (!suffix && method === "GET") {
        return json(route, 200, projectResponse(project));
      }
      if (!suffix && method === "PUT") {
        const body = parseBody(route) as Partial<MockCourseProject>;
        projectRequests.push({ method, url: path, body });
        Object.assign(project, body, { updated_at: now() });
        return json(route, 200, projectResponse(project));
      }
      if (!suffix && method === "DELETE") {
        projectRequests.push({ method, url: path, body: undefined });
        projects.delete(projectId);
        return route.fulfill({ status: 204, body: "" });
      }
    }

    if (method === "GET" && path === "/api/video/tasks") {
      if (failListStatus !== undefined) {
        return json(route, failListStatus, { detail: "Mock list failure" });
      }
      return json(route, 200, [...tasks.values()].map(brief));
    }

    if (method === "GET" && path === "/api/video/voices") {
      return json(route, 200, VOICES);
    }

    if (path.endsWith("/preview") && path.includes("/voices/")) {
      return route.fulfill({
        status: 200,
        contentType: "audio/mpeg",
        body: Buffer.alloc(0),
      });
    }

    if (method === "GET" && path === "/api/video/bgm") {
      return json(route, 200, BGM);
    }

    if (path.endsWith("/preview") && path.includes("/bgm/")) {
      return route.fulfill({
        status: 200,
        contentType: "audio/mpeg",
        body: Buffer.alloc(0),
      });
    }

    if (method === "GET" && path === "/api/video/templates") {
      return json(route, 200, []);
    }

    if (method === "GET" && path === "/api/video/providers") {
      return json(route, 200, { image: [], video: [] });
    }

    const taskMatch = /^\/api\/video\/tasks\/([^/]+)(?:\/(.*))?$/.exec(path);
    if (taskMatch) {
      const taskId = decodeURIComponent(taskMatch[1] ?? "");
      const suffix = taskMatch[2] ?? "";
      const task = tasks.get(taskId);

      if (!task) return json(route, 404, { detail: "Task not found" });

      if (!suffix && method === "GET") return json(route, 200, detail(task));

      if (!suffix && method === "DELETE") {
        deleteRequests.push({ method, url: path, body: undefined });
        tasks.delete(taskId);
        storyboards.delete(taskId);
        return route.fulfill({ status: 204, body: "" });
      }

      if (suffix === "recompose" && method === "POST") {
        retryRequests.push({ method, url: path, body: parseBody(route) });
        if (failRetryStatus !== undefined) {
          const status = failRetryStatus;
          failRetryStatus = undefined;
          return json(route, status, { detail: "Storyboard edit required" });
        }
        setTaskStatus(taskId, "composing", { progress: 35 });
        return json(route, 200, { task_id: taskId, status: "composing" });
      }

      if (suffix === "storyboard" && method === "GET") {
        const storyboard = storyboards.get(taskId);
        if (!storyboard) {
          return json(route, 404, { detail: "Storyboard not found" });
        }
        return json(route, 200, storyboard);
      }

      if (suffix === "thumbnail" && method === "GET") {
        if (task.artifact_missing || !task.thumbnail_url) {
          return json(route, 404, { detail: "Thumbnail not found" });
        }
        return route.fulfill({
          status: 200,
          path: `${process.cwd()}/public/course-samples/knowledge-card.webp`,
          contentType: "image/webp",
        });
      }

      if (suffix === "download" && method === "GET") {
        downloadRequests.push({ method, url: path, body: undefined });
        return route.fulfill({
          status: 200,
          path: `${process.cwd()}/public/course-samples/image-motion.mp4`,
          contentType: "video/mp4",
        });
      }

      if (suffix === "events" && method === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: "event: done\ndata: {}\n\n",
        });
      }

      if (suffix === "render" && method === "POST") {
        renderRequests.push({ method, url: path, body: parseBody(route) });
        setTaskStatus(taskId, "composing", { progress: 35 });
        return json(route, 200, { ok: true });
      }

      if (suffix.startsWith("storyboard/frames/") && method === "PUT") {
        frameUpdateRequests.push({
          method,
          url: path,
          body: parseBody(route),
        });
        const frameIndex = Number(suffix.split("/").pop());
        const updates = parseBody(route) as Record<string, unknown>;
        const storyboard = storyboards.get(taskId);
        if (storyboard?.frames) {
          storyboard.frames = storyboard.frames.map((frame) =>
            frame.index === frameIndex ? { ...frame, ...updates } : frame,
          );
        }
        return json(route, 200, {
          storyboard,
          updated: Object.fromEntries(
            Object.keys(updates).map((key) => [String(frameIndex), key]),
          ),
        });
      }

      if (suffix === "storyboard/frames" && method === "POST") {
        const body = parseBody(route) as { index?: number };
        const storyboard = storyboards.get(taskId);
        if (storyboard?.frames) {
          const index = body.index ?? storyboard.frames.length;
          storyboard.frames.splice(index, 0, {
            index,
            narration: "（新分镜，请编辑旁白）",
          });
          storyboard.frames = storyboard.frames.map((frame, i) => ({
            ...frame,
            index: i,
          }));
        }
        return json(route, 200, { status: "added" });
      }

      if (
        suffix.startsWith("frames/") &&
        suffix.endsWith("/upload-visual") &&
        method === "POST"
      ) {
        uploadVisualRequests.push({
          method,
          url: path,
          body: route.request().postData(),
        });
        const frameIndex = Number(suffix.split("/")[1]);
        const storyboard = storyboards.get(taskId);
        if (storyboard?.frames) {
          storyboard.frames = storyboard.frames.map((frame) =>
            frame.index === frameIndex
              ? {
                  ...frame,
                  visual_url: null,
                  local_visual_path: `visuals/frame_${String(frameIndex).padStart(3, "0")}.jpg`,
                  visual_type: "image",
                }
              : frame,
          );
        }
        return json(route, 200, {
          ok: true,
          path: `visuals/frame_${String(frameIndex).padStart(3, "0")}.jpg`,
        });
      }

      if (suffix === "storyboard/config" && method === "PUT") {
        const body = parseBody(route) as Record<string, unknown>;
        const storyboard = storyboards.get(taskId);
        if (storyboard) {
          storyboard.config = { ...(storyboard.config ?? {}), ...body };
        }
        return json(route, 200, { ok: true });
      }

      if (suffix === "storyboard/reorder" && method === "PUT") {
        const body = parseBody(route) as {
          from_index: number;
          to_index: number;
        };
        const storyboard = storyboards.get(taskId);
        if (storyboard?.frames) {
          const frames = [...storyboard.frames];
          const [moved] = frames.splice(body.from_index, 1);
          if (moved) frames.splice(body.to_index, 0, moved);
          storyboard.frames = frames.map((frame, index) => ({
            ...frame,
            index,
          }));
        }
        return json(route, 200, { storyboard });
      }
    }

    return json(route, 404, {
      detail: `Unhandled video API: ${method} ${path}`,
    });
  });

  return {
    createRequests,
    frameUpdateRequests,
    uploadVisualRequests,
    renderRequests,
    downloadRequests,
    retryRequests,
    deleteRequests,
    projectRequests,
    get createdTaskId() {
      return createdTaskId;
    },
    seedTask,
    clearTasks,
    failNextList(status = 500) {
      failListStatus = status;
    },
    clearListFailure() {
      failListStatus = undefined;
    },
    failNextRetry(status: 409) {
      failRetryStatus = status;
    },
    seedProject,
    project(projectId: string) {
      return projects.get(projectId);
    },
    setTaskStatus,
    upsertStoryboard,
    failNextCreate(status: 422 | 503, detail: unknown) {
      failCreate = { status, detail };
    },
    task(taskId: string) {
      return tasks.get(taskId);
    },
  };
}
