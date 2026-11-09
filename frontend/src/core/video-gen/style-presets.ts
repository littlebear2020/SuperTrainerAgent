/**
 * 风格预设 —— 任务级与项目级的**单一来源**（Task 1，2026-08-01）。
 *
 * 修复前：任务级 4 个泛化词（默认/专业/轻松/电影感）描述的是**语气**而非画风，
 * 对生图模型没有指导力；项目级另有 5 个视觉预设，且在 course-project-dialog.tsx
 * 与 course-project-detail.tsx 里各抄了一份 —— 三套词汇互不相干。
 *
 * ⚠️ **value 必须是语言无关的 key，绝不能是英文字面量。**
 * 历史教训：项目级此前把 value 直接存成 `'3D render'`/`'flat illustration'`，
 * 而 `course_models.style_prefix` 会把它**原样拼进生图 prompt** —— 这正是
 * 反馈 5「选了风格后提示词变英文」的根因之一（真实任务 vg_d0779ddd81f8 的
 * storyboard.json 里，中文正文被英文前缀夹在中间）。
 * 现在 UI 只出 key，由后端 `STYLE_PREFIXES` 查表翻译成中文 prefix。
 */

import type { VideoTaskType } from "./types";

/** 分组名（2026-08-19 项目级收敛后仅剩「推荐」：职场/实景/叙事 旧组随旧 7 项退役） */
export const STYLE_PRESET_GROUPS = ["推荐"] as const;

export type StylePresetGroup = (typeof STYLE_PRESET_GROUPS)[number];

export type StylePreset = {
  value: string;
  label: string;
  /** 无 group 者不参与分组渲染（仅 default） */
  group?: StylePresetGroup;
  /**
   * 用户向一句话风格描述（2026-09-09 风格预览图设计）：仅 7 个 first-class key
   * 填写，供任务创建对话框选中预览区展示。**刻意不复用后端 STYLE_PREFIXES 原文**
   * ——prompt 工程语言（「冷白主光从侧上方打入」）用户读不出「适不适合我的课」；
   * 代价是放弃与 STYLE_PREFIXES 的逐字快照对齐，「改风格词须重跑生成脚本」的
   * 约定覆盖该风险（backend/scripts/generate_style_previews.py docstring）。
   */
  description?: string;
};

/**
 * 一级清单（8 项 = default + 推荐 7）。
 * 五风格体系（2026-08-17 Phase 0 样张 20/20 用户终审进 v1）为唯一 first-class；
 * 08-01 旧 7 项自 2026-08-19 起从两级 UI 退役（任务级 T1 先行、项目级随后收敛），
 * 移入 BACKEND_ONLY_STYLE_PRESETS，存量值由动态承载兜底（不回落清空）。
 */
export const STYLE_PRESETS: readonly StylePreset[] = [
  { value: "default", label: "默认（不设定）" },
  {
    value: "precision_eng",
    label: "精密工程",
    group: "推荐",
    description: "金属质感的精密工程渲染，冷冽专业，适合产品与技术类课程",
  },
  {
    value: "paper_collage",
    label: "纸艺拼贴",
    group: "推荐",
    description: "真实纸张拼贴的手工质感，柔和轻快，适合概念讲解类课程",
  },
  {
    value: "papercraft_theater",
    label: "纸艺剧场",
    group: "推荐",
    description: "立体纸雕舞台的剧场层次感，适合故事化与情景化讲解",
  },
  {
    value: "character_3d",
    label: "3D 角色",
    group: "推荐",
    description: "皮克斯风 Q 版卡通角色，适合轻松活泼的培训场景",
  },
  {
    value: "corporate_live",
    label: "商务实景",
    group: "推荐",
    description: "写实商务实拍质感与现代办公场景，适合职场与制度类课程",
  },
  {
    value: "isometric_info",
    label: "等距信息图",
    group: "推荐",
    description: "2.5D 等距视角信息图，清爽留白，适合流程与结构说明",
  },
  {
    value: "whiteboard",
    label: "白板手绘",
    group: "推荐",
    description: "马克笔手绘简笔画，亲切随性，适合思路演示与知识拆解",
  },
] as const;

/**
 * 存量旧值 → 新 key 的只读别名映射。
 *
 * **只读兼容，不写库、不做 DB 迁移** —— 老 payload 既不能 422，也不能退化成
 * 无风格前缀。实测存量数据：
 *   - 项目级英文字面量：cp_f3d9cc794a25='3D render'、cp_d11fb1691f4f='flat illustration'、
 *     cp_a9bd1420219b='watercolor'
 *   - 任务级旧下拉值：professional / casual
 *
 * `watercolor` 虽已从 UI 下拉移除（用户决定删「水彩手绘」），但**必须保留可解析**，
 * 否则 cp_a9bd1420219b 那个项目会突然失去风格前缀。
 */
export const LEGACY_STYLE_VALUES: Readonly<Record<string, string>> = {
  // 任务级旧下拉值
  professional: "corporate",
  casual: "warm_casual",
  // 项目级英文字面量（实测存量）
  "3D render": "education_3d",
  "flat illustration": "education_flat",
  // UI 已移除但存量项目仍在用 —— 保留可解析（见 BACKEND_ONLY_STYLE_KEYS）
  watercolor: "watercolor",
};

/**
 * 后端查表保留、但**不出现在两级 UI 下拉**里的 key（2026-08-19 项目级收敛扩容：
 * 08-01 旧 7 项并入，与 watercolor/education_flat 同一待遇）。
 *
 * - `watercolor`：用户 2026-08-01 决定删「水彩手绘」，但实测存量项目
 *   cp_a9bd1420219b 的 visual_style_preset 正是它；
 * - `education_flat`：2026-08-17 五风格终审退役（样张色块化劣质）；
 * - 旧 7 项（corporate 等）：任务级 T1（2026-08-18）先移出，项目级随后收敛——
 *   两级均不给新选，后端 `STYLE_PREFIXES` 继续认，存量值由承载项显示。
 * 即「UI 不给新选，老数据照旧生效」。label 供承载项显示（不裸显英文 key）。
 */
export const BACKEND_ONLY_STYLE_PRESETS: readonly StylePreset[] = [
  { value: "corporate", label: "商务专业" },
  { value: "education_3d", label: "3D 渲染" },
  { value: "minimalist", label: "极简图形" },
  { value: "photorealistic", label: "写实摄影" },
  { value: "documentary", label: "纪录片感" },
  { value: "cinematic", label: "电影感" },
  { value: "warm_casual", label: "轻松温暖" },
  { value: "watercolor", label: "水彩手绘" },
  { value: "education_flat", label: "扁平插画" },
] as const;

export const BACKEND_ONLY_STYLE_KEYS: readonly string[] =
  BACKEND_ONLY_STYLE_PRESETS.map((p) => p.value);

/**
 * 项目级（CourseProject.visual_style_preset）下拉的**基座选项**（2026-08-19 收敛：
 * 不设置 + 推荐 7，旧 7 项移出项目级 UI）。
 *
 * 与任务级同一套 key，仅「不设定」的表示法不同：**项目级用空串 `""`**
 * （历史语义：空串 = 未设置视觉风格，`course_models.style_prefix` 据此跳过注入），
 * 任务级用 `"default"`。这里保持各自的历史表示法，**不做统一** ——
 * 改动存储值会影响存量项目的读取语义，属无收益的高风险改写。
 *
 * 渲染请用 projectStyleOptions(stored)：存量旧值（旧 7 项 / watercolor /
 * education_flat / 旧别名归一结果）会追加承载项，不回落清空。
 */
export const PROJECT_STYLE_OPTIONS: readonly StylePreset[] = [
  { value: "", label: "不设置" },
  ...STYLE_PRESETS.filter((p) => p.value !== "default"),
];

/**
 * 项目级下拉选项（基座 + 存量承载）：与任务级 taskStyleOptions 同构的动态承载。
 *
 * 原生 <select> 的 value 找不到匹配 <option> 时会静默回落第一项「不设置」，
 * 用户打开老项目看到风格空了、一保存就真把风格清掉（静默数据丢失）——
 * 承载项只对**后端认识的 key**（BACKEND_ONLY）追加；未知值不猜、不凭空造选项。
 * 承载项 value 用归一后的 key：项目级 state 经 optionValueForStored 初始化，
 * 保存时即完成旧字面量 → key 的归一写回（'3D render' → 'education_3d'）。
 */
export function projectStyleOptions(
  stored: string | null | undefined,
): StylePreset[] {
  const options = [...PROJECT_STYLE_OPTIONS];
  const resolved = resolveStyleValue(stored);
  if (
    stored &&
    resolved &&
    !options.some((o) => o.value === resolved) &&
    BACKEND_ONLY_STYLE_KEYS.includes(resolved)
  ) {
    options.push({ value: resolved, label: carryLabelFor(resolved) });
  }
  return options;
}

/**
 * 把**存量存储值**映射成当前下拉里真实存在的 option value（用于受控 select 显示）。
 *
 * 与 `resolveStyleValue` 的区别：那个用于「语义解析」，未知值原样返回；
 * 这个用于「渲染」—— 解析结果若不在选项里，只能回落空串（显示「不设置」），
 * 否则原生 select 会自己静默回落，行为更难排查。**未知值绝不错指成某个具体风格。**
 */
export function optionValueForStored(
  stored: string | null | undefined,
): string {
  if (!stored) return "";
  const resolved = resolveStyleValue(stored);
  if (PROJECT_STYLE_OPTIONS.some((o) => o.value === resolved)) return resolved;
  // 后端保留项（旧 7 项 / watercolor / education_flat）：返回归一 key 交由
  // projectStyleOptions 承载显示——返回 "" 等于让老项目保存时静默清空风格。
  return BACKEND_ONLY_STYLE_KEYS.includes(resolved) ? resolved : "";
}

/** 解析任意历史值为当前 key；未知值原样返回（不猜） */
export function resolveStyleValue(value: string | null | undefined): string {
  if (!value) return "";
  if (STYLE_PRESETS.some((p) => p.value === value)) return value;
  return LEGACY_STYLE_VALUES[value] ?? value;
}

// ── 任务级五风格分表（T1，2026-08-18 两份反馈 P-01，Phase2 偏离修复）──────
//
// Phase 2（2026-08-17）只把五风格置顶进 STYLE_PRESETS，任务级下拉仍 15 项全出
// （default + 推荐 7 + 旧 7），未按引擎分流——而后端 style_presets.py 早已导出
// 视频/图片两张五风格表（视频：精密工程/纸艺拼贴/纸艺剧场/3D角色/商务实景；
// 图片：精密工程/纸艺拼贴/等距信息图/白板手绘/商务实景；三风格两用、四档专属）。
// 旧 7 项自此移出任务级 UI，存量旧风格任务由承载项兜底（见 taskStyleOptions）。
// 两表与后端同名常量**逐项静态对齐**（后端无 endpoint 暴露，tuple 快照测试是
// 跨端一致性的唯一防线）。

export const VIDEO_STYLE_KEYS: readonly string[] = [
  "precision_eng",
  "paper_collage",
  "papercraft_theater",
  "character_3d",
  "corporate_live",
];

export const IMAGE_STYLE_KEYS: readonly string[] = [
  "precision_eng",
  "paper_collage",
  "isometric_info",
  "whiteboard",
  "corporate_live",
];

/** ai_visual/vimax 用：两表并集 7 项（视频 5 + 图片专属 2），顺序=「推荐」组展示序 */
const UNION_STYLE_KEYS: readonly string[] = [
  ...VIDEO_STYLE_KEYS,
  ...IMAGE_STYLE_KEYS.filter((k) => !VIDEO_STYLE_KEYS.includes(k)),
];

// ── 风格预览图（2026-09-09 设计：任务创建对话框风格下拉的可视化参考）──────
//
// 资产 = frontend/public/style-previews/<key>.jpg（960×540，一次性脚本
// backend/scripts/generate_style_previews.py 以生产 config.yaml 默认生图链产出后
// 提交 git，运行时零 API）。UI 形态 = SelectItem 缩略图 + 选中预览区
// （video-gen-page.tsx CreateTaskDialog）。

/** 预览图版本号：重出图时 +1，破浏览器缓存（public/ 静态资源无内容指纹） */
const STYLE_PREVIEW_VERSION = 1;

/**
 * 风格预览图路径（仿 layoutFallbackImage() 模式）：仅一级 7 key 有图；
 * default / 跟随项目（FOLLOW_PROJECT）/ 存量承载项（旧 7 项、watercolor 等）/
 * 未知值 → null（调用方以 null 判「不渲染缩略图与预览区」）。
 * 判定基于两表并集（与 UNION_STYLE_KEYS 同源），天然排除非 first-class 项。
 */
export function stylePreviewImage(key: string): string | null {
  return UNION_STYLE_KEYS.includes(key)
    ? `/style-previews/${key}.jpg?v=${STYLE_PREVIEW_VERSION}`
    : null;
}

/**
 * 任务级风格 key 按引擎分派（T1）。
 *
 * 五值 taskType 中 **remotion 不进本函数**——它无生图/生视频步骤，风格下拉由
 * style-visibility 层（shouldShowStyleSelector，类型谓词）整体排除；本函数参数
 * 类型即「排除 remotion」后的联合，传 remotion 是编译错误而非运行时分支。
 * ai_visual（历史别名）与 vimax 的画面两链路都可能走 → 两表并集 7 项。
 */
export function styleKeysForTaskType(
  taskType: Exclude<VideoTaskType, "remotion">,
): readonly string[] {
  if (taskType === "ai_image") return IMAGE_STYLE_KEYS;
  if (taskType === "ai_video") return VIDEO_STYLE_KEYS;
  return UNION_STYLE_KEYS;
}

/** 承载项 label：不在一级清单的旧值去后端保留表（旧 7 项 + watercolor /
 * education_flat 的中文 label）找；再找不到就原样显示（未知值不猜）。
 * C7（2026-08-24 计划）起导出：任务详情 InfoRow 的风格原始 key 翻译复用
 * （此前模块私有，详情页只能显示未翻译的英文 key）。 */
export function carryLabelFor(resolved: string): string {
  const known = [...STYLE_PRESETS, ...BACKEND_ONLY_STYLE_PRESETS].find(
    (p) => p.value === resolved,
  );
  return known?.label ?? resolved;
}

/**
 * 任务级风格下拉选项（T1）：默认 + 引擎分表五风格；受控值不在选项集时**追加
 * 承载项**。存量旧风格任务（cinematic 等旧 7 项、professional 等旧别名）打开
 * 下拉不回落清空——Radix Select 受控值无对应 item 时触发器渲染成空白，静默
 * 丢弃比显式清空更难排查（同 PROJECT_STYLE_OPTIONS 承载 watercolor 的理由）。
 * 承载项 value 用**原始受控值**（而非解析结果）：受控显示必须与 state 逐字
 * 相等，解析目标只在 label 上呈现。
 */
export function taskStyleOptions(
  taskType: Exclude<VideoTaskType, "remotion">,
  currentStyle: string,
): StylePreset[] {
  const keys = styleKeysForTaskType(taskType);
  const options: StylePreset[] = [
    { value: "default", label: "默认（不设定）" },
    ...keys.map((key) => ({
      value: key,
      label: STYLE_PRESETS.find((p) => p.value === key)!.label,
    })),
  ];
  const resolved = resolveStyleValue(currentStyle);
  if (currentStyle && currentStyle !== "default" && !keys.includes(resolved)) {
    options.push({ value: currentStyle, label: carryLabelFor(resolved) });
  }
  return options;
}

/**
 * 切换任务类型后的风格值（T1）：受控值不在新类型选项集 → 回落 default。
 * 三风格两用（precision_eng/paper_collage/corporate_live）跨引擎保留；
 * 专属档（如 ai_video 的「纸艺剧场」切到 ai_image）回落——残留值在新引擎下
 * 无选项，静默失效比清空更糟（与切换类型清空 selectedProvider 同语义）。
 */
export function styleAfterTypeChange(
  nextType: Exclude<VideoTaskType, "remotion">,
  currentStyle: string,
): string {
  const resolved = resolveStyleValue(currentStyle);
  if (!resolved || resolved === "default") return "default";
  return styleKeysForTaskType(nextType).includes(resolved)
    ? resolved
    : "default";
}
