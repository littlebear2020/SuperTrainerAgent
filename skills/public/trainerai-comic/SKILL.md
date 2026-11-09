---
name: trainerai-comic
description: 知识漫画创作工具，支持6种画风（白描/漫画/写实/水墨/粉笔/极简）x 7种氛围，含5种预设风格。生成角色设计表、分镜脚本和连续页面图片，输出合并PDF。触发词：'知识漫画'、'教育漫画'、'画漫画'、'教程漫画'。
---

# Knowledge Comic Creator

Create original knowledge comics with flexible art style x tone combinations.

## User Input Tools

When this skill prompts the user, follow this tool-selection rule (priority order):

1. **Prefer built-in user-input tools** exposed by the current agent runtime — e.g., `AskUserQuestion`, `request_user_input`, `clarify`, `ask_user`, or any equivalent.
2. **Fallback**: if no such tool exists, emit a numbered plain-text message and ask the user to reply with the chosen number/answer for each question.
3. **Batching**: if the tool supports multiple questions per call, combine all applicable questions into a single call; if only single-question, ask them one at a time in priority order.

Concrete `AskUserQuestion` references below are examples — substitute the local equivalent in other runtimes.

## Image Generation Tools

When this skill needs to render an image, use `tsx /mnt/skills/public/trainerai-imagine/scripts/main.ts` as the image generation backend. Read the trainerai-imagine SKILL.md for its documented interface before invoking it.

**Prompt file requirement (hard)**: write each image's full, final prompt to a standalone file under `prompts/` (naming: `NN-{type}-[slug].md`) BEFORE invoking any backend. The backend receives the prompt file (or its content); the file is the reproducibility record and lets you switch backends without regenerating prompts.

## Reference Images

Users may supply reference images to guide art style, palette, scene composition, or subject. This is **separate from** the auto-generated character sheet (Step 7.1) — both can coexist: user refs guide the look, the character sheet anchors recurring character identity.

**Intake**: Accept via `--ref <files...>` or when the user provides file paths / pastes images in conversation.
- File path(s) → copy to `refs/NN-ref-{slug}.{ext}` alongside the comic output
- Pasted image with no path → ask the user for the path (per the User Input Tools rule above), or extract style traits verbally as a text fallback
- No reference → skip this section

**Usage modes** (per reference):

| Usage | Effect |
|-------|--------|
| `direct` | Pass the file to the backend as a reference image on every page (or selected pages) |
| `style` | Extract style traits (line treatment, texture, mood) and append to every page's prompt body |
| `palette` | Extract hex colors and append to every page's prompt body |

**Record in each page's prompt frontmatter** when refs exist:

```yaml
references:
  - ref_id: 01
    filename: 01-ref-scene.png
    usage: direct
```

**At generation time**:
- Verify each referenced file exists on disk
- If `usage: direct` AND the chosen backend accepts multiple reference images → pass both the character sheet (Step 7.2) and the user refs via the backend's ref parameter; compress images first per Step 7.1's guidance to avoid payload failures
- If the backend accepts only one ref → prefer the character sheet for pages with recurring characters; embed user-ref traits in the prompt body instead
- For `style`/`palette` usage → embed extracted traits in every page's prompt text (applies regardless of backend capability)

## Options

### Visual Dimensions

| Option | Values | Description |
|--------|--------|-------------|
| `--art` | ligne-claire (default), manga, realistic, ink-brush, chalk, minimalist | Art style / rendering technique |
| `--tone` | neutral (default), warm, dramatic, romantic, energetic, vintage, action | Mood / atmosphere |
| `--layout` | standard (default), cinematic, dense, splash, mixed, webtoon, four-panel | Panel arrangement |
| `--aspect` | 3:4 (default, portrait), 4:3 (landscape), 16:9 (widescreen) | Page aspect ratio |
| `--lang` | auto (default), zh, en, ja, etc. | Output language |
| `--ref <files...>` | File paths | Reference images applied to every page for style / palette / scene guidance. See [Reference Images](#reference-images) above. |

### Partial Workflow Options

| Option | Description |
|--------|-------------|
| `--storyboard-only` | Generate storyboard only, skip prompts and images |
| `--prompts-only` | Generate storyboard + prompts, skip images |
| `--images-only` | Generate images from existing prompts directory |
| `--regenerate N` | Regenerate specific page(s) only (e.g., `3` or `2,5,8`) |

Details: [/mnt/skills/public/trainerai-comic/references/partial-workflows.md](/mnt/skills/public/trainerai-comic/references/partial-workflows.md)

### Art, Tone & Preset Catalogue

- **Art styles** (6): `ligne-claire`, `manga`, `realistic`, `ink-brush`, `chalk`, `minimalist`. Full definitions at `/mnt/skills/public/trainerai-comic/references/art-styles/<style>.md`.
- **Tones** (7): `neutral`, `warm`, `dramatic`, `romantic`, `energetic`, `vintage`, `action`. Full definitions at `/mnt/skills/public/trainerai-comic/references/tones/<tone>.md`.
- **Presets** (5) with special rules beyond plain art+tone:

  | Preset | Equivalent | Hook |
  |--------|-----------|------|
  | `ohmsha` | manga + neutral | Visual metaphors, no talking heads, gadget reveals |
  | `wuxia` | ink-brush + action | Qi effects, combat visuals, atmospheric |
  | `shoujo` | manga + romantic | Decorative elements, eye details, romantic beats |
  | `concept-story` | manga + warm | Visual symbol system, growth arc, dialogue+action balance |
  | `four-panel` | minimalist + neutral + four-panel layout | 起承转合 structure, B&W + spot color, stick-figure characters |

  Full rules at `/mnt/skills/public/trainerai-comic/references/presets/<preset>.md` — load the file when a preset is picked.

- **Compatibility matrix** and **content-signal → preset** table live in [/mnt/skills/public/trainerai-comic/references/auto-selection.md](/mnt/skills/public/trainerai-comic/references/auto-selection.md). Read it before recommending combinations in Step 2.

## Script Directory

**Important**: All scripts are located in the `scripts/` subdirectory of this skill.

**Agent Execution Instructions**:
1. Script path = `/mnt/skills/public/trainerai-comic/scripts/<script-name>.ts`
2. Run scripts with `tsx`

**Script Reference**:
| Script | Purpose |
|--------|---------|
| `scripts/merge-to-pdf.ts` | Merge comic pages into PDF |

## File Structure

Output directory: `comic/{topic-slug}/`
- Slug: 2-4 words kebab-case from topic (e.g., `alan-turing-bio`)
- Conflict: append timestamp (e.g., `turing-story-20260118-143052`)

**Contents**:
| File | Description |
|------|-------------|
| `source-{slug}.{ext}` | Source files |
| `analysis.md` | Content analysis |
| `storyboard.md` | Storyboard with panel breakdown |
| `characters/characters.md` | Character definitions |
| `characters/characters.png` | Character reference sheet |
| `prompts/NN-{cover\|page}-[slug].md` | Generation prompts |
| `NN-{cover\|page}-[slug].png` | Generated images |
| `{topic-slug}.pdf` | Final merged PDF |

## Language Handling

**Detection Priority**:
1. `--lang` flag (explicit)
2. Environment variable setting
3. User's conversation language
4. Source content language

**Rule**: Use user's input language or saved language preference for ALL interactions:
- Storyboard outlines and scene descriptions
- Image generation prompts
- User selection options and confirmations
- Progress updates, questions, errors, summaries

Technical terms remain in English.

## Workflow

### Progress Checklist

```
Comic Progress:
- [ ] Step 1: Setup & Analyze
  - [ ] 1.1 Load config
  - [ ] 1.2 Analyze, 1.3 Check existing
- [ ] Step 2: Confirmation - Style & options
- [ ] Step 3: Generate storyboard + characters
- [ ] Step 4: Review outline (conditional)
- [ ] Step 5: Generate prompts
- [ ] Step 6: Review prompts (conditional)
- [ ] Step 7: Generate images
  - [ ] 7.1 Generate character sheet (if needed) → characters/characters.png
  - [ ] 7.2 Generate pages (with --ref if character sheet exists)
- [ ] Step 8: Merge to PDF
- [ ] Step 9: Completion report
```

### Flow

```
Input → [Config] → Analyze → [Check Existing?] → [Confirm: Style + Reviews] → Storyboard → [Review?] → Prompts → [Review?] → Images → PDF → Complete
```

### Step Summary

| Step | Action | Key Output |
|------|--------|------------|
| 1.1 | Load config | Config loaded |
| 1.2 | Analyze content | `analysis.md` |
| 1.3 | Check existing directory | Handle conflicts |
| 2 | Confirm style, focus, audience, reviews | User preferences |
| 3 | Generate storyboard + characters | `storyboard.md`, `characters/` |
| 4 | Review outline (if requested) | User approval |
| 5 | Generate prompts | `prompts/*.md` |
| 6 | Review prompts (if requested) | User approval |
| 7.1 | Generate character sheet (if needed) | `characters/characters.png` |
| 7.2 | Generate pages (with character ref if available) | `*.png` files |
| 8 | Merge to PDF | `{slug}.pdf` |
| 9 | Completion report | Summary |

### Step 7: Image Generation

**Use `tsx /mnt/skills/public/trainerai-imagine/scripts/main.ts`** as the image generation backend. Read its SKILL.md for the documented interface before invoking it.

**7.1 Character sheet** — generate it (to `characters/characters.png`, aspect `4:3`) when the comic is multi-page with recurring characters. Skip for simple presets (e.g., four-panel minimalist) or single-page comics. Compress to JPEG before use-as-`--ref` (`sips -s format jpeg -s formatOptions 80 …` on macOS, `pngquant --quality=65-80 …` elsewhere) to avoid payload failures. The prompt file at `characters/characters.md` must exist before invoking the backend.

**7.2 Pages** — each page's prompt MUST already be at `prompts/NN-{cover|page}-[slug].md` before invoking the backend; the file is the reproducibility record. Strategy depends on the character sheet:

| Character sheet | Backend `--ref` | Strategy |
|-----------------|-----------------|----------|
| Exists | Supported | Pass sheet as `--ref` on every page |
| Exists | Not supported | Prepend character descriptions to every prompt file |
| Skipped | — | All descriptions inline in prompt |

**Backup rule**: existing `prompts/…md` and `…png` files → rename with `-backup-YYYYMMDD-HHMMSS` suffix before regenerating. Aspect ratio from storyboard (default `3:4`; preset may override).

**`--ref` failure recovery**: compress sheet → retry → still fails → drop `--ref` and embed character descriptions in the prompt text.

Full step-by-step workflow (analysis, storyboard, review gates, regeneration variants): [/mnt/skills/public/trainerai-comic/references/workflow.md](/mnt/skills/public/trainerai-comic/references/workflow.md).

## References

**Core Templates**:
- [/mnt/skills/public/trainerai-comic/references/analysis-framework.md](/mnt/skills/public/trainerai-comic/references/analysis-framework.md) - Deep content analysis
- [/mnt/skills/public/trainerai-comic/references/character-template.md](/mnt/skills/public/trainerai-comic/references/character-template.md) - Character definition format
- [/mnt/skills/public/trainerai-comic/references/storyboard-template.md](/mnt/skills/public/trainerai-comic/references/storyboard-template.md) - Storyboard structure
- [/mnt/skills/public/trainerai-comic/references/ohmsha-guide.md](/mnt/skills/public/trainerai-comic/references/ohmsha-guide.md) - Ohmsha manga specifics

**Style Definitions**:
- `/mnt/skills/public/trainerai-comic/references/art-styles/` - Art styles (ligne-claire, manga, realistic, ink-brush, chalk, minimalist)
- `/mnt/skills/public/trainerai-comic/references/tones/` - Tones (neutral, warm, dramatic, romantic, energetic, vintage, action)
- `/mnt/skills/public/trainerai-comic/references/presets/` - Presets with special rules (ohmsha, wuxia, shoujo, concept-story, four-panel)
- `/mnt/skills/public/trainerai-comic/references/layouts/` - Layouts (standard, cinematic, dense, splash, mixed, webtoon, four-panel)

**Workflow**:
- [/mnt/skills/public/trainerai-comic/references/workflow.md](/mnt/skills/public/trainerai-comic/references/workflow.md) - Full workflow details
- [/mnt/skills/public/trainerai-comic/references/auto-selection.md](/mnt/skills/public/trainerai-comic/references/auto-selection.md) - Content signal analysis
- [/mnt/skills/public/trainerai-comic/references/partial-workflows.md](/mnt/skills/public/trainerai-comic/references/partial-workflows.md) - Partial workflow options

**Config**:
- [/mnt/skills/public/trainerai-comic/references/config/watermark-guide.md](/mnt/skills/public/trainerai-comic/references/config/watermark-guide.md) - Watermark configuration

## Page Modification

| Action | Steps |
|--------|-------|
| **Edit** | **Update prompt file FIRST** → `--regenerate N` → Regenerate PDF |
| **Add** | Create prompt at position → Generate with character ref → Renumber subsequent → Update storyboard → Regenerate PDF |
| **Delete** | Remove files → Renumber subsequent → Update storyboard → Regenerate PDF |

**IMPORTANT**: When updating pages, ALWAYS update the prompt file (`prompts/NN-{cover|page}-[slug].md`) FIRST before regenerating. This ensures changes are documented and reproducible.

## Notes

- Image generation: 10-30 seconds per page
- Auto-retry once on generation failure
- Use stylized alternatives for sensitive public figures
- Maintain style consistency via session ID
- **Step 2 confirmation required** - do not skip
- **Steps 4/6 conditional** - only if user requested in Step 2
- **Step 7.1 character sheet** - recommended for multi-page comics, optional for simple presets
- **Step 7.2 character reference** - use `--ref` if sheet exists; compress/convert on failure; fall back to prompt-only
- Watermark/language configured via environment variables
- Merge-to-pdf script uses `writeFileSync` and is invoked with `tsx` (not `bun`)
