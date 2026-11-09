#!/usr/bin/env python3
"""仓库治理约定机械校验（docs 治理 + 测试落位；AGENTS.md「文档治理」约定的硬兜底）。

校验项：
1. docs/ 根目录不得出现 changelog-*.md（一律放 docs/changelog/）。
2. docs/changelog/ 命名：changelog-YYYY-MM-DD-<主题>.md（4 份历史无主题文件除外）。
3. docs/plans/ 与 docs/plans/pending/ 命名：YYYY-MM-DD-<主题>-plan.md（archive/ 为历史冻结区，不校验）。
4. docs/ 根目录 dated 文件/目录只保留当月；上月及更早应归档 docs/archive/YYYY-MM/。
5. docs/ 根目录无日期文档限常青白名单（新增常青文档须自觉加入下方白名单）。
6. 仓库根目录 .md 限 ROOT_MD_WHITELIST（防根目录文档增生，2026-09-30 治理）。
7. CLAUDE.md 必须存在（至少仓库根）且为指针形态（含 `@./AGENTS.md` 导入行）——约定载体一律是 AGENTS.md，防双轨漂移（2026-09-30 归一；2026-10-05 补强根存在性校验）。
8. 测试落位（2026-10-06 加固）：backend 的 test_*.py / *_test.py 只允许在 backend/tests/
   （豁免：backend/scripts/ 存量 live 验收探针——手动 `uv run python` 执行、非 pytest 套件；
   backend/packages/vimax/ vendored 区）；frontend 的 .test.ts(x) 只允许在
   frontend/tests/unit/、frontend/tests/e2e/、frontend/src/，.spec.ts(x) 只允许在 frontend/tests/e2e/
   （豁免 frontend/tests/e2e-remote/ 手动验收件——有意在 playwright testDir 之外）。
   放错目录的测试 pytest/vitest/playwright 收集不到——静默不跑、CI 假绿。新例外须登记进下方集中常量。
9. plan 状态头（2026-10-07 加固）：docs/plans/ 与 docs/plans/pending/ 下的 plan 头部 10 行内
   必须声明执行状态（已执行 / 未执行），防旧计划被 agent 误读为待办或现况。archive/ 冻结区不校验。
10. backend/docs 纳管（2026-10-08，docs-system-optimization 批 1 T5）：只放常青笔记与 API 参考；
    一次性产物（日期前缀 YYYY-MM-DD-* / 阶段后缀）判违例 → 应走 docs/archive 月度归档；
    新增常青文档用大写下划线命名，既有非大写形态登记 BACKEND_DOCS_WHITELIST。
11. 服务器红线关键词唯一来源（2026-10-08，批 2 B4，**非阻断警告**）：三份服务器文档指针化后，
    同一事实只在权威文档陈述；非权威文档中出现关键词且该行未含权威文档名（即非指针行）→ warning。
    AGENTS.md 红线节镜像是有意设计，不在此规则范围。
12/13/16/17. 见下方 TLDR_CUTOFF / PENDING_AGE_WARN_DAYS / BACKLOG_ENTRY_RE / EVIDENCE_CUTOFF 常量注释。
14. 二进制禁区门禁（2026-10-09 机械门禁批 ①）：CONVENTION_BASE_SHA/CONVENTION_HEAD_SHA 同时
    存在时（CI 经 quick-checks.yml 传入），diff range 内触及 docker/sandbox/cache/ 或
    .baseline-m0/ 且 range 内 commit message 无豁免 token → violation（阻断）；
    本地裸跑（未设环境变量）静默跳过，行为不变。
15. AGENTS.md 禁区节 ↔ rule.json 一致性（2026-10-09 机械门禁批 ③）：两侧目录禁区集合须相等
    （AGENTS 有/规则没有 = AI 评审漏守；规则有/AGENTS 没有 = 文档失声），漂移即 violation；
    任一文件缺失或解析失败同样 violation（响亮失败，不静默跳过）。
18. plan 状态头唯一性（2026-10-13 MA-P0 批 C6）：plan 全文块引用状态行（> **状态** / > **执行状态**）
    必须唯一——双状态头 = 机器与人读到冲突信号（roster plan 实例），>1 即 violation
    （无日期门控，存量已于同批 C5 清零）。
19. AGENTS.md 核心路径清单 ↔ check_main_push_paths.py 一致性（2026-10-13 MA-P0 批 C10）：
    禁直推核心路径清单一式两份（AGENTS.md「Git 约定」节镜像 + 脚本 CORE_PATH_PATTERNS SoT），
    集合漂移即 violation（响亮失败，沿用校验项 15 禁区镜像模式）。

退出码：0 全过，1 有违例。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"

EVERGREEN_ROOT_DOCS = frozenset(
    {
        "360-api-reference.md",
        "README.md",
        "architecture-overview.md",
        "company-server-deployment-guide.md",
        "findings-backlog.md",
        "personal-server-deployment-guide.md",
        "production-server-ops-manual.md",
        "remotion-course-pipeline.md",
        "task-pipeline.md",
    }
)

LEGACY_CHANGELOGS = frozenset(
    {
        "changelog-2026-05-24.md",
        "changelog-2026-06-01.md",
        "changelog-2026-07-21.md",
        "changelog-2026-08-05.md",
    }
)

ROOT_MD_WHITELIST = frozenset({"AGENTS.md", "CLAUDE.md", "README.md"})

ALLOWED_ROOT_DIRS = frozenset({"archive", "changelog", "plans"})

CHANGELOG_RE = re.compile(r"^changelog-\d{4}-\d{2}-\d{2}-.+\.md$")
PLAN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-.+-plan\.md$")
DATE_RE = re.compile(r"(\d{4})-(\d{2})-\d{2}")
CHECKED_SUFFIXES = {".md", ".html", ".svg", ".png", ".pdf"}

# 测试落位扫描（校验项 8）：按目录"名字"逐层跳过——勿在测试源码树下使用这些目录名
# （路径锚定化的精细排除留待后续，2026-10-06 终审 Minor-3）
TEST_SCAN_EXCLUDE_DIRS = frozenset(
    {
        ".git",
        ".deer-flow",
        ".venv",
        "__pycache__",
        "node_modules",
        ".next",
        ".worktrees",
        ".superpowers",
        "temp",
        "test-output",
        "playwright-report",
        "test-results",
    }
)

# backend 测试文件豁免前缀：backend/scripts/ = 存量 live 验收探针（16 件，2026-10-06 抽样
# test_doubao_tts_live.py 确认性质：手动 `uv run python` 执行、非 pytest 套件）；
# backend/packages/vimax/ = vendored 豁免防御。
BACKEND_TEST_EXEMPT_PREFIXES = ("backend/scripts/", "backend/packages/vimax/")

# frontend 测试文件豁免前缀：tests/e2e-remote/ = 手动验收件（manual-acceptance.spec.ts），
# 有意在 playwright testDir 之外、不参与收集（2026-10-06 终审登记）。
FRONTEND_TEST_EXEMPT_PREFIXES = ("frontend/tests/e2e-remote/",)

# backend/docs 纳管（校验项 10）：白名单 = 2026-10-08 批 1 T2/T3 裁决后的实际存量
# （含小写形态的 summarization.md；AUTH_TEST_PLAN / BYTEDANCE_MODELS_INTEGRATION_PLAN
# 系有现行代码引用的改判保留件）。子目录仅 360-api（API 参考系）。
BACKEND_DOCS = REPO_ROOT / "backend" / "docs"
BACKEND_DOCS_WHITELIST = frozenset(
    {
        "AUTH_TEST_PLAN.md",
        "BACKEND_MODULE_NOTES.md",
        "BYTEDANCE_ARK_API_REFERENCE.md",
        "BYTEDANCE_MODELS_INTEGRATION_PLAN.md",
        "PROMPT_DIALECT_REFERENCE.md",
        "VIDEO_GEN_NOTES.md",
        "summarization.md",
    }
)
BACKEND_DOCS_DIRS = frozenset({"360-api"})
# 新增常青文档命名形态：大写下划线（如 VIDEO_GEN_NOTES.md）
BACKEND_DOCS_EVERGREEN_RE = re.compile(r"^[A-Z0-9]+(?:_[A-Z0-9]+)*\.md$")

# 校验项 12（TL;DR 阻断，2026-10-08 批 4 D3）：文件名日期 ≥ 此日常量（= 批 4 执行日，
# 只约束新增、不回溯存量）的 plan/changelog 必须有 TL;DR 段。
TLDR_CUTOFF = "2026-10-08"
# 校验项 13（pending 时效，非阻断）：pending 计划文件名日期超 30 天 → warning。
PENDING_AGE_WARN_DAYS = 30
# 校验项 17（changelog 验收记录节，2026-10-12 批 B2，先非阻断）：文件名日期 ≥ 此日
# （= 批 B 执行日，只约束新增）的 changelog 缺「验收记录」节 → warning；约 2 周后升阻断。
EVIDENCE_CUTOFF = "2026-10-12"

# 校验项 16（findings backlog 行格式，阻断，2026-10-12 批 A2）：docs/findings-backlog.md 的
# 条目行必须匹配 `- [ ] YYYY-MM-DD ...` / `- [x] ...` 形态（日期+一句话+指针，防自由书写退化）。
BACKLOG_ENTRY_RE = re.compile(r"^- \[[ x]\] \d{4}-\d{2}-\d{2}\s")

# 服务器红线关键词 → 唯一权威文档（校验项 11，批 2 B4，非阻断）。
# 非权威文档中出现关键词且该行未含权威文档名（指针行豁免）→ warning。
SERVER_DOCS = (
    "company-server-deployment-guide.md",
    "production-server-ops-manual.md",
    "personal-server-deployment-guide.md",
)
SERVER_KEYWORD_AUTHORITY = {
    "22222": "production-server-ops-manual.md",
    "system prune": "production-server-ops-manual.md",
}

# 校验项 14（二进制禁区门禁）：AI 评审对二进制 diff 无条件排除，两区主体改动永远不进
# AI 评审视野，只能机械门禁把关。与 rule.json 两条二进制禁区条目、AGENTS.md 禁区节同源
# ——增删须三处对齐（本常量 / rule.json / AGENTS.md）；后两者由校验项 15 的相等性检查互守，
# 本常量靠人工随改动同步（check 脚本不检查自身，2026-10-09 机械门禁批 ①）。
BINARY_FORBIDDEN_PREFIXES = (
    "docker/sandbox/cache/",
    "video-templates/course-standard/.baseline-m0/",
)
# 豁免 token：刻意更新场景（chrome/字体包升级、diff-gallery 改版式）在 commit message 附上
# 即放行——留在 git log 里可审计（git log --grep 全量可查）。
BINARY_ZONE_EXEMPT_TOKEN = "[binary-zone-ok]"

BACKEND_TEST_FILE_RE = re.compile(r"^(test_.+|.+_test)\.py$")
FRONTEND_TEST_FILE_RE = re.compile(r"^.+\.(test|spec)\.tsx?$")
# plan 状态头：「状态：已执行/未执行」「执行状态：…已执行」「（未执行，…）」等形态（校验项 9）
PLAN_STATUS_RE = re.compile(r"状态[*：:\s]*[^。\n]{0,40}(?:已执行|未执行)|[（(](?:已执行|未执行)")
# 校验项 18（plan 状态头唯一性，2026-10-13 MA-P0 批 C6）：块引用状态行（> **状态**… / > **执行状态**…）
# 全文必须唯一——双状态头会让机器与人读到冲突信号（roster plan 实例，P0-4 机械门）。
PLAN_STATUS_HEADER_LINE_RE = re.compile(r"^>\s*\*\*(?:执行)?状态\*\*")


def first_year_month(name: str) -> tuple[int, int] | None:
    match = DATE_RE.search(name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def check_binary_zone(base: str, head: str, violations: list[str]) -> None:
    """校验项 14：二进制禁区门禁（仅在 CI 传入 CONVENTION_BASE_SHA/CONVENTION_HEAD_SHA 时生效）。"""
    diff = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        violations.append(f"校验项14：git diff {base}...{head} 执行失败——{diff.stderr.strip()[:200]}")
        return
    hits = sorted({p for p in diff.stdout.splitlines() if p.startswith(BINARY_FORBIDDEN_PREFIXES)})
    if not hits:
        return
    log = subprocess.run(
        ["git", "log", "--format=%B", f"{base}..{head}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if log.returncode != 0:
        violations.append(f"校验项14：git log {base}..{head} 执行失败——{log.stderr.strip()[:200]}")
        return
    if BINARY_ZONE_EXEMPT_TOKEN in log.stdout:
        return
    violations.append(
        f"校验项14：本次变更触及二进制禁区 {hits}——离线部署镜像唯一来源/像素回归基线，"
        "AI 评审对二进制 diff 无条件排除，只能由本门禁把关；"
        f"刻意更新请在 commit message 附 {BINARY_ZONE_EXEMPT_TOKEN} 豁免（留 git log 可审计痕迹）"
    )


def check_core_path_sync(violations: list[str]) -> None:
    """校验项 19：AGENTS.md 核心路径清单 ↔ check_main_push_paths.py CORE_PATH_PATTERNS 一致性。

    解析规则（与两侧写法耦合，改写法须同步）：AGENTS 侧锚定含「不可直推核心路径清单」的行，
    随后收集两空格缩进 `- ` 条目行里的全部反引号 token（遇到第一个非条目行即止）；
    脚本侧取 CORE_PATH_PATTERNS 常量元组内的字符串字面量。集合漂移或文件缺失均响亮违例。
    """
    agents_md = REPO_ROOT / "AGENTS.md"
    guard = REPO_ROOT / "scripts" / "check_main_push_paths.py"
    if not agents_md.is_file() or not guard.is_file():
        violations.append(
            "校验项19：AGENTS.md 或 scripts/check_main_push_paths.py 缺失——"
            "核心路径清单一式两份互守，缺一即违例（响亮失败）"
        )
        return
    agents_patterns: set[str] = set()
    in_list = False
    for line in agents_md.read_text(encoding="utf-8", errors="replace").splitlines():
        if "不可直推核心路径清单" in line:
            in_list = True
            continue
        if in_list:
            if line.startswith("  - `"):  # 只收反引号起首的清单条目，说明性 bullet 不误收（PR#14 评审）
                agents_patterns.update(re.findall(r"`([^`]+)`", line))
            else:
                break
    block = re.search(
        r"CORE_PATH_PATTERNS\s*=\s*\(([^)]*)\)",
        guard.read_text(encoding="utf-8", errors="replace"),
        re.S,
    )
    script_patterns: set[str] = set(re.findall(r'"([^"]+)"', block.group(1))) if block else set()
    # 解析失败必须响亮违例——两侧空集相等不得静默通过（PR#14 复评修复，对齐校验项 15 口径）
    if not agents_patterns:
        violations.append("校验项19：AGENTS.md 未解析到核心路径清单（锚点行缺失或无条目）")
    if not script_patterns:
        violations.append("校验项19：check_main_push_paths.py 未解析到 CORE_PATH_PATTERNS 常量")
    if agents_patterns and script_patterns and agents_patterns != script_patterns:
        violations.append(
            f"校验项19：核心路径清单两侧漂移——AGENTS 独有 {sorted(agents_patterns - script_patterns)}，"
            f"脚本独有 {sorted(script_patterns - agents_patterns)}；"
            "改清单须 AGENTS.md 与 scripts/check_main_push_paths.py 同改（P0-3 镜像互守）"
        )


def _agents_forbidden_zones() -> tuple[set[str], bool]:
    """AGENTS.md 禁区节的目录禁区集合 + AGENTS.md 文件是否找到。

    （节本身缺失时返回空集合——由调用侧的集合比对兜底为响亮违例，不静默。）
    解析规则（与本文件写法耦合，改写法须同步）：定位 `## 边界与禁区` 节（到下一 `## ` 止）；
    每个 `- ` 条目取首个 `——`/`--` 之前、以 `/` 结尾的反引号 token——可排除
    TRAINERAI_PATCHES.md / config.example.yaml 等行内提及的非目录 token。
    """
    agents = REPO_ROOT / "AGENTS.md"
    if not agents.is_file():
        return set(), False
    zones: set[str] = set()
    in_section = False
    for line in agents.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## "):
            if in_section:
                break
            in_section = line[3:].startswith("边界与禁区")
            continue
        if not in_section or not line.lstrip().startswith("- "):
            continue
        entry = line.lstrip()[2:]
        dash_positions = [p for p in (entry.find("——"), entry.find("--")) if p >= 0]
        region = entry[: min(dash_positions)] if dash_positions else entry
        zones.update(token[:-1] for token in re.findall(r"`([^`]+)`", region) if token.endswith("/"))
    return zones, True


def check_forbidden_zone_sync(violations: list[str]) -> None:
    """校验项 15：AGENTS.md 禁区节 ↔ rule.json【禁区】条目一致性（纯状态检查，本地/CI 都跑）。"""
    zones, agents_found = _agents_forbidden_zones()
    if not agents_found:
        violations.append("校验项15：AGENTS.md 或「## 边界与禁区」节缺失——解析不到禁区集合（响亮失败，不静默跳过）")
        return
    rule_path = REPO_ROOT / ".opencodereview" / "rule.json"
    if not rule_path.is_file():
        violations.append("校验项15：.opencodereview/rule.json 缺失——无法提取【禁区】条目")
        return
    try:
        rules = json.loads(rule_path.read_text(encoding="utf-8")).get("rules", [])
    except (json.JSONDecodeError, OSError) as exc:
        violations.append(f"校验项15：rule.json 解析失败——{exc}")
        return
    rule_zones = {
        str(item["path"])[: -len("/**")]
        for item in rules
        if isinstance(item, dict) and str(item.get("rule", "")).startswith("【禁区】") and str(item.get("path", "")).endswith("/**")
    }
    only_agents = sorted(zones - rule_zones)
    only_rule = sorted(rule_zones - zones)
    if only_agents:
        violations.append(
            f"校验项15：AGENTS.md 禁区节有而 rule.json 无 {only_agents}——AI 评审漏守，"
            "须同步 .opencodereview/rule.json（AGENTS.md 指针行：改禁区时同步）"
        )
    if only_rule:
        violations.append(
            f"校验项15：rule.json 有而 AGENTS.md 禁区节无 {only_rule}——文档失声，"
            "须同步 AGENTS.md「边界与禁区」节"
        )


def main() -> int:
    if not DOCS.is_dir():
        print(f"FAIL docs/ 目录不存在（期望路径：{DOCS}）")
        return 1

    current = (date.today().year, date.today().month)
    violations: list[str] = []
    warnings: list[str] = []

    for entry in sorted(REPO_ROOT.iterdir()):
        if entry.is_file() and entry.suffix == ".md" and entry.name not in ROOT_MD_WHITELIST:
            violations.append(
                f"根目录 {entry.name}：仓库根目录 .md 限 {sorted(ROOT_MD_WHITELIST)}；"
                "文档请放 docs/（常青进 EVERGREEN_ROOT_DOCS， dated 产物当月驻留）"
            )

    for entry in sorted(DOCS.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_file():
            if entry.suffix.lower() not in CHECKED_SUFFIXES:
                continue
            if entry.name.startswith("changelog-"):
                violations.append(f"{entry.name}：changelog 不得放 docs/ 根目录，应移入 docs/changelog/")
                continue
            month = first_year_month(entry.name)
            if month is None:
                if entry.name not in EVERGREEN_ROOT_DOCS:
                    violations.append(
                        f"{entry.name}：docs/ 根目录无日期文档限常青白名单；"
                        "非常青文档请加日期前缀，常青文档需登记进本脚本 EVERGREEN_ROOT_DOCS"
                    )
            elif month < current:
                violations.append(
                    f"{entry.name}：非当月 dated 文件应归档 docs/archive/{month[0]:04d}-{month[1]:02d}/"
                )
        elif entry.is_dir():
            if entry.name in ALLOWED_ROOT_DIRS:
                continue
            month = first_year_month(entry.name)
            if month is None:
                violations.append(
                    f"{entry.name}/：docs/ 根目录子目录限 {sorted(ALLOWED_ROOT_DIRS)} + 当月 dated 目录"
                )
            elif month < current:
                violations.append(
                    f"{entry.name}/：非当月 dated 目录应归档 docs/archive/{month[0]:04d}-{month[1]:02d}/"
                )

    changelog_dir = DOCS / "changelog"
    if changelog_dir.is_dir():
        for entry in sorted(changelog_dir.iterdir()):
            if entry.is_file() and entry.suffix == ".md" and entry.name not in LEGACY_CHANGELOGS:
                if not CHANGELOG_RE.match(entry.name):
                    violations.append(
                        f"changelog/{entry.name}：命名应为 changelog-YYYY-MM-DD-<主题>.md"
                    )
                # 校验项 12（TL;DR，阻断）：文件名日期 ≥ TLDR_CUTOFF 的新增件，头部 15 行内须有 TL;DR
                if entry.name[10:20] >= TLDR_CUTOFF:
                    head15 = "\n".join(entry.read_text(encoding="utf-8", errors="replace").splitlines()[:15])
                    if "TL;DR" not in head15:
                        violations.append(
                            f"changelog/{entry.name}：缺 TL;DR——头部 15 行内须有 ≤5 行 TL;DR（2026-10-08 约定，只约束新增）"
                        )
                # 校验项 17（验收记录节，先非阻断 warning）：新增 changelog 须含「验收记录」节
                if entry.name[10:20] >= EVIDENCE_CUTOFF:
                    text = entry.read_text(encoding="utf-8", errors="replace")
                    if "## 验收记录" not in text:
                        warnings.append(
                            f"changelog/{entry.name}：缺「验收记录」节（2026-10-12 约定：每行 = 命令 → 实际输出关键行；约 2 周后升阻断）"
                        )

    for plans_sub in (DOCS / "plans", DOCS / "plans" / "pending"):
        if not plans_sub.is_dir():
            continue
        for entry in sorted(plans_sub.iterdir()):
            if not (entry.is_file() and entry.suffix == ".md"):
                continue
            if not PLAN_RE.match(entry.name):
                violations.append(
                    f"plans/{entry.relative_to(DOCS / 'plans')}：命名应为 YYYY-MM-DD-<主题>-plan.md"
                )
            lines = entry.read_text(encoding="utf-8", errors="replace").splitlines()  # 每 plan 只读一次（PR#14 评审）
            head = "\n".join(lines[:10])
            if not PLAN_STATUS_RE.search(head):
                violations.append(
                    f"plans/{entry.relative_to(DOCS / 'plans')}：缺状态头——头部 10 行内需声明"
                    "「状态：已执行 / 未执行」（防旧计划被误读为待办或现况，2026-10-07 约定）"
                )
            # 校验项 18：状态头唯一性——全文块引用状态行 >1 即违例
            status_header_lines = [
                lineno for lineno, line in enumerate(lines, 1) if PLAN_STATUS_HEADER_LINE_RE.search(line)
            ]
            if len(status_header_lines) > 1:
                violations.append(
                    f"plans/{entry.relative_to(DOCS / 'plans')}:{status_header_lines}：双状态头——"
                    "全文只允许一行块引用状态头；执行后回填须改原行而非新增（2026-10-13 约定）"
                )
            # 校验项 12（TL;DR，阻断）：plan 在状态头之后 15 行内须有 TL;DR
            if entry.name[:10] >= TLDR_CUTOFF:
                status_idx = next((i for i, ln in enumerate(lines[:10]) if PLAN_STATUS_RE.search(ln)), 0)
                after = "\n".join(lines[status_idx : status_idx + 16])
                if "TL;DR" not in after:
                    violations.append(
                        f"plans/{entry.relative_to(DOCS / 'plans')}：缺 TL;DR——状态头之后 15 行内须有 ≤5 行 TL;DR（2026-10-08 约定，只约束新增）"
                    )
            # 校验项 13（pending 时效，非阻断 warning）：定稿超 30 天
            if plans_sub.name == "pending":
                m = DATE_RE.search(entry.name)
                if m and (date.today() - date(int(m.group(1)), int(m.group(2)), int(m.group(0)[8:10]))).days > PENDING_AGE_WARN_DAYS:
                    warnings.append(
                        f"plans/pending/{entry.name}：定稿超 {PENDING_AGE_WARN_DAYS} 天——执行前先 `git diff <基准commit> --stat` 评估漂移面"
                    )

    if not (REPO_ROOT / "CLAUDE.md").is_file():
        violations.append(
            "CLAUDE.md：根 CLAUDE.md 缺失——必须存在且为 @./AGENTS.md 指针"
            "（Claude Code 在仓库根的约定入口，防根会话漏载）"
        )

    for claude_md in [REPO_ROOT / "CLAUDE.md", *sorted(REPO_ROOT.glob("*/CLAUDE.md"))]:
        if not claude_md.is_file() or "node_modules" in claude_md.parts:
            continue
        text = claude_md.read_text(encoding="utf-8", errors="replace")
        if "@./AGENTS.md" not in text and "@AGENTS.md" not in text:
            violations.append(
                f"{claude_md.relative_to(REPO_ROOT)}：CLAUDE.md 必须是 @./AGENTS.md 指针形态"
                "（约定载体归 AGENTS.md，防双轨漂移）"
            )

    # 校验项 10：backend/docs 只放常青笔记与 API 参考
    if BACKEND_DOCS.is_dir():
        for entry in sorted(BACKEND_DOCS.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                if entry.name not in BACKEND_DOCS_DIRS:
                    violations.append(
                        f"backend/docs/{entry.name}/：backend/docs 子目录限 {sorted(BACKEND_DOCS_DIRS)}"
                        "（API 参考系）；新目录须登记进本脚本 BACKEND_DOCS_DIRS"
                    )
                continue
            if entry.suffix.lower() != ".md":
                continue
            if entry.name in BACKEND_DOCS_WHITELIST:
                continue
            if DATE_RE.search(entry.name):
                violations.append(
                    f"backend/docs/{entry.name}：日期前缀文件为一次性产物，应走 docs/archive/YYYY-MM/ 月度归档"
                )
            elif not BACKEND_DOCS_EVERGREEN_RE.match(entry.name):
                violations.append(
                    f"backend/docs/{entry.name}：新增常青文档用大写下划线命名（如 VIDEO_GEN_NOTES.md），"
                    "既有非大写形态须登记进本脚本 BACKEND_DOCS_WHITELIST"
                )

    # 校验项 8：测试落位——backend/frontend 各自门内检查，门外的测试形态文件不判定
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in TEST_SCAN_EXCLUDE_DIRS]
        for name in filenames:
            if not (BACKEND_TEST_FILE_RE.match(name) or FRONTEND_TEST_FILE_RE.match(name)):
                continue
            rel = (Path(dirpath).relative_to(REPO_ROOT) / name).as_posix()
            if rel.startswith("backend/") and BACKEND_TEST_FILE_RE.match(name):
                if rel.startswith("backend/tests/") or rel.startswith(BACKEND_TEST_EXEMPT_PREFIXES):
                    continue
                violations.append(
                    f"{rel}：backend 测试文件须放 backend/tests/（放错目录 pytest 收集不到、CI 假绿）；"
                    "例外须登记进本脚本 BACKEND_TEST_EXEMPT_PREFIXES"
                )
            elif rel.startswith("frontend/") and FRONTEND_TEST_FILE_RE.match(name):
                if rel.startswith(FRONTEND_TEST_EXEMPT_PREFIXES):
                    continue
                if rel.startswith("frontend/tests/e2e/"):
                    continue  # playwright testDir：.test/.spec 均由默认 testMatch 收集
                if rel.startswith(("frontend/tests/unit/", "frontend/src/")) and name.endswith((".test.ts", ".test.tsx")):
                    continue  # vitest 只收 .test.*；.spec 落在这里两门都不收
                violations.append(
                    f"{rel}：frontend 测试文件落位错误——vitest 只收 tests/unit|src/ 下的 .test.ts(x)，"
                    "playwright 只收 tests/e2e/ 下用例；放错位置静默不跑、CI 假绿；"
                    "例外须登记进本脚本 FRONTEND_TEST_EXEMPT_PREFIXES"
                )

    # 校验项 16：findings-backlog 条目行格式（阻断）
    backlog = DOCS / "findings-backlog.md"
    if backlog.is_file():
        for lineno, line in enumerate(backlog.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ">")):
                continue
            if not BACKLOG_ENTRY_RE.match(stripped):
                violations.append(
                    f"docs/findings-backlog.md:{lineno}：条目行须为 `- [ ] YYYY-MM-DD 一句话 + 指针` 形态（2026-10-12 约定）"
                )

    # 校验项 11：服务器红线关键词唯一来源（非阻断 warning，不改退出码）
    for keyword, authority in SERVER_KEYWORD_AUTHORITY.items():
        for doc in SERVER_DOCS:
            if doc == authority:
                continue
            path = DOCS / doc
            if not path.is_file():
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if keyword in line and authority not in line:
                    warnings.append(
                        f"docs/{doc}:{lineno}：含红线关键词 {keyword!r} 但非指针行——"
                        f"该事实应只在 {authority} 陈述，此处应改为指针行"
                    )

    # 校验项 14：二进制禁区门禁（CI 传入 diff range 时生效；本地裸跑未设环境变量即静默跳过）
    base_sha = os.environ.get("CONVENTION_BASE_SHA")
    head_sha = os.environ.get("CONVENTION_HEAD_SHA")
    if base_sha and head_sha:
        check_binary_zone(base_sha, head_sha, violations)

    # 校验项 15：AGENTS.md 禁区节 ↔ rule.json 一致性（纯状态检查，本地/CI 都跑）
    check_forbidden_zone_sync(violations)

    # 校验项 19：AGENTS.md 核心路径清单 ↔ check_main_push_paths.py 一致性（纯状态检查）
    check_core_path_sync(violations)

    for item in warnings:
        print(f"WARNING {item}")

    if not violations:
        print(
            "OK 仓库治理约定全过"
            "（docs 命名/位置/月度归档/根目录白名单/CLAUDE 指针/测试落位/plan 状态头/"
            "backend/docs 形态/二进制禁区门禁/禁区规则镜像一致性）"
        )
        return 0

    print(f"FAIL 仓库治理约定违例 {len(violations)} 项：")
    for item in violations:
        print(f"  - {item}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
