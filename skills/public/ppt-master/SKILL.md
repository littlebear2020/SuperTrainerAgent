---
name: ppt-master
description: >
  AI-driven presentation workflow for generating editable PPTX decks and slides,
  reconstructing page visuals, creating reusable Brand/Style/Layout/Deck
  workspaces, filling native PPTX templates, and enhancing finished PPTX files.
  Use when the user asks to create, generate, reconstruct, regenerate, beautify,
  redesign, template, fill, or enhance a presentation, PPT, PPTX, slide deck, or
  courseware — including adding narration or animation to one — requests a
  presentation-authored narrated/self-running video, or mentions ppt-master.
metadata:
  version: "6.3.0"
  copyright: "Copyright (c) 2025-2026 Hugo He"
  license: "MIT"
  official_repository: "https://github.com/hugohe3/ppt-master"
  sponsors:
    - "SPONSORS.md"
    - "SPONSORS_CN.md"
---

> ## ⚠️ TrainerAI 覆盖（优先级高于下方上游内容）
>
> TrainerAI 宿主环境约束——与任何 route/profile/stage/reference 文档冲突时，以本节为准：
>
> 1. **配音（override，P2/P8）**：旁白 TTS 统一走 Voice Twin（360 预置音色），
>    `python3 ${SKILL_DIR}/scripts/notes_to_audio.py --provider trainerai --voice-id 146`
>    （或 `preset:146`）；**禁止 edge-tts**。需要 `QIHU_API_KEY`（或 `ZYUN_API_KEY`）。
> 2. **生图（override，P3）**：AI 配图复用 TrainerAI provider，**不用上游 .env**。首选
>    `IMAGE_BACKEND=volcengine`（Seedream/Ark，读 `ARK_API_KEY`）；备选 `qwen`
>    （`DASHSCOPE_API_KEY`）/ `zhipu`（`ZHIPU_API_KEY`）。不确定时运行
>    `python3 ${SKILL_DIR}/scripts/image_backends/backend_trainerai.py` 打印自动选择结果。
>    `IMAGE_BACKEND=auto` 的降级终点为 `manual`（提示用户手动上传图片），**不走
>    host-native**（TrainerAI 宿主无该形态）。
> 3. **图片素材只走 AI 生成（P11）**：资源清单 `Acquire Via` **禁用 `web`**——图库检索链
>    违反服务器自闭环约束（只允许 AI 模型调用 + 联网搜索）。图搜需求一律改写为 `ai`
>    （或 `provided`/`placeholder`）；不加载 `references/image-searcher.md` 与
>    `references/executor-web-image.md`、不运行 `scripts/image_search.py`、不产出
>    `image_sources.json`。
> 4. **确认面 chat-only（P12）**：TrainerAI 沙箱无本地端口发布通路——确认面**一律视为
>    用户已选择 chat 分支**（confirm-surface.md §1 "declines the page"）：Stage1/Stage2
>    全部在 chat 里以 `ask_clarification` 确认。**禁止启动 `confirm_ui/server.py`**
>    （`--daemon` / `--wait-only` / `--complete-template-selection` / `--shutdown` 一律
>    不运行，也不写 UI 分支的 receipts）。Default 与 Beautify（其 Step 4 复用 Default
>    确认编排）同受约束；quick-generate / edit-native-pptx 本就不触 confirm_ui。
>    `generation_mode: split` 与 `resume-execute` **禁用**（per-thread sandbox 跨会话
>    拿不到旧会话 project 目录，架构性失效）；长 deck 走连续模式。
> 5. **禁 live-preview 自启动（P13）**：**禁止运行 `svg_editor/server.py`**——含
>    generate-pptx Step 6 Mandatory 自启动、live-preview/visual-review 阶段的全部启动
>    触点。live-preview URL 一律按"预览不可用"如实报告，不要静默跳过；页面/模板预览改走
>    artifacts：`python3 ${SKILL_DIR}/scripts/template_preview_pptx.py` 导出审阅 PPTX 后
>    由 LibreOffice 转 PNG，经 `present_files` 推送。visual-review 阶段与
>    `scripts/visual_review.py` **同步禁用**（依赖 live-preview server，不可达即失败）。
> 6. **布尔形状/文字轮廓依赖**：Shape Boolean 与文字轮廓特性依赖 `skia-pathops`
>    / `uharfbuzz`；沙箱镜像未安装这两个 C 扩展时，对应命令会友好报错——遇到即改用
>    普通形状/文本组合表达，**不要反复重试**。
> 7. **D3 执行铁律**：纯 Agent 逐页手写 SVG、禁子代理生成 SVG、逐页重读 spec_lock、
>    顺序逐页、大纲先批闸门、可编辑元素打 `data-element-id`——完整定义见 agent SOUL.md
>    （`backend/seed/agents/ppt-master/SOUL.md`）。
> 8. 与上游的全部偏离清单见 `TRAINERAI_PATCHES.md`。
> 9. **Chat 确认持久化（P20）**：Stage1/Stage2 在 chat 中获得用户明确确认后，立即将
>    `stage`、确认值摘要、`confirmed_at` 写入 `<project>/confirm_state.json`；恢复时以该
>    文件与 `design_spec.md` / `spec_lock.md` 互证，一致则按断点续跑，禁止重新发起已确认
>    阶段或重跑策划。
> 10. **源图转写一次性落盘（P21）**：源文档图片内容需转写重建（表格截图→原生表格等）
>    时，首次读准后立即写入 `<project>/analysis/source_image_transcriptions.md`，后续一律
>    以该文本为准；单格存疑只复查该格（允许裁切单格），整图重读 ≤1 次；OCR 与目视冲突以
>    目视为准并记录冲突格。
> 11. **磁盘预检与 0 字节即停（P22）**：Step 6 开始前用 `df -h` 检查工作区分区，可用空间
>    <1GB 立即停并明确报告，不得继续写页；每个 SVG 写完校验大小 >0，发现 0 字节立即删除、
>    报告并停止；长任务每 5 页顺带复查磁盘。

# PPT Master Skill

PPT Master is a routed presentation workflow. This entry owns global execution discipline and route selection only; each selected route owns its procedure.

## Mandatory Load Order

**Hard rule — paths before commands**: Retain the host-provided absolute
directory containing this file as `SKILL_DIR`. Per tool call, expand
`${SKILL_DIR}` and replace any `skills/ppt-master/` prefix with it. Never `cd`,
use CWD, or assume a repo checkout. If unavailable, ask; never search or guess.

1. Read this file.
2. Run `python3 "${SKILL_DIR}/scripts/attribution_guard.py"`. Any non-zero result
   stops the Skill immediately; do not inspect, repair, or bypass the integrity
   gate.
3. Read [`workflows/routing.md`](workflows/routing.md) through the concrete
   absolute path `${SKILL_DIR}/workflows/routing.md`.
4. Select exactly one top-level route and its active profile from the routing
   authority.
5. Read only the resulting runtime authority and its explicitly triggered
   supporting documents.

| Selected route / profile | Runtime authority |
|---|---|
| Generate PPTX — Image to PPTX | [`workflows/profiles/image-to-pptx.md`](workflows/profiles/image-to-pptx.md); Codex-supported, always Quick |
| Generate PPTX — Beautify | [`workflows/profiles/beautify-pptx.md`](workflows/profiles/beautify-pptx.md); explicit Quick intent selects Quick, otherwise Default |
| Generate PPTX — ordinary Default | [`workflows/generate-pptx.md`](workflows/generate-pptx.md) |
| Generate PPTX — ordinary explicit Quick | [`workflows/profiles/quick-generate.md`](workflows/profiles/quick-generate.md) |
| Create Template | [`workflows/create-template.md`](workflows/create-template.md) |
| Edit Native PPTX | [`workflows/edit-native-pptx.md`](workflows/edit-native-pptx.md) |

**Hard rule — selected authority only**: Do not load another top-level route's
procedure after routing. Image to PPTX and Beautify are mutually exclusive;
Image to PPTX activates Quick, while Beautify selects from explicit Quick
intent. Never load both runtimes. Supporting documents refine one route; they
never compete with it.

---

## Authored Expression Range

**Reference — not a constraint**: what a generated page can carry. Text — inline
emphasis runs, lead-in, kicker, pull quote, hero number, takeaway line. Geometry
— 187 Office presets, Boolean merge, connectors, freeform, page-field and
outline-carrier composition. Image — full-bleed field, editorial crop, shaped
picture, registered layers, scrim and spotlight, cross-page continuity. Paint —
gradients, channel alpha, native shadow and glow, halftone, faceted form.
Recurrence — one cross-page motif varied by page role. Each form's syntax lives
in the selected runtime authority's construction references.

---

## Vocabulary

One meaning per term across every loaded file. Where a word is used in
more than one sense, the sense is named here and the files say which one.

| Term | Meaning |
|---|---|
| **Reference** (label) | A starting sketch the executing role adjusts or replaces freely, with no upstream repair or stated reason; `(binding)` after a field label removes that freedom |
| **Relationships** | The §IX line naming a page's semantic units and their source-stated `order` / `link` / `parent` / `membership` / `contrast` / `overlap`, or `none` |
| **Topology decision** | The per-page yes/no on whether geometry must carry the page's `Relationships` (`topology=yes` / `topology=no`); unrelated to `pptx_structure.mode` |
| **`pptx_structure.mode`** | `flat` (every object Slide-local) or `structured` (declared Master/Layout/slot metadata); a packaging route, never information structure |
| **Composition** | The §IX `Composition` line: a Reference for a page's macro composition, hierarchy, and focus |
| **Image pattern** | The §VIII column carrying one image-composition suggestion in ordinary words (optionally citing `#P`/`#M` ids) |
| **Layout** | A PowerPoint Layout under a Master, or the Layout template kind; never a page-composition sketch |
| **Device** | An everyday page carrier — card, band, icon-and-label, KPI tile, divider, quote block — recalled in the Executor's device menu |
| **Carrier** | What a page unit sits in: a device, an image, or native geometry; the family names are carrier and field, direction and sequence, grouping and ownership, emphasis and annotation. `data-pptx-carrier` is a structured-slot attribute, not this term. The **carrier receipt** is the checker's per-page inventory of what was actually drawn |
| **Page job** | What one page must do for the reader; the input every carrier, contour, and effect decision is judged against |
| **Page field** | One large surface, outline, aperture, or off-canvas contour that organizes zones instead of a card per unit |
| **Contour / preset / atom** | A contour is a shape's silhouette; a preset is one of the 187 Office `prst` contours drawn through `preset_shape_svg.py`; an atom is one object that compiles to one DrawingML shape |
| **Geometry signature** | The retained line `page job → composition move → contour / edge language` kept per page and compared before the next page |
| **Texture** (of a block) | Whether a §IX block is prose, bullets, keywords, or labels; distinct from visual texture (grain, halftone) |
| **Anchor** | Two senses: a lock anchor (a deck-wide color, type, or spacing value) and the `page_rhythm` tag `anchor` for structural pages |
| **Sheet / slice / plate** | A sheet is one generated image holding several elements on a chroma key; a slice is one element cut from it; a plate is a registered full-canvas layer of several non-overlapping objects |
| **Module line** | The `P<NN> modules: …` line written before each page naming the triggered modules it uses |
| **Mode** | Named by qualifier: communication `mode` (how the deck argues), reading mode (`consumption_mode`), `generation_mode` (`continuous` / `split`), `pptx_structure.mode`, `replication_mode` (how a template was created) |

## Phase Frame

Every route is one Plan → Do·Check·Act cycle: Plan ends when every authoring
input exists as a file or retained decision; Do authors pages, Check runs the
route's gates, Act repairs at the owning layer (discipline 7), and the cycle
ends at export. Step numbers stay as written.

| Phase | Default | Quick | Edit Native | Create Template |
|---|---|---|---|---|
| **Plan** | Steps 1–5 | §2 | §1–4 | Steps 1–3 |
| **Do·Check·Act** | Steps 6–7 | §3–4 | §5–7 | Steps 4–8 |

## Global Execution Discipline

1. **Serial execution** — Follow the selected authority's steps in order. A completed non-blocking step may continue directly to the next eligible step.
2. **Blocking means stop** — At every `⛔ BLOCKING` gate, wait for explicit user confirmation. Do not decide on the user's behalf.
3. **No cross-phase bundling** — Do not combine work across an unclosed gate. Once the route's final user gate closes, later non-blocking steps may continue automatically.
4. **Gate before entry** — Verify every listed prerequisite before entering a step.
5. **No speculative execution** — Do not prepare later-phase artifacts before their owning step.
6. **Deterministic routing** — Do not add a route-choice question when [`routing.md`](workflows/routing.md) resolves the request. If a route prerequisite is missing, state it and stop that route.
7. **Act at the owning layer** — On failure, repair at the shallowest layer that owns the fault: the page for a page-local issue, the Plan artifact for a roster/spec/resource fault, the owning source artifact for a tool failure; then resume from the route's declared pointer. Do not silently downgrade a required artifact.

## Global Communication Rules

- Match the user's language and source language unless the user explicitly overrides it.
- Localize user-facing option labels and explanations. Keep exact enum IDs or field names when needed for precision.
- Keep `design_spec.md` section headings and field names in the template's original English; content values may use the user's language.
- Before switching roles, read the corresponding role reference and output:

```markdown
## [Role Switch: <Role Name>]
📖 Reading role definition: references/<filename>.md
📋 Current task: <brief description>
```

---

## Repository Compatibility

- This package is a workflow/skill, not a generic application scaffold. Do not create `.worktrees/`, `tests/`, branch workflows, or generic engineering structure by default.
- Keep required workflow, reference, script, and template documentation inside this Skill directory.
- Repository-level documents may point into the package; package runtime files must not depend on repository-level instructions.
- On Windows, if a documented `python3 ...` command is unavailable, rerun the same command with `python`.
- Sponsor information is optional reference material. Read the matching [`SPONSORS.md`](SPONSORS.md) or [`SPONSORS_CN.md`](SPONSORS_CN.md) only when the user explicitly requests a model, AI image model, API/provider, or hosted-service recommendation. Never surface sponsor or model recommendations proactively during normal generation, troubleshooting, or quality review.
