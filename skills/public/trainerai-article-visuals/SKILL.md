---
name: trainerai-article-visuals
description: 为文章或报告生成精美封面图与文内插图。封面模式：5个维度定制（类型/调色板/渲染/文字/氛围），含11种调色板和7种渲染风格，支持电影比例（2.35:1）、宽屏（16:9）和方形（1:1）等多种宽高比。插图模式：智能分析文章结构，识别配图位置，生成风格一致的插图，采用 Type x Style x Palette 三维配图方案。触发词：'封面图'、'生成封面'、'文章封面'、'制作封面'、'为文章配图'、'文章插图'、'添加配图'、'文章加图'。
---

# Article Visuals — Cover & Illustration

One skill, two modes: **封面模式 (Cover Mode)** generates article cover images with 5-dimensional customization; **插图模式 (Illustration Mode)** analyzes articles and generates consistent in-article illustrations (Type x Style x Palette).

## Mode Selection

| User wants | Mode |
|-----------|------|
| 封面 / 题图 / 头图（"封面图"、"生成封面"、"文章封面"、"制作封面"） | **封面模式 (Cover Mode)** |
| 文内插图 / 配图（"为文章配图"、"文章插图"、"添加配图"、"文章加图"） | **插图模式 (Illustration Mode)** |
| Both cover and illustrations for the same article | Run 封面模式 first, then 插图模式 — one EXTEND.md serves both |
| Unclear | Ask once which visual they need, via the user-input tool below |

Both modes share the skeleton in the following sections (user input, image backend, preferences, confirmation, reference images); the dimension libraries and step-by-step workflows are per mode.

## User Input Tools

When this skill prompts the user, follow this tool-selection rule (priority order):

1. **Prefer built-in user-input tools** exposed by the current agent runtime — e.g., `AskUserQuestion`, `request_user_input`, `clarify`, `ask_user`, or any equivalent.
2. **Fallback**: if no such tool exists, emit a numbered plain-text message and ask the user to reply with the chosen number/answer for each question.
3. **Batching**: if the tool supports multiple questions per call, combine all applicable questions into a single call; if only single-question, ask them one at a time in priority order.

Concrete `AskUserQuestion` references below are examples — substitute the local equivalent in other runtimes.

## Image Generation Tools

When this skill needs to render an image, resolve the backend in this order:

1. **Current-request override** — if the user names a specific backend in the current message, use it.
2. **Auto-select**:
   - If the current runtime exposes a native image tool (e.g., Codex `imagegen`, Hermes `image_generate`), use it. Runtime-native tools are preferred by default — agents that know their own tool inventory should surface the native one here.
   - Otherwise, use `trainerai-imagine` as the default image backend.
3. **If none are available**, tell the user and ask how to proceed.

**Prompt file requirement (hard)**: write each image's full, final prompt to a standalone file under `prompts/` (naming: `NN-{type}-[slug].md`) BEFORE invoking any backend. The backend receives the prompt file (or its content); the file is the reproducibility record and lets you switch backends without regenerating prompts.

When using `trainerai-imagine`, read the trainerai-imagine SKILL.md for its documented interface before invoking it, then call:
```bash
tsx /mnt/skills/public/trainerai-imagine/scripts/main.ts --prompt <prompt-file> --output <output-path> [--aspect <ratio>] [--ref <files...>]
```

## Preferences (EXTEND.md)

Both modes read a single shared EXTEND.md. Resolution order:

```bash
# macOS, Linux, WSL, Git Bash
test -f .baoyu-skills/trainerai-article-visuals/EXTEND.md && echo "project"
test -f "${XDG_CONFIG_HOME:-$HOME/.config}/baoyu-skills/trainerai-article-visuals/EXTEND.md" && echo "xdg"
test -f "$HOME/.baoyu-skills/trainerai-article-visuals/EXTEND.md" && echo "user"
```

```powershell
# PowerShell (Windows)
if (Test-Path .baoyu-skills/trainerai-article-visuals/EXTEND.md) { "project" }
$xdg = if ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME } else { "$HOME/.config" }
if (Test-Path "$xdg/baoyu-skills/trainerai-article-visuals/EXTEND.md") { "xdg" }
if (Test-Path "$HOME/.baoyu-skills/trainerai-article-visuals/EXTEND.md") { "user" }
```

| Result | Action |
|--------|--------|
| Found | Read, parse, display summary → Continue |
| Not found | ⛔ **BLOCKING**: Run first-time setup ONLY ([references/config/first-time-setup.md](/mnt/skills/public/trainerai-article-visuals/references/config/first-time-setup.md)) → Complete and save EXTEND.md → Then continue |

Schema: shared keys (`watermark`, `language`, `preferred_image_backend`) plus per-mode sections (`cover.*`, `illustration.*`). Each mode reads only its own section; mode reference docs name the keys accordingly (e.g., `cover.quick_mode`, `illustration.default_output_dir`).

## Confirmation Policy

Default behavior: **confirm before generation**.

- Treat explicit skill invocation, a file path, matched keywords/signals/presets, documented auto-selection, and environment/preference defaults as **recommendation inputs only**. None of them authorizes skipping confirmation.
- Do **not** start the generation steps (Cover Mode Steps 3–4; Illustration Mode Steps 4+) until the user completes the mode's confirmation step (Cover Mode Step 2; Illustration Mode Step 3).
- Skip confirmation only when the current request explicitly says to do so, for example: `--quick`, "直接生成", "不用确认", "跳过确认", "按默认出图", or equivalent wording.
- If confirmation is skipped explicitly, state the assumed settings in the next user-facing update before generating (cover: dimensions / aspect / language / backend; illustration: type / density / style / palette / language / backend).

## Reference Images (Shared Rules)

Users may supply reference images via `--ref <files...>` or by providing file paths / pasting images in conversation. Refs guide style, palette, composition, or subject.

Common rules for both modes:

- **Files before frontmatter**: only add `references` to a prompt file's frontmatter when the image files are ACTUALLY SAVED on disk (cover mode: `refs/`; illustration mode: `references/` under the output directory). Verify with `test -f` before writing.
- **Usage types**: `direct` (pass via `--ref` to a ref-capable backend) | `style` (extract visual traits into prompt text) | `palette` (extract colors into prompt text).
- **Verbal extraction** (user can't provide a file): omit the `references` frontmatter field, append extracted style/colors to the prompt body instead.
- When the chosen backend supports batch input, `direct`-usage entries in each prompt file's `references:` frontmatter should be propagated into its batch payload so backends can pass them through.

Mode-specific detection, storage, and processing rules: cover → [references/cover/workflow/reference-images.md](/mnt/skills/public/trainerai-article-visuals/references/cover/workflow/reference-images.md); illustration → [references/illustration/workflow.md](/mnt/skills/public/trainerai-article-visuals/references/illustration/workflow.md) (Step 1.0 saves, Step 5.3 processes per-illustration usage).

## Changing Preferences

- Edit EXTEND.md directly: pin a backend (`preferred_image_backend`), change per-mode defaults (`cover.*` / `illustration.*`), enable the shared `watermark`, set `language`.
- Retrigger first-time setup: delete EXTEND.md — the next run re-asks via [references/config/first-time-setup.md](/mnt/skills/public/trainerai-article-visuals/references/config/first-time-setup.md).

---

## Cover Mode（封面模式）

Generate elegant cover images for articles with 5-dimensional customization.

### Options

| Option | Description |
|--------|-------------|
| `--type <name>` | hero, conceptual, typography, metaphor, scene, minimal |
| `--palette <name>` | warm, elegant, cool, dark, earth, vivid, pastel, mono, retro, duotone, macaron |
| `--rendering <name>` | flat-vector, hand-drawn, painterly, digital, pixel, chalk, screen-print |
| `--style <name>` | Preset shorthand (see [Style Presets](/mnt/skills/public/trainerai-article-visuals/references/cover/style-presets.md)) |
| `--text <level>` | none, title-only, title-subtitle, text-rich |
| `--mood <level>` | subtle, balanced, bold |
| `--font <name>` | clean, handwritten, serif, display |
| `--aspect <ratio>` | 16:9 (default), 2.35:1, 4:3, 3:2, 1:1, 3:4 |
| `--lang <code>` | Title language (en, zh, ja, etc.) |
| `--no-title` | Alias for `--text none` |
| `--quick` | Skip confirmation, use auto-selection |
| `--ref <files...>` | Reference images for style/composition guidance |

### Five Dimensions

| Dimension | Values | Default |
|-----------|--------|---------|
| **Type** | hero, conceptual, typography, metaphor, scene, minimal | auto |
| **Palette** | warm, elegant, cool, dark, earth, vivid, pastel, mono, retro, duotone, macaron | auto |
| **Rendering** | flat-vector, hand-drawn, painterly, digital, pixel, chalk, screen-print | auto |
| **Text** | none, title-only, title-subtitle, text-rich | title-only |
| **Mood** | subtle, balanced, bold | balanced |
| **Font** | clean, handwritten, serif, display | clean |

Auto-selection rules: [references/cover/auto-selection.md](/mnt/skills/public/trainerai-article-visuals/references/cover/auto-selection.md)

### Galleries

**Types**: hero, conceptual, typography, metaphor, scene, minimal
→ Details: [references/cover/types.md](/mnt/skills/public/trainerai-article-visuals/references/cover/types.md)

**Palettes**: warm, elegant, cool, dark, earth, vivid, pastel, mono, retro, duotone, macaron
→ Details: [references/cover/palettes/](/mnt/skills/public/trainerai-article-visuals/references/cover/palettes/)

**Renderings**: flat-vector, hand-drawn, painterly, digital, pixel, chalk, screen-print
→ Details: [references/cover/renderings/](/mnt/skills/public/trainerai-article-visuals/references/cover/renderings/)

**Text Levels**: none (pure visual) | title-only (default) | title-subtitle | text-rich (with tags)
→ Details: [references/cover/dimensions/text.md](/mnt/skills/public/trainerai-article-visuals/references/cover/dimensions/text.md)

**Mood Levels**: subtle (low contrast) | balanced (default) | bold (high contrast)
→ Details: [references/cover/dimensions/mood.md](/mnt/skills/public/trainerai-article-visuals/references/cover/dimensions/mood.md)

**Fonts**: clean (sans-serif) | handwritten | serif | display (bold decorative)
→ Details: [references/cover/dimensions/font.md](/mnt/skills/public/trainerai-article-visuals/references/cover/dimensions/font.md)

### File Structure

Output directory: `cover-image/{topic-slug}/` (setup default `independent`; EXTEND.md `cover.default_output_dir` may override with `same-dir` / `imgs-subdir` to place the cover next to the article)

```
<output-dir>/
├── source-{slug}.{ext}    # Source files
├── refs/                  # Reference images (if provided)
│   ├── ref-01-{slug}.{ext}
│   └── ref-01-{slug}.md   # Description file
├── prompts/cover.md       # Generation prompt
└── cover.png              # Output image
```

**Slug**: 2-4 words, kebab-case. Conflict: append `-YYYYMMDD-HHMMSS`

### Workflow

#### Progress Checklist

```
Cover Image Progress:
- [ ] Step 1: Analyze content + save refs + determine output dir
- [ ] Step 2: Confirm options (6 dimensions) unless --quick
- [ ] Step 3: Create prompt
- [ ] Step 4: Generate image
- [ ] Step 5: Completion report
```

#### Step 1: Analyze Content

1. **Save reference images** (if provided) → [references/cover/workflow/reference-images.md](/mnt/skills/public/trainerai-article-visuals/references/cover/workflow/reference-images.md)
2. **Save source content** (if pasted, save to `source.md`)
3. **Analyze content**: topic, tone, keywords, visual metaphors
4. **Deep analyze references** ⚠️: Extract specific, concrete elements (see reference-images.md)
5. **Detect language**: Compare source and user input language
6. **Determine output directory**: Per File Structure rules

**⚠️ People in Reference Images:**

If reference images contain **people** who should appear in the cover:

- **Model supports `--ref`** (default): Copy image to `refs/`, pass via `--ref` at generation. No description file needed — the model sees the face directly.
- **Model does NOT support `--ref`** (Seedream 3.0): Create `refs/ref-NN-{slug}.md` with per-character description (hair, glasses, skin tone, clothing). Embed as MUST/REQUIRED instructions in prompt text.

See [reference-images.md](/mnt/skills/public/trainerai-article-visuals/references/cover/workflow/reference-images.md) for full decision table.

#### Step 2: Confirm Options ⚠️

**Hard gate**: this step is mandatory per the [Confirmation Policy](#confirmation-policy) — Steps 3–4 cannot start until the user confirms here (or explicitly opts out with `--quick` / equivalent wording in the current request).

**MUST use `AskUserQuestion` tool** to present options as interactive selection — NOT plain text tables. Present up to 4 questions in a single `AskUserQuestion` call (Type, Palette, Rendering, Font + Settings). Each question shows the recommended option first with reason, followed by alternatives.

Full confirmation flow and question format: [references/cover/workflow/confirm-options.md](/mnt/skills/public/trainerai-article-visuals/references/cover/workflow/confirm-options.md)

| Condition | Skipped | Still Asked |
|-----------|---------|-------------|
| `--quick` | 6 dimensions | Aspect ratio (unless `--aspect`) |
| All 6 + `--aspect` specified | All | None |

#### Step 3: Create Prompt

Save to `prompts/cover.md`. Template: [references/cover/workflow/prompt-template.md](/mnt/skills/public/trainerai-article-visuals/references/cover/workflow/prompt-template.md)

**CRITICAL - References in Frontmatter**:
- Files saved to `refs/` → Add to frontmatter `references` list
- Style extracted verbally (no file) → Omit `references`, describe in body
- Before writing → Verify: `test -f refs/ref-NN-{slug}.{ext}`

**Reference elements in body** MUST be detailed, prefixed with "MUST"/"REQUIRED", with integration approach.

#### Step 4: Generate Image

1. **Backup existing** `cover.png` if regenerating
2. **Select backend** via the `## Image Generation Tools` rule at the top: use `trainerai-imagine` by default, or whatever native image tool is available. If multiple, ask the user once.
3. **Write the full final prompt** to `prompts/01-cover-[slug].md` (hard requirement) BEFORE invoking the backend.
4. **Process references** from prompt frontmatter:
   - `direct` usage → pass via `--ref` (use ref-capable backend)
   - `style`/`palette` → extract traits, append to prompt
5. **Generate**: Call the chosen backend with the prompt file, output path, aspect ratio
6. On failure: auto-retry once

#### Step 5: Completion Report

```
Cover Generated!

Topic: [topic]
Type: [type] | Palette: [palette] | Rendering: [rendering]
Text: [text] | Mood: [mood] | Font: [font] | Aspect: [ratio]
Title: [title or "visual only"]
Language: [lang]
References: [N images or "extracted style" or "none"]
Location: [directory path]

Files:
- source-{slug}.{ext}
- prompts/cover.md
- cover.png
```

### Image Modification

| Action | Steps |
|--------|-------|
| **Regenerate** | Backup → Update prompt file FIRST → Regenerate |
| **Change dimension** | Backup → Confirm new value → Update prompt → Regenerate |

### Composition Principles

- **Whitespace**: 40-60% breathing room
- **Visual anchor**: Main element centered or offset left
- **Characters**: Simplified silhouettes; NO realistic humans
- **Title**: Use exact title from user/source; never invent

---

## Illustration Mode（插图模式）

Analyze articles, identify illustration positions, generate images with Type x Style x Palette consistency.

### Three Dimensions

| Dimension | Controls | Examples |
|-----------|----------|----------|
| **Type** | Information structure | infographic, scene, flowchart, comparison, framework, timeline |
| **Style** | Rendering approach | notion, warm, minimal, blueprint, watercolor, elegant |
| **Palette** | Color scheme (optional) | macaron, warm, neon — overrides style's default colors |

Combine freely: `--type infographic --style vector-illustration --palette macaron`

Or use presets: `--preset edu-visual` → type + style + palette in one flag. See [Style Presets](/mnt/skills/public/trainerai-article-visuals/references/illustration/style-presets.md).

### Types

| Type | Best For |
|------|----------|
| `infographic` | Data, metrics, technical |
| `scene` | Narratives, emotional |
| `flowchart` | Processes, workflows |
| `comparison` | Side-by-side, options |
| `framework` | Models, architecture |
| `timeline` | History, evolution |

### Styles

See [references/illustration/styles.md](/mnt/skills/public/trainerai-article-visuals/references/illustration/styles.md) for Core Styles, full gallery, and Type x Style compatibility.

### Workflow

```
- [ ] Step 1: Pre-check (references, config)
- [ ] Step 2: Analyze content
- [ ] Step 3: Confirm settings (AskUserQuestion)
- [ ] Step 4: Generate outline
- [ ] Step 5: Generate images
- [ ] Step 6: Finalize
```

#### Step 1: Pre-check

Load configuration from environment variables. If no config is set, use built-in defaults. If interactive and config is missing, prompt for essential settings (preferred type/style/palette, language, output directory).

Full procedures: [references/illustration/workflow.md](/mnt/skills/public/trainerai-article-visuals/references/illustration/workflow.md#step-1-pre-check)

#### Step 2: Analyze

| Analysis | Output |
|----------|--------|
| Content type | Technical / Tutorial / Methodology / Narrative |
| Purpose | information / visualization / imagination |
| Core arguments | 2-5 main points |
| Positions | Where illustrations add value |

**CRITICAL**: Metaphors → visualize underlying concept, NOT literal image.

Full procedures: [references/illustration/workflow.md](/mnt/skills/public/trainerai-article-visuals/references/illustration/workflow.md#step-2-setup--analyze)

#### Step 3: Confirm Settings

**Hard gate**: this step is mandatory per the [Confirmation Policy](#confirmation-policy) — Steps 4+ cannot start until the user confirms here (or explicitly opts out with "直接生成" / equivalent wording in the current request).

**ONE AskUserQuestion, max 4 Qs. Q1-Q2 REQUIRED. Q3 required unless preset chosen.**

| Q | Options |
|---|---------|
| **Q1: Preset or Type** | [Recommended preset], [alt preset], or manual: infographic, scene, flowchart, comparison, framework, timeline, mixed |
| **Q2: Density** | minimal (1-2), balanced (3-5), per-section (Recommended), rich (6+) |
| **Q3: Style** | [Recommended], minimal-flat, sci-fi, hand-drawn, editorial, scene, poster, Other — **skip if preset chosen** |
| Q4: Palette | Default (style colors), macaron, warm, neon — **skip if preset includes palette** |
| Q5: Language | When article language differs from default |

Full procedures: [references/illustration/workflow.md](/mnt/skills/public/trainerai-article-visuals/references/illustration/workflow.md#step-3-confirm-settings-)

#### Step 4: Generate Outline

Save `outline.md` with frontmatter (type, density, style, palette, image_count) and entries:

```yaml
## Illustration 1
**Position**: [section/paragraph]
**Purpose**: [why]
**Visual Content**: [what]
**Filename**: 01-infographic-concept-name.png
```

Full template: [references/illustration/workflow.md](/mnt/skills/public/trainerai-article-visuals/references/illustration/workflow.md#step-4-generate-outline)

#### Step 5: Generate Images

**BLOCKING: Prompt files MUST be saved before ANY image generation.** This is a hard requirement regardless of which backend is chosen — the prompt file is the reproducibility record.

1. For each illustration, create a prompt file per [references/illustration/prompt-construction.md](/mnt/skills/public/trainerai-article-visuals/references/illustration/prompt-construction.md)
2. Save to `prompts/NN-{type}-{slug}.md` with YAML frontmatter
3. Prompts **MUST** use type-specific templates with structured sections (ZONES / LABELS / COLORS / STYLE / ASPECT)
4. LABELS **MUST** include article-specific data: actual numbers, terms, metrics, quotes
5. **DO NOT** pass ad-hoc inline prompts to `--prompt` without saving prompt files first
6. Use `tsx /mnt/skills/public/trainerai-imagine/scripts/main.ts` as the backend. Do this once per session before any generation.
7. **Execution strategy**: When multiple illustrations have saved prompt files and the task is now plain generation, prefer the backend's batch interface (if it offers one) over spawning subagents. Use subagents only when each image still needs separate prompt iteration or creative exploration. If the backend has no batch interface, generate sequentially.
8. Process references (`direct`/`style`/`palette`) per prompt frontmatter
9. Apply watermark if enabled via config
10. Generate from saved prompt files; retry once on failure

Full procedures: [references/illustration/workflow.md](/mnt/skills/public/trainerai-article-visuals/references/illustration/workflow.md#step-5-generate-images)

#### Step 6: Finalize

Insert `![description]({relative-path}/NN-{type}-{slug}.png)` after paragraphs. Path computed relative to article file based on output directory setting.

```
Article Illustration Complete!
Article: [path] | Type: [type] | Density: [level] | Style: [style] | Palette: [palette or default]
Images: X/N generated
```

### Output Directory

Output directory is determined by config (set during first-time setup, default is `imgs-subdir`):

| `illustration.default_output_dir` | Output Path | Markdown Insert Path |
|-----------------------------------|-------------|----------------------|
| `imgs-subdir` (default) | `{article-dir}/imgs/` | `imgs/NN-{type}-{slug}.png` |
| `same-dir` | `{article-dir}/` | `NN-{type}-{slug}.png` |
| `illustrations-subdir` | `{article-dir}/illustrations/` | `illustrations/NN-{type}-{slug}.png` |
| `independent` | `illustrations/{topic-slug}/` | `illustrations/{topic-slug}/NN-{type}-{slug}.png` (relative to cwd) |

All auxiliary files (outline, prompts) are saved inside the output directory:

```
{output-dir}/
├── outline.md
├── prompts/
│   └── NN-{type}-{slug}.md
└── NN-{type}-{slug}.png
```

When input is **pasted content** (no file path), always uses `illustrations/{topic-slug}/` with `source-{slug}.{ext}` saved alongside.

**Slug**: 2-4 words, kebab-case. **Conflict**: append `-YYYYMMDD-HHMMSS`.

### Modification

| Action | Steps |
|--------|-------|
| Edit | Update prompt → Regenerate → Update reference |
| Add | Position → Prompt → Generate → Update outline → Insert |
| Delete | Delete files → Remove reference → Update outline |

---

## References

**Shared**: [config/first-time-setup.md](/mnt/skills/public/trainerai-article-visuals/references/config/first-time-setup.md)

**Cover mode**:
**Dimensions**: [text.md](/mnt/skills/public/trainerai-article-visuals/references/cover/dimensions/text.md) | [mood.md](/mnt/skills/public/trainerai-article-visuals/references/cover/dimensions/mood.md) | [font.md](/mnt/skills/public/trainerai-article-visuals/references/cover/dimensions/font.md)
**Palettes**: [references/cover/palettes/](/mnt/skills/public/trainerai-article-visuals/references/cover/palettes/)
**Renderings**: [references/cover/renderings/](/mnt/skills/public/trainerai-article-visuals/references/cover/renderings/)
**Types**: [references/cover/types.md](/mnt/skills/public/trainerai-article-visuals/references/cover/types.md)
**Auto-Selection**: [references/cover/auto-selection.md](/mnt/skills/public/trainerai-article-visuals/references/cover/auto-selection.md)
**Style Presets**: [references/cover/style-presets.md](/mnt/skills/public/trainerai-article-visuals/references/cover/style-presets.md)
**Compatibility**: [references/cover/compatibility.md](/mnt/skills/public/trainerai-article-visuals/references/cover/compatibility.md)
**Visual Elements**: [references/cover/visual-elements.md](/mnt/skills/public/trainerai-article-visuals/references/cover/visual-elements.md)
**Workflow**: [confirm-options.md](/mnt/skills/public/trainerai-article-visuals/references/cover/workflow/confirm-options.md) | [prompt-template.md](/mnt/skills/public/trainerai-article-visuals/references/cover/workflow/prompt-template.md) | [reference-images.md](/mnt/skills/public/trainerai-article-visuals/references/cover/workflow/reference-images.md)

**Illustration mode**:

| File | Content |
|------|---------|
| [references/illustration/workflow.md](/mnt/skills/public/trainerai-article-visuals/references/illustration/workflow.md) | Detailed procedures |
| [references/illustration/usage.md](/mnt/skills/public/trainerai-article-visuals/references/illustration/usage.md) | Command syntax |
| [references/illustration/styles.md](/mnt/skills/public/trainerai-article-visuals/references/illustration/styles.md) | Style gallery + Palette gallery |
| [references/illustration/style-presets.md](/mnt/skills/public/trainerai-article-visuals/references/illustration/style-presets.md) | Preset shortcuts (type + style + palette) |
| [references/illustration/prompt-construction.md](/mnt/skills/public/trainerai-article-visuals/references/illustration/prompt-construction.md) | Prompt templates |
