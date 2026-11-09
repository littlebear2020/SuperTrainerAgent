"""Spike 0.0 — cross-frame character consistency (core-value Go/No-Go).

Validates the reference-image chain that ViMax's character-consistency
depends on, against the REAL 360 gpt-image-2 endpoint:
  1. Generate a front portrait of a character (text-to-image, no reference).
  2. Generate a side portrait using the front as reference (i2i edit).
  3. Generate a "shot 2" scene using the front as reference (cross-shot).
  4. Download all three to spike_0_0_output/ for visual inspection.

You then subjectively score: does the character look the same across
front/side/shot2? >=4/5 = Go. This is the technical core of "角色每帧不一样"
(character differs every frame) — if reference editing can't hold a face
consistent across views, vimax's premise fails.

Optional: --i2v runs seedance image-to-video on the front portrait (8s).
Note: qihu_video.py may need `extra_body`补 (plan 关键技术发现); if --i2v
fails, that's a finding, not a script bug.

Run from backend/:
    uv run python scripts/spike_0_0_cross_frame.py [--style "realistic movie style"] [--i2v]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx
import yaml

from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider


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
        # data URL — decode base64 part
        import base64

        header, b64 = url.split(",", 1)
        dest.write_bytes(base64.b64decode(b64))
        return
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)


async def run(out_dir: Path, style: str, do_i2v: bool, config_path: Path) -> None:
    api_key, base_url = load_qihu_config(config_path)
    provider = GptImage2Provider(api_key=api_key, base_url=base_url)
    out_dir.mkdir(parents=True, exist_ok=True)

    character = "a young woman with short black hair, wearing a red scarf, determined eyes"

    print("[1/3] front portrait (text-to-image) ...")
    front = await provider.generate_image(f"Full-body front-view portrait of {character}. Pure white background. Style: {style}")
    print(f"      -> {front.url[:80]}...")
    await _download(front.url, out_dir / "01_front.png")

    print("[2/3] side portrait (i2i edit, front as reference) ...")
    side = await provider.generate_image(
        f"Full-body side-view portrait of the SAME character, facing left. Pure white background. Style: {style}",
        reference_image_paths=[front.url],
    )
    print(f"      -> {side.url[:80]}...")
    await _download(side.url, out_dir / "02_side.png")

    print("[3/3] shot 2 scene (cross-shot, front as reference) ...")
    shot2 = await provider.generate_image(
        f"The same character {character} sitting at a café table by the window, holding a coffee cup. Keep her face and clothing consistent with the reference. Style: {style}",
        reference_image_paths=[front.url],
    )
    print(f"      -> {shot2.url[:80]}...")
    await _download(shot2.url, out_dir / "03_shot2.png")

    await provider.close()

    if do_i2v:
        print("\n[optional] seedance i2v on front portrait (8s) ...")
        from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

        video = QihuVideoProvider(api_key=api_key, base_url=base_url)
        try:
            vref = await video.generate_video(
                f"The character slowly turns her head, sipping coffee. Style: {style}",
                duration=8.0,
                image_url=front.url,
            )
            await _download(vref.url, out_dir / "04_i2v.mp4")
            print(f"      -> {vref.url[:80]}...")
        except Exception as e:
            print(f"      i2v FAILED (may need extra_body per plan): {type(e).__name__}: {e}")
        finally:
            await video.close()

    print(f"\n=== outputs in {out_dir} ===")
    print("Visually compare 01_front / 02_side / 03_shot2:")
    print("  - Is it the SAME character (face, hair, scarf)?")
    print("  - Cross-frame consistency >=4/5 = Go for vimax core value.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", default="realistic movie style")
    ap.add_argument("--i2v", action="store_true", help="also run seedance image-to-video on the front portrait")
    ap.add_argument("--out", default=None)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    config_path = Path(args.config) if args.config else Path(__file__).resolve().parent.parent.parent / "config.yaml"
    out_dir = Path(args.out) if args.out else Path(__file__).resolve().parent.parent / "spike_0_0_output"
    if not config_path.exists():
        print(f"ERROR: config not found at {config_path}", file=sys.stderr)
        return 2
    try:
        asyncio.run(run(out_dir, args.style, args.i2v, config_path))
    except Exception as e:
        print(f"\n=== Spike FAILED: {type(e).__name__}: {e} ===", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
