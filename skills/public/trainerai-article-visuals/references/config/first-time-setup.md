---
name: first-time-setup
description: First-time setup flow for trainerai-article-visuals preferences
---

# First-Time Setup

## Overview

When no EXTEND.md is found, guide user through preference setup.

**⛔ BLOCKING OPERATION**: This setup MUST complete before ANY other workflow steps (either mode). Do NOT:
- Ask about reference images
- Ask about content/article
- Ask about dimensions (type, palette, rendering) or style preferences
- Proceed to content analysis

ONLY ask the questions in this setup flow, save EXTEND.md, then continue.

## Setup Flow

```
No EXTEND.md found
        │
        ▼
┌─────────────────────┐
│ AskUserQuestion     │
│ (all questions,     │
│  batched by group)  │
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│ Create EXTEND.md    │
└─────────────────────┘
        │
        ▼
    Continue to Step 1
```

## Questions

**Language**: Use user's input language or saved language preference.

One EXTEND.md serves both modes. Ask ALL question groups below — shared questions plus both mode-specific sets. Batch into AskUserQuestion calls (max 4 questions per call):

- **Call 1 (Shared)**: Watermark, Save Location
- **Call 2 (Cover mode)**: Preferred Type, Preferred Palette, Preferred Rendering, Default Aspect Ratio
- **Call 3 (Cover + Illustration)**: Cover Output Directory, Quick Mode, Illustration Preferred Style, Illustration Output Directory

### Shared Question 1: Watermark

```yaml
header: "Watermark"
question: "Watermark text for generated cover images / illustrations? Type your watermark content (e.g., name, @handle)"
options:
  - label: "No watermark (Recommended)"
    description: "Clean images, can enable later in EXTEND.md"
```

Position defaults to bottom-right. Applies to both modes.

### Shared Question 2: Save Location

```yaml
header: "Save"
question: "Where to save preferences?"
options:
  - label: "Project (Recommended)"
    description: ".baoyu-skills/ (this project only)"
  - label: "User"
    description: "~/.baoyu-skills/ (all projects)"
```

### Cover Question 1: Preferred Type

```yaml
header: "Cover Type"
question: "Default cover type preference?"
options:
  - label: "Auto-select (Recommended)"
    description: "Choose based on content analysis each time"
  - label: "hero"
    description: "Large visual impact - product launch, announcements"
  - label: "conceptual"
    description: "Concept visualization - technical, architecture"
```

### Cover Question 2: Preferred Palette

```yaml
header: "Cover Palette"
question: "Default cover color palette preference?"
options:
  - label: "Auto-select (Recommended)"
    description: "Choose based on content analysis each time"
  - label: "elegant"
    description: "Sophisticated - soft coral, muted teal, dusty rose"
  - label: "warm"
    description: "Friendly - orange, golden yellow, terracotta"
  - label: "cool"
    description: "Technical - engineering blue, navy, cyan"
```

### Cover Question 3: Preferred Rendering

```yaml
header: "Cover Rendering"
question: "Default cover rendering style preference?"
options:
  - label: "Auto-select (Recommended)"
    description: "Choose based on content analysis each time"
  - label: "hand-drawn"
    description: "Sketchy organic illustration with personal touch"
  - label: "flat-vector"
    description: "Clean modern vector with geometric shapes"
  - label: "digital"
    description: "Polished precise digital illustration"
```

### Cover Question 4: Default Aspect Ratio

```yaml
header: "Cover Aspect"
question: "Default aspect ratio for cover images?"
options:
  - label: "16:9 (Recommended)"
    description: "Standard widescreen - YouTube, presentations, versatile"
  - label: "2.35:1"
    description: "Cinematic widescreen - article headers, blog posts"
  - label: "1:1"
    description: "Square - Instagram, WeChat, social cards"
  - label: "3:4"
    description: "Portrait - Xiaohongshu, Pinterest, mobile content"
```

Note: More ratios (4:3, 3:2) available during generation. This sets the default recommendation.

### Cover Question 5: Default Output Directory

```yaml
header: "Cover Output"
question: "Default output directory for cover images?"
options:
  - label: "Independent (Recommended)"
    description: "cover-image/{topic-slug}/ - separate from article"
  - label: "Same directory"
    description: "{article-dir}/ - alongside the article file"
  - label: "imgs subdirectory"
    description: "{article-dir}/imgs/ - images folder near article"
```

### Cover Question 6: Quick Mode

```yaml
header: "Cover Quick"
question: "Enable quick mode by default for covers?"
options:
  - label: "No (Recommended)"
    description: "Confirm dimension choices each time"
  - label: "Yes"
    description: "Skip confirmation, use auto-selection"
```

### Illustration Question 1: Preferred Style

```
header: "Illustration Style"
question: "Default illustration style preference? Or type another style name or your custom style"
options:
  - label: "sketch-notes (Recommended)"
    description: "Warm cream paper, black hand-drawn lines, soft pastel blocks — educational infographic feel. Great default for most articles."
  - label: "None"
    description: "Auto-select based on content analysis (falls back to sketch-notes when no strong signal)"
  - label: "notion"
    description: "Minimalist hand-drawn line art"
  - label: "warm"
    description: "Friendly, approachable, personal"
```

### Illustration Question 2: Output Directory

```
header: "Illustration Output"
question: "Where to save generated illustrations when illustrating a file?"
options:
  - label: "imgs-subdir (Recommended)"
    description: "{article-dir}/imgs/ — images in a subdirectory next to the article"
  - label: "same-dir"
    description: "{article-dir}/ — images alongside the article file"
  - label: "illustrations-subdir"
    description: "{article-dir}/illustrations/ — separate illustrations subdirectory"
  - label: "independent"
    description: "illustrations/{topic-slug}/ — standalone directory in cwd"
```

## Save Locations

| Choice | Path | Scope |
|--------|------|-------|
| Project | `.baoyu-skills/trainerai-article-visuals/EXTEND.md` | Current project |
| User | `~/.baoyu-skills/trainerai-article-visuals/EXTEND.md` | All projects |

## After Setup

1. Create directory if needed
2. Write EXTEND.md with frontmatter
3. Confirm: "Preferences saved to [path]"
4. Continue to Step 1 of the active mode

## EXTEND.md Template

One file, shared keys + per-mode sections. Each mode reads only its own section (`cover.*` / `illustration.*`); `watermark`, `language`, and `preferred_image_backend` are shared.

```yaml
---
version: 1
watermark:
  enabled: [true/false]
  content: "[user input or empty]"
  position: bottom-right
  opacity: 0.7
language: null
preferred_image_backend: auto
cover:
  preferred_type: [selected type or null]
  preferred_palette: [selected palette or null]
  preferred_rendering: [selected rendering or null]
  preferred_text: title-only
  preferred_mood: balanced
  default_aspect: [16:9/2.35:1/1:1/3:4]
  default_output_dir: [independent/same-dir/imgs-subdir]
  quick_mode: [true/false]
  custom_palettes: []
illustration:
  preferred_style:
    name: [selected style or null]
    description: ""
  preferred_palette: null
  default_output_dir: imgs-subdir  # same-dir | imgs-subdir | illustrations-subdir | independent
  custom_styles: []
---
```

`preferred_image_backend: auto` is the baked-in default — first-time setup does not ask about it. The `## Image Generation Tools` rule in SKILL.md then picks the runtime-native tool (Codex `imagegen`, Hermes `image_generate`, etc.) when available, and falls back to installed backends.

## Modifying Preferences Later

See the `## Changing Preferences` section in `SKILL.md` for the canonical list of common edits (pin backend, change defaults, retrigger setup). The full schema is the EXTEND.md template above.

**EXTEND.md Supports**: Watermark (shared) | Language (shared) | Image backend preference (shared) | Cover: preferred type / palette / rendering / text / mood, default aspect ratio, default output directory, quick mode, custom palettes | Illustration: preferred style, preferred palette, default output directory, custom styles
