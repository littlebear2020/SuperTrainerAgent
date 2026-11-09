# Usage Examples

Extended CLI examples. SKILL.md shows the minimum set; read this file when the user asks about provider-specific invocation, batch generation, or less-common flags.

## Core Patterns

```bash
# Basic text-to-image
tsx {baseDir}/scripts/main.ts --prompt "A cat" --image cat.png

# With aspect ratio
tsx {baseDir}/scripts/main.ts --prompt "A landscape" --image out.png --ar 16:9

# High quality
tsx {baseDir}/scripts/main.ts --prompt "A cat" --image out.png --quality 2k

# Prompt from files
tsx {baseDir}/scripts/main.ts --promptfiles system.md content.md --image out.png

# With reference images (any provider family that supports refs)
tsx {baseDir}/scripts/main.ts --prompt "Make blue" --image out.png --ref source.png
```

## Per-Provider

```bash
# OpenAI
tsx {baseDir}/scripts/main.ts --prompt "A cat" --image out.png --provider openai --model gpt-image-2

# Azure OpenAI (model = deployment name)
tsx {baseDir}/scripts/main.ts --prompt "A cat" --image out.png --provider azure --model gpt-image-2

# OpenAI GPT Image 2 custom 4K size
tsx {baseDir}/scripts/main.ts --prompt "A cinematic landscape" --image out.png --provider openai --model gpt-image-2 --size 3840x2160

# Z.AI GLM-image
tsx {baseDir}/scripts/main.ts --prompt "一张带清晰中文标题的科技海报" --image out.png --provider zai

# Z.AI with custom size
tsx {baseDir}/scripts/main.ts --prompt "A science illustration with labels" --image out.png --provider zai --model glm-image --size 1472x1088

# Seedream (Volcengine ARK, OpenAI-compatible)
tsx {baseDir}/scripts/main.ts --prompt "A cat" --image out.png --provider seedream

# Seedream with reference images
tsx {baseDir}/scripts/main.ts --prompt "A cinematic portrait" --image out.png --provider seedream --model doubao-seedream-5-0-260128 --ref source.png --ar 3:2
```

## Batch Mode

```bash
# Batch from saved prompt files
tsx {baseDir}/scripts/main.ts --batchfile batch.json

# Batch with explicit worker count
tsx {baseDir}/scripts/main.ts --batchfile batch.json --jobs 4 --json
```

### Batch File Format

```json
{
  "jobs": 4,
  "tasks": [
    {
      "id": "hero",
      "promptFiles": ["prompts/hero.md"],
      "image": "out/hero.png",
      "provider": "openai",
      "model": "gpt-image-2",
      "ar": "16:9",
      "quality": "2k"
    },
    {
      "id": "diagram",
      "promptFiles": ["prompts/diagram.md"],
      "image": "out/diagram.png",
      "ref": ["references/original.png"]
    }
  ]
}
```

Paths in `promptFiles`, `image`, and `ref` are resolved relative to the batch file's directory. `jobs` is optional (overridden by CLI `--jobs`). A top-level array without the `jobs` wrapper is also accepted.
