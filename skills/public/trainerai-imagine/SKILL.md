---
name: trainerai-imagine
description: AI 图片生成核心引擎，支持5家供应商（360 API/Google Gemini、OpenAI、Azure、智谱Z.AI、豆包Seedream）。默认优先使用360 API的google/gemini-3.1-flash-image-preview。提供批量生成、参考图引导和高质量预设。当任何技能需要AI生成图片时使用。触发词：'生成图片'、'AI画图'、'画一张'。
license: MIT
metadata:
  openclaw:
    homepage: https://github.com/JimLiu/baoyu-skills#trainerai-imagine
    requires:
      anyBins:
        - npx
required-secrets:
  - name: QIHU_API_KEY
  - name: OPENAI_API_KEY
    optional: true
  - name: AZURE_OPENAI_API_KEY
    optional: true
  - name: ARK_API_KEY
    optional: true

---

# Image Generation (AI SDK)

Official API-based image generation. Supports 5 providers: **360 API (Google Gemini)** as primary, plus OpenAI GPT Image 2, Azure OpenAI, Z.AI GLM-Image, and Seedream (豆包).

## User Input Tools

When this skill prompts the user, follow this tool-selection rule (priority order):

1. **Prefer built-in user-input tools** exposed by the current agent runtime — e.g., `AskUserQuestion`, `request_user_input`, `clarify`, `ask_user`, or any equivalent.
2. **Fallback**: if no such tool exists, emit a numbered plain-text message and ask the user to reply with the chosen number/answer for each question.
3. **Batching**: if the tool supports multiple questions per call, combine all applicable questions into a single call; if only single-question, ask them one at a time in priority order.

Concrete `AskUserQuestion` references below are examples — substitute the local equivalent in other runtimes.

## Script Directory

`{baseDir}` = this SKILL.md's directory. Main script: `{baseDir}/scripts/main.ts`.

- **Local path**: `skills/public/trainerai-imagine/`
- **Sandbox path**: `/mnt/skills/public/trainerai-imagine/`

## Configuration

Configuration is handled via environment variables (see Environment Variables section below). No separate preferences file is needed.

**Load priority**: CLI args > env vars > `<cwd>/.baoyu-skills/.env` > `~/.baoyu-skills/.env`

## Usage

Minimum working examples — see `references/usage-examples.md` for the full set including per-provider invocations and batch mode.

```bash
# Basic
tsx {baseDir}/scripts/main.ts --prompt "A cat" --image cat.png

# With aspect ratio and high quality
tsx {baseDir}/scripts/main.ts --prompt "A landscape" --image out.png --ar 16:9 --quality 2k

# Prompt from files
tsx {baseDir}/scripts/main.ts --promptfiles system.md content.md --image out.png

# With reference image
tsx {baseDir}/scripts/main.ts --prompt "Make blue" --image out.png --ref source.png

# OpenAI GPT Image 2
tsx {baseDir}/scripts/main.ts --prompt "A cat" --image out.png --provider openai --model gpt-image-2

# Azure OpenAI
tsx {baseDir}/scripts/main.ts --prompt "A cat" --image out.png --provider azure --model gpt-image-2

# Z.AI GLM-Image
tsx {baseDir}/scripts/main.ts --prompt "A cat" --image out.png --provider zai

# Seedream (豆包)
tsx {baseDir}/scripts/main.ts --prompt "A cat" --image out.png --provider seedream

# 360 API (Google Gemini) — default primary
tsx {baseDir}/scripts/main.ts --prompt "A cat" --image out.png --provider qihu360 --model google/gemini-3.1-flash-image-preview

# Batch mode
tsx {baseDir}/scripts/main.ts --batchfile batch.json --jobs 4
```

## Options

| Option | Description |
|--------|-------------|
| `--prompt <text>`, `-p` | Prompt text |
| `--promptfiles <files...>` | Read prompt from files (concatenated) |
| `--image <path>` | Output image path (required in single-image mode) |
| `--batchfile <path>` | JSON batch file for multi-image generation |
| `--jobs <count>` | Worker count for batch mode (default: auto, max from config, built-in default 10) |
| `--provider qihu360\|openai\|azure\|zai\|seedream` | Force provider (default: auto-detect: openai > seedream > qihu360 > azure > zai; **--ref auto-routes to seedream** for identity preservation) |
| `--model <id>`, `-m` | Model ID — see provider references for defaults and allowed values |
| `--ar <ratio>` | Aspect ratio (`16:9`, `1:1`, `4:3`, …) |
| `--size <WxH>` | Explicit size (e.g., `1024x1024`; for `gpt-image-2`, width/height must be multiples of 16, max edge 3840px, ratio no wider than 3:1) |
| `--quality normal\|2k` | Quality preset (default: `2k`) |
| `--imageApiDialect openai-native\|ratio-metadata` | OpenAI-compatible endpoint dialect — use `ratio-metadata` for gateways that expect aspect-ratio `size` plus `metadata.resolution` |
| `--ref <files...>` | Reference images. Supported by OpenAI GPT Image edits, Azure OpenAI edits (PNG/JPG only), Seedream 5.0/4.5/4.0. Not supported by Seedream 3.0 or SeedEdit 3.0 |
| `--n <count>` | Number of images |
| `--json` | JSON output |

## Environment Variables

**At least ONE provider API key is required.** You do NOT need all of them — the script auto-detects which provider to use based on which keys are present. All other variables (base URL, model, etc.) are optional with sensible defaults.

### Required (pick one)

| Variable | Provider |
|----------|----------|
| `QIHU_API_KEY` | 360 API (Google Gemini) — **default primary** |
| `OPENAI_API_KEY` | OpenAI |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI (also needs `AZURE_OPENAI_BASE_URL`) |
| `ZAI_API_KEY` (alias `BIGMODEL_API_KEY`) | Z.AI |
| `ARK_API_KEY` | Seedream (豆包) |

### Optional — all have built-in defaults, do NOT need to be set

| Variable | Description | Default |
|----------|-------------|---------|
| `QIHU_BASE_URL` | 360 API endpoint | `https://api.360.cn` |
| `QIHU_IMAGE_MODEL` | 360 API model | `google/gemini-3.1-flash-image-preview` |
| `OPENAI_IMAGE_MODEL` | OpenAI model | `gpt-image-2` |
| `ZAI_IMAGE_MODEL` / `BIGMODEL_IMAGE_MODEL` | Z.AI model | `glm-image` |
| `SEEDREAM_IMAGE_MODEL` | Seedream model | `doubao-seedream-5-0-pro-260628` |
| `AZURE_OPENAI_DEPLOYMENT` (alias `AZURE_OPENAI_IMAGE_MODEL`) | Azure deployment name | — |
| `AZURE_OPENAI_BASE_URL` | Azure endpoint | — |
| `AZURE_API_VERSION` | Azure API version | `2025-04-01-preview` |
| `OPENAI_IMAGE_API_DIALECT` | `openai-native` \| `ratio-metadata` | `openai-native` |
| `BAOYU_IMAGE_GEN_MAX_WORKERS` | Batch worker cap | 10 |
| `BAOYU_IMAGE_GEN_<PROVIDER>_CONCURRENCY` | Per-provider concurrency | 3 |
| `BAOYU_IMAGE_GEN_<PROVIDER>_START_INTERVAL_MS` | Per-provider start-gap | 1100 |

## Model Resolution

Priority (highest to lowest) applies to every provider:

1. CLI flag `--model <id>`
2. Env var `<PROVIDER>_IMAGE_MODEL`
3. Built-in default

For OpenAI, the built-in default is `gpt-image-2`. `gpt-image-1.5`, `gpt-image-1`, and GPT Image snapshots remain selectable with `--model` or `OPENAI_IMAGE_MODEL`.

For Azure, `--model` is the Azure deployment name. `AZURE_OPENAI_DEPLOYMENT` is the preferred env var; `AZURE_OPENAI_IMAGE_MODEL` is kept as a backward-compatible alias. If your Azure deployment is named after the underlying model, use `gpt-image-2`; otherwise use the exact custom deployment name.

**Display model info before each generation**:

- `Using [provider] / [model]`
- `Switch model: --model <id> | env <PROVIDER>_IMAGE_MODEL`

## OpenAI-Compatible Gateway Dialects

`provider=openai` means the auth and routing entrypoint is OpenAI-compatible. It does **not** guarantee the upstream image API uses OpenAI native semantics. When a gateway expects a different wire format, set `OPENAI_IMAGE_API_DIALECT` or `--imageApiDialect`:

- `openai-native`: pixel `size` (`1536x1024`) and native OpenAI quality fields
- `ratio-metadata`: aspect-ratio `size` (`16:9`) plus `metadata.resolution` (`1K|2K|4K`) and `metadata.orientation`

Use `openai-native` for the OpenAI native API or strict clones; try `ratio-metadata` for compatibility gateways. Current limitation: `ratio-metadata` applies only to text-to-image; reference-image edits still need `openai-native` or a provider with first-class edit support.

## Provider Selection

1. `--ref` provided + no `--provider` → auto-select OpenAI → Azure → Seedream (qihu360 does not support reference images)
2. `--provider` specified → use it (if `--ref`, must be openai/azure/seedream)
3. Only one API key present → use that provider
4. Multiple keys → default priority: **qihu360 → openai → azure → zai → seedream**

## Auto-Fallback

No automatic fallback chain is configured by default. When the primary provider (openai/gpt-image-2) fails all 3 retry attempts, the generation fails with an error.

To enable a custom fallback chain, add to `.baoyu-skills/trainerai-imagine/EXTEND.md`:

```yaml
fallback:
  qihu360: openai
  openai: null  # no further fallback
```

## Quality Presets

| Preset | Use case |
|--------|----------|
| `normal` | Quick previews |
| `2k` (default) | Covers, illustrations, infographics |

For OpenAI native `gpt-image-2`, `normal` maps to `quality=medium` and a low-latency valid size near the requested aspect ratio; `2k` maps to `quality=high` and 2048px-class sizes such as `2048x2048`, `2048x1152`, or `1152x2048`. Use explicit `--size` for valid custom or 4K outputs, e.g. `3840x2160`.

## Aspect Ratios

Supported: `1:1`, `16:9`, `9:16`, `4:3`, `3:4`, `2.35:1`.

- OpenAI: `gpt-image-2` uses the closest valid custom size for the requested ratio; older GPT Image and DALL·E models use their closest supported fixed size
- Z.AI / Seedream: model-specific ratio handling

## Batch File Format

Use `--batchfile <path>` to generate multiple images from a JSON file. Example:

```json
[
  {
    "prompt": "A sunset over mountains",
    "image": "sunset.png",
    "ar": "16:9",
    "quality": "2k"
  },
  {
    "prompt": "Abstract geometric pattern",
    "image": "abstract.png",
    "ref": ["reference.png"]
  }
]
```

Each entry supports the same options as CLI flags (`prompt`, `image`, `provider`, `model`, `ar`, `size`, `quality`, `ref`, `n`).

## Generation Mode

**Default**: sequential. **Batch parallel**: enabled automatically when `--batchfile` contains 2+ pending tasks.

| Situation | Prefer | Why |
|-----------|--------|-----|
| One image, or 1-2 simple images | Sequential | Lower coordination overhead, easier debugging |
| Multiple images with saved prompt files | Batch (`--batchfile`) | Reuses finalized prompts, applies shared throttling/retries, predictable throughput |
| Each image still needs its own reasoning / prompt writing / style exploration | Subagents | Work is still exploratory, each needs independent analysis |
| Input is `outline.md` + `prompts/` (e.g. from `trainerai-article-visuals` illustration mode) | Batch — use `scripts/build-batch.ts` to assemble the payload | The outline + prompt files already contain everything needed |

Rule of thumb: once prompt files are saved and the task is "generate all of these", prefer batch over subagents. Use subagents only when generation is coupled with per-image thinking or divergent creative exploration.

**Parallel behavior**:

- Default worker count is automatic, capped by config, built-in default 10
- Provider-specific throttling applies only in batch mode; defaults are tuned for throughput while avoiding RPM bursts
- Override with `--jobs <count>`
- Each image retries up to 3 attempts
- Final output includes success count, failure count, and per-image failure reasons

## Error Handling

- Missing API key → error with setup instructions
- Generation failure → auto-retry up to 3 attempts per image
- Invalid aspect ratio → warning, proceed with default
- Reference images with unsupported provider/model → error with fix hint

## Provider-Specific Guides

Each provider has its own quirks (model families, size rules, ref support, limits). Read these when the user picks that provider or asks for non-default behavior:

| Provider | Reference |
|----------|-----------|
| Z.AI (GLM-Image, cogview-4) | `references/providers/zai.md` |

## References

| File | Content |
|------|---------|
| `references/usage-examples.md` | Extended CLI examples across providers and batch mode |
| `references/providers/zai.md` | Z.AI GLM-image / cogview-4 |
