# Video Gen 图片动效 / AI 动态视频产品优化交接

> 文档类型：代码调研 + 交互原型交接；不是实施完成记录，也不是已批准的开发计划。
> 调研与原型验收：2026-11-19；交接整理：2026-11-22。
> 代码基准：`90cab56f`（交接整理时 HEAD）。
> 当前状态：没有修改生产代码；Demo 已于 2026-11-22 恢复到原路径并重新通过完整交互验收，详见第 6、8 节。
>
> **TL;DR**：围绕图片动效、AI 动态视频两条流程核查了创建、分镜、生成、二次编辑和重新合成链路；确认竖屏被横框裁切的前端预览缺陷，以及字幕 ASS 缓存警告未展示的问题；正常二次编辑能力已有完整链路，但 BGM 换曲、画幅修改和强制一行/两行并非当前能力，不得虚构。

## 1. 接手目标与用户约束

目标是在现有能力上改善用户使用体验，而不是重新发明一条视频生成管线。

必须遵守：

- 以当前代码为准，不以 Demo、文案或既有产品介绍推断后端能力。
- 只做中等规格、风险可控的修改；优先复用已有 API、状态和组件。
- 不做架构级改动，不新增后端 API，不改 Worker、数据库、Provider 或模型配置。
- 不为了视觉效果新增无实际用途的控件、指标、状态或装饰性面板。
- 不把“保存设置”写成“已经进入成片”；必须说明重生配音或重新合成的生效条件。
- 不虚构动态试生成、主体焦点编辑、成片后换 BGM、字幕行数开关等不存在的功能。
- 保留两条真实路径：**直接生成**与**先编辑分镜**。
- 保留创建期 BGM 试听/上传、视觉风格样图、字幕预览，以及分镜的 AI 画面提示词能力。
- 如后续需要后端配合才能彻底修复问题，先单独说明并取得用户同意，不擅自扩大范围。

## 2. 两条流程的真实差异

| 项目 | 图片动效 `ai_image` | AI 动态视频 `ai_video` |
|---|---|---|
| 画面 | 生成或上传静态图片 | 正式渲染时逐镜生成动态视频 |
| 图片动效来源 | 合成阶段的中心推近/拉远；图片主体不会自行运动 | 不适用 |
| 分镜静态预览 | 可作为正式画面 | 只用于确认静态构图；模型支持时可作为首帧，不代表最终运动效果 |
| 二次编辑 | 可改旁白、画面提示词、来源、素材，按类型重生后重新合成 | 同左；改动态画面仍需正式生成，不能把静态预览当成动态成片 |
| 画幅 | 9:16、16:9、1:1 | 同左；支持目标比例的 Provider 会接收该比例 |

画幅的后端目标尺寸不是虚构：

- `backend/app/gateway/video_gen/service.py:223-237`：9:16 → 1152×2048；16:9 → 2048×1152；1:1 → 1152×1152。
- `backend/app/gateway/video_gen/visual_sources/ai_video.py:64-74`：向支持的视频 Provider 传递目标比例。
- `backend/tests/test_video_gen_aspect_ratio.py` 有对应守护。

因此“竖屏预览被横向裁切”应归因于前端展示，不要误修为后端生成比例或 Provider 配置。

## 3. 当前链路中应保留的有效细节

### 3.1 创建任务

创建界面应围绕真实输入组织，不要把“输出格式 MP4”当作需要用户选择的高级项：

- 标题、可选课程项目、脚本文本和文档上传。
- 画幅、语言、配音音色及试听。
- 图片动效/AI 动态视频各自的能力边界说明。
- 高级设置中的视觉风格及样图、生成模型、画质、图文镜占比上限。
- 背景音乐选择、真实试听和上传；有 BGM 时再呈现音量、自动压低背景音乐设置。
- 字幕样式与效果预览、语速。
- 底部保留“先编辑分镜”和“直接生成视频”两种进入方式，并解释适用场景。

### 3.2 分镜编辑

每个分镜至少保留：

- 分镜选择、时长、视觉来源。
- 当前素材预览；动态视频的静态预览不得冒充动态效果。
- 旁白编辑。
- **画面提示词编辑及 AI 生成画面提示词入口**。
- 重新生成预览图、替换素材、试听本帧、插入和删除。
- 草稿保存状态及正式渲染前确认。

不得为了简化布局而删除画面提示词，也不得将真实模型生成能力换成“炫酷动画预览”。

### 3.3 生成中与结果页

- 后台处理状态、已生成分镜和等待中的分镜。
- 停止后续生成时说明已产生费用不退、已有素材保留。
- 成片播放、查看分镜、下载当前版本、二次编辑。
- 降级任务须区分“占位画面”和“静态首帧占位”，允许定位到对应分镜修复。
- 重生画面/配音之后，只有重新合成才代表进入整片。

## 4. 成品二次编辑的真实边界

配置更新接口只接受 `voice_id`、`tts_speed`、`bgm_volume`、`subtitle_style`，见：

- `backend/app/gateway/routers/video_gen.py:1499-1516`。
- `backend/app/gateway/routers/video_gen.py:1519-1625`。
- `frontend/src/core/video-gen/api.ts:248-279`。

| 修改项 | 当前是否支持 | 生效路径与注意事项 |
|---|---|---|
| 旁白文本 | 支持 | 保存后重生对应配音；之后重新合成 |
| 音色 `voice_id` | 支持 | 保存后逐帧或全部重生配音；仅保存配置不会改变已有音频 |
| 语速 `tts_speed` | 支持 | 同上 |
| BGM 音量 `bgm_volume` | 支持 | 保存后重新合成；Worker 实际读取该配置 |
| BGM 当前曲目 | **不支持成片后更换** | 不要新增换曲控件 |
| `bgm_ducking` | **不支持成片后修改** | 不要新增“降噪”之类的错误命名或开关 |
| 字幕样式 | 支持 | 保存后重新生成 ASS 并重新合成；`none` 会删除旧 ASS |
| 字幕文本和时间 | 支持 | 保存 locked SRT、重建 ASS，再重新合成烧录 |
| 画面比例 | **不支持成片后修改** | 配置端点明确禁止 width/height 修改 |
| 强制一行/两行 | **不支持** | 不要伪造选择器，详见第 5.3 节 |

已核对的正常生效链路：

- 批量配音重生读取最新音色和语速，重写音频、SRT/ASS，并清理相关合成缓存：`backend/app/gateway/video_gen/service.py:2016-2140`。
- BGM 混音实际读取 `bgm_volume`、`bgm_ducking`：`docker/production-worker/pipelines/video_gen_mixed.py:287-296`；AI 视觉链也有对应处理：`docker/production-worker/pipelines/video_gen_ai_visual.py:744-754`。
- 重新合成会从最新 SRT 刷新 ASS：`backend/app/gateway/video_gen/service.py:1069-1094`。
- Worker 烧字幕时 ASS 优先：`docker/production-worker/pipelines/_ffmpeg_helpers.py:503-548`。

**结论不是“二次编辑都无效”。正常路径已有实现和测试；异常反馈缺口见下一节。**

## 5. 已确认问题与证据等级

### 5.1 画幅预览固定横框并裁切媒体【代码确认；未改生产】

位置：

- `frontend/src/components/workspace/video-gen/video-gen-storyboard.tsx:492` 固定 `aspect-video`。
- 同文件 `:507`、`:513`、`:523`、`:529` 使用 `object-cover`。
- `frontend/src/components/workspace/video-gen/completed-video-grid.tsx:207` 的成片播放也固定 `aspect-video`。

影响：9:16 内容被放入横向框，不能代表真实任务画幅；方形也无法正确预览。

建议：

- 从已有 `storyboard.config.width/height` 推导 9:16、16:9、1:1，使用显式静态类映射。
- 分镜编辑和成片主播放器使用真实比例容器，媒体用 `object-contain`。
- 素材与目标比例不一致时显示留边，并说明 Worker 正式合成会放大、居中裁切；相关滤镜见 `docker/production-worker/pipelines/video_gen_mixed.py:350-379`。
- 不为“预览好看”而把素材裁切伪装成已经完成的 Worker 输出。
- 列表卡片封面是否保持统一横向卡片是另一个设计问题，不要求本批一并修改；不要为了卡片比例新增 API。

### 5.2 字幕 ASS 缓存警告未进入前端反馈【代码确认；异常实片未复现】

后端在两处返回 `ass_warning`：

- 样式配置更新：`backend/app/gateway/routers/video_gen.py:1595-1597`、`:1623-1624`。
- 手工字幕保存：`backend/app/gateway/routers/video_gen.py:1822-1824`、`:1859-1860`。

前端问题：

- `frontend/src/core/video-gen/api.ts:259-262` 和 `:308-313` 的返回类型没有声明 `ass_warning`。
- `frontend/src/core/video-gen/hooks.ts:514-525` 对手工字幕统一提示保存成功，没有处理警告。
- `frontend/src/components/workspace/video-gen/video-gen-detail-audio-settings.tsx:223-240` 的配置保存成功回调同样没有处理警告。
- 全 `frontend/src` 搜索未发现 `ass_warning` 消费方。

条件性后果：如果 ASS 派生失败，配置或 SRT 本体可能已经保存，但旧 ASS 仍可能被 Worker 优先使用；界面却提示纯成功。**不是每次保存都会失败。**

实现时不要只“加一个 toast”就声称彻底修复：

- `backend/app/gateway/routers/video_gen.py:1564-1573` 在样式值未变化时会跳过 ASS 重建，同值重试未必重新派生。
- `backend/app/gateway/video_gen/service.py:1085-1092` 的 recompose ASS 刷新异常只记录日志。
- 当前纯前端批次可以先补返回类型、警告展示和测试，消除“纯成功”的误导；彻底收口同值重试与 recompose 异常需要另开一个小型后端 bug 任务并取得用户同意。

### 5.3 自动两行与手工单行的规则差异【既定行为；不是行数开关失效】

自动字幕：

- 横屏最大 16 全角，竖屏 13 全角：`backend/app/gateway/video_gen/subtitle_engine.py:439-443`。
- 超宽时在合法断点预折，最多两行；无合法断点可保持原文：`:777-805`。
- ASS 使用 `WrapStyle: 2`，关闭 libass 自动折行：`:954-1006`。
- 1:1 因高度不大于宽度，走横屏口径：`backend/app/gateway/video_gen/service.py:979`。

手工字幕：

- 后端把换行折叠为空格：`backend/app/gateway/routers/video_gen.py:1633-1648`。
- 前端已有“仅支持单行，换行将合并为空格”提示：`frontend/src/components/workspace/video-gen/video-gen-detail-subtitle-edit.tsx:253-255`。
- 当前没有“一行/两行”配置项；手工编辑器只编辑已有 cue 的时间和文本，不新增/删除 cue。

设计结论：本批只解释规则，不添加假的行数选择器。若用户以后明确要开放手工换行，再单独评估字幕保存规则的小改，不要与当前布局优化混在一起。

## 6. 最后一版 Demo 的设计与当前文件状态

### 6.1 最后验收版包含的方案

- 创建页按“画面与视觉 / 音乐与字幕”分组，保留风格样图、BGM 试听/上传、字幕样式预览和语速。
- 分镜、二次编辑、成片播放器统一按 9:16、16:9、1:1 展示；媒体 `object-fit: contain`。
- 竖屏使用居中窄框，方形使用方形框，横屏使用横向宽框。
- 字幕预览使用真实函数验证过的示例：15 全角文本“长期积累会从第一笔收益慢慢开始”，竖屏输出为“长期积累会从第”+“一笔收益慢慢开始”，横屏保持一行。
- 二次编辑把配音与语速、字幕样式与预览分区；BGM 只保留音量，无 BGM 时禁用无意义滑块。
- 音色/语速保存后引导重生配音；字幕样式/BGM 音量保存后引导重新合成；字幕编辑保留时间校验和覆盖提醒。
- 不提供成片后换曲、改画幅、强制行数等假控件。

### 6.2 文件已恢复（2026-11-22）

当前可交付文件：

- `temp/worktree-archive-20261122/home-plan/temp/video-gen-flow-demo/bundle.html`（单文件，媒体已内嵌，可直接用浏览器打开）。
- `temp/worktree-archive-20261122/home-plan/temp/video-gen-flow-demo/src/App.tsx`。
- `temp/worktree-archive-20261122/home-plan/temp/video-gen-flow-demo/src/App.css`。

恢复过程：

- 从原会话的 Write/Edit 记录重放了 143 次修改，恢复的是最后一版画幅与二次编辑设计，不是早期版本。
- 原始图片、视频、BGM 和风格样图仍存在于项目资源目录，已按原路径复制回来；旁白试听音频按原命令重新生成。
- 前端脚手架和依赖重新安装，不代表恢复原 `node_modules`；恢复后的源码构建和完整交互回归均通过。
- 临时恢复脚本和验证脚本在验收后清理，不作为交付物保留。

清理原因调查结论：

- Demo 位于 `.gitignore:110` 忽略的 `temp/`，未进入 Git 历史；单纯删除分支不会删除这个目录。
- 已查到的 2026-11-17 分支/worktree 清理针对其他工作树；2026-11-20 的清理只删除了 12 个经哈希校验的重复文件，没有触碰 Demo。
- 已检索的 Kimi Code/Claude 工具日志中，没有发现 `git clean`、删除整个 `temp/` 或删除 `temp/worktree-archive-20261122/home-plan/temp/video-gen-flow-demo/` 的执行记录。因而**不能确认具体由哪次操作移除**，也不能把分支清理直接认定为根因。
- 上次 Demo 收尾只删除临时验证脚本和两张新增调试图，没有删除源码或 `bundle.html`。

注意：`temp/` 仍是 Git 忽略区，未来的目录清理仍可能移除 Demo。若需长期保留，应另行取得用户同意，把源码移交到受 Git 跟踪的位置；不要默认它已经受版本控制保护。

### 6.3 Demo 不是生产能力证明

最后的 Demo 使用本地状态和示例资源验证交互，以下均不能当成生产接口证据：

- “模拟正常完成”“模拟降级完成”等原型验收入口。
- 演示费用、任务进度、音频时长和本地任务状态。
- 示例图片、视频、音色、BGM 和字幕数据。
- 本地上传文档后的示例文本填充、原型内 AI 提示词生成。
- 横向示例媒体放入竖屏容器产生的留边，只是素材比例不一致的展示示例，不是 Worker 实片输出。

## 7. 建议的后续实施顺序

以下是交接建议，不代表已经获得实施授权：

- [ ] **先修真实画幅预览**：分镜编辑与成片主播放器读取现有宽高配置，保持 `object-contain`，补 9:16、16:9、1:1 的组件/行为测试。
- [ ] **再整理创建和二次编辑布局**：复用当前 `AudioSettingsSection`、`SubtitleEditSection` 和已有 mutation，保留三选弹层、dirty 基线、保存守卫和字幕校验，不重写状态机。
- [ ] **补 `ass_warning` 前端反馈**：API 返回类型 + 两个成功回调 + 异常响应测试；不得继续只提示成功，也不得宣称已彻底修复后端异常。
- [ ] **单独决定是否处理 ASS 后端重试闭环**：这是另一个需要用户批准的小后端 bug 范围，不夹带进纯前端优化。
- [ ] **不改 Worker/Provider/数据库/配置 schema，不新增 API**；若实现时发现必须触碰，应停下并回报，而不是扩权。

生产组件可能的触达点：

- `frontend/src/components/workspace/video-gen/video-gen-storyboard.tsx`
- `frontend/src/components/workspace/video-gen/completed-video-grid.tsx`
- `frontend/src/components/workspace/video-gen/video-gen-detail-audio-settings.tsx`
- `frontend/src/components/workspace/video-gen/video-gen-detail-subtitle-edit.tsx`
- `frontend/src/core/video-gen/api.ts`
- `frontend/src/core/video-gen/hooks.ts`

开始修改前仍需阅读各组件当前实现和 `frontend/AGENTS.md`，按现有测试落位添加测试，不修改 `components/ui/`、`components/ai-elements/` registry 禁区。

## 8. 已有验收记录

2026-11-19 在当时的代码与 Demo 上执行过：

```bash
cd backend && uv run pytest \
  tests/test_video_gen_aspect_ratio.py \
  tests/test_video_gen_config_update.py \
  tests/test_video_gen_subtitle_edit.py \
  tests/test_video_gen_edit_effectiveness.py \
  tests/test_subtitle_engine.py \
  tests/test_subtitle_wrap.py -q
```

实际结果：**195 passed，1 warning**。另有本地 OpenTelemetry 导出连接失败日志，不影响该次 pytest 通过结果；不要把它误写为视频生成失败。

```bash
cd temp/worktree-archive-20261122/home-plan/temp/video-gen-flow-demo && pnpm build
```

实际结果：TypeScript + Vite 构建成功。

```bash
pnpm exec oxlint --no-ignore src/App.tsx src/main.tsx
```

实际结果：**0 warnings / 0 errors**。Demo 的裸 `pnpm lint` 因临时目录忽略规则提示“未找到文件”，不能拿该命令当作源码 lint 通过证据。

当时的 Playwright 原型回归覆盖：

- 三种画幅容器宽高比；竖屏/横屏字幕预览差异。
- 创建分组、BGM 真实试听/上传、风格样图、字幕样式预览。
- 分镜 AI 提示词、自动保存、预览重生、插入/删除、混合来源渲染计数。
- 二次编辑保存守卫、逐帧重生、上传替换、重新合成、音色/语速三选。
- 字幕时间校验、连续多轮编辑、390px 无横向溢出。
- `pageErrors` 为空。
- 内置浏览器对最终单文件验收时，BGM 音量从 15% 调整并保存后，“重新合成”变为可用。

### 恢复后的复验（2026-11-22）

- `pnpm build`：TypeScript + Vite 构建成功。
- `pnpm exec oxlint --no-ignore src/App.tsx src/main.tsx`：0 warnings / 0 errors。
- 重新打包的 `bundle.html` 约 15.59 MiB，图片、音频和视频均内嵌。
- 使用恢复期间的最终 `verify-v6.mjs` 对单文件重新执行完整 Playwright 回归：创建、三种画幅、分镜、二次编辑、音频三选、字幕校验、取消/修复、连续编辑及 390px 移动端全部通过，`pageErrors` 为空。
- 恢复后的分镜与二次编辑截图已人工查看，确认竖屏容器、提示词和音频/字幕区域仍为最后设计。

临时验证脚本和测试截图已在验收后清理；**当前不随 Demo 保留 `verify-v6.mjs`，后续复验需按上述场景重新编写测试，不要引用不存在的脚本。**

## 9. 尚未验证与禁止误报

- 没有调用真实付费生成 API，没有做真实用户任务的端到端成片验收。
- 正常二次编辑结论来自代码链路和定向测试，不等于已对所有 Provider、所有线上任务实测。
- `ass_warning` 异常分支未做实片复现；应写“代码确认的反馈缺口”，不能写“每次字幕保存都失败”。
- 自动字幕折行样例直接调用过真实后端函数；Demo 的其它生成过程仍是原型模拟。
- 当前工作区可能存在用户的其他未提交改动；不要清理、回滚或提交无关文件。

## 10. 接手后的最小检查单

1. 先读本交接第 4～7 节，再读当前代码，核对行号是否漂移。
2. 向用户确认本次是继续完善设计、恢复 Demo，还是开始生产实施；三者不要混淆。
3. 若实施，先明确允许修改路径和验收场景，避免扩大到 Worker、Provider 或数据库。
4. 每修改一个界面元素，都能回答“它对应哪个已有字段、API 或真实规则”。
5. 交付前至少验证：三种画幅、BGM 有/无两种状态、音色/语速保存后的下一步、字幕样式/文本重新合成、异常反馈、390px 布局。
6. 按根 `AGENTS.md` 执行对应门禁和 PR 规则；本交接不替代完成定义。
