"""ViMax nano-banana format A/B — current wire format vs official 360 API doc.

Compares two wire formats for ViMax's reference-image editing path:
  A. CURRENT  (nano_banana.py today): model=gemini-3-pro-image-preview,
     top-level ``images`` array (OpenAI-compatible shape, confirmed working
     by 0.0 spike + vimax_image_ab_test.py).
  B. OFFICIAL (360 /v1/images/edits docs example 3): model=gemini-3-pro-image-preview-edit,
     ``extra_body.image_urls`` array + ``extra_body.generation_config.imageConfig``.

Method: nano-banana generates 1 reference portrait; BOTH formats then edit the
SAME 5 scene shots using that reference URL. Only the wire format differs.

Decision:
  - B better/equal → switch ViMax to official format (more compliant, possibly better)
  - B worse/unavailable → keep current format (annotate the rationale)

Run from backend/:
    PYTHONPATH=. uv run python scripts/vimax_nano_format_ab_test.py
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

from app.gateway.video_gen.providers.nano_banana import NanoBananaImageProvider


def _load_dotenv() -> None:
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

# Official 360 nano-banana edit model (per /v1/images/edits docs example 3).
_OFFICIAL_EDIT_MODEL = "google/gemini-3-pro-image-preview-edit"


async def edit_official(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    base_url: str,
    prompt: str,
    ref_url: str,
) -> str:
    """Call /v1/images/edits with the OFFICIAL nano-banana edit format (example 3).

    Body shape mirrors the 360 docs exactly:
      model = gemini-3-pro-image-preview-edit
      extra_body.image_urls = [ref_url]
      extra_body.generation_config.imageConfig = {aspectRatio, imageSize, personGeneration}
    """
    body = {
        "model": _OFFICIAL_EDIT_MODEL,
        "prompt": prompt,
        "extra_body": {
            "image_urls": [ref_url],
            "generation_config": {
                "imageConfig": {
                    "aspectRatio": "1:1",
                    "imageSize": "1K",
                    "personGeneration": "ALLOW_ALL",
                }
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    resp = await client.post(f"{base_url}/v1/images/edits", headers=headers, json=body)
    if resp.status_code != 200:
        raise RuntimeError(f"official edit HTTP {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    items = data.get("data", [])
    if not items:
        raise RuntimeError(f"official edit empty data: {data}")
    item = items[0]
    if item.get("url"):
        return item["url"]
    if item.get("b64_json"):
        return f"data:image/png;base64,{item['b64_json']}"
    raise RuntimeError(f"official edit no url/b64_json: {item}")


async def run(out_dir: Path, config_path: Path) -> None:
    api_key, base_url = load_qihu_config(config_path)
    nano = NanoBananaImageProvider(api_key=api_key, base_url=base_url, aspect_ratio="1:1")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: reference portrait (nano-banana text-to-image) — shared by both formats.
    print("[1/3] reference portrait (nano-banana text-to-image) ...")
    ref = await nano.generate_image(REFERENCE_PROMPT)
    ref_path = out_dir / "00_reference.png"
    await _download(ref.url, ref_path)
    ref_url = ref.url
    print(f"      -> {ref_path.name}  (url: {ref_url[:70]}...)")

    # Step 2: 5 shots × 2 formats, concurrent within each shot.
    print("[2/3] 5 shots × 2 formats (A=current, B=official) ...")
    timeout = httpx.Timeout(15.0, read=300.0, write=30.0, pool=30.0)
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout) as http_client:
        for shot_id, shot_prompt in SHOTS:
            print(f"  {shot_id}:")
            a_task = nano.generate_image(shot_prompt, reference_image_paths=[ref_url])
            b_task = edit_official(
                http_client,
                api_key=api_key,
                base_url=base_url,
                prompt=shot_prompt,
                ref_url=ref_url,
            )
            a_res, b_res = await asyncio.gather(a_task, b_task, return_exceptions=True)

            entry: dict[str, Any] = {"shot_id": shot_id}
            a_ok = not isinstance(a_res, Exception)
            b_ok = not isinstance(b_res, Exception)

            if a_ok:
                await _download(a_res.url, out_dir / f"{shot_id}_A_current.png")
                entry["A"] = f"{shot_id}_A_current.png"
                print(f"    A current   -> {entry['A']}")
            else:
                entry["A_err"] = f"{type(a_res).__name__}: {a_res}"
                print(f"    A current   FAILED: {entry['A_err']}")
            if b_ok:
                await _download(b_res, out_dir / f"{shot_id}_B_official.png")
                entry["B"] = f"{shot_id}_B_official.png"
                print(f"    B official  -> {entry['B']}")
            else:
                entry["B_err"] = f"{type(b_res).__name__}: {b_res}"
                print(f"    B official  FAILED: {entry['B_err']}")
            results.append(entry)

    await nano.close()

    # Step 3: comparison HTML.
    print("[3/3] comparison HTML ...")
    html_path = out_dir / "comparison.html"
    html_path.write_text(_build_html(results), encoding="utf-8")
    print(f"\n=== outputs in {out_dir} ===")
    print(f"Open in browser: file://{html_path}")


def _build_html(results: list[dict[str, Any]]) -> str:
    rows = ""
    for r in results:
        a_cell = f'<img src="{r["A"]}">' if r.get("A") else f'<div class="err">FAILED<br>{r.get("A_err", "")[:160]}</div>'
        b_cell = f'<img src="{r["B"]}">' if r.get("B") else f'<div class="err">FAILED<br>{r.get("B_err", "")[:160]}</div>'
        rows += f"""
      <tr>
        <td class="label">{r["shot_id"]}</td>
        <td class="col-a">{a_cell}</td>
        <td class="col-b">{b_cell}</td>
      </tr>"""
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>ViMax nano-banana 格式 A/B — 当前 vs 官方</title>
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
  .col-a {{ background: #fff3e0; }}
  .col-b {{ background: #e8f5e9; }}
  .err {{ color: #c00; font-size: 12px; padding: 20px; }}
  .hint {{ color: #666; font-size: 12px; margin-top: 24px; line-height: 1.7; }}
  code {{ background: #eee; padding: 1px 4px; border-radius: 3px; font-size: 12px; }}
</style>
</head>
<body>
<h1>ViMax nano-banana 格式 A/B — 当前实现 vs 官方 360 API</h1>
<p style="color:#666;font-size:13px;">角色:{CHARACTER}</p>

<div class="ref">
  <h2>参考图（基准肖像 — nano-banana 文生图，两种格式共用此 URL 作为编辑输入）</h2>
  <img src="00_reference.png">
</div>

<h2>5 镜头对比（同一参考图 URL，同一 prompt，只换 wire format）</h2>
<table>
  <tr>
    <th>镜头</th>
    <th class="col-a">A · 当前实现<br><small>model=<code>gemini-3-pro-image-preview</code><br>顶层 <code>images</code> 数组</small></th>
    <th class="col-b">B · 官方推荐<br><small>model=<code>gemini-3-pro-image-preview-edit</code><br><code>extra_body.image_urls</code></small></th>
  </tr>
  {rows}
</table>

<div class="hint">
  <b>评估维度（同第一次 A/B）：</b> ① 面部一致 ② 发型/发色 ③ 服装 ④ 整体身份<br><br>
  <b>决策规则：</b><br>
  • <b>B 更好或相当 → 切官方格式</b>（更合规，效果不降；用专门的 -edit 模型 + 官方 image_urls 通道）<br>
  • <b>B 更差或不可用 → 保持当前</b>（顶层 images 事实可用，在 nano_banana.py 注释说明依据）<br>
  • <b>条件</b>：B 部分镜头失败/漂移 → 记录差异，按场景选格式
</div>
</body>
</html>"""


def main() -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    config_path = Path(args.config) if args.config else Path(__file__).resolve().parent.parent.parent / "config.yaml"
    out_dir = Path(args.out) if args.out else Path(__file__).resolve().parent.parent / ".ab_test_output_v2"
    if not config_path.exists():
        print(f"ERROR: config not found at {config_path}", file=sys.stderr)
        return 2
    try:
        asyncio.run(run(out_dir, config_path))
    except Exception as e:
        print(f"\n=== format A/B test FAILED: {type(e).__name__}: {e} ===", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
