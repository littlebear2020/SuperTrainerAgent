#!/usr/bin/env bash
# ⚠️ 已作废（2026-10-12 花名册批 supersede，勿重放）：重放会把已删模型加回、把
# config_version 改小——现行增量走 scripts/apply_config_increments_2026-10-12.py。
# ============================================================================
# config.yaml 增量脚本（2026-09-23 长任务稳定性批，计划 §五）
#
# 用法：
#   ./scripts/apply_config_increments_2026-09-23.sh --target test  [--dry-run]
#   ./scripts/apply_config_increments_2026-09-23.sh --target prod  [--dry-run]
#   ./scripts/apply_config_increments_2026-09-23.sh --target test  --config /path/config.yaml
#
# 两栏值（计划 §五配置增量表）：
#   ─────────────────────────────┬──────────────┬──────────────
#   配置项                        │ 生产默认      │ 测试机覆盖
#   ─────────────────────────────┼──────────────┼──────────────
#   config_version                │ 21           │ 21  ← 并入脚本（review #7，
#   │             │        任何起始值改写到 21；说明见下方 upsert 处注释）
#   empty_response_max_retries    │ 3            │ 3
#   empty_response_cooldown_base  │ 2.0          │ 2.0
#   empty_response_cooldown_max   │ 8.0          │ 8.0
#   empty_response_trim_on_retry  │ true         │ true
#   sandbox_failure_circuit_breaker_threshold │ 5 │ 5
#   disk_min_free_bytes_block     │ 5 GiB        │ 2 GiB  ← 唯一分叉
#   disk_warn_free_ratio          │ 0.15         │ 0.15
#   token_budget_warn_tokens      │ 12M 三任务   │ 12M 三任务
#   subagents.custom_agents 三定义 │ 移除         │ 移除
#   ─────────────────────────────┴──────────────┴──────────────
#
# 幂等性：重复执行输出不变（值已到位则原地改写同值；键缺失则文件末尾追加；
# custom_agents 块已删则跳过）。--dry-run 打印 diff 不落盘。
#
# 兼容性（2026-09-23 review #2）：目标机（测试机/生产）均为 Linux（GNU sed），
# 故 sed 原地改写统一用 `sed -i.bak … && rm -f *.bak`（GNU/BSD 双兼容），
# 不用 BSD 专属的 `sed -i '' -E`（GNU 会把 '' 当空脚本、s 表达式当文件名报错）。
#
# 注意：config.yaml 为 gitignored 手改文件（K 系列手册口径）；本脚本不碰
# 其余内容（custom_agents / token_budget_warn_tokens 块删除只按缩进边界裁剪）。
# flat 键的原地改写是整行替换——会丢弃行尾注释（如 `disk_min_free_bytes_block:
# …  # 生产 5GiB`）；这些键的权威值即本脚本两栏表，注释丢失不影响语义。
# alembic upgrade（empty_fallback_count 迁移）与 gateway 重启是上线 checklist
# 独立步骤，不在本脚本范围。
# ============================================================================
set -euo pipefail

TARGET=""
DRY_RUN=0
CONFIG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="${2:-}"; shift 2 ;;
    --config) CONFIG="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

if [[ "$TARGET" != "prod" && "$TARGET" != "test" ]]; then
  echo "用法: $0 --target {prod|test} [--config <config.yaml>] [--dry-run]" >&2
  exit 2
fi
if [[ -z "$CONFIG" ]]; then
  CONFIG="$(cd "$(dirname "$0")/.." && pwd)/config.yaml"
fi
if [[ ! -f "$CONFIG" ]]; then
  echo "config.yaml 不存在: $CONFIG" >&2
  exit 2
fi

# ── 两栏值 ──────────────────────────────────────────────────────────────────
if [[ "$TARGET" == "prod" ]]; then
  DISK_BLOCK=5368709120   # 5 GiB
else
  DISK_BLOCK=2147483648   # 2 GiB（64G 小盘测试机）
fi

WORK="$(mktemp "${TMPDIR:-/tmp}/cfg-inc.XXXXXX")"
trap 'rm -f "$WORK"' EXIT
cp "$CONFIG" "$WORK"

# upsert_flat <key> <value> —— 顶层标量键：存在则原地改写，缺失则文件末尾追加。
upsert_flat() {
  local key="$1" value="$2"
  if grep -qE "^[[:space:]]*${key}:" "$WORK"; then
    # 原地整行改写（GNU/BSD 双兼容的 -i.bak 形态）。
    # 注意：整行替换会丢弃行尾注释——这些键的权威值即本脚本两栏表。
    sed -i.bak -E "s|^[[:space:]]*${key}:.*|${key}: ${value}|" "$WORK" && rm -f "${WORK}.bak"
  else
    printf '\n%s: %s\n' "$key" "$value" >> "$WORK"
  fi
}

# remove_block <line_regex> <parent_pos> —— 按缩进边界整块删除（幂等）。
# 命中 line_regex 的行起，删除其后所有空行与缩进更深（首个非空白列 >
# parent_pos）的行，直到缩进回到不深于父行的首个行为止（不吞后续兄弟键）。
# parent_pos = 父行首个非空白的 1-based 列号（顶层键=1；custom_agents 缩进 2 → 3）。
remove_block() {
  local line_re="$1" parent_pos="$2"
  awk -v line_re="$line_re" -v parent_pos="$parent_pos" '
    BEGIN { skipping = 0 }
    {
      if (skipping) {
        # 空行或更深缩进 → 仍属于块内
        if ($0 ~ /^[[:space:]]*$/) { next }
        line_indent = match($0, /[^ ]/);
        if (line_indent > parent_pos) { next }
        skipping = 0
        # 落回浅缩进行 → 正常输出（不吞兄弟键）
      }
      if (!skipping && $0 ~ line_re) {
        skipping = 1
        next
      }
      if (!skipping) { print }
    }
  ' "$WORK" > "${WORK}.new" && mv "${WORK}.new" "$WORK"
}

upsert_flat empty_response_max_retries 3
upsert_flat empty_response_cooldown_base_seconds 2.0
upsert_flat empty_response_cooldown_max_seconds 8.0
upsert_flat empty_response_trim_on_retry true
upsert_flat sandbox_failure_circuit_breaker_threshold 5
upsert_flat disk_min_free_bytes_block "$DISK_BLOCK"
upsert_flat disk_warn_free_ratio 0.15

# token_budget_warn_tokens（review #5）：目标机可能已有块形态（从新版
# config.example.yaml 拷贝，或 make config-upgrade 合并产物——example 中该键
# 即 3 行块式）。upsert_flat 只改写命中的首行，块形态会残留孤儿缩进行 →
# YAML ParserError（gateway 起不来）。故块形态先按缩进边界整块移除再写单行；
# flow 形态（行内带值）无需移除，upsert_flat 原地改写即幂等。
if grep -qE '^token_budget_warn_tokens:[[:space:]]*$' "$WORK"; then
  remove_block '^token_budget_warn_tokens:[[:space:]]*$' 1
fi
# 单行 flow mapping（YAML 合法且 sed 友好）
upsert_flat token_budget_warn_tokens '{default: 12000000, ppt-master: 12000000, course-video: 12000000}'

# config_version（review #7，Q3 定稿）：并入脚本，运维免一步易漏的手改。
# 任何起始值（19/20/21）一律改写到 21——启动时 AppConfig 会比对新旧版本并
# 告警 outdated，保持与 example 一致。
upsert_flat config_version 21

# ── 移除 subagents.custom_agents 三定义（D2）───────────────────────────────
# 按缩进边界裁剪（remove_block 泛化版）：命中 `  custom_agents:`（subagents
# 直下二级键，parent_pos=3）后整块删除，不吞兄弟键。幂等：块不存在则跳过。
remove_block '^  custom_agents:[[:space:]]*$' 3

# ── 结果呈现 / 落盘 ─────────────────────────────────────────────────────────
if [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] target=$TARGET config=$CONFIG —— 变更预览："
  diff -u "$CONFIG" "$WORK" || true
  echo "[dry-run] 未落盘。确认无误后去掉 --dry-run 实跑。"
else
  if diff -q "$CONFIG" "$WORK" >/dev/null 2>&1; then
    echo "config.yaml 已是目标状态（幂等，无变更）。"
  else
    cp "$WORK" "$CONFIG"
    echo "已写入 ${CONFIG}（target=${TARGET}）。生效需 gateway 重启（D2 三子代理移除亦然）。"
  fi
fi
