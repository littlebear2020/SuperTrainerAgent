"""A/B 对比探针：当前单句画面指令 vs 结构化画面指令（C1 验收用，2026-08-09）。

条件与生产一致：
  - A 组 system prompt = storyboard_planner._IMAGE_PROMPT_SYSTEM（直接 import，防漂移）
  - B 组 system prompt = 结构化候选指令（本文件内 CANDIDATE_SYSTEM）
  - 同一 LLM（生产主 LLM doubao-seed-2.1-pro-ark，Ark 直连）
  - 同一生图模型/尺寸（gpt-image-2 via 360，1280x720，与 config.yaml 默认一致）
  - 同一组旁白（4 条固定）

产物：test-output/ab-image-prompt-2026-08-09/
  A1..A4.png / B1..B4.png + report.md（含两组 prompt 全文、文件大小、usage.amount）

运行：cd backend && uv run python scripts/probe_ab_image_prompt.py
（一次性探针，不随 pytest 套件跑——避免全量挂死，见记忆 pytest-full-run-hangs）
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.gateway.video_gen.orchestration.storyboard_planner import (  # noqa: E402
    _IMAGE_PROMPT_SYSTEM,
    _parse_json_prompts,
)

# --- B 组候选指令（C1 草稿：参照 vimax storyboard_artist 结构，JSON 键契约不变）---
CANDIDATE_SYSTEM = (
    "你是专业的视觉创意设计师与分镜师。为每一条旁白创作一条详细的**中文**画面描述"
    "（50-100 字），用于 AI 文生图。\n"
    "每条描述必须包含以下要素：\n"
    "1. 主体与动作：画面中的人物/物体是谁、在做什么；有人物时交代外貌特征"
    "（年龄段、着装、姿态、表情）\n"
    "2. 场景与环境：地点、时间、关键道具与背景元素\n"
    "3. 构图：主体在画面中的位置关系（如三分法、对称构图、引导线、前景/中景/背景层次）\n"
    "4. 光影：光源方向与质感（如柔和窗光、侧逆光、均匀柔光、高对比硬光）\n"
    "5. 色调与氛围：主色调与情绪基调（如暖橙温馨、冷蓝专业、低饱和沉稳）\n"
    "6. 风格一致性：整套描述统一为「现代企业宣传片写实摄影风，电影质感，浅景深」，"
    "各条之间风格、色调不跳跃\n"
    "只描述画面中可见的内容，不要出现文字、字幕、水印、logo。"
    '严格输出 JSON：{"image_prompts": ["描述1", "描述2", ...]} '
    "每条旁白对应一条描述，顺序一致。不要输出其他任何文字。"
)

# --- C 组候选指令（中间路线：A 的自由度 + 禁文字/风格统一硬约束，2026-08-09 用户拍板方向）---
MIDDLE_SYSTEM = (
    "你是专业的视觉创意设计师。为每一条旁白创作一条详细的**中文**画面描述"
    "（50-100 字），刻画场景、氛围与象征元素。\n"
    "硬性要求：\n"
    "1. 画面中不得出现任何文字、字母、数字、标语、水印、logo"
    "（象征意义用视觉元素表达，不要靠文字符号）；"
    "避免选择天然携带文字的道具作为画面主体（如白板、海报、屏幕、桌牌、书籍封面）\n"
    "2. 整套描述风格统一（统一的写实/插画风格与色调基调），各条之间不跳跃\n"
    '严格输出 JSON：{"image_prompts": ["描述1", "描述2", ...]} '
    "每条旁白对应一条描述，顺序一致。不要输出其他任何文字。"
)

# --- 固定测试条件 ---
TITLE = "新员工入职培训：企业文化与价值观"
NARRATIONS = [
    "欢迎加入我们的团队，在这里，每一位成员都是公司最宝贵的财富。",
    "我们的使命，是让每一次学习都转化为真实可见的成长。",
    "协作与信任，是我们面对一切挑战时最坚实的基石。",
    "现在，让我们一起开启这段全新的职业成长旅程。",
]
IMAGE_SIZE = "1280x720"
ARK_MODEL = "doubao-seed-2-1-pro-260628"
OUT_DIR = BACKEND.parent / "test-output" / "ab-image-prompt-2026-08-09"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (BACKEND.parent / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def gen_prompts(client: httpx.Client, ark_key: str, system: str) -> list[str]:
    joined = json.dumps({"narrations": NARRATIONS}, ensure_ascii=False, indent=2)
    resp = client.post(
        "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        headers={"Authorization": f"Bearer {ark_key}"},
        json={
            "model": ARK_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": joined},
            ],
            "temperature": 0.7,
            "max_tokens": 4096,
        },
        timeout=180,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    prompts = _parse_json_prompts(content, "image_prompts")
    assert len(prompts) == len(NARRATIONS), f"prompt count mismatch: {prompts}"
    return prompts


def gen_image(client: httpx.Client, qihu_key: str, prompt: str) -> tuple[bytes, str]:
    """Returns (png_bytes, amount). 走生产默认端点 /v1/images/edits（纯文生图不带参考图）。"""
    resp = client.post(
        "https://api.360.cn/v1/images/edits",
        headers={
            "Authorization": f"Bearer {qihu_key}",
            "Content-Type": "application/json",
        },
        json={"model": "openai/gpt-image-2", "prompt": prompt, "size": IMAGE_SIZE},
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    amount = str(data.get("usage", {}).get("amount", "?"))
    item = data["data"][0]
    if item.get("b64_json"):
        import base64

        return base64.b64decode(item["b64_json"]), amount
    img = client.get(item["url"], timeout=120, follow_redirects=True)
    img.raise_for_status()
    return img.content, amount


def main() -> None:
    env = load_env()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: list[str] = [
        f"# A/B 画面指令对比（{TITLE}）",
        "",
        f"- LLM: {ARK_MODEL}（Ark 直连，生产主 LLM）",
        f"- 生图: gpt-image-2 via 360 /v1/images/edits, {IMAGE_SIZE}",
        "",
    ]

    variants = {
        "A": ("A", _IMAGE_PROMPT_SYSTEM),
        "B": ("B", CANDIDATE_SYSTEM),
        "C": ("C", MIDDLE_SYSTEM),
    }
    only = sys.argv[1].upper() if len(sys.argv) > 1 else None
    selected = [variants[only]] if only else [variants["A"], variants["B"]]

    with httpx.Client() as client:
        for tag, system in selected:
            report.append(f"## {tag} 组 system prompt\n\n```\n{system}\n```\n")
            t0 = time.monotonic()
            prompts = gen_prompts(client, env["ARK_API_KEY"], system)
            report.append(f"prompt 生成耗时 {time.monotonic() - t0:.1f}s\n")
            for i, (narr, prompt) in enumerate(zip(NARRATIONS, prompts), 1):
                t0 = time.monotonic()
                png, amount = gen_image(client, env["QIHU_API_KEY"], prompt)
                path = OUT_DIR / f"{tag}{i}.png"
                path.write_bytes(png)
                kb = len(png) / 1024
                report.append(f"### {tag}{i}（{kb:.0f} KB，生图 {time.monotonic() - t0:.0f}s，amount={amount}）\n\n- 旁白：{narr}\n- 画面描述：{prompt}\n")
                print(f"[{tag}{i}] {kb:.0f} KB  amount={amount}  {prompt[:40]}...")

    (OUT_DIR / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"\n产物目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
