#!/usr/bin/env bash
# 与 M0 基线（.baseline-m0/）逐张 diff。用法：bash scripts/diff-gallery.sh [outdir]
# 判定：ImageMagick compare AE（不同像素数）= 0 视为一致；报告每张的 AE 值。
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${1:-out}"
BASE=".baseline-m0"
total=0
pass=0
fail=0
for base_png in "$BASE"/*/*.png; do
  rel="${base_png#"$BASE"/}"
  new="$OUT/$rel"
  total=$((total + 1))
  if [[ ! -f "$new" ]]; then
    echo "MISSING $rel"
    fail=$((fail + 1))
    continue
  fi
  ae=$(compare -metric AE "$base_png" "$new" /dev/null 2>&1 || true)
  ae_num=$(echo "$ae" | awk '{print $1}')
  if [[ "$ae_num" == "0" ]]; then
    pass=$((pass + 1))
  else
    echo "DIFF $rel AE=$ae"
    fail=$((fail + 1))
  fi
done
echo "diff summary: total=$total pass=$pass fail=$fail"
[[ $fail -eq 0 ]]
