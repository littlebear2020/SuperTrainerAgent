import {
  HOME_COURSE_ITEMS,
  type HomeCourseItem,
} from "@/components/workspace/home-course-catalog";
import { WORK_CAPABILITY_IDS } from "@/core/workspace/video-assets-route";

export const WORK_CAPABILITIES: readonly HomeCourseItem[] =
  WORK_CAPABILITY_IDS.map((id) => {
    const item = HOME_COURSE_ITEMS.find((candidate) => candidate.id === id);
    if (!item) throw new Error(`Missing home course capability: ${id}`);
    return item;
  });
