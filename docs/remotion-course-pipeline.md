# Remotion 课程视频管线（remotion_course）详解与批次史

> 2026-09-30 从根 `AGENTS.md` 迁出——原节占全文 89%，其中批次史为参考性质。常规范（每会话必知）留在根 `AGENTS.md` 的 remotion_course 节；本文档为完整口径 + 历史记录，按需查阅。
> 时间线：2026-09-08 初建；2026-09-10 独立管线改造；2026-09-11 审查修复批；2026-10-06/07 draft 后台配音与 verify_drift 漂移守卫批。

- `video_type="remotion_course"` 走后端任务管线：**脚本解析/分镜规划/素材决策/版式
  内容全链路为独立管线**（不套用 ai_image/ai_video 口径），合成交棒**独立
  remotion-worker 池**（`WORKER_POOL=remotion`，认领谓词
  `task_type IN ('remotion_course','remotion_still')`；ffmpeg 池白名单=registry 派生）。
- 既有 ai_image/ai_video 代码的边界口径：**逐行等价（缩进变化）**——remotion_course
  分支均为新增；存量 draft 重渲染时非槽位版式的 ai_image/graphic 帧不再生图
  （收窄方向，独立管线计划风险 8 已认可）。
- 模板 = `video-templates/course-standard/`（19 版式 × 4 主题，`DataDrivenCourse`
  数据驱动主 Composition；props 由 gateway `remotion_course_props.py` 构建，worker
  只消费）。改模板必须重建 remotion-worker 镜像（TEMPLATE_VERSION hash 防漂移）。
- 品牌包：`CourseProject` 的 `remotion_theme/font_family/logo_path/intro_outro`
  （`brand_pack.py` 单一事实源）；不挂项目用系统默认（dark-blue，D12）。
- **独立解析（2026-09-10 独立管线计划 T2）**：`orchestration/course_script.py`
  `parse_course_script` 双档（`course_parse_mode`：`faithful` 保真默认 / `rewrite`
  AI 改写）一次产出结构化单元（layout + layout_content + narration + image_ref?
  + 可选信息图导向 visual_prompt + chapter）；机器校验（数字等值归一/覆盖率/防扩写，
  失败带更严 prompt 重试一次）→ 容量契约拆分（bullets>4 拆连续单元 +
  `layout_overflow_risk` warn 留痕）→ 元帧门槛（`gate_meta_frames`：单元<8/章节<3
  降级元帧）→ `units_to_frames` 帧构建。校验不过/解析失败 → `units_from_paragraphs`
  确定性兜底（degradation `course_parse_fidelity_fallback`）；`ai_normalize_script=False`
  = API 层纯切分档（零 AI）。标题进 content 不逐字念；列表要点进 content 且完整
  内容仍在旁白念出。
- **plan_course 整体跳过（P3）**：锚定卡/调度表/逐帧扩写/路由护栏/graphic spec 全套
  不执行（service 侧跳过 + `plan_course` 内部 `remotion_course` 早退双保险）；TTS 前
  拆帧守卫同样跳过（P4，旁白跑 `strip_image_markers` 防御网）。调度表 layout hint
  addon 成为死路径（保留未删）。
- **视觉源（T4，`visual_sources/remotion_course_visuals.py`）**：工厂
  `make_visual_source("remotion_course")` 返回 `RemotionCourseVisualSource`（内持
  ai_image/ai_video 既有源，provider 调用层复用）；主管线绕过 `_has_mixed_sources`
  判定直走工厂（P5a）。生图条件（§3.4 四者同时）：图槽版式
  （ImageTextLeft/Right、QuotePage、EndingPage、FullVisualPage）+ 用户/AI 设
  `visual_source=="ai_image"` + image_prompt 非空 + 无 local_visual_path；AI 视频
  仅用户显式设置 `visual_source="ai_video"` 的 VideoClipPage 帧（parser 永不产出）；
  其余帧渲染占位。roster/锚图/参考图 kwargs 接收并忽略。
- **单帧重生守卫（P6）**：`_regenerate_frame` 对嵌入图（`visuals/embedded/`）与
  用户上传（`visuals/frame_`）帧拒绝视觉重生（ValueError"请替换或删除"），防
  P0-A1 清空误删素材；audio_only 不受限；None 源帧走工厂新分支（修前必抛
  ValueError）。
- **docx 内嵌图（T6，`orchestration/course_staging.py`）**：`POST /script/extract?
  with_images=1`（默认关=markitdown 口径不变）stdlib OOXML 原位提取（文本+表格
  逐行+`【图片N】**全部标记**进 image_ref/image_refs_extra，2026-09-11 补）+小图
  过滤 ignored_images 计数，staging 暂存
  `<storage>/script-uploads/{extract_id}/`（ULID，TTL 24h 每次 extract 顺带清理）；
  create 带 `script_extract_id` 认领搬进 `task_dir/visuals/embedded/`（**仅
  image_paths=None 的 create 路径认领**——reparse 传入重建映射，B1 修复：残留
  extract_id 重认领会覆盖成 {} + 假留痕；端点侧同时 pop）；过期认领 →
  degradation `embedded_images_expired` + payload warning，任务继续。pdf/md 图片
  不支持自动分配（pdf 有提示；前端对 pdf 也发 with_images 触发后端提示 + 行内
  pdfNote，2026-09-11 闭环）。**一单元多图顺延（§3.6）**：`defer_extra_images`
  首图进槽、其余顺延到后续无图单元（帧 `image_note`="来自邻近单元的文档配图"，
  编辑器素材区展示；degradation `embedded_image_deferred` 留痕，无处顺延 → 丢弃
  留痕）。
- **数据契约（T1/T5/T7）**：`StoryboardFrame.layout_content`（版式结构化内容，
  仅 remotion_course 消费；编辑器写回=生效值）；`remotion_course_props._layout_props`
  确定性默认值 + layout_content 白名单 overlay（类型矫正/非法丢弃；无 content 行为
  与旧版一致）；still cache key 含 layout_content。PUT frame 端点接受
  `layout_content`（dict 透传/显式 null 清除）与 `visual_source`（remotion_course
  校验：仅 ai_image/ai_video/null，ai_video 仅 VideoClipPage）。
- **重新解析（T8，决策 15）**：`POST /tasks/{id}/reparse`（body
  `{"course_parse_mode": "faithful"|"rewrite"}`）仅 draft（409 否则；覆盖写盘前
  **复查一次状态**收窄与渲染的竞态窗口，2026-09-11）、整量覆盖重建
  storyboard（v1 语义：手改与已上传素材丢失，前端二次确认明示）；嵌入图映射从
  `task_dir/visuals/embedded/` 重建（不依赖 staging，payload 残留 extract_id 同步
  摘除）；复用 `_build_course_storyboard`（P1 直渲/P2 草稿/reparse 三处共用）。
  PUT frame 的 `visual_source` 支持**显式 null**（取消 AI 生成，仅 remotion_course；
  model_fields_set 区分「未传」与「显式 null」，与 layout/layout_content 同口径）。
- 版式优先级链（D14）：`frame.layout`（用户手改 > parser 产出）> 确定性规则表
  （`_deterministic_layout`；`assign_deterministic_layouts` 钩子保留补 None 帧）。
- QC（D19）：worker 三扫描发现异常 → `qc_repair` 状态（不标 ready）→ gateway
  watcher 首次清视觉重生 / 二次降级占位交付（`_process_qc_repair_tasks`）。
- 静帧预览（T7）：`remotion_still` 任务（priority=20，短租约 600s，独立 still 槽），
  缓存 key = layout+brand+帧内容 hash+layout_content；30s 超时前端降级 M0 示意图（D15）。
- 草稿后台配音与漂移守卫（2026-10-06/07 两批）：draft 态（`auto_render=False`）后台
  逐帧 TTS 由 `_generate_draft_audio` 承担（持 `_synth_lock` 整帧集补配，≤2 轮）；
  合回分镜前过 verify_drift 内容等价比对（`_merge_audio_into_storyboard`，
  remotion_course 磁盘侧先过同一 `strip_image_markers`）——草稿期间手改的内容不被
  配音回写覆盖；audio_ready/audio_error 互斥、storyboard 消失有守卫。
- **前端**：分镜页按 `task_type` 路由分流（`StoryboardRouteSplit` loading 骨架，
  路由判定后停 task 轮询——enabled 门控防 draft 态 2s 永久轮询回归）→
  `RemotionCourseStoryboard`（版式卡片形态：版式切换+静帧预览+schema 驱动结构化
  内容表单（`layout-content-schema.ts`，测试直读后端 `LAYOUT_CONTENT_FIELDS` 源码
  跨仓守卫防三方漂移）+旁白试听+素材区门控（图槽/VideoClipPage 才显示；嵌入/
  上传帧只替换；**素材来源按路径前缀判定** `isEmbeddedOrUploadedPath`——
  `visuals/embedded/`、`visuals/frame_` 为用户素材，`visuals/img_`、`shot_` 为 AI
  本地产物可「重新生成」；无素材显示占位提示；顺延图帧展示 image_note）；
  帧卡 key=内容签名（旁白/版式/layout_content 变化即重挂载——reparse 后本地
  草稿不残留）；配图描述**失焦保存**；draft 态重排（未分组拖拽手柄 + 全视图
  上移/下移，复用 reorder 端点）；「重新解析」双弹层；「开始渲染」确认弹层含
  占位检查与**真会生成**的配图/视频计数（排除已有素材帧）。创建页：话题生成
  模式移除（supportsGenerate=false）、「解析方式」radio（保真/改写+双档旁白
  样例对比，样例不改数字）、docx 带图提示（含忽略计数+expires_at 过期拦截——
  提交与存草稿双路径）、pdf 上传行内提示（图片不自动分配）、隐藏画面比例/画质
  （渲染恒 1080p；payload 层 aspect 恒 16:9，防竖屏字幕启发式）。详情页
  `FrameOpsPanel` 换 remotion_course 专属形态（版式+静帧+素材区，替换「重生
  画面」；保留「重生配音」）。
- ⚠️ 旧 `video_type="remotion"` 是 chat-handoff 链路（agent 现写代码），与
  `remotion_course` 语义不同，前端门控不得合并。
- **入口归一（D1，2026-09-09 feedback-round1）**：video-gen 页创建 Tab「文字动效」
  = `remotion_course`（提交即走管线）；chat-handoff 入口从 video-gen 页**彻底隐藏**
  （Agent Chat 能力保留在 Agent 工作台 course-video agent，文件
  `remotion-chat-handoff.ts` 保留供其使用）。存量 remotion 任务列表只读兼容。
- 本机 E2E 注意：gateway 的 `REMOTION_WORKER_HEALTH_URL` 本地指向
  `http://localhost:8041`——本地跑 remotion_course 需在 8041 起原生 remotion 池
  worker（`WORKER_POOL=remotion HEALTH_PORT=8041 REMOTION_CHROME_PATH="" python3
  worker.py`，DB/STORAGE 指向 backend/.deer-flow；容器 worker 混 host SQLite 会
  disk I/O error）。D16 探针 60s 缓存：起/停 worker 后需等缓存过期或重启 gateway。
- **2026-09-11 审查修复批**（3 blocker + 5 后端 major + 6 前端 major + 2 计划
  偏离补交付）：reparse 二次认领覆盖嵌入图（B1，认领仅 create 路径 + 端点 pop）；
  AI 帧误判嵌入/上传（B2，路径前缀判定）；visual_source 显式 null（B3）；视觉源
  子调用传**真过滤视图**而非 only_frames（覆盖率闸分母=目标集——残留
  visual_source 的上传替换帧不再被打成 coverage too low）；个位数百分比等值
  （"5%"↔"百分之五"，CN 百分比分支去 _keep）；拆分钳制（句数不足时收缩拆数
  不产空旁白帧，余量并末组 + `bullets_overflow_unsplit` 留痕）；extract
  with_images 失败回退前 seek(0)；chart 矫正 NaN/inf 防御（json.loads 可携带裸
  NaN/Infinity）；"二零二四"位值拼接；staging 认领半途失败清残留 + PIL 不识别
  格式过滤收紧 + schema max_length=40 对齐认领白名单。
- **2026-09-11 二轮审查批**（17/17 属实）：`_regenerate_frame` 对 remotion_course
  **不做帧级 override**（帧源改派裸源会架空课程源——覆盖率误杀 + 注入通道不一致；
  i2v 预览保留改按帧源判）；兜底解析单 \n 文本回落行级切分（防整脚本塌 1 帧）；
  重排下移发 index+2（后端 insert-before 语义）+ 组边界禁用；编辑器 task 查询
  拿到即停轮询（resolvedTask 模式）；创建对话框关闭重置提取态 + 提交前标记集
  比对（改贴新脚本不携带旧 extract_id）；reparse 清解析类旧留痕；帧卡 key 不含
  index（重排不丢未保存草稿）；计数/占位判定与后端四条件逐字对齐；素材区重生走
  useRegenerateFrame；两处 isAiMaterialFrame 共用；路由分流错误分支可重试；数字
  字段串态保存转换；ContentEditor dirty 派生；pdf 双 warning 拼接；extract 响应
  去 base64 字节；留痕 1-based + narration 定位。
- 测试：`tests/test_course_script.py`（解析器/校验/门槛/帧构建/多图顺延）、
  `tests/test_remotion_course_pipeline_split.py`（P1-P6 分流+认领门控）、
  `tests/test_remotion_course_visuals.py`（分派矩阵+过滤视图）、`tests/test_course_staging.py`
  （docx/staging/认领/回退倒带）、`tests/test_remotion_course_api.py`（T7/T8 端点+B1/B3/竞态回归）、
  `tests/test_remotion_course_props.py`（+layout_content overlay+NaN 防御）、
  `tests/test_storyboard_persistence.py`（+layout_content 往返）；
  前端 `layout-content-schema.test.ts`（跨仓守卫：直读后端 LAYOUT_CONTENT_FIELDS 源码比对）
  + `remotion-course-options.test.tsx`。
