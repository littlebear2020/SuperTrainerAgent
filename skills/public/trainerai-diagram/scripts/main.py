#!/usr/bin/env python3
"""Convert SVG to PNG using cairosvg (replaces sharp for diagram skill)."""

import argparse
import json
import os
import re
import sys


def parse_viewbox(svg_str: str) -> tuple[float, float] | None:
    """Extract dimensions from SVG viewBox or width/height attributes."""
    vb_match = re.search(r'viewBox\s*=\s*"([^"]+)"', svg_str)
    if vb_match:
        parts = vb_match.group(1).replace(",", " ").split()
        if len(parts) >= 4:
            w, h = float(parts[2]), float(parts[3])
            if w > 0 and h > 0:
                return w, h

    w_match = re.search(r'\bwidth\s*=\s*"(\d+(?:\.\d+)?)"', svg_str)
    h_match = re.search(r'\bheight\s*=\s*"(\d+(?:\.\d+)?)"', svg_str)
    if w_match and h_match:
        return float(w_match.group(1)), float(h_match.group(1))

    return None


def get_output_path(input_path: str, scale: int, custom_output: str | None = None) -> str:
    """Determine output file path."""
    if custom_output:
        return os.path.abspath(custom_output)

    base, _ = os.path.splitext(input_path)
    suffix = f"@{scale}x" if scale != 1 else ""
    return f"{base}{suffix}.png"


def convert_svg_to_png(input_path: str, output_path: str, scale: int) -> dict:
    """Convert SVG to PNG using cairosvg."""
    import cairosvg

    with open(input_path, "r", encoding="utf-8") as f:
        svg_str = f.read()

    dims = parse_viewbox(svg_str)
    if not dims:
        print("Error: Cannot determine SVG dimensions from viewBox or width/height attributes", file=sys.stderr)
        sys.exit(1)

    width = round(dims[0] * scale)
    height = round(dims[1] * scale)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    cairosvg.svg2png(
        url=input_path,
        write_to=output_path,
        output_width=width,
        output_height=height,
    )

    return {"input": input_path, "output": output_path, "width": width, "height": height}


def main():
    parser = argparse.ArgumentParser(description="Convert SVG to @2x PNG")
    parser.add_argument("input", help="Input SVG file path")
    parser.add_argument("-o", "--output", help="Output path (default: <input>@2x.png)")
    parser.add_argument("-s", "--scale", type=int, default=2, help="Scale factor (default: 2)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    if not os.path.exists(input_path):
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)
    if not input_path.lower().endswith(".svg"):
        print("Error: Input must be an SVG file", file=sys.stderr)
        sys.exit(1)
    if args.scale <= 0:
        print(f"Invalid scale: {args.scale}", file=sys.stderr)
        sys.exit(1)

    try:
        output_path = get_output_path(input_path, args.scale, args.output)
        result = convert_svg_to_png(input_path, output_path, args.scale)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"{result['input']} -> {result['output']} ({result['width']}x{result['height']})")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
