export const VIDEO_ASSETS_PATH = "/workspace/video-gen";

export const WORK_CAPABILITY_IDS = [
  "course-video",
  "ai_image",
  "ai_video",
  "creative-studio",
  "ppt-master",
  "podcast-studio",
] as const;

export type WorkCapability = (typeof WORK_CAPABILITY_IDS)[number];
export type VideoAssetsTab = "works" | "assets";
export type VideoAssetsRoute =
  | { tab: "works"; capability: WorkCapability }
  | { tab: "assets" };

interface SearchParamsLike {
  get(name: string): string | null;
}

export function isWorkCapability(
  value: string | null | undefined,
): value is WorkCapability {
  return WORK_CAPABILITY_IDS.some((capability) => capability === value);
}

export function parseVideoAssetsRoute(
  input: string | SearchParamsLike,
): VideoAssetsRoute {
  const params = typeof input === "string" ? new URLSearchParams(input) : input;
  if (params.get("tab") === "assets") {
    return { tab: "assets" };
  }
  const capability = params.get("capability");
  return {
    tab: "works",
    capability: isWorkCapability(capability) ? capability : "course-video",
  };
}

export function buildVideoAssetsHref(route: VideoAssetsRoute): string {
  if (route.tab === "assets") {
    return `${VIDEO_ASSETS_PATH}?tab=assets`;
  }
  if (route.capability === "course-video") {
    return VIDEO_ASSETS_PATH;
  }
  const params = new URLSearchParams({
    tab: "works",
    capability: route.capability,
  });
  return `${VIDEO_ASSETS_PATH}?${params.toString()}`;
}
