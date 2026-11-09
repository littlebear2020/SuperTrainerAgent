---
name: chart-visualization
description: 当用户需要数据可视化时使用此技能。智能选择图表类型（支持 13 种常见图表），根据详细规格提取参数，通过本地 Python 脚本（matplotlib/seaborn）离线渲染为 PNG 图片，无需联网。
compatibility:
  python: ">=3.9"
---

# Chart Visualization Skill

This skill transforms data into chart images with a fully **local, offline** renderer (`scripts/chart.py`, matplotlib/seaborn). No external SaaS or network access is involved; the output is a PNG file on disk that can be embedded into docx/pdf or shown to the user directly.

## Workflow

To visualize data, follow these steps:

### 1. Intelligent Chart Selection

Analyze the user's data features and pick the most appropriate **supported** chart type:

- **Time Series**: `generate_line_chart` (trends), `generate_area_chart` (accumulated trends, `stack=true` for part-to-whole over time). Use `generate_dual_axes_chart` for two series with different scales (e.g. 营收柱状 + 利润率折线).
- **Comparisons**: `generate_column_chart` (vertical bars) or `generate_bar_chart` (horizontal bars, good for Top-N ranking). Use `generate_histogram_chart` for frequency distributions, `generate_heatmap_chart` for intensity over two category axes (e.g. 星期×时段).
- **Part-to-Whole**: `generate_pie_chart` (`innerRadius>0` for donut) or `generate_funnel_chart` (process stages / conversion).
- **Relationships**: `generate_scatter_chart` (correlation between two continuous variables).
- **Multi-dimensional**: `generate_radar_chart` (4~8 dimensions).
- **Statistical Distribution**: `generate_boxplot_chart` or `generate_violin_chart`.

### 2. Parameter Extraction

Once a chart type is selected, read the corresponding file in `references/` (e.g. `references/generate_line_chart.md`) for required and optional fields, then map the user's data into the `args` format.

Common optional fields across chart types: `title`, `width` (default 600), `height` (default 400), `axisXTitle`, `axisYTitle`, `theme` (`default`/`academy`/`dark`), `style.backgroundColor`, `style.palette` (color list), `style.lineWidth` (line/area/radar). `style.texture` is **not supported** by the local renderer and is ignored with a warning.

### 3. Chart Generation

Invoke `scripts/chart.py` with a spec and an output path.

**Spec Format**（与旧版 generate.js 的 payload 口径一致）:
```json
{
  "tool": "generate_column_chart",
  "args": {
    "data": [{"category": "一季度", "value": 120}, {"category": "二季度", "value": 180}],
    "title": "季度销售额",
    "axisXTitle": "季度",
    "axisYTitle": "销售额（万元）"
  }
}
```

**Execution Command:**
```bash
python /mnt/skills/public/chart-visualization/scripts/chart.py \
  --spec '<spec_json_or_path_to_json_file>' \
  --out /mnt/user-data/outputs/chart.png
```

- `--spec` accepts an inline JSON string or a path to a `.json` file.
- The spec may also be an **array** of spec items; outputs are then written as `<out_stem>_1.png`, `<out_stem>_2.png`, ...
- On success the script prints the output PNG path(es) to stdout, one per line. Errors (including unsupported chart types) go to stderr and the exit code is non-zero — always check stderr before reporting success.
- Chinese labels work out of the box: the script selects a CJK font automatically (`Noto Sans CJK SC`, falling back to `WenQuanYi Zen Hei` / `PingFang SC` / `SimHei`). If none is found it prints a WARNING to stderr — surface that to the user instead of delivering a chart full of tofu boxes.

### 4. Result Return

Return the following to the user:
- The local PNG file path (embeddable in docx/pdf via the corresponding skills).
- The complete `args` (specification) used for generation.

## Unsupported Chart Types

**Map charts are NOT supported offline**: `generate_district_map`, `generate_path_map`, `generate_pin_map`. Summarize geographic data in a table or a `generate_bar_chart` instead, and tell the user why.

Long-tail chart types from the legacy 26-type catalog are also unavailable locally. When the user asks for one, degrade gracefully — pick the closest supported type, state the substitution explicitly, and proceed:

| 需求（旧类型） | 本地替代方案 |
| --- | --- |
| `generate_treemap_chart` 矩形树图 | `generate_pie_chart` 或 `generate_bar_chart` |
| `generate_word_cloud_chart` 词云 | `generate_bar_chart`（Top-N 词频） |
| `generate_liquid_chart` 水波图 | `generate_pie_chart`（[已完成, 剩余]） |
| `generate_sankey_chart` 桑基图 | `generate_funnel_chart` 或分组 `generate_bar_chart` |
| `generate_venn_chart` 韦恩图 | `generate_pie_chart` |
| `generate_network_graph` / `generate_mind_map` / `generate_organization_chart` / `generate_fishbone_diagram` / `generate_flow_diagram` | 本地不渲染结构图；改用文字大纲，或使用 trainerai-diagram 类图示技能 |
| `generate_spreadsheet` 表格 | Markdown 表格或 xlsx 技能 |

## Reference Material

Detailed per-type specs live in `references/` (13 files, one per supported chart type, including `generate_heatmap_chart.md`). Consult them to ensure `args` matches the expected schema.
