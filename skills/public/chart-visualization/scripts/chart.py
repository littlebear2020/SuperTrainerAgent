"""
Local chart rendering script using matplotlib/seaborn.

Renders JSON chart specs to PNG files entirely offline — no external SaaS.
The spec format stays compatible with the legacy generate.js payloads:
{"tool": "generate_<type>_chart", "args": {...}} (or an array of such items).

Usage:
    python chart.py --spec '<json_or_file>' --out /path/to/chart.png
"""

import argparse
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless rendering, no display server required

from matplotlib import font_manager  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402

try:
    import seaborn  # noqa: E402
except ImportError:  # seaborn is cosmetic-only; plain matplotlib still works
    seaborn = None

# --- CJK font setup ----------------------------------------------------------
# matplotlib's default DejaVu Sans has no CJK glyphs; without an explicit CJK
# font every Chinese label renders as tofu boxes. The sandbox image ships
# fonts-noto-cjk, so prefer Noto and fall back through common CJK fonts.
CJK_FONT_CANDIDATES = [
    "Noto Sans CJK SC",
    "WenQuanYi Zen Hei",
    "PingFang SC",
    "SimHei",
]


def _select_cjk_font() -> str:
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in CJK_FONT_CANDIDATES:
        if name in available:
            return name
    print(
        "WARNING: no CJK font found (tried: %s); "
        "Chinese text may render as empty boxes." % ", ".join(CJK_FONT_CANDIDATES),
        file=sys.stderr,
    )
    return CJK_FONT_CANDIDATES[0]


CJK_FONT = _select_cjk_font()
matplotlib.rcParams["font.family"] = CJK_FONT
matplotlib.rcParams["axes.unicode_minus"] = False

DEFAULT_PALETTE = [
    "#5B8FF9",
    "#61DDAA",
    "#65789B",
    "#F6BD16",
    "#7262FD",
    "#78D3F8",
    "#9661BC",
    "#F6903D",
    "#008685",
    "#F08BB4",
]

THEME_STYLES = {"default": "whitegrid", "academy": "ticks", "dark": "darkgrid"}


# --- shared helpers ------------------------------------------------------------


def _apply_theme(args: dict) -> None:
    """Apply theme/style options. Must run before figure creation."""
    theme = args.get("theme") or "default"
    style = args.get("style") or {}

    if seaborn is not None:
        seaborn.set_theme(style=THEME_STYLES.get(theme, "whitegrid"))

    if theme == "dark":
        matplotlib.rcParams.update(
            {
                "figure.facecolor": "#1f1f1f",
                "axes.facecolor": "#1f1f1f",
                "savefig.facecolor": "#1f1f1f",
                "text.color": "#e6e6e6",
                "axes.labelcolor": "#e6e6e6",
                "axes.edgecolor": "#888888",
                "xtick.color": "#cccccc",
                "ytick.color": "#cccccc",
            }
        )

    background = style.get("backgroundColor")
    if background:
        matplotlib.rcParams.update(
            {
                "figure.facecolor": background,
                "axes.facecolor": background,
                "savefig.facecolor": background,
            }
        )

    # seaborn.set_theme() resets rcParams — re-assert the CJK font afterwards.
    matplotlib.rcParams["font.family"] = CJK_FONT
    matplotlib.rcParams["axes.unicode_minus"] = False

    if style.get("texture") not in (None, "default"):
        print(
            "WARNING: style.texture is not supported by the local renderer; ignored.",
            file=sys.stderr,
        )


def _palette(args: dict, n: int) -> list:
    custom = (args.get("style") or {}).get("palette")
    base = [c for c in (custom or DEFAULT_PALETTE) if c]
    if not base:
        base = DEFAULT_PALETTE
    return [base[i % len(base)] for i in range(max(1, n))]


def _line_width(args: dict) -> float:
    raw = (args.get("style") or {}).get("lineWidth")
    try:
        return float(raw) if raw else 2.0
    except (TypeError, ValueError):
        return 2.0


def _new_figure(args: dict, polar: bool = False):
    width = float(args.get("width") or 600)
    height = float(args.get("height") or 400)
    fig = plt.figure(figsize=(width / 100.0, height / 100.0), dpi=100)
    ax = fig.add_subplot(111, projection="polar" if polar else None)
    return fig, ax


def _finish(fig, ax, args: dict, out_path: Path, legend: bool = False) -> None:
    if args.get("title"):
        ax.set_title(str(args["title"]))
    if args.get("axisXTitle") and not isinstance(ax, matplotlib.projections.polar.PolarAxes):
        ax.set_xlabel(str(args["axisXTitle"]))
    if args.get("axisYTitle") and not isinstance(ax, matplotlib.projections.polar.PolarAxes):
        ax.set_ylabel(str(args["axisYTitle"]))
    if legend:
        ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, format="png")
    plt.close(fig)


def _require_data(args: dict) -> list:
    data = args.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("args.data must be a non-empty array")
    return data


def _unique(values) -> list:
    seen = []
    for v in values:
        if v not in seen:
            seen.append(v)
    return seen


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _pivot_category_group(data: list):
    """Pivot [{category, value, group?}] into categories/groups/lookup."""
    has_group = any("group" in d for d in data)
    categories = _unique(d.get("category") for d in data)
    groups = _unique(d.get("group") for d in data) if has_group else []
    values = {}
    for d in data:
        key = (d.get("group") if has_group else None, d.get("category"))
        values[key] = values.get(key, 0.0) + _to_float(d.get("value"))
    return categories, groups, values, has_group


def _pivot_time_group(data: list):
    """Pivot [{time, value, group?}] into times/groups/lookup."""
    has_group = any("group" in d for d in data)
    times = _unique(d.get("time") for d in data)
    groups = _unique(d.get("group") for d in data) if has_group else []
    values = {}
    for d in data:
        key = (d.get("group") if has_group else None, d.get("time"))
        values[key] = values.get(key, 0.0) + _to_float(d.get("value"))
    return times, groups, values, has_group


def _set_category_ticks(ax, labels, horizontal=False):
    positions = list(range(len(labels)))
    if horizontal:
        ax.set_yticks(positions)
        ax.set_yticklabels([str(v) for v in labels])
    else:
        ax.set_xticks(positions)
        ax.set_xticklabels([str(v) for v in labels])
        if len(labels) > 8:
            plt.setp(ax.get_xticklabels(), rotation=45, ha="right")


# --- renderers -----------------------------------------------------------------


def render_column(args: dict, out_path: Path) -> None:
    """Vertical bar chart: [{category, value, group?}], grouped or stacked."""
    _apply_theme(args)
    data = _require_data(args)
    categories, groups, values, has_group = _pivot_category_group(data)
    fig, ax = _new_figure(args)
    colors = _palette(args, max(1, len(groups)))
    n = len(categories)
    xs = list(range(n))
    legend = False

    if has_group and args.get("group", True):
        k = len(groups)
        w = 0.8 / k
        for gi, g in enumerate(groups):
            vals = [values.get((g, c), 0.0) for c in categories]
            offsets = [x - 0.4 + w / 2 + gi * w for x in xs]
            ax.bar(offsets, vals, width=w, color=colors[gi], label=str(g))
        legend = True
    elif has_group and args.get("stack", False):
        bottoms = [0.0] * n
        for gi, g in enumerate(groups):
            vals = [values.get((g, c), 0.0) for c in categories]
            ax.bar(xs, vals, bottom=bottoms, color=colors[gi], label=str(g))
            bottoms = [b + v for b, v in zip(bottoms, vals)]
        legend = True
    else:
        vals = [values.get((None, c), 0.0) for c in categories]
        ax.bar(xs, vals, width=0.6, color=colors[0])
        for x, v in zip(xs, vals):
            ax.text(x, v, "%g" % v, ha="center", va="bottom", fontsize=9)

    _set_category_ticks(ax, categories)
    _finish(fig, ax, args, out_path, legend=legend)


def render_bar(args: dict, out_path: Path) -> None:
    """Horizontal bar chart: [{category, value, group?}], grouped or stacked."""
    _apply_theme(args)
    data = _require_data(args)
    categories, groups, values, has_group = _pivot_category_group(data)
    fig, ax = _new_figure(args)
    colors = _palette(args, max(1, len(groups)))
    n = len(categories)
    ys = list(range(n))
    legend = False

    if has_group and args.get("group", False):
        k = len(groups)
        h = 0.8 / k
        for gi, g in enumerate(groups):
            vals = [values.get((g, c), 0.0) for c in categories]
            offsets = [y - 0.4 + h / 2 + gi * h for y in ys]
            ax.barh(offsets, vals, height=h, color=colors[gi], label=str(g))
        legend = True
    elif has_group and args.get("stack", True):
        lefts = [0.0] * n
        for gi, g in enumerate(groups):
            vals = [values.get((g, c), 0.0) for c in categories]
            ax.barh(ys, vals, left=lefts, color=colors[gi], label=str(g))
            lefts = [l + v for l, v in zip(lefts, vals)]
        legend = True
    else:
        vals = [values.get((None, c), 0.0) for c in categories]
        ax.barh(ys, vals, height=0.6, color=colors[0])
        for y, v in zip(ys, vals):
            ax.text(v, y, " %g" % v, ha="left", va="center", fontsize=9)

    _set_category_ticks(ax, categories, horizontal=True)
    ax.invert_yaxis()  # first category on top
    _finish(fig, ax, args, out_path, legend=legend)


def render_line(args: dict, out_path: Path) -> None:
    """Line chart: [{time, value, group?}]."""
    _apply_theme(args)
    data = _require_data(args)
    times, groups, values, has_group = _pivot_time_group(data)
    fig, ax = _new_figure(args)
    series = groups if has_group else [None]
    colors = _palette(args, len(series))
    xs = list(range(len(times)))

    for si, g in enumerate(series):
        vals = [values.get((g, t), float("nan")) for t in times]
        ax.plot(
            xs,
            vals,
            marker="o",
            markersize=4,
            linewidth=_line_width(args),
            color=colors[si],
            label=str(g) if has_group else None,
        )

    _set_category_ticks(ax, times)
    _finish(fig, ax, args, out_path, legend=has_group)


def render_area(args: dict, out_path: Path) -> None:
    """Area chart: [{time, value, group?}], optionally stacked."""
    _apply_theme(args)
    data = _require_data(args)
    times, groups, values, has_group = _pivot_time_group(data)
    fig, ax = _new_figure(args)
    colors = _palette(args, max(1, len(groups)))
    xs = list(range(len(times)))
    lw = _line_width(args)

    if has_group and args.get("stack", False):
        matrix = [[values.get((g, t), 0.0) for t in times] for g in groups]
        ax.stackplot(xs, matrix, labels=[str(g) for g in groups], colors=colors)
        legend = True
    else:
        series = groups if has_group else [None]
        for si, g in enumerate(series):
            vals = [values.get((g, t), float("nan")) for t in times]
            ax.plot(xs, vals, linewidth=lw, color=colors[si],
                    label=str(g) if has_group else None)
            ax.fill_between(xs, vals, color=colors[si], alpha=0.25)
        legend = has_group

    _set_category_ticks(ax, times)
    _finish(fig, ax, args, out_path, legend=legend)


def render_pie(args: dict, out_path: Path) -> None:
    """Pie/donut chart: [{category, value}]; innerRadius>0 yields a donut."""
    _apply_theme(args)
    data = _require_data(args)
    categories = [str(d.get("category")) for d in data]
    vals = [_to_float(d.get("value")) for d in data]
    if sum(vals) <= 0:
        raise ValueError("pie chart requires at least one positive value")
    inner = _to_float(args.get("innerRadius"))
    inner = min(max(inner, 0.0), 0.95)

    fig, ax = _new_figure(args)
    colors = _palette(args, len(vals))
    wedgeprops = {"width": 1.0 - inner} if inner > 0 else None
    ax.pie(
        vals,
        labels=categories,
        colors=colors,
        autopct="%1.1f%%",
        startangle=90,
        counterclock=False,
        wedgeprops=wedgeprops,
        textprops={"fontsize": 9},
    )
    _finish(fig, ax, args, out_path)


def render_radar(args: dict, out_path: Path) -> None:
    """Radar chart: [{name, value, group?}]."""
    _apply_theme(args)
    data = _require_data(args)
    has_group = any("group" in d for d in data)
    dims = _unique(d.get("name") for d in data)
    if len(dims) < 3:
        raise ValueError("radar chart needs at least 3 dimensions")
    groups = _unique(d.get("group") for d in data) if has_group else [None]
    values = {}
    for d in data:
        key = (d.get("group") if has_group else None, d.get("name"))
        values[key] = _to_float(d.get("value"))

    fig, ax = _new_figure(args, polar=True)
    colors = _palette(args, len(groups))
    n = len(dims)
    angles = [2 * math.pi * i / n for i in range(n)]
    closed = angles + angles[:1]

    for gi, g in enumerate(groups):
        vals = [values.get((g, dim), 0.0) for dim in dims]
        vc = vals + vals[:1]
        ax.plot(
            closed,
            vc,
            color=colors[gi],
            linewidth=_line_width(args),
            label=str(g) if has_group else None,
        )
        ax.fill(closed, vc, color=colors[gi], alpha=0.15)

    ax.set_xticks(angles)
    ax.set_xticklabels([str(d) for d in dims])
    _finish(fig, ax, args, out_path, legend=has_group)


def render_scatter(args: dict, out_path: Path) -> None:
    """Scatter chart: [{x, y, group?}]."""
    _apply_theme(args)
    data = _require_data(args)
    has_group = any("group" in d for d in data)
    groups = _unique(d.get("group") for d in data) if has_group else [None]
    fig, ax = _new_figure(args)
    colors = _palette(args, len(groups))

    for gi, g in enumerate(groups):
        pts = [d for d in data if (d.get("group") if has_group else None) == g]
        ax.scatter(
            [_to_float(d.get("x")) for d in pts],
            [_to_float(d.get("y")) for d in pts],
            color=colors[gi],
            alpha=0.8,
            label=str(g) if has_group else None,
        )

    _finish(fig, ax, args, out_path, legend=has_group)


def render_histogram(args: dict, out_path: Path) -> None:
    """Histogram: data is a plain number array; binNumber customizes bins."""
    _apply_theme(args)
    data = _require_data(args)
    vals = [_to_float(v) for v in data]
    bins = args.get("binNumber")
    try:
        bins = int(bins) if bins else "auto"
    except (TypeError, ValueError):
        bins = "auto"

    fig, ax = _new_figure(args)
    ax.hist(vals, bins=bins, color=_palette(args, 1)[0], edgecolor="white")
    _finish(fig, ax, args, out_path)


def render_heatmap(args: dict, out_path: Path) -> None:
    """Heatmap: [{x, y, value}] — color intensity over two category axes."""
    _apply_theme(args)
    data = _require_data(args)
    x_labels = _unique(d.get("x") for d in data)
    y_labels = _unique(d.get("y") for d in data)
    cell = {}
    for d in data:
        cell[(d.get("y"), d.get("x"))] = _to_float(d.get("value"))
    matrix = [[cell.get((y, x), 0.0) for x in x_labels] for y in y_labels]

    fig, ax = _new_figure(args)
    im = ax.imshow(matrix, cmap="YlGnBu", aspect="auto")
    ax.set_xticks(range(len(x_labels)))
    ax.set_xticklabels([str(x) for x in x_labels])
    ax.set_yticks(range(len(y_labels)))
    ax.set_yticklabels([str(y) for y in y_labels])
    if len(x_labels) > 8:
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

    flat = [v for row in matrix for v in row]
    v_min, v_max = min(flat), max(flat)
    span = (v_max - v_min) or 1.0
    for yi in range(len(y_labels)):
        for xi in range(len(x_labels)):
            v = matrix[yi][xi]
            shade = "white" if (v - v_min) / span > 0.6 else "black"
            ax.text(xi, yi, "%g" % v, ha="center", va="center",
                    fontsize=8, color=shade)
    fig.colorbar(im, ax=ax, shrink=0.8)
    _finish(fig, ax, args, out_path)


def render_funnel(args: dict, out_path: Path) -> None:
    """Funnel chart: [{category, value}] in process order."""
    _apply_theme(args)
    data = _require_data(args)
    cats = [str(d.get("category")) for d in data]
    vals = [_to_float(d.get("value")) for d in data]
    v_max = max(vals) or 1.0
    n = len(vals)

    fig, ax = _new_figure(args)
    colors = _palette(args, n)
    for i, (c, v) in enumerate(zip(cats, vals)):
        y = n - 1 - i  # first stage on top
        ax.barh(y, v, left=(v_max - v) / 2.0, height=0.7, color=colors[i])
        ax.text(v_max / 2.0, y, "%s: %g" % (c, v), ha="center", va="center",
                fontsize=9)
    ax.set_xlim(0, v_max * 1.05)
    ax.set_ylim(-0.5, n - 0.5)
    ax.axis("off")
    _finish(fig, ax, args, out_path)


def render_dual_axes(args: dict, out_path: Path) -> None:
    """Dual-axes chart: categories + series[{type: column|line, data, axisYTitle?}]."""
    _apply_theme(args)
    categories = args.get("categories")
    series = args.get("series")
    if not categories or not isinstance(categories, list):
        raise ValueError("args.categories must be a non-empty array")
    if not series or not isinstance(series, list):
        raise ValueError("args.series must be a non-empty array")
    if len(series) > 2:
        print(
            "WARNING: dual_axes supports at most 2 series; extra series are dropped.",
            file=sys.stderr,
        )
        series = series[:2]

    xs = list(range(len(categories)))
    fig, ax = _new_figure(args)
    ax2 = ax.twinx() if len(series) > 1 else None
    colors = _palette(args, len(series))
    col_total = sum(1 for s in series if s.get("type") != "line")
    col_seen = 0

    for si, s in enumerate(series):
        target = ax if si == 0 else ax2
        vals = [_to_float(v) for v in (s.get("data") or [])][: len(categories)]
        name = s.get("axisYTitle") or s.get("name") or "series%d" % (si + 1)
        if s.get("type") == "line":
            target.plot(xs, vals, marker="o", markersize=4,
                        linewidth=_line_width(args), color=colors[si], label=name)
        else:
            w = 0.8 / col_total
            offsets = [x - 0.4 + w / 2 + col_seen * w for x in xs]
            target.bar(offsets, vals, width=w, color=colors[si],
                       alpha=0.85, label=name)
            col_seen += 1
        if s.get("axisYTitle"):
            target.set_ylabel(str(s["axisYTitle"]))

    ax.set_zorder((ax2 or ax).get_zorder() + 1)  # keep left-axis artists on top
    ax.patch.set_visible(False)
    _set_category_ticks(ax, categories)

    handles, labels = ax.get_legend_handles_labels()
    if ax2 is not None:
        h2, l2 = ax2.get_legend_handles_labels()
        handles, labels = handles + h2, labels + l2
    if labels:
        ax.legend(handles, labels, loc="best", fontsize=9)

    if args.get("title"):
        ax.set_title(str(args["title"]))
    if args.get("axisXTitle"):
        ax.set_xlabel(str(args["axisXTitle"]))
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, format="png")
    plt.close(fig)


def _bucket_by_category(data: list):
    cats = _unique(d.get("category") for d in data)
    buckets = {c: [] for c in cats}
    for d in data:
        buckets[d.get("category")].append(_to_float(d.get("value")))
    return cats, [buckets[c] for c in cats]


def render_boxplot(args: dict, out_path: Path) -> None:
    """Boxplot: [{category, value}]."""
    _apply_theme(args)
    data = _require_data(args)
    if any("group" in d for d in data):
        print(
            "WARNING: grouped boxplot is not supported locally; "
            "grouped by category only.",
            file=sys.stderr,
        )
    cats, buckets = _bucket_by_category(data)

    fig, ax = _new_figure(args)
    colors = _palette(args, len(cats))
    bp = ax.boxplot(buckets, patch_artist=True)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax.set_xticks(range(1, len(cats) + 1))
    ax.set_xticklabels([str(c) for c in cats])
    _finish(fig, ax, args, out_path)


def render_violin(args: dict, out_path: Path) -> None:
    """Violin chart: [{category, value}]."""
    _apply_theme(args)
    data = _require_data(args)
    if any("group" in d for d in data):
        print(
            "WARNING: grouped violin plot is not supported locally; "
            "grouped by category only.",
            file=sys.stderr,
        )
    cats, buckets = _bucket_by_category(data)

    fig, ax = _new_figure(args)
    colors = _palette(args, len(cats))
    vp = ax.violinplot(buckets, showmeans=True)
    for i, body in enumerate(vp["bodies"]):
        body.set_facecolor(colors[i % len(colors)])
        body.set_alpha(0.7)
    ax.set_xticks(range(1, len(cats) + 1))
    ax.set_xticklabels([str(c) for c in cats])
    _finish(fig, ax, args, out_path)


# --- dispatch -------------------------------------------------------------------

RENDERERS = {
    "generate_column_chart": render_column,
    "generate_bar_chart": render_bar,
    "generate_line_chart": render_line,
    "generate_area_chart": render_area,
    "generate_pie_chart": render_pie,
    "generate_radar_chart": render_radar,
    "generate_scatter_chart": render_scatter,
    "generate_histogram_chart": render_histogram,
    "generate_heatmap_chart": render_heatmap,
    "generate_funnel_chart": render_funnel,
    "generate_dual_axes_chart": render_dual_axes,
    "generate_boxplot_chart": render_boxplot,
    "generate_violin_chart": render_violin,
}

# Legacy tool names that exist in the wild but cannot be rendered locally.
# The hint names the closest supported replacement (or states none exists).
UNSUPPORTED_HINTS = {
    "generate_district_map": "map charts are not supported offline; summarize regions in a table or with generate_bar_chart instead",
    "generate_path_map": "map charts are not supported offline; describe routes in a table or text instead",
    "generate_pin_map": "map charts are not supported offline; list locations in a table instead",
    "generate_treemap_chart": "use generate_pie_chart or generate_bar_chart instead",
    "generate_word_cloud_chart": "use generate_bar_chart with top-N word frequencies instead",
    "generate_liquid_chart": "use generate_pie_chart with [value, 1 - value] instead",
    "generate_sankey_chart": "use generate_funnel_chart or a grouped generate_bar_chart instead",
    "generate_venn_chart": "use generate_pie_chart instead",
    "generate_network_graph": "node-edge graphs are not supported offline; describe the structure in text instead",
    "generate_mind_map": "mind maps are not supported offline; use an outline in text instead",
    "generate_organization_chart": "org charts are not supported offline; use an indented text outline instead",
    "generate_fishbone_diagram": "fishbone diagrams are not supported offline; use a categorized list instead",
    "generate_flow_diagram": "flow diagrams are not supported offline; use a numbered step list instead",
    "generate_spreadsheet": "use a markdown table or the xlsx skill instead",
}


def _load_spec(spec_arg: str):
    """Accept either a JSON string or a path to a JSON file (legacy behavior)."""
    try:
        candidate = Path(spec_arg)
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    except OSError:
        pass
    return json.loads(spec_arg)


def _numbered_out(out: Path, index: int) -> Path:
    return out.with_name("%s_%d%s" % (out.stem, index, out.suffix))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render chart specs to local PNG files (offline)."
    )
    parser.add_argument(
        "--spec",
        required=True,
        help="Chart spec JSON string, or path to a JSON spec file",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output PNG path ('<stem>_<i>.png' per item when spec is an array)",
    )
    args = parser.parse_args()

    try:
        spec = _load_spec(args.spec)
    except json.JSONDecodeError as e:
        print("Error: cannot parse --spec as JSON: %s" % e, file=sys.stderr)
        sys.exit(2)

    specs = spec if isinstance(spec, list) else [spec]
    out = Path(args.out)
    failures = 0

    for i, item in enumerate(specs):
        tool = (item or {}).get("tool")
        chart_args = (item or {}).get("args") or {}
        out_path = _numbered_out(out, i + 1) if len(specs) > 1 else out

        renderer = RENDERERS.get(tool)
        if renderer is None:
            hint = UNSUPPORTED_HINTS.get(
                tool, "unknown tool; see SKILL.md for supported chart types"
            )
            print(
                "Error: '%s' is not supported by the local renderer: %s"
                % (tool, hint),
                file=sys.stderr,
            )
            failures += 1
            continue

        try:
            renderer(chart_args, out_path)
            print(out_path)
        except Exception as e:
            print("Error: failed to render '%s': %s" % (tool, e), file=sys.stderr)
            failures += 1

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
