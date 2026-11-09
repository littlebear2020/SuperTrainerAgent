#!/bin/bash
# sync-remotion-skills.sh — 从 GitHub 重新 vendor remotion-dev/skills 到
# skills/public/remotion-official/ 并更新 VERSION 戳（2026-09-08 T2）。
#
# 用法（需要有网环境；离线生产服务器只消费仓库内副本，不要跑本脚本）：
#   scripts/sync-remotion-skills.sh [ref]
#     ref: 上游 git ref（tag/branch/commit），默认 main。
#
# 只会同步计划纳入的 5 个 skill（不装 best-practices 路由包——上游 issue #24
# 安装后子链接失效）：
#   remotion-markup / remotion-captions / remotion-create / remotion-render / remotion-docs
set -euo pipefail

REF="${1:-main}"
REPO="https://github.com/remotion-dev/skills.git"
TARGET_DIR="skills/public/remotion-official"
SKILLS_TO_VENDOR=(remotion-markup remotion-captions remotion-create remotion-render remotion-docs)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR" "${STAGING:-}"' EXIT

echo "== Cloning remotion-dev/skills @ $REF =="
git clone --depth 1 --branch "$REF" "$REPO" "$TMP_DIR/skills-repo" 2>/dev/null || {
  # --branch 不支持 commit sha 时退化为全浅克后 checkout
  git clone --depth 1 "$REPO" "$TMP_DIR/skills-repo"
  git -C "$TMP_DIR/skills-repo" fetch --depth 1 origin "$REF" || true
  git -C "$TMP_DIR/skills-repo" checkout "$REF"
}

UPSTREAM_COMMIT="$(git -C "$TMP_DIR/skills-repo" rev-parse HEAD)"
UPSTREAM_VERSION="$(python3 -c "import json;print(json.load(open('$TMP_DIR/skills-repo/package.json'))['version'])" 2>/dev/null || echo unknown)"
echo "   upstream commit: $UPSTREAM_COMMIT"
echo "   upstream version: $UPSTREAM_VERSION"

echo "== Verifying upstream completeness =="
# 先校验上游完整，再动 $TARGET_DIR——否则缺 skill 时"未改动"是假话
for skill in "${SKILLS_TO_VENDOR[@]}"; do
  if [ ! -d "$TMP_DIR/skills-repo/skills/$skill" ]; then
    echo "!! 上游缺少 skills/$skill —— 中止，未改动 $TARGET_DIR" >&2
    exit 1
  fi
done

echo "== Replacing $TARGET_DIR (staged, atomic) =="
# 先复制到暂存目录再整体 mv——cp 中途失败/中断不会留下半个 $TARGET_DIR
STAGING="$TARGET_DIR.new"
rm -rf "$STAGING"
mkdir -p "$STAGING"
for skill in "${SKILLS_TO_VENDOR[@]}"; do
  cp -R "$TMP_DIR/skills-repo/skills/$skill" "$STAGING/$skill"
  echo "   vendored: $skill"
done

cat > "$STAGING/VERSION" <<EOF
upstream: https://github.com/remotion-dev/skills (remotion monorepo, Remotion License)
version: $UPSTREAM_VERSION
commit: $UPSTREAM_COMMIT
vendored_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
skills: ${SKILLS_TO_VENDOR[*]}
note: best-practices 路由包未 vendor（上游 issue #24 安装后子链接失效）
EOF

rm -rf "$TARGET_DIR"
mv "$STAGING" "$TARGET_DIR"

echo "== Done. VERSION:"
cat "$TARGET_DIR/VERSION"
echo ""
echo "提醒：模板 remotion 版本须与规则版本对齐（当前模板见"
echo "skills/public/auto-video-flow/templates/remotion-base/package.json）；"
echo "若上游版本前进，同步升级模板 remotion 三件套并重建 sandbox 镜像（K16）。"
