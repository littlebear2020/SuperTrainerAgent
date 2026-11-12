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
export const DEFAULT_WORK_CAPABILITY = "course-video" satisfies WorkCapability;
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
    capability: isWorkCapability(capability)
      ? capability
      : DEFAULT_WORK_CAPABILITY,
  };
}

export function buildVideoAssetsHref(
  route: VideoAssetsRoute,
  currentSearch = "",
): string {
  const params = new URLSearchParams(currentSearch);
  params.delete("tab");
  params.delete("capability");
  if (route.tab === "assets") {
    params.set("tab", "assets");
  } else if (route.capability !== DEFAULT_WORK_CAPABILITY) {
    params.set("tab", "works");
    params.set("capability", route.capability);
  }
  const search = params.toString();
  return search ? `${VIDEO_ASSETS_PATH}?${search}` : VIDEO_ASSETS_PATH;
}
