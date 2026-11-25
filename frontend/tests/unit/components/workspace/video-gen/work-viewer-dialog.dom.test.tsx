import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkMedia } from "@/components/workspace/video-gen/work-media";
import { WorkViewerDialog } from "@/components/workspace/video-gen/work-viewer-dialog";
import type { AgentThread } from "@/core/threads/types";
import type { VideoTaskBrief } from "@/core/video-gen/types";
import type { WorkFile, WorkMediaKind, WorkSummary } from "@/core/works/types";

vi.mock("@/core/i18n/hooks", async () => {
  const { translations } = await import("@/core/i18n/translations");
  return { useI18n: () => ({ t: translations["zh-CN"] }) };
});

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function makeFile(
  name: string,
  mediaKind: WorkMediaKind,
  overrides: Partial<WorkFile> = {},
): WorkFile {
  const url = `https://media.test/${encodeURIComponent(name)}`;
  return {
    id: `file:${name}`,
    name,
    mediaKind,
    size: 1234,
    modifiedAt: "2026-01-02T00:00:00Z",
    exists: true,
    previewUrl: ["video", "image", "audio"].includes(mediaKind) ? url : null,
    downloadUrl: url,
    ...overrides,
  };
}

const thread = {
  thread_id: "thread-1",
  metadata: { agent_name: "course-video" },
  values: { title: "Agent work", messages: [], artifacts: [] },
} as unknown as AgentThread;

const task: VideoTaskBrief = {
  id: "task-1",
  user_id: "user-1",
  domain: "video_gen",
  task_type: "ai_video",
  status: "ready",
  progress: 100,
  title: "Task work",
  error_message: null,
  retry_count: 0,
  dirty_frames: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
  thumbnail_url: null,
  cost: null,
  currency: null,
};

function makeWork(
  files: WorkFile[],
  options: { source?: "agent" | "task"; primaryFile?: WorkFile | null } = {},
): WorkSummary {
  const source = options.source ?? "agent";
  return {
    id: source === "agent" ? "thread:thread-1" : "task:task-1",
    capability: source === "agent" ? "course-video" : "ai_video",
    title: source === "agent" ? "Agent work" : "Task work",
    source:
      source === "agent"
        ? { kind: "agent", thread }
        : { kind: "video_task", task },
    agentDeliveryState: source === "agent" ? "delivered" : null,
    sortAt: "2026-01-02T00:00:00Z",
    files,
    primaryFile:
      options.primaryFile === undefined
        ? (files.find((file) => file.exists) ?? null)
        : options.primaryFile,
  };
}

function button(text: string): HTMLButtonElement {
  const result = Array.from(document.querySelectorAll("button")).find((item) =>
    item.textContent?.includes(text),
  );
  if (!result) throw new Error(`button not found: ${text}`);
  return result;
}

describe("WorkViewerDialog", () => {
  let container: HTMLDivElement;
  let root: Root;
  let onOpenChange: ReturnType<typeof vi.fn<(open: boolean) => void>>;
  let onContinue: ReturnType<typeof vi.fn<(work: WorkSummary) => void>>;
  let onTaskNavigate: ReturnType<typeof vi.fn<(task: VideoTaskBrief) => void>>;

  beforeEach(() => {
    onOpenChange = vi.fn();
    onContinue = vi.fn();
    onTaskNavigate = vi.fn();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    document.body.innerHTML = "";
  });

  async function render(work: WorkSummary | null, open = true) {
    await act(async () => {
      root.render(
        <WorkViewerDialog
          work={work}
          open={open}
          onOpenChange={onOpenChange}
          onContinue={onContinue}
          onTaskNavigate={onTaskNavigate}
        />,
      );
    });
  }

  it("uses non-autoplay controls and switches between multiple delivery files", async () => {
    const video = makeFile("lesson.mp4", "video");
    const audio = makeFile("voice.mp3", "audio");
    await render(makeWork([video, audio]));

    const videoElement = document.querySelector("video");
    expect(videoElement?.controls).toBe(true);
    expect(videoElement?.autoplay).toBe(false);
    expect(videoElement?.getAttribute("preload")).toBe("metadata");
    expect(videoElement?.src).toBe(video.previewUrl);
    expect(button("lesson.mp4").getAttribute("aria-pressed")).toBe("true");
    expect(button("voice.mp3").getAttribute("aria-pressed")).toBe("false");

    await act(async () => button("voice.mp3").click());

    const audioElement = document.querySelector("audio");
    expect(document.querySelector("video")).toBeNull();
    expect(audioElement?.controls).toBe(true);
    expect(audioElement?.autoplay).toBe(false);
    expect(audioElement?.src).toBe(audio.previewUrl);
    expect(button("voice.mp3").getAttribute("aria-pressed")).toBe("true");
  });

  it("contains images and offers honest downloads for documents and other files", async () => {
    const image = makeFile("poster.png", "image");
    await render(makeWork([image]));
    expect(document.querySelector("img")?.className).toContain(
      "object-contain",
    );

    const deck = makeFile("slides.pptx", "document", {
      previewUrl: null,
    });
    await render(makeWork([deck]));
    expect(document.body.textContent).toContain(
      "下载后使用 PowerPoint/WPS 查看编辑",
    );
    expect(document.querySelector("iframe")).toBeNull();
    expect(document.querySelector<HTMLAnchorElement>("a[download]")?.href).toBe(
      deck.downloadUrl,
    );

    const other = makeFile("active.svg", "other", { previewUrl: null });
    await render(makeWork([other]));
    expect(document.querySelector("img, video, audio, iframe")).toBeNull();
    expect(document.body.textContent).toContain("此文件类型暂不支持在线预览");
    expect(document.querySelector("a[download]")).not.toBeNull();
  });

  it("never guesses media or download URLs for a missing file", async () => {
    const missing = makeFile("missing.mp4", "video", {
      exists: false,
      size: null,
      modifiedAt: null,
      previewUrl: null,
      downloadUrl: null,
    });
    await render(makeWork([missing], { primaryFile: null }));

    expect(document.body.textContent).toContain("文件当前不可用");
    expect(document.querySelector("video, audio, img")).toBeNull();
    expect(document.querySelector("a[download]")).toBeNull();
    expect(document.querySelector("[src]")).toBeNull();
  });

  it("replaces failed media with an honest fallback while retaining download", async () => {
    const video = makeFile("lesson.mp4", "video");
    await render(makeWork([video]));

    await act(async () => {
      document.querySelector("video")?.dispatchEvent(new Event("error"));
    });

    expect(document.querySelector("video")).toBeNull();
    expect(document.body.textContent).toContain("媒体暂时无法播放");
    expect(document.querySelector<HTMLAnchorElement>("a[download]")?.href).toBe(
      video.downloadUrl,
    );
  });

  it("tries image decoding for an unnamed legacy output before failing", async () => {
    const legacy = {
      ...makeFile("legacy-output", "video", { name: null }),
      mediaKindGuessed: true,
    } as WorkFile;
    await render(makeWork([legacy], { source: "task" }));

    await act(async () => {
      document.querySelector("video")?.dispatchEvent(new Event("error"));
    });
    const image = document.querySelector("img");
    expect(image?.src).toBe(legacy.previewUrl);
    expect(document.body.textContent).not.toContain("媒体暂时无法播放");

    await act(async () => {
      image?.dispatchEvent(new Event("error"));
    });
    expect(document.body.textContent).toContain("媒体暂时无法播放");
    expect(document.querySelector("a[download]")).not.toBeNull();
  });

  it("uses the mediaKindGuessed marker instead of inferring legacy output from its name", async () => {
    const legacy = {
      ...makeFile("known-output.bin", "video", { name: "known-output.bin" }),
      mediaKindGuessed: true,
    } as WorkFile;
    await render(makeWork([legacy], { source: "task" }));

    await act(async () => {
      document.querySelector("video")?.dispatchEvent(new Event("error"));
    });

    expect(document.querySelector("img")?.src).toBe(legacy.previewUrl);
    expect(document.body.textContent).not.toContain("媒体暂时无法播放");
  });

  it("unmounts media when closed and resets the selected file when work changes", async () => {
    const first = makeFile("first.mp4", "video");
    const second = makeFile("second.mp4", "video");
    await render(makeWork([first, second]));
    expect(document.querySelector("video")?.src).toBe(first.previewUrl);
    await act(async () => button("second.mp4").click());
    expect(document.querySelector("video")?.src).toBe(second.previewUrl);

    await render(makeWork([first, second]), false);
    expect(document.querySelector("video, audio")).toBeNull();

    const replacement = makeFile("replacement.mp4", "video");
    const alternate = makeFile("alternate.mp4", "video");
    await render(makeWork([replacement, alternate]));
    expect(document.querySelector("video")?.src).toBe(replacement.previewUrl);
    expect(button("replacement.mp4").getAttribute("aria-pressed")).toBe("true");

    await render(makeWork([first, second]));
    expect(document.querySelector("video")?.src).toBe(first.previewUrl);
    expect(button("first.mp4").getAttribute("aria-pressed")).toBe("true");
  });

  it("calls source-specific continue and detail callbacks", async () => {
    const file = makeFile("lesson.mp4", "video");
    const agentWork = makeWork([file]);
    await render(agentWork);
    await act(async () => button("继续对话").click());
    expect(onContinue).toHaveBeenCalledExactlyOnceWith(agentWork);

    await render(makeWork([file], { source: "task" }));
    await act(async () => button("详情").click());
    expect(onTaskNavigate).toHaveBeenCalledExactlyOnceWith(task);
  });

  it("renders no media source, download, or version claims for an empty work", async () => {
    await render(makeWork([], { primaryFile: null }));

    expect(document.body.textContent).toContain("尚未交付文件");
    expect(document.querySelector("[src]")).toBeNull();
    expect(document.querySelector("a[download]")).toBeNull();
    expect(document.body.textContent).not.toContain("版本");
    expect(document.body.textContent).not.toContain("恢复");
  });
});

describe("WorkMedia card cover", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it("does not attach video or audio sources to list covers", async () => {
    const video = makeFile("lesson.mp4", "video");
    await act(async () => {
      root.render(<WorkMedia work={makeWork([video])} onOpen={vi.fn()} />);
    });

    expect(container.querySelector("video, audio")).toBeNull();
    expect(container.querySelector("[src]")).toBeNull();
    expect(container.textContent).toContain("lesson.mp4");
  });

  it("lazy-loads real Agent image covers", async () => {
    const image = makeFile("poster.png", "image");
    await act(async () => {
      root.render(<WorkMedia work={makeWork([image])} onOpen={vi.fn()} />);
    });

    const cover = container.querySelector("img");
    expect(cover?.getAttribute("loading")).toBe("lazy");
    expect(cover?.src).toBe(image.previewUrl);
  });
});
