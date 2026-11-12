import type { InitialVideoTaskType } from "./video-gen/video-gen-create-dialog";

export type CourseItemId =
  | "course-video"
  | "ai_image"
  | "ai_video"
  | "creative-studio"
  | "ppt-master"
  | "podcast-studio";

type HomeCourseMedia = {
  kind: "video" | "image";
  src: string;
  poster?: string;
};

type HomeCourseItemBase = {
  id: CourseItemId;
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
    kind: "agent",
    agentName: "course-video",
    media: {
      kind: "video",
      src: "/course-samples/remotion.mp4",
      poster: "/course-samples/text-motion-poster.webp",
    },
  },
  {
    id: "ai_image",
    kind: "video-task",
    initialTaskType: "ai_image",
    media: {
      kind: "video",
      src: "/course-samples/image-motion.mp4",
      poster: "/course-samples/image-motion-poster.webp",
    },
  },
  {
    id: "ai_video",
    kind: "video-task",
    initialTaskType: "ai_video",
    media: {
      kind: "video",
      src: "/course-samples/ai-video.mp4",
      poster: "/course-samples/ai-video-poster.webp",
    },
  },
  {
    id: "creative-studio",
    kind: "agent",
    agentName: "creative-studio",
    media: { kind: "image", src: "/course-samples/knowledge-card.webp" },
  },
  {
    id: "ppt-master",
    kind: "agent",
    agentName: "ppt-master",
    media: { kind: "image", src: "/course-samples/ppt-work.webp" },
  },
  {
    id: "podcast-studio",
    kind: "agent",
    agentName: "podcast-studio",
    media: { kind: "image", src: "/course-samples/podcast-work.webp" },
  },
];
