#!/usr/bin/env bash
# ⚠️ 已作废（2026-10-12 花名册批 supersede，勿重放）：重放会把已删模型加回、把
# config_version 改小——现行增量走 scripts/apply_config_increments_2026-10-12.py。
# ============================================================================
# config.yaml 增量脚本（2026-09-24 稳定性批整改 R2，计划 §三 / §十 Q2）
#
# 用法：
#   ./scripts/apply_config_increments_2026-09-24.sh [--config /path/config.yaml] [--dry-run]
#
# 变更内容（两项）：
#   1. models[] 列表中 gpt-6-luna / gpt-6-sol 两个条目各新增一行：
#       reasoning_effort_none_with_tools: true
#      作用：360 网关 gpt-6 带 function tools 时对任何非 none 的
#      reasoning_effort 一律 400（DEF-01'）——该 flag 让 PatchedChatOpenAI 在
#      请求体带 tools 时把顶层与 extra_body 两处 effort 强制为 none。
#   2. 同两条目摘除 temperature: 0.7 —— gpt-6 不支持非默认 temperature
#      （spike 2026-09-24 实测 400 "Only the default (1) value is supported"，
#      09-23 批模板带入的隐形阻断）。
#
# 幂等性：重复执行输出不变（键已存在则原地改写同值；缺失则插入条目末行）。
# 定位方式：按 `- name: <model>` 锚定（各环境行号有偏移，不依赖行号）。
# 回滚位：roster 摘 gpt-6 两条（纯 config 操作）；或本脚本无逆删除需求时
#   手工删掉两行即回到原状。
# 生效：需 gateway 重启（models 段虽 mtime 热加载，但 agent 构建在 run 级
#   取 ModelConfig，重启最稳）。
#
# 注意：config.yaml 为 gitignored 手改文件（K 系列手册口径）；本脚本不碰
# 其余内容。与 apply_config_increments_2026-09-23.sh 若都需执行，必须串行
# （整文件拷贝覆盖，并行互毁）。
# ============================================================================
set -euo pipefail

CONFIG=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$CONFIG" ]]; then
  CONFIG="$(cd "$(dirname "$0")/.." && pwd)/config.yaml"
fi
if [[ ! -f "$CONFIG" ]]; then
  echo "config.yaml 不存在: $CONFIG" >&2
  exit 2
fi

WORK="$(mktemp "${TMPDIR:-/tmp}/cfg-inc-0924.XXXXXX")"
trap 'rm -f "$WORK"' EXIT
cp "$CONFIG" "$WORK"

# upsert_model_entry_field <model_name> <key> <value>
#   按 `- name: <model_name>` 锚定列表条目（models[] 条目顶格、键 2 空格缩进
#   的本地 config 形态），在条目内 upsert 单个标量键：存在则原地整行改写，
#   缺失则插到条目末行。条目边界 = 下一个 `- name:` / 任意顶格行 / 空行。
#   （09-23 脚本的 upsert_flat 只管顶层标量键，不适用列表条目——Q7 要求的
#   列表条目处理函数即此。）
upsert_model_entry_field() {
  local model_name="$1" key="$2" value="$3"
  awk -v entry_re="^- name: ${model_name}[[:space:]]*$" -v key="$key" -v value="$value" '
    BEGIN { in_entry = 0; n = 0; key_done = 0 }
    {
      if (in_entry) {
        if ($0 ~ /^- name:/ || $0 ~ /^[[:space:]]*$/ || $0 ~ /^[^[:space:]#]/) {
          if (!key_done) arr[n++] = "  " key ": " value
          for (i = 0; i < n; i++) print arr[i]
          n = 0; in_entry = 0
          # 落回边界行的正常处理（下方统一 print）
        } else {
          if ($0 ~ ("^  " key ":")) { arr[n++] = "  " key ": " value; key_done = 1; next }
          arr[n++] = $0; next
        }
      }
      if (!in_entry && $0 ~ entry_re) { in_entry = 1; key_done = 0; n = 0; print; next }
      print
    }
    END {
      if (in_entry) {
        if (!key_done) arr[n++] = "  " key ": " value
        for (i = 0; i < n; i++) print arr[i]
      }
    }
  ' "$WORK" > "${WORK}.new" && mv "${WORK}.new" "$WORK"
}

# remove_model_entry_field <model_name> <key>
#   条目内删除单个键（幂等：键不存在则跳过）。条目边界同上——边界必须
#   复位 in_entry，否则会把后续所有条目的同名字段一并误删。
remove_model_entry_field() {
  local model_name="$1" key="$2"
  awk -v entry_re="^- name: ${model_name}[[:space:]]*$" -v key_re="^  ${key}:" '
    BEGIN { in_entry = 0 }
    {
      if (!in_entry) {
        if ($0 ~ entry_re) { in_entry = 1 }
        print
        next
      }
      if ($0 ~ /^- name:/ || $0 ~ /^[^[:space:]#]/) { in_entry = 0; print; next }
      if ($0 ~ key_re) { next }
      print
    }
  ' "$WORK" > "${WORK}.new" && mv "${WORK}.new" "$WORK"
}

upsert_model_entry_field gpt-6-luna reasoning_effort_none_with_tools true
upsert_model_entry_field gpt-6-sol reasoning_effort_none_with_tools true

# gpt-6 不支持非默认 temperature（spike 2026-09-24 实测 400：只支持默认 1）——
# 09-23 批模板带入的 temperature: 0.7 是隐形阻断，摘除恢复默认。
remove_model_entry_field gpt-6-luna temperature
remove_model_entry_field gpt-6-sol temperature

# ── 结果呈现 / 落盘 ─────────────────────────────────────────────────────────
if [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] config=$CONFIG —— 变更预览："
  diff -u "$CONFIG" "$WORK" || true
  echo "[dry-run] 未落盘。确认无误后去掉 --dry-run 实跑。"
else
  if diff -q "$CONFIG" "$WORK" >/dev/null 2>&1; then
    echo "config.yaml 已是目标状态（幂等，无变更）。"
  else
    # 落盘前 best-effort YAML 语法校验：优先 backend venv，退回带 yaml 的
    # python3；两者皆不可用则跳过校验（09-23 脚本同款行为）。
    # 注意根 python3 可能无 yaml 模块——「无校验器」≠「解析失败」，分开判。
    VALIDATOR=""
    if [[ -x "$(cd "$(dirname "$0")/.." && pwd)/backend/.venv/bin/python" ]]; then
      VALIDATOR="$(cd "$(dirname "$0")/.." && pwd)/backend/.venv/bin/python"
    elif python3 -c "import yaml" 2>/dev/null; then
      VALIDATOR="python3"
    fi
    if [[ -n "$VALIDATOR" ]] && ! "$VALIDATOR" -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" "$WORK" 2>/dev/null; then
      echo "校验失败：改写产物不是合法 YAML，已放弃落盘（原文件未动）。" >&2
      diff -u "$CONFIG" "$WORK" || true
      exit 1
    fi
    cp "$WORK" "$CONFIG"
    if [[ -n "$VALIDATOR" ]]; then
      echo "已写入 ${CONFIG}（gpt-6 两条目 +reasoning_effort_none_with_tools: true，YAML 校验通过）。生效需 gateway 重启。"
    else
      echo "已写入 ${CONFIG}（gpt-6 两条目 +reasoning_effort_none_with_tools: true；未做 YAML 校验——本机无可用校验器）。生效需 gateway 重启。"
    fi
  fi
fi
