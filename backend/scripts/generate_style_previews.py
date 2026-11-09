"""一次性生成任务创建对话框「风格」下拉的静态预览图（2026-09-09 设计）。

设计文档：docs/plans/2026-09-09-style-preview-design.md
产物：frontend/public/style-previews/<key>.jpg ×7（960×540 JPEG q85，单张 ~100KB）
——跑一次提交 git，运行时零 API、零额度消耗；前端经 style-presets.ts 的
stylePreviewImage() 纯静态引用（仿 public/remotion-layouts/ 惯例）。

⚠️ 维护约定（无自动同步检测，改词必重跑）：
  改 STYLE_PREFIXES 风格词后**必须重跑本脚本**更新预览图，并把
  frontend/src/core/video-gen/style-presets.ts 的 STYLE_PREVIEW_VERSION +1
  破浏览器缓存（public/ 静态资源无内容指纹，老用户否则看旧图）。

用法（cd backend && uv run python scripts/generate_style_previews.py）：
  --dry-run        只打印各 key 的完整 prompt，不调 provider（对词/排错用）
  --only <key>     只重出单张（某张不满意单独重生成；注意 character_3d /
                   corporate_live 含人物语义，重抽长相会变，属预期非 bug）

生成语义（与任务级风格注入同源）：
  prompt = style_prefix_for(key) 原样前缀（不传 no_people——任务级风格注入用的
  就是原样前缀，预览忠实该语义；character_3d / corporate_live 出现人物是正确
  行为）+ 固定示例主体；
  provider = make_ai_image(None) config 默认链（恒有 Throttle 包裹；跨 provider
  Fallback 仅 ai_image_provider="seedream"（Ark 直连）主路才挂 gpt-image-2，
  seedream-360 等 360 网关主路为单路——registry._build_ai_image 契约），
  size=2048x1152（seedream-360 三档中的 16:9 档，Ark 直连同值直传），
  PIL 居中裁切 + 缩放归一化 960×540。

引擎一致性（设计 §3.2）：**必须以生产部署的 config.yaml 为准跑一次**——预览图
与生产生图引擎同源是「效果参考」价值的根基。本脚本读项目根 config.yaml 的
video_gen 段（$ENV 占位从项目根 .env 解析，同原 backend/temp_ac1_images.py
探针脚本范式——该探针已于 2026-09-30 清理，范式描述保留），
与 FastAPI 请求上下文无关。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import sys
import time
from pathlib import Path

import httpx
import yaml
from dotenv import dotenv_values
from PIL import Image

BACKEND = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.gateway.video_gen.config import VideoGenConfig  # noqa: E402
from app.gateway.video_gen.providers.registry import build_provider_registry  # noqa: E402
from app.gateway.video_gen.style_presets import (  # noqa: E402
    IMAGE_STYLE_KEYS,
    VIDEO_STYLE_KEYS,
    style_prefix_for,
)

OUT_DIR = PROJECT_ROOT / "frontend" / "public" / "style-previews"
OUT_SIZE = (960, 540)  # 统一 16:9 横版（设计 §3.1）
JPEG_QUALITY = 85
# seedream-360 三档中的 16:9 档（_map_to_seedream_size 恰好命中）；Ark 直连 /
# gpt-image-2 均按该值直传。两个 provider 不传 size 默认 1152x2048（9:16 竖版），
# 必须显式下发。
GEN_SIZE = "2048x1152"
ATTEMPTS = 3

# 7 个 key：两表并集（视频 5 + 图片专属 2），与前端 UNION_STYLE_KEYS 同源派生，
# 不手抄清单——改两表时本脚本自动跟随。三风格两用共用一张（设计 §六：同 key
# 不分引擎出两版）。
STYLE_KEYS: tuple[str, ...] = (*VIDEO_STYLE_KEYS, *(k for k in IMAGE_STYLE_KEYS if k not in VIDEO_STYLE_KEYS))

# 固定示例主体（设计 §3.2）：7 张共用同一主体——画面差异全部来自风格前缀，
# 横向可比性最强；不要求画面出现文字（AI 渲染中文易乱码，信息图风格让模型
# 自绘图形元素即可）。
DEMO_SUBJECT = "企业培训课程画面：一个由三段步骤组成的业务流程场景，构图完整，主体清晰"

# 按 key 覆盖主体的口子（设计 §3.2 修订 3）：默认空 = 全部共用 DEMO_SUBJECT。
# 信息图类风格（whiteboard / isometric_info）若全共用主体导致预览失真，可在此
# 单独放宽（如允许「可包含简单图形与色块分区」）；原则不变——同 key 的图仍由
# 风格前缀主导差异。
SUBJECT_OVERRIDES: dict[str, str] = {
    # 例："whiteboard": "企业培训课程画面：白板上的三步流程示意图，可包含简单图形与色块分区",
}


def prompt_for(key: str) -> str:
    """原样风格前缀 + 示例主体（与风格锚图端点的拼接方式一致：中文逗号连接）。"""
    prefix = style_prefix_for(key)
    subject = SUBJECT_OVERRIDES.get(key, DEMO_SUBJECT)
    return f"{prefix}，{subject}"


def _load_default_provider():
    """读生产 config.yaml 的 video_gen 段构建默认生图链（同原 backend/temp_ac1_images.py 探针脚本范式；该探针已于 2026-09-30 清理，范式描述保留）。"""
    cfg_raw = yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")).get("video_gen") or {}
    env = dotenv_values(PROJECT_ROOT / ".env")
    # $ENV 占位解析：config.yaml 约定（值以 $ 开头即环境变量名，可带 {} 包裹）
    for k, v in list(cfg_raw.items()):
        if isinstance(v, str) and v.startswith("$"):
            cfg_raw[k] = env.get(v.strip("$").strip("{}"), "")
    config = VideoGenConfig(**cfg_raw)
    if not config.qihu_api_key and not config.ark_api_key:
        sys.exit("config.yaml video_gen 段 qihu_api_key / ark_api_key 解析后均为空——检查项目根 .env 后重试")
    registry = build_provider_registry(config)
    return registry, config.ai_image_provider


async def _fetch_bytes(url: str) -> bytes:
    """ImageRef.url（data: URI 或远程 URL）→ bytes。CDN 会拦无 UA 的裸调（403）。"""
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[1])
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "TrainerAI-VideoGen/1.0 (style previews)"})
        resp.raise_for_status()
        return resp.content


def _normalize_to_jpeg(data: bytes, dest: Path) -> None:
    """任意尺寸产物 → 居中裁切 16:9 → 缩放 960×540 JPEG q85（设计 §3.1）。"""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    tw, th = OUT_SIZE
    target = tw / th
    w, h = img.size
    if w / h > target:  # 过宽 → 左右居中裁切
        nw = round(h * target)
        img = img.crop(((w - nw) // 2, 0, (w + nw) // 2, h))
    elif w / h < target:  # 过高 → 上下居中裁切
        nh = round(w / target)
        img = img.crop((0, (h - nh) // 2, w, (h + nh) // 2))
    img = img.resize(OUT_SIZE, Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=JPEG_QUALITY)


async def main() -> int:
    parser = argparse.ArgumentParser(description="生成任务创建对话框风格下拉静态预览图（docs/plans/2026-09-09-style-preview-design.md）")
    parser.add_argument("--only", choices=STYLE_KEYS, help="只重出单张（重抽人物长相会变，属预期）")
    parser.add_argument("--dry-run", action="store_true", help="只打印各 key 完整 prompt，不调 provider")
    args = parser.parse_args()

    keys = (args.only,) if args.only else STYLE_KEYS
    if args.dry_run:
        for key in keys:
            print(f"[{key}]\n  {prompt_for(key)}")
        return 0

    registry, provider_name = _load_default_provider()
    provider = registry.make_ai_image(None)  # None = config 默认链（恒 Throttle；Fallback 仅 seedream/Ark 主路）
    if type(provider).__name__ == "_StubAIImageProvider":
        sys.exit(f"默认生图链是 stub（ai_image_provider={provider_name} 未配置/无 key）——检查 config.yaml video_gen 段")
    print(f"provider: config 默认链（ai_image_provider={provider_name}，size={GEN_SIZE}）", flush=True)

    failures: list[str] = []
    for key in keys:
        prompt = prompt_for(key)
        dest = OUT_DIR / f"{key}.jpg"
        for attempt in range(1, ATTEMPTS + 1):
            try:
                t0 = time.monotonic()
                ref = await provider.generate_image(prompt, size=GEN_SIZE)
                data = await _fetch_bytes(ref.url or "")
                _normalize_to_jpeg(data, dest)
                cost = f" cost≈¥{ref.cost_cny}" if getattr(ref, "cost_cny", None) else ""
                print(f"[{key}] {dest.name} done in {time.monotonic() - t0:.1f}s{cost}", flush=True)
                break
            except Exception as e:  # noqa: BLE001
                print(f"[{key}] attempt {attempt} failed: {e}", flush=True)
                if attempt == ATTEMPTS:
                    failures.append(key)
                else:
                    await asyncio.sleep(3)
    await registry.close()
    if failures:
        print(f"FAILED: {failures}（可 --only <key> 单独重试）", flush=True)
        return 1
    print(f"done: {len(keys)} 张 → {OUT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
