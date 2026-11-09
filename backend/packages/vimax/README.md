# vimax (vendored)

Vendored from **ViMax** (`autolongvideogeneration` v1.1.0, **MIT License**) — the
character-consistency long-video generation pipeline. See `LICENSE` for the
upstream license terms. Source: `/Users/littlebear/Documents/ViMax-main`.

## What's vendored (阶段 2.1)
- `vimax/agents/` — LLM specialists (CharacterExtractor, Screenwriter,
  CharacterPortraitsGenerator, ReferenceImageSelector, StoryboardArtist, …)
- `vimax/pipelines/` — script2video / idea2video / novel2movie
- `vimax/interfaces/` — data models (Character, Scene, ShotDescription, …)
- `vimax/utils/` — rate_limiter, retry, video (moviepy concat), image, text, …
- `vimax/tools/` — `protocols.py` (ImageGenerator/VideoGenerator Protocols) +
  `render_backend.py` (class_path factory). Concrete generators are NOT
  vendored — supplied by the host app via `RenderBackend` class_path (§5.3 bridge).

## NOT vendored (per plan §5.1)
- `agent_runtime/` (ReAct loop / TUI / CLI — DeerFlow replaces)
- `ui/`, `main_*.py`, `tests/`
- `tools/*_yunwu_api.py` + google/openrouter concrete generators (host provides
  via the §5.3 provider bridge)

## Import rewrite
ViMax uses absolute imports (`from agents import *`). Vendored under the `vimax/`
package, every internal import was rewritten to `from vimax.<pkg> …` (40
statements across 21 files; 0 remaining un-prefixed). Deps (moviepy /
scenedetect[opencv] / opencv-python / pillow / pyyaml / tenacity / requests /
aiohttp / chardet) declared in `pyproject.toml` (阶段 2.2).
