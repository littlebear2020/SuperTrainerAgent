"""PPT converter sidecar — lightweight Flask service for LibreOffice conversion.

Runs as a separate container to avoid bloating the Gateway image.
Provides two endpoints:
  - POST /extract  — extract PPTX content (title, body, notes) → JSON
  - POST /convert  — convert PPTX to slide images → ZIP
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path

from flask import Flask, jsonify, request, send_file

app = Flask(__name__)

# Shared secret for authenticating requests from Gateway.
# Both containers must set PPT_CONVERTER_SECRET to the same value.
_SHARED_SECRET = os.environ.get("PPT_CONVERTER_SECRET", "")
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB

# 每容器并发转换闸（2026-08-31 落实）：此前 compose 注释声称 "Semaphore(2)/container"
# 但代码里根本不存在——Flask dev server 默认 threaded，突发时每副本会无界并发
# spawn soffice（每个 0.5~2G），32 核机上雷群。现在把它变成真的：soffice 雷群在
# /convert（/extract 只是 python-pptx 进程内解析，无子进程），只闸 /convert。
# 4 副本 × 2 = 8 并发，与 compose 容量注释对齐。
_CONVERT_SEMAPHORE = threading.Semaphore(int(os.environ.get("PPT_CONVERTER_MAX_CONCURRENT", "2")))


@app.before_request
def _auth_and_limit():
    """Validate shared secret on mutating endpoints and check content length."""
    if request.path == "/health":
        return None  # skip auth for health checks

    # SEC-H5 (fail-closed): an empty secret refuses every non-health request
    # (401) instead of silently skipping auth. Deliberately "refuse requests"
    # rather than "refuse to start": the dev compose healthcheck +
    # depends_on chain would break if the container exited at boot — callers
    # recover by configuring PPT_CONVERTER_SECRET on both containers.
    if not _SHARED_SECRET:
        return jsonify({"error": "PPT_CONVERTER_SECRET not configured"}), 401
    token = request.headers.get("X-Converter-Token", "")
    if token != _SHARED_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    # Enforce upload size limit
    content_length = request.content_length
    if content_length is not None and content_length > _MAX_UPLOAD_BYTES:
        return jsonify({"error": f"File too large (max {_MAX_UPLOAD_BYTES // 1024 // 1024}MB)"}), 413


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/extract", methods=["POST"])
def extract():
    """Extract text and notes from a PPTX file.

    Accepts multipart file upload. Returns JSON with slide data and analysis.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    uploaded = request.files["file"]
    with tempfile.TemporaryDirectory() as tmpdir:
        pptx_path = Path(tmpdir) / "input.pptx"
        uploaded.save(str(pptx_path))

        try:
            from pptx import Presentation

            prs = Presentation(str(pptx_path))
            slides = []
            for i, slide in enumerate(prs.slides, 1):
                title = ""
                body_parts = []
                for shape in slide.shapes:
                    if not shape.has_text_frame:
                        continue
                    text = shape.text_frame.text.strip()
                    if not text:
                        continue

                    is_placeholder = False
                    ph_type = None
                    try:
                        if shape.placeholder_format is not None:
                            is_placeholder = True
                            ph_type = shape.placeholder_format.type
                    except (ValueError, AttributeError):
                        pass

                    if is_placeholder:
                        if ph_type in (1, 3):
                            title = text
                            continue
                        if ph_type in (2, 7):
                            body_parts.append(text)
                            continue

                    if not title:
                        title = text
                    else:
                        body_parts.append(text)

                notes = ""
                if slide.has_notes_slide:
                    notes_tf = slide.notes_slide.notes_text_frame
                    if notes_tf:
                        notes = re.sub(r"\s+", " ", notes_tf.text).strip()

                slides.append({
                    "slide_number": i,
                    "title": title,
                    "body": "\n".join(body_parts),
                    "notes": notes,
                    "has_notes": bool(notes),
                })

            # Analysis
            total = len(slides)
            with_notes = sum(1 for s in slides if s["has_notes"])
            lengths = [len(s["notes"]) for s in slides if s["has_notes"]]
            avg_length = sum(lengths) / len(lengths) if lengths else 0.0
            ratio = with_notes / total if total > 0 else 0.0
            sufficient = (ratio >= 0.6 and avg_length >= 30) or (ratio >= 0.4 and avg_length >= 50)

            return jsonify({
                "slides": slides,
                "analysis": {
                    "total_slides": total,
                    "slides_with_notes": with_notes,
                    "avg_length": round(avg_length, 1),
                    "is_sufficient": sufficient,
                },
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500


@app.route("/convert", methods=["POST"])
def convert():
    """Convert PPTX to slide images (JPEG).

    Accepts multipart file upload + optional 'dpi' form field.
    Returns a ZIP file with slide-01.jpg, slide-02.jpg, ...
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    uploaded = request.files["file"]
    dpi = int(request.form.get("dpi", 200))

    with _CONVERT_SEMAPHORE, tempfile.TemporaryDirectory() as tmpdir:
        pptx_path = Path(tmpdir) / "input.pptx"
        uploaded.save(str(pptx_path))

        slides_dir = Path(tmpdir) / "slides"
        slides_dir.mkdir()

        try:
            # PPTX → PDF
            pdf_dir = Path(tmpdir) / "_pdf"
            pdf_dir.mkdir()
            cmd = _find_soffice() + [
                "--headless", "--convert-to", "pdf",
                "--outdir", str(pdf_dir), str(pptx_path.resolve()),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                return jsonify({"error": f"LibreOffice failed: {r.stderr[:300]}"}), 500

            pdf_files = list(pdf_dir.glob("*.pdf"))
            if not pdf_files:
                return jsonify({"error": "No PDF generated"}), 500

            # PDF → JPEG
            img_prefix = str(slides_dir / "slide")
            r = subprocess.run(
                ["pdftoppm", "-jpeg", "-r", str(dpi), str(pdf_files[0]), img_prefix],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode != 0:
                return jsonify({"error": f"pdftoppm failed: {r.stderr[:300]}"}), 500

            # Rename to zero-padded format
            for img_path in sorted(slides_dir.glob("slide-*.jpg")):
                match = re.search(r"slide-(\d+)\.jpg$", img_path.name)
                if match:
                    num = int(match.group(1))
                    new_name = f"slide-{num:02d}.jpg"
                    img_path.rename(img_path.parent / new_name)

            # Create ZIP
            zip_path = Path(tmpdir) / "slides.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                for img in sorted(slides_dir.glob("*.jpg")):
                    zf.write(img, img.name)

            return send_file(str(zip_path), mimetype="application/zip", as_attachment=True, download_name="slides.zip")
        except Exception as e:
            return jsonify({"error": str(e)}), 500


def _find_soffice() -> list[str]:
    for cmd in ("soffice", "libreoffice", "/usr/bin/soffice", "/usr/bin/libreoffice"):
        if shutil.which(cmd):
            return [cmd]
    return ["soffice"]


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8030))
    app.run(host="0.0.0.0", port=port)
