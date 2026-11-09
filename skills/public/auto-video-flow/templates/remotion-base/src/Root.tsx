import "./index.css";
import { Composition, CalculateMetadataFunction } from "remotion";
import { MyComposition } from "./Composition";
import {
  DataDrivenVideo,
  DataDrivenVideoProps,
} from "./DataDrivenComposition";

/* 数据驱动合成的示例 props（Studio 预览 + calculateMetadata 的兜底输入）。
   渲染时经 --props 传入的真实 props 会整体替换此默认值。 */
const DEFAULT_DATA_DRIVEN_PROPS: DataDrivenVideoProps = {
  title: "示例视频",
  fps: 30,
  width: 1920,
  height: 1080,
  totalDurationFrames: 270,
  style: "default",
  transition: "fade",
  transitionDurationFrames: 15,
  bgmEnabled: false,
  bgmVolume: 0.2,
  scenes: [
    { index: 0, narration: "第一场景：开场白", durationInFrames: 90, audioFile: "" },
    { index: 1, narration: "第二场景：主体内容", durationInFrames: 90, audioFile: "" },
    { index: 2, narration: "第三场景：总结收尾", durationInFrames: 90, audioFile: "" },
  ],
  subtitleSegments: [
    { sceneIndex: 0, startFrame: 10, endFrame: 80, text: "第一场景字幕" },
    { sceneIndex: 1, startFrame: 100, endFrame: 170, text: "第二场景字幕" },
    { sceneIndex: 2, startFrame: 190, endFrame: 260, text: "第三场景字幕" },
  ],
};

/**
 * 时长音频驱动（T3 载体）：durationInFrames 由 props 内各场景的实测音频帧数
 * 求和得出，不再依赖手工同步 SCENES 帧数。fps/width/height 同样以 props 为准，
 * 使竖屏（1080x1920）等非默认规格经 props 直接生效。
 */
const calcDataDrivenMetadata: CalculateMetadataFunction<
  DataDrivenVideoProps
> = ({ props }) => {
  const contentFrames = props.scenes.reduce(
    (acc, s) => acc + s.durationInFrames,
    0,
  );
  return {
    durationInFrames:
      contentFrames > 0 ? contentFrames : Math.max(1, props.totalDurationFrames),
    fps: props.fps || 30,
    width: props.width || 1920,
    height: props.height || 1080,
  };
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="MyComp"
        component={MyComposition}
        durationInFrames={60}
        fps={30}
        width={1920}
        height={1080}
      />
      <Composition
        id="DataDrivenVideo"
        component={DataDrivenVideo}
        durationInFrames={270}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={DEFAULT_DATA_DRIVEN_PROPS}
        calculateMetadata={calcDataDrivenMetadata}
      />
    </>
  );
};
