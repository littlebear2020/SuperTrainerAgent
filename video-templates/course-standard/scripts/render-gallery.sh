#!/usr/bin/env bash
# 渲染 19 版式 × 4 主题全量静帧（frame=60，动效已落定）。
# 用法：bash scripts/render-gallery.sh [outdir]  （默认 out/）
# 口径与 M0 基线一致：1920×1080 @30fps，Composition 150 帧取第 60 帧。
# 注：文件名直接预置 kebab-case（避开 macOS bash3/sed 方言差异）。
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${1:-out}"
# CompositionId:png文件名（与 M0 基线命名一致）
LAYOUTS=(
  CoverPage:cover-page
  ObjectivesPage:objectives-page
  OutlinePage:outline-page
  ChapterDivider:chapter-divider
  ConceptPage:concept-page
  ImageTextLeft:image-text-left
  ImageTextRight:image-text-right
  FullVisualPage:full-visual-page
  VideoClipPage:video-clip-page
  ComparisonPage:comparison-page
  ChartPage:chart-page
  ProcessPage:process-page
  CodePage:code-page
  QuotePage:quote-page
  CasePage:case-page
  SummaryPage:summary-page
  QuizPage:quiz-page
  EndingPage:ending-page
  EndCard:end-card
)
THEMES=(dark-blue light dark-green warm)

mkdir -p "$OUT"
for theme in "${THEMES[@]}"; do
  mkdir -p "$OUT/$theme"
  for pair in "${LAYOUTS[@]}"; do
    layout="${pair%%:*}"
    file="${pair##*:}"
    target="$OUT/$theme/$file.png"
    if [[ -f "$target" ]]; then
      echo "skip $target (exists)"
      continue
    fi
    echo "render ${layout}-${theme} -> $target"
    npx remotion still "src/index.ts" "${layout}-${theme}" "$target" --frame=60 --log=error
  done
done
echo "gallery done: $(find "$OUT" -name '*.png' | wc -l | tr -d ' ') stills"
