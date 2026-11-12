import type { InitialVideoTaskType } from "./video-gen/video-gen-create-dialog";

export type CourseEntryId = "video" | "text";
export type CourseItemId =
  | "course-video"
  | "ai_image"
  | "ai_video"
  | "creative-studio"
  | "ppt-master"
  | "podcast-studio";

type HomeCourseMedia = { kind: "video" | "image"; src: string };

type HomeCourseItemBase = {
  id: CourseItemId;
  entry: CourseEntryId;
  media: HomeCourseMedia;
};

export type HomeCourseItem = HomeCourseItemBase &
  (
    | {
        kind: "agent";
        agentName:
          | "course-video"
          | "creative-studio"
          | "ppt-master"
          | "podcast-studio";
      }
    | { kind: "video-task"; initialTaskType: InitialVideoTaskType }
  );

export const HOME_COURSE_ITEMS: readonly HomeCourseItem[] = [
  {
    id: "course-video",
    entry: "video",
    kind: "agent",
    agentName: "course-video",
    media: { kind: "video", src: "/course-samples/remotion.mp4" },
  },
  {
    id: "ai_image",
    entry: "video",
    kind: "video-task",
    initialTaskType: "ai_image",
    media: { kind: "video", src: "/course-samples/image-motion.mp4" },
  },
  {
    id: "ai_video",
    entry: "video",
    kind: "video-task",
    initialTaskType: "ai_video",
    media: { kind: "video", src: "/course-samples/ai-video.mp4" },
  },
  {
    id: "creative-studio",
    entry: "text",
    kind: "agent",
    agentName: "creative-studio",
    media: { kind: "image", src: "/course-samples/knowledge-card.webp" },
  },
  {
    id: "ppt-master",
    entry: "text",
    kind: "agent",
    agentName: "ppt-master",
    media: { kind: "image", src: "/course-samples/ppt-work.webp" },
  },
  {
    id: "podcast-studio",
    entry: "text",
    kind: "agent",
    agentName: "podcast-studio",
    media: { kind: "image", src: "/course-samples/podcast-work.webp" },
  },
];
