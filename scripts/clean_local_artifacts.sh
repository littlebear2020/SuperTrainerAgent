#!/usr/bin/env bash
# 本地产物清理（temp/ test-output/ logs/ + backend/.deer-flow 体积报告）。
# 默认 dry-run（只报告不删除）；--apply 才真正删除；--days N 设置保留天数（默认 30）。
# backend/.deer-flow 是本地运行数据（含 DB/上传/workspace），本脚本只报告体积、
# 永不自动删除；确认要清请手动按目录处理。

set -euo pipefail

APPLY=0
DAYS=30
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --days) DAYS="$2"; shift ;;
    *) echo "未知参数：$1（用法：$0 [--apply] [--days N]）" >&2; exit 2 ;;
  esac
  shift
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "===== 体积现状 ====="
for d in temp test-output logs backend/.deer-flow; do
  if [ -d "$ROOT/$d" ]; then
    size=$(du -sh "$ROOT/$d" | cut -f1)
    printf "  %-28s %s\n" "$d" "$size"
  fi
done

echo ""
echo "===== backend/.deer-flow 分解（只报告，不自动清）====="
if [ -d "$ROOT/backend/.deer-flow" ]; then
  du -sk "$ROOT"/backend/.deer-flow/* 2>/dev/null | sort -rn | head -10 | while IFS=$'\t' read -r kb path; do
    size=$(du -sh "$path" | cut -f1)
    printf "  %-40s %s\n" "${path#"$ROOT"/}" "$size"
  done
fi

echo ""
echo "===== 超过 ${DAYS} 天未修改的候选（temp/ test-output/ logs/）====="
FOUND=0
for d in temp test-output logs; do
  [ -d "$ROOT/$d" ] || continue
  while IFS= read -r -d '' f; do
    FOUND=1
    if [ "$APPLY" -eq 1 ]; then
      rm -rf "$f"
      echo "  已删 ${f#"$ROOT"/}"
    else
      echo "  候选 ${f#"$ROOT"/}"
    fi
  done < <(find "$ROOT/$d" -mindepth 1 -maxdepth 1 -mtime +"$DAYS" -print0)
done
[ "$FOUND" -eq 0 ] && echo "  （无）"

LOG_MB=200
echo ""
echo "===== 大日志轮转（logs/ 单文件 ≥ ${LOG_MB}M）====="
ROTATED=0
for f in "$ROOT"/logs/*.log; do
  [ -f "$f" ] || continue
  size_mb=$(( $(stat -f %z "$f") / 1048576 ))
  [ "$size_mb" -lt "$LOG_MB" ] && continue
  ROTATED=1
  if [ "$APPLY" -eq 1 ]; then
    mv "$f" "$f.$(date +%Y%m%d)" && gzip "$f.$(date +%Y%m%d)"
    echo "  已轮转 ${f#"$ROOT"/}（${size_mb}M → $(basename "$f").$(date +%Y%m%d).gz）"
  else
    echo "  候选 ${f#"$ROOT"/}（${size_mb}M）"
  fi
done
[ "$ROTATED" -eq 0 ] && echo "  （无）"
# 轮转后通知本机 nginx 重开日志（在跑才发；mv 后旧 fd 仍写旧 inode，必须 reopen）
if [ "$APPLY" -eq 1 ] && [ "$ROTATED" -eq 1 ] && pgrep -f "nginx: master" >/dev/null 2>&1; then
  pkill -USR1 -f "nginx: master" && echo "已通知 nginx 重开日志（USR1）"
fi

echo ""
if [ "$APPLY" -eq 1 ]; then
  echo "已执行清理（--apply）。"
else
  echo "dry-run 未删除任何文件。确认无误后加 --apply 执行；保留天数用 --days N 调整。"
fi
