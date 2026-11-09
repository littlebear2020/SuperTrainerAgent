"""ViMax image backend A/B — nano-banana vs gpt-image-2.

Re-validates whether ``openai/gpt-image-2`` can replace
``google/gemini-3-pro-image-preview`` (nano-banana) for ViMax's character
cross-shot consistency. The 0.0 spike (``spike_0_0_cross_frame.py``) found
gpt-image-2 had "total identity failure"; this script re-tests with a stricter,
side-by-side protocol now that the 360 gateway models may have improved.

Method (isolate the variable):
  1. nano-banana generates 1 reference portrait (text-to-image, no reference).
  2. BOTH providers generate the SAME 5 scene shots using that reference as the
     i2i edit input — identical prompt, identical reference, only the model differs.
  3. Outputs a side-by-side comparison HTML for human grading.

Decision rule:
  - GO     = gpt-image-2 face identity holds → switch ViMax to gpt-image-2
  - NO-GO  = gpt-image-2 identity lost → keep nano-banana for ViMax
  - COND   = partial drift → keep nano-banana, register gpt-image-2 as fallback

Run from backend/:
    PYTHONPATH=. uv run python scripts/vimax_image_ab_test.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider
from app.gateway.video_gen.providers.nano_banana import NanoBananaImageProvider


def _load_dotenv() -> None:
    """Load project-root .env into os.environ (best-effort, no override)."""
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _resolve(value: str) -> str:
    if isinstance(value, str) and value.startswith("$"):
        env_name = value[1:]
        resolved = os.environ.get(env_name)
        if not resolved:
            raise RuntimeError(f"config references env var {env_name!r} which is not set")
        return resolved
    return value


def load_qihu_config(config_path: Path) -> tuple[str, str]:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    vg = cfg.get("video_gen", {})
    api_key = _resolve(vg.get("qihu_api_key", ""))
    base_url = vg.get("qihu_base_url", "https://api.360.cn")
    if not api_key:
        raise RuntimeError("video_gen.qihu_api_key not set in config.yaml")
    return api_key, base_url


async def _download(url: str, dest: Path) -> None:
    if url.startswith("data:"):
        header, b64 = url.split(",", 1)
        dest.write_bytes(base64.b64decode(b64))
        return
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)


CHARACTER = "a 28-year-old Chinese woman with shoulder-length black hair, wearing a white button-up shirt and thin-rimmed glasses"

REFERENCE_PROMPT = f"Front-view portrait of {CHARACTER}. Neutral light gray background, professional headshot, soft natural lighting. Style: realistic photography, high detail."

# Each shot repeats the character description + explicit "SAME ... consistent
# with the reference image" instruction (mirrors spike_0_0 phrasing).
SHOTS: list[tuple[str, str]] = [
    (
        "01_office_halfbody",
        f"The SAME {CHARACTER} sitting at an office desk working on a laptop, "
        "focused expression. Modern office background, half-body front view. "
        "Keep her face, hair, and clothing consistent with the reference image. "
        "Style: realistic photography.",
    ),
    (
        "02_cafe_side",
        f"The SAME {CHARACTER} holding a coffee cup in a cozy cafe, side profile view facing left. Warm cafe lighting. Keep her face, hair, and clothing consistent with the reference image. Style: realistic photography.",
    ),
    (
        "03_park_fullbody",
        f"The SAME {CHARACTER} walking in a sunny park, full body shot. Green trees background, daytime. Keep her face, hair, and clothing consistent with the reference image. Style: realistic photography.",
    ),
    (
        "04_meeting_gesture",
        f"The SAME {CHARACTER} giving a presentation in a meeting room, gesturing with her hands, half body. Conference room background. Keep her face, hair, and clothing consistent with the reference image. Style: realistic photography.",
    ),
    (
        "05_street_night",
        f"The SAME {CHARACTER} walking on a city street at night, wide shot. Neon lights background, evening. Keep her face, hair, and clothing consistent with the reference image. Style: realistic photography.",
    ),
]


async def run(out_dir: Path, config_path: Path) -> None:
    api_key, base_url = load_qihu_config(config_path)
    nano = NanoBananaImageProvider(api_key=api_key, base_url=base_url, aspect_ratio="1:1")
    gpt2 = GptImage2Provider(api_key=api_key, base_url=base_url)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: reference portrait (nano-banana, no ref) — shared by both providers.
    print("[1/3] reference portrait (nano-banana, text-to-image) ...")
    ref = await nano.generate_image(REFERENCE_PROMPT)
    ref_path = out_dir / "00_reference.png"
    await _download(ref.url, ref_path)
    print(f"      -> {ref_path.name}")
    ref_url = ref.url

    # Step 2: 5 shots × 2 providers. Within each shot, run both providers
    # concurrently (2 in flight — well under 360 gateway limits).
    print("[2/3] 5 shots × 2 providers (i2i edit, same reference) ...")
    results: list[dict[str, Any]] = []
    for shot_id, shot_prompt in SHOTS:
        print(f"  {shot_id}:")
        nano_img, gpt_img = await asyncio.gather(
            nano.generate_image(shot_prompt, reference_image_paths=[ref_url]),
            gpt2.generate_image(shot_prompt, reference_image_paths=[ref_url]),
            return_exceptions=True,
        )

        nano_ok = not isinstance(nano_img, Exception)
        gpt_ok = not isinstance(gpt_img, Exception)

        entry: dict[str, Any] = {"shot_id": shot_id}
        if nano_ok:
            await _download(nano_img.url, out_dir / f"{shot_id}_nano.png")
            entry["nano"] = f"{shot_id}_nano.png"
            print(f"    nano-banana  -> {entry['nano']}")
        else:
            entry["nano_err"] = f"{type(nano_img).__name__}: {nano_img}"
            print(f"    nano-banana  FAILED: {entry['nano_err']}")
        if gpt_ok:
            await _download(gpt_img.url, out_dir / f"{shot_id}_gpt2.png")
            entry["gpt2"] = f"{shot_id}_gpt2.png"
            print(f"    gpt-image-2  -> {entry['gpt2']}")
        else:
            entry["gpt2_err"] = f"{type(gpt_img).__name__}: {gpt_img}"
            print(f"    gpt-image-2  FAILED: {entry['gpt2_err']}")
        results.append(entry)

    await nano.close()
    await gpt2.close()

    # Step 3: comparison HTML.
    print("[3/3] comparison HTML ...")
    html_path = out_dir / "comparison.html"
    html_path.write_text(_build_html(results), encoding="utf-8")
    print(f"\n=== outputs in {out_dir} ===")
    print(f"Open in browser: file://{html_path}")


def _build_html(results: list[dict[str, Any]]) -> str:
    rows = ""
    for r in results:
        nano_cell = f'<img src="{r["nano"]}">' if r.get("nano") else f'<div class="err">FAILED<br>{r.get("nano_err", "")[:120]}</div>'
        gpt_cell = f'<img src="{r["gpt2"]}">' if r.get("gpt2") else f'<div class="err">FAILED<br>{r.get("gpt2_err", "")[:120]}</div>'
        rows += f"""
      <tr>
        <td class="label">{r["shot_id"]}</td>
        <td class="col-nano">{nano_cell}</td>
        <td class="col-gpt">{gpt_cell}</td>
      </tr>"""
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>ViMax 图像 A/B — nano-banana vs gpt-image-2</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", sans-serif; margin: 24px; background: #f5f5f5; color: #222; }}
  h1 {{ font-size: 20px; margin-bottom: 4px; }}
  h2 {{ font-size: 15px; margin-top: 28px; }}
  .ref img {{ width: 320px; border: 1px solid #ccc; }}
  table {{ border-collapse: collapse; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  td, th {{ border: 1px solid #ddd; padding: 10px; text-align: center; vertical-align: top; }}
  th {{ background: #2c2c2c; color: white; font-size: 13px; }}
  td.label {{ background: #f0f0f0; font-family: ui-monospace, monospace; font-size: 12px; width: 140px; }}
  img {{ width: 320px; height: auto; display: block; }}
  .col-nano {{ background: #e8f5e9; }}
  .col-gpt {{ background: #e3f2fd; }}
  .err {{ color: #c00; font-size: 12px; padding: 20px; }}
  .hint {{ color: #666; font-size: 12px; margin-top: 24px; line-height: 1.7; }}
</style>
</head>
<body>
<h1>ViMax 图像 A/B 测试 — 角色跨镜头一致性</h1>
<p style="color:#666;font-size:13px;">角色:{CHARACTER}</p>

<div class="ref">
  <h2>参考图（基准肖像 — nano-banana 生成，两个 provider 共用此图作为 i2i 编辑输入）</h2>
  <img src="00_reference.png">
</div>

<h2>5 镜头对比（同一参考图，同一 prompt，只换 provider）</h2>
<table>
  <tr>
    <th>镜头</th>
    <th class="col-nano">nano-banana<br><small>google/gemini-3-pro-image-preview</small></th>
    <th class="col-gpt">gpt-image-2<br><small>openai/gpt-image-2</small></th>
  </tr>
  {rows}
</table>

<div class="hint">
  <b>评估维度：</b><br>
  ① 面部一致 — 5 张脸是不是同一个人<br>
  ② 发型/发色一致 — 齐肩黑发是否保持<br>
  ③ 服装一致 — 白衬衫 + 细框眼镜是否保持<br>
  ④ 整体身份 — 综合能否认出是同一人<br><br>
  <b>决策规则：</b><br>
  • <b>GO</b>：gpt-image-2 面部一致 + 整体身份可辨认 → ViMax 切 gpt-image-2，统一所有生图<br>
  • <b>NO-GO</b>：gpt-image-2 身份丢失（不同人/性别漂移）→ ViMax 保留 nano-banana，通用生图单独用 gpt-image-2<br>
  • <b>条件 GO</b>：部分镜头漂移 → 保留 nano-banana，gpt-image-2 作备选
</div>
</body>
</html>"""


def main() -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="output directory")
    ap.add_argument("--config", default=None, help="config.yaml path")
    args = ap.parse_args()

    config_path = Path(args.config) if args.config else Path(__file__).resolve().parent.parent.parent / "config.yaml"
    out_dir = Path(args.out) if args.out else Path(__file__).resolve().parent.parent / ".ab_test_output"
    if not config_path.exists():
        print(f"ERROR: config not found at {config_path}", file=sys.stderr)
        return 2
    try:
        asyncio.run(run(out_dir, config_path))
    except Exception as e:
        print(f"\n=== A/B test FAILED: {type(e).__name__}: {e} ===", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
