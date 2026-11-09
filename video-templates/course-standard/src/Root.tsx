import React from "react";
import { Composition } from "remotion";
import { ThemeName } from "./theme";
import { LayoutSceneProps, SceneContent } from "./content";
import { CourseProps } from "./course-props";
import { DataDrivenCourse, calculateCourseMetadata } from "./DataDrivenCourse";
import { LAYOUT_REGISTRY } from "./LayoutRouter";
import { SAMPLE_COURSE, SAMPLE_SUBTITLES } from "./sample/fixtures";

/**
 * Compositions：
 * - DataDrivenCourse：数据驱动主 Composition（生产入口，worker render 目标）；
 * - 76 个画廊 Composition（19 版式 × 4 主题）：M0 静帧复现与 T7 静帧预览降级用。
 *   画廊 props 注入 fixtures 样例文案 + M0 字幕条文本（subtitleText 仅画廊用）。
 */
const GALLERY_LAYOUTS = Object.keys(LAYOUT_REGISTRY);

const galleryDefaultProps = (layoutId: string, themeName: ThemeName) => {
  const scene = SAMPLE_COURSE.scenes.find((s) => s.layout === layoutId);
  const content: SceneContent = (scene?.layoutProps ?? {}) as SceneContent;
  const props: LayoutSceneProps = {
    themeName,
    content,
    subtitleText: SAMPLE_SUBTITLES[layoutId],
  };
  return props;
};

const sampleCourseProps: CourseProps = {
  title: SAMPLE_COURSE.title,
  fps: SAMPLE_COURSE.fps,
  width: SAMPLE_COURSE.width,
  height: SAMPLE_COURSE.height,
  brand: { themeName: "dark-blue", brandName: SAMPLE_COURSE.brandName },
  scenes: SAMPLE_COURSE.scenes.map((s, i) => ({
    index: i,
    narration: s.narration,
    durationInFrames: 150,
    layout: s.layout,
    layoutProps: s.layoutProps as Record<string, unknown>,
  })),
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="DataDrivenCourse"
        component={DataDrivenCourse}
        defaultProps={sampleCourseProps}
        calculateMetadata={calculateCourseMetadata}
        fps={30}
        width={1920}
        height={1080}
        durationInFrames={150}
      />
      {(["dark-blue", "light", "dark-green", "warm"] as ThemeName[]).flatMap((themeName) =>
        GALLERY_LAYOUTS.map((layoutId) => (
          <Composition
            key={`${layoutId}-${themeName}`}
            id={`${layoutId}-${themeName}`}
            component={LAYOUT_REGISTRY[layoutId]}
            defaultProps={galleryDefaultProps(layoutId, themeName)}
            durationInFrames={150}
            fps={30}
            width={1920}
            height={1080}
          />
        )),
      )}
    </>
  );
};
