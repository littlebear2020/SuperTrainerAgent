import { expect, test, vi, beforeEach } from "vitest";

import { fetch } from "@/core/api/fetcher";
import {
  getFrameVisualUrl,
  listBGM,
  uploadBGM,
  uploadFrameVisual,
} from "@/core/video-gen/api";

// Mock the fetch wrapper and config before importing the api module
vi.mock("@/core/api/fetcher", () => ({
  fetch: vi.fn(),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

const mockFetch = vi.mocked(fetch);

/** Cast partial mock response to Response for vitest mocks. */
function asRes(obj: Record<string, unknown>): Response {
  return obj as unknown as Response;
}

beforeEach(() => {
  mockFetch.mockReset();
});

// ── listBGM ──────────────────────────────────────────────────────────────

test("listBGM calls GET /api/video/bgm and returns built_in + custom", async () => {
  const bgmList = {
    built_in: [
      {
        id: "morning_piano",
        name: "清晨钢琴",
        preview_url: "/api/video/bgm/morning_piano/preview",
        source: "built-in",
        tags: ["钢琴", "清晨", "轻快"],
        duration: 179,
      },
    ],
    custom: [
      {
        id: "custom_abc12345",
        name: "my_song.mp3",
        preview_url: "/api/video/bgm/custom_abc12345/preview",
        source: "custom",
      },
    ],
  };
  mockFetch.mockResolvedValueOnce(
    asRes({ ok: true, status: 200, json: () => Promise.resolve(bgmList) }),
  );

  const result = await listBGM();
  expect(result).toEqual(bgmList);
  expect(mockFetch).toHaveBeenCalledWith("http://localhost:8001/api/video/bgm");
});

test("listBGM throws on non-ok response", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: false,
      status: 500,
      json: () => Promise.resolve({ detail: "Internal error" }),
    }),
  );

  await expect(listBGM()).rejects.toThrow("Internal error");
});

// ── uploadBGM ────────────────────────────────────────────────────────────

test("uploadBGM sends file via FormData to POST /api/video/bgm/upload", async () => {
  const uploadResponse = {
    id: "custom_def67890",
    name: "uploaded.mp3",
    preview_url: "/api/video/bgm/custom_def67890/preview",
  };
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: true,
      status: 200,
      json: () => Promise.resolve(uploadResponse),
    }),
  );

  const file = new File(["audio data"], "uploaded.mp3", { type: "audio/mpeg" });
  const result = await uploadBGM(file);
  expect(result).toEqual(uploadResponse);

  const [url, options] = mockFetch.mock.calls[0]!;
  expect(url).toBe("http://localhost:8001/api/video/bgm/upload");
  expect(options).toEqual(expect.objectContaining({ method: "POST" }));
  expect(options!.body).toBeInstanceOf(FormData);
});

test("uploadBGM throws on non-ok response", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: false,
      status: 400,
      json: () => Promise.resolve({ detail: "File too large (max 30MB)" }),
    }),
  );

  const file = new File(["x"], "big.mp3", { type: "audio/mpeg" });
  await expect(uploadBGM(file)).rejects.toThrow("File too large");
});

// ── uploadFrameVisual ────────────────────────────────────────────────────

test("uploadFrameVisual sends file to POST /api/video/tasks/{id}/frames/{idx}/upload-visual", async () => {
  const uploadResult = { ok: true, path: "visuals/frame_000.jpg" };
  mockFetch.mockResolvedValueOnce(
    asRes({ ok: true, status: 200, json: () => Promise.resolve(uploadResult) }),
  );

  const file = new File(["\xff\xd8\xff\xe0fake"], "test.jpg", {
    type: "image/jpeg",
  });
  const result = await uploadFrameVisual("task-123", 0, file);
  expect(result).toEqual(uploadResult);

  const [url, options] = mockFetch.mock.calls[0]!;
  expect(url).toBe(
    "http://localhost:8001/api/video/tasks/task-123/frames/0/upload-visual",
  );
  expect(options).toEqual(expect.objectContaining({ method: "POST" }));
  expect(options!.body).toBeInstanceOf(FormData);
});

test("uploadFrameVisual encodes task ID with special chars", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ ok: true, path: "visuals/frame_002.mp4" }),
    }),
  );

  const file = new File(["data"], "clip.mp4", { type: "video/mp4" });
  await uploadFrameVisual("task/with+special", 2, file);

  const [url] = mockFetch.mock.calls[0]!;
  expect(url).toContain("task%2Fwith%2Bspecial");
  expect(url).toContain("/frames/2/upload-visual");
});

test("uploadFrameVisual throws on non-ok response", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: false,
      status: 400,
      json: () => Promise.resolve({ detail: "Unsupported file type" }),
    }),
  );

  const file = new File(["data"], "test.txt", { type: "text/plain" });
  await expect(uploadFrameVisual("task-123", 0, file)).rejects.toThrow(
    "Unsupported file type",
  );
});

test("uploadFrameVisual throws on task not found (404)", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: "Task not found" }),
    }),
  );

  const file = new File(["data"], "test.jpg", { type: "image/jpeg" });
  await expect(uploadFrameVisual("nonexistent", 0, file)).rejects.toThrow(
    "Task not found",
  );
});

// ── getFrameVisualUrl (Task 3 / F8: cache-buster) ───────────────────────

test("getFrameVisualUrl without version keeps plain URL", () => {
  expect(getFrameVisualUrl("vg_abc", 3)).toBe(
    "http://localhost:8001/api/video/tasks/vg_abc/frames/3/visual",
  );
});

test("getFrameVisualUrl with version appends cache-buster", () => {
  expect(getFrameVisualUrl("vg_abc", 3, 1721352000000)).toBe(
    "http://localhost:8001/api/video/tasks/vg_abc/frames/3/visual?v=1721352000000",
  );
});
