#!/usr/bin/env python3
"""config.yaml 行级 patcher（2026-10-12 花名册调整批，决策 9；同日加固批扩到 28）。

复演本地 config.yaml 改动到测试机/公司服务器（config.yaml 是 gitignored 手改文件，
git 不送达）：
  1. config_version → 28（任何起始值；27=花名册批，28=加固批 vimax_image_backend
     新字段——该字段及 vimax_video_backend 由代码缺省生效，**不写入 config**
     （C5c 角色 spike 是生产配置落值的硬门禁，spike 未过不落值））；
  2. 删 7 条模型块（mimo-v2.6-flash/pro、gpt-6-luna/sol、gemini-3.8-flash、
     deepseek-v4-flash-vision-exp 六条 360 + Ark 聊天 doubao-seed-2.1-pro-ark）+
     2026-09-23 批头注（块删除按 `- name:` 锚点 + 缩进边界，沿用 09-23 .sh
     remove_block 思路的 Python 实现，规避 GNU/BSD sed 差异）；
  3. models[0] 换序：qwen3.8-flash 提为默认位（含头注），glm-5.3-flash 顺延
     models[1]，两者 display_name 同步（默认后缀迁移）；
  4. 追加 3 条 claude 360 通道模型（gpt-6.1-sol spike 证伪不入册：chat/completions
     上 function tools 全形态 400——上游要求 /v1/responses，勿照旧计划加第 4 条）；
  5. video_gen 四字段迁移（script_model/subtitle_llm_model → qwen3.8-flash；
     vimax_llm_model → qwen/qwen3.8-max + base_url 切回 360）。

形态：--target prod|test（本批两栏值相同，参数仅为沿用 09-23 CLI 形态）/
--dry-run（打印 unified diff 不落盘）/ 幂等复跑（已到位的操作跳过，输出不变）/
完成后 yaml.safe_load 结构校验（版本/花名册顺序/video_gen 字段）。

用法（服务器上建议用 backend venv 跑，系统 python3 缺 pyyaml 时校验降级为 WARNING）：
  cd backend && uv run python ../scripts/apply_config_increments_2026-10-12.py --target test --dry-run
  cd backend && uv run python ../scripts/apply_config_increments_2026-10-12.py --target prod

注意：config.yaml 为 gitignored 手改文件（部署手册 K 系列口径）；本脚本不碰其余
内容（块删除/改写只按锚点 + 缩进边界）；整行替换会丢弃行尾注释（权威值即本脚本，
同 09-23 先例）。旧 09-23/09-24 两个 .sh 已加作废头注——勿重放（会把已删模型加回、
把 config_version 改小）。
"""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import sys
from pathlib import Path

TARGET_VERSION = 28
REMOVED_MODELS = [
    "mimo-v2.6-flash",
    "mimo-v2.6-pro",
    "gpt-6-luna",
    "gpt-6-sol",
    "gemini-3.8-flash",
    "deepseek-v4-flash-vision-exp",
    "doubao-seed-2.1-pro-ark",
]
# 2026-09-23 批在 models: 段内的三行头注（其四条模型全删后成孤儿注释）
ORPHAN_COMMENT_MARKERS = (
    "# ── 2026-09-23 新增（docs/plans/2026-09-23-360-new-models-integration-plan.md）──",
    "# 计费回执：360 chat usage 无 amount 字段 → 4 条均暂缺 pricing（TBD），",
    "# 待 /api/admin/model-pricing 补录后控制台出成本报表；官方价见各条注释。",
)
DEFAULT_SWAP_COMMENT = [
    "# 2026-10-12 默认位易主（models[0]）：qwen3.8-flash 三模型压测胜出（650 样本，",
    "# docs/2026-10-12-flash-trio-benchmark.md）——TTFT ~1.5s / 并发 p50 2.7-2.8s /",
    "# 90s 持续负载零漂移全维度第一；token 单价 ≈2.3 倍于 glm-5.3-flash（¥1/3 vs",
    "# ¥0.4/1.4 per M），用户知情接受（计划 §1.6 决策 4），上位后观察一周",
    "# slow_llm_warn_seconds=90 慢调用告警再最终定稿。",
]
GLM_DEMOTE_COMMENT = "# 2026-10-12 起让出默认位（models[1]，压测败于 qwen3.8-flash：恒思考坐实致 TTFT 偏高）。"
NEW_MODELS_BLOCK = """# ── 2026-10-12 新增（docs/plans/2026-10-12-model-config-roster-adjustment-plan.md）──
# spike 留档 temp/results.json：claude 三条经 360 网关 OpenAI 兼容封装可用（PatchedChatOpenAI
# 路径成立）；vision 全答对主色（supports_vision=true 实证）；thinking 顶层/budget/effort
# 形态被网关接受但 reasoning_tokens=0 且无 reasoning 字段 = 参数无效 → 显式
# supports_thinking: false（默认同值，写出来作 spike 结论存档）。计费回执无 amount 字段 →
# 暂缺 pricing（TBD，与 2026-09-23 批同口径），待 /api/admin/model-pricing 补录。
# 原计划第 4 条 gpt-6.1-sol 经 spike 证伪不入册：chat/completions 上 function tools
# 全形态 400（上游要求 /v1/responses）且 effort 不支持 none 档——延后处理，详见 changelog。
# 2026-10-13 事故修正：temperature 必须为 null——Bedrock 上游对 claude 拒收该参数
# （360 网关 400 code 10137 "`temperature` is deprecated for this model"，测试机实测；
# spike 当时未带 temperature 组合故漏检）。ChatOpenAI 该字段 Optional，None = 不携带。
- name: claude-haiku-5-5
  display_name: 360·Claude Haiku 5.5
  use: deerflow.models.patched_openai:PatchedChatOpenAI
  model: anthropic/claude-haiku-5-5
  api_key: $QIHU_API_KEY
  base_url: https://api.360.cn/v1
  request_timeout: 180.0
  max_retries: 2
  max_tokens: 32768
  temperature: null
  supports_vision: true
  supports_thinking: false
  stream_chunk_timeout: 240
- name: claude-sonnet-5-5
  display_name: 360·Claude Sonnet 5.5
  use: deerflow.models.patched_openai:PatchedChatOpenAI
  model: anthropic/claude-sonnet-5-5
  api_key: $QIHU_API_KEY
  base_url: https://api.360.cn/v1
  request_timeout: 180.0
  max_retries: 2
  max_tokens: 32768
  temperature: null
  supports_vision: true
  supports_thinking: false
  stream_chunk_timeout: 240
- name: claude-opus-5-5
  display_name: 360·Claude Opus 5.5
  use: deerflow.models.patched_openai:PatchedChatOpenAI
  model: anthropic/claude-opus-5-5
  api_key: $QIHU_API_KEY
  base_url: https://api.360.cn/v1
  request_timeout: 180.0
  max_retries: 2
  max_tokens: 32768
  temperature: null
  supports_vision: true
  supports_thinking: false
  stream_chunk_timeout: 240
"""
VIDEO_GEN_FIELDS = {
    "vimax_llm_model": "qwen/qwen3.8-max",
    "vimax_llm_base_url": "https://api.360.cn/v1",
    "script_model": "qwen3.8-flash",
    "subtitle_llm_model": "qwen3.8-flash",
}
EXPECTED_MODELS = [
    "qwen3.8-flash",
    "glm-5.3-flash",
    "qwen3.8-max",
    "gpt-5.6-terra",
    "claude-haiku-5-5",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
]


def _block_range(lines: list[str], name: str) -> tuple[int, int] | None:
    """模型块范围 [start, end)：`- name: <name>` 锚点行 + 后续缩进≥2/空行。

    块内注释（如 `  # 官方价...`）缩进 2，随块删除；models 段内的顶层注释
    （缩进 0）不属于块，由孤儿注释清扫单独处理。
    """
    anchor = re.compile(rf"^- name: {re.escape(name)}\s*$")
    for i, line in enumerate(lines):
        if anchor.match(line):
            end = i + 1
            while end < len(lines) and (lines[end].startswith(" ") or lines[end].strip() == ""):
                end += 1
            return i, end
    return None


def _remove_model_blocks(lines: list[str]) -> tuple[list[str], list[str]]:
    """删 7 条模型块 + 09-23 头注孤儿行。返回 (新行列表, 操作日志)。"""
    log = []
    for name in REMOVED_MODELS:
        rng = _block_range(lines, name)
        if rng is None:
            log.append(f"  skip 删 {name}（不在册，幂等）")
            continue
        start, end = rng
        # 吸收块上方紧邻的、属于该块的缩进 0 spike 注释行？——不存在该形态
        # （缩进 0 注释只有 09-23 头注与 glm 恒思考注，前者单独清扫，后者属 glm 保留）。
        del lines[start:end]
        log.append(f"  删除模型块 {name}（{end - start} 行）")
    # 09-23 头注孤儿清扫
    kept = []
    removed_markers = 0
    for line in lines:
        if any(line.strip() == m for m in ORPHAN_COMMENT_MARKERS):
            removed_markers += 1
            continue
        kept.append(line)
    if removed_markers:
        log.append(f"  清扫 2026-09-23 头注孤儿行 ×{removed_markers}")
        lines = kept
    return lines, log


def _bump_version(lines: list[str]) -> tuple[list[str], str]:
    for i, line in enumerate(lines):
        m = re.match(r"^config_version: (\d+)\s*$", line)
        if m:
            if int(m.group(1)) == TARGET_VERSION:
                return lines, f"  skip config_version 已是 {TARGET_VERSION}（幂等）"
            lines[i] = f"config_version: {TARGET_VERSION}\n"
            return lines, f"  config_version {m.group(1)} → {TARGET_VERSION}"
    raise SystemExit("config_version 行未找到——目标文件形态异常，中止")


def _promote_qwen_to_models0(lines: list[str]) -> tuple[list[str], str]:
    """qwen3.8-flash 块提至 models[0]（含默认位头注），glm 顺延并调 display_name。"""
    if lines and any(re.match(r"^- name: ", ln) for ln in lines):
        first_model_idx = next(i for i, ln in enumerate(lines) if re.match(r"^- name: ", ln))
        if re.match(r"^- name: qwen3\.8-flash\s*$", lines[first_model_idx]):
            # 已在默认位——补头注（若无）+ 调 display_name（幂等）
            lines = _ensure_comment_above(lines, first_model_idx, DEFAULT_SWAP_COMMENT)
            lines = _set_display_name(lines, "qwen3.8-flash", "360·Qwen 3.8 Flash（默认）")
            lines = _set_display_name(lines, "glm-5.3-flash", "360·GLM 5.3 Flash")
            return lines, "  skip models[0] 已是 qwen3.8-flash（幂等，头注/display_name 已核对）"
    rng = _block_range(lines, "qwen3.8-flash")
    if rng is None:
        raise SystemExit("qwen3.8-flash 块未找到——中止（本批前提是该条在册）。若目标 config.yaml 的\nmodels 列表使用 2 空格缩进形态（`  - name:`），本脚本按仓库零缩进锚点\n（`^- name:`）匹配不到——先核对目标文件形态再重跑，勿手工绕过中止。")
    start, end = rng
    block = lines[start:end]
    del lines[start:end]
    models_idx = next(i for i, ln in enumerate(lines) if re.match(r"^models:\s*$", ln))
    insert_at = models_idx + 1
    # 注意换行拼接：block 行来自 splitlines(keepends=True) 已带 \n，注释行是裸串
    # ——统一归一（keepends 行重复加 \n 会产生行间空行，且双换行字符串让
    # display_name 的 `.*$` 匹配失手，酿成"run2 才补 display_name"的非幂等）。
    new_lines = [ln if ln.endswith("\n") else ln + "\n" for ln in [*DEFAULT_SWAP_COMMENT, *block]]
    lines[insert_at:insert_at] = new_lines
    lines = _set_display_name(lines, "qwen3.8-flash", "360·Qwen 3.8 Flash（默认）")
    # glm 降位注记 + 摘除（默认）后缀
    glm_rng = _block_range(lines, "glm-5.3-flash")
    if glm_rng:
        lines = _ensure_comment_above(lines, glm_rng[0], [GLM_DEMOTE_COMMENT])
    lines = _set_display_name(lines, "glm-5.3-flash", "360·GLM 5.3 Flash")
    return lines, "  models[0] ← qwen3.8-flash（glm 顺延 models[1]，display_name 同步）"


def _ensure_comment_above(lines: list[str], block_start: int, comment_lines: list[str]) -> list[str]:
    """块上方若无该注释（按首行匹配）则插入。返回新列表（不改入参）。"""
    marker = comment_lines[0]
    j = block_start - 1
    while j >= 0 and lines[j].strip().startswith("#"):
        if lines[j].strip() == marker.strip():
            return lines
        j -= 1
    out = list(lines)
    out[block_start:block_start] = [c + "\n" for c in comment_lines]
    return out


def _set_display_name(lines: list[str], name: str, display: str) -> list[str]:
    rng = _block_range(lines, name)
    if rng is None:
        raise SystemExit(f"{name} 块未找到（display_name 调整前提）")
    out = list(lines)
    for i in range(rng[0], rng[1]):
        if re.match(r"^\s+display_name: .*$", out[i]):
            out[i] = f"  display_name: {display}\n"
            break
    return out


def _append_new_models(lines: list[str]) -> tuple[list[str], str]:
    if _block_range(lines, "claude-haiku-5-5"):
        return lines, "  skip 新增 claude 三条（已在册，幂等）"
    # 首选锚点：tool_groups:（真实两栏 config 均紧随 models 列表之后）
    tg = next((i for i, ln in enumerate(lines) if re.match(r"^tool_groups:\s*$", ln)), None)
    if tg is None:
        # 兜底（终审 F1 修正）：必须从 models: 行向后扫到列表区末尾——绝不能从文件
        # 头找首个顶层键（会把模型块插到 config_version 之前，产出非法 YAML 落盘）。
        models_idx = next((i for i, ln in enumerate(lines) if re.match(r"^models:\s*$", ln)), None)
        if models_idx is None:
            raise SystemExit("models: 段与 tool_groups: 锚点均未找到——中止（目标文件形态异常）")
        tg = models_idx + 1
        while tg < len(lines):
            ln = lines[tg]
            if ln.strip() == "" or ln.startswith("#") or ln.startswith("- ") or ln.startswith(" "):
                tg += 1
            else:
                break
    lines[tg:tg] = [ln + "\n" for ln in NEW_MODELS_BLOCK.rstrip("\n").split("\n")]
    return lines, "  追加 claude 三条（haiku/sonnet/opus，PatchedChatOpenAI + 360 通道）"


def _migrate_video_gen(lines: list[str]) -> tuple[list[str], list[str]]:
    """video_gen 段内四字段整行替换（值已到位则跳过，幂等）。"""
    log = []
    vg_start = next((i for i, ln in enumerate(lines) if re.match(r"^video_gen:\s*$", ln)), None)
    if vg_start is None:
        raise SystemExit("video_gen: 段未找到——中止")
    vg_end = next((i for i in range(vg_start + 1, len(lines)) if re.match(r"^\S", lines[i]) and not lines[i].startswith("#")), len(lines))
    for key, value in VIDEO_GEN_FIELDS.items():
        pat = re.compile(rf"^  {re.escape(key)}: .*$")
        target = f"  {key}: {value}\n"
        hit = False
        for i in range(vg_start + 1, vg_end):
            if pat.match(lines[i]):
                if lines[i] == target:
                    log.append(f"  skip video_gen.{key} 已是新值（幂等）")
                else:
                    lines[i] = target
                    log.append(f"  video_gen.{key} → {value}")
                hit = True
                break
        if not hit:
            lines.insert(vg_start + 1, target)
            log.append(f"  video_gen.{key} 缺失，插入 {value}")
            vg_end += 1
    return lines, log


def _check_structure(cfg: dict) -> list[str]:
    """结构断言（空列表 = 通过）。"""
    problems = []
    if cfg.get("config_version") != TARGET_VERSION:
        problems.append(f"config_version 应为 {TARGET_VERSION}，实际 {cfg.get('config_version')}")
    names = [m["name"] for m in cfg.get("models", [])]
    if names != EXPECTED_MODELS:
        problems.append(f"花名册顺序不符：{names}")
    vg = cfg.get("video_gen", {})
    for key, value in VIDEO_GEN_FIELDS.items():
        if vg.get(key) != value:
            problems.append(f"video_gen.{key} 应为 {value}，实际 {vg.get(key)}")
    return problems


def _verify_text(text: str) -> tuple[list[str] | None, list[str]]:
    """写盘前预检：返回 (structure_problems, fatal)。

    fatal 仅在 YAML **不可解析**时非空（如锚点错插产出的非法结构，终审 F1）——
    调用方必须中止且不得落盘；structure_problems（可解析但与期望不符，如目标机
    花名册良性漂移）不阻断写盘，交由落盘后 _verify 把关退出码（终审 F2）。
    """
    try:
        import yaml
    except ImportError:
        return None, []  # 无 pyyaml 时跳过预检（不阻塞），落盘后 _verify 再兜底
    try:
        cfg = yaml.safe_load(text)
    except Exception as e:
        return None, [f"patched 内容无法 yaml 解析（不落盘）：{type(e).__name__}: {e}"]
    return _check_structure(cfg), []


def _verify(path: Path) -> list[str]:
    """落盘后文件级结构校验（pyyaml 缺失时降级 WARNING）。"""
    try:
        import yaml
    except ImportError:
        return ["WARNING: 系统 python3 无 pyyaml——跳过结构校验（建议 cd backend && uv run python 重跑本脚本）"]
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    problems = _check_structure(cfg)
    if problems:
        return problems
    names = [m["name"] for m in cfg.get("models", [])]
    return [f"结构校验通过（version={TARGET_VERSION}，花名册 {len(names)} 条，video_gen 四字段到位）"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--target", choices=["prod", "test"], required=True, help="目标环境（本批两栏值相同）")
    parser.add_argument("--dry-run", action="store_true", help="打印 unified diff 不落盘")
    parser.add_argument("--config", type=Path, default=None, help="config.yaml 路径（默认仓库根）")
    args = parser.parse_args()

    path = args.config or Path(__file__).resolve().parent.parent / "config.yaml"
    if not path.exists():
        print(f"目标文件不存在：{path}", file=sys.stderr)
        return 2
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)

    print(f"== apply_config_increments_2026-10-12 --target {args.target}{' --dry-run' if args.dry_run else ''} ==")
    print(f"目标：{path}")
    lines, log = _remove_model_blocks(lines)
    lines, vlog = _bump_version(lines)
    lines, plog = _promote_qwen_to_models0(lines)
    lines, alog = _append_new_models(lines)
    lines, vglog = _migrate_video_gen(lines)
    for entry in [*log, vlog, plog, alog, *vglog]:
        print(entry)

    patched = "".join(lines)

    # 写盘前预检（终审 F1 修正）：YAML 不可解析 → 中止且不落盘；结构漂移仅预警。
    pre_problems, fatal = _verify_text(patched)
    for msg in fatal:
        print(msg, file=sys.stderr)
    if fatal:
        return 1
    for msg in pre_problems or []:
        print(f"WARNING 结构预检（可解析但与期望不符，将照常写盘、以落盘后校验为准）：{msg}")

    verify_failed = False
    if patched == original:
        print("结果：无变化（幂等复跑确认）")
    elif args.dry_run:
        diff = difflib.unified_diff(original.splitlines(keepends=True), lines, fromfile=str(path), tofile=f"{path} (patched)")
        sys.stdout.writelines(diff)
        print("（dry-run：未落盘）")
    else:
        backup = path.with_suffix(path.suffix + ".bak-20261012-patcher")
        shutil.copy2(path, backup)
        path.write_text(patched, encoding="utf-8")
        print(f"已写回（备份：{backup.name}）")

    if not args.dry_run or patched == original:
        msgs = _verify(path)
        for msg in msgs:
            print(msg)
        # 终审 F2 修正：结构校验发现问题必须反映到退出码（部署 runbook 以 && 链接本脚本）
        if any(not m.startswith("结构校验通过") and not m.startswith("WARNING") for m in msgs):
            verify_failed = True
            print("结构校验未通过——退出码置 1（部署链应中止，勿重启 gateway）", file=sys.stderr)
    if args.dry_run and patched != original:
        print("（dry-run 有变化：结构校验在正式执行后进行）")
    return 1 if verify_failed else 0


if __name__ == "__main__":
    sys.exit(main())
