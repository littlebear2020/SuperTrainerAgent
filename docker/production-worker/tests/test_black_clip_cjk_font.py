"""行为锁：黑场占位片必须指定 CJK 字体，否则中文渲染成方块。

线上实测（2026-07-29）：过期 visual_url 导致帧降级为黑场占位，
用户看到「黑屏 + 画面中间各种方块乱码」。根因是 _make_black_subtitle_clip
的 drawtext 既无 fontfile= 也无 font=，ffmpeg 回落内建默认字体（Latin-only），
而默认文案 "画面生成中" 与旁白都是中文。

容器装了 fonts-noto-cjk（Dockerfile:5），兄弟路径 video_gen_mixed.py:382
早已用 font='Noto Sans CJK SC'——只有这条黑场路径漏装。

字体事实：Debian fonts-noto-cjk 只提供 .ttc（backend/tests/test_worker_fonts.py
已锁死这一点，勿写 .ttf）。
"""

from pipelines._ffmpeg_helpers import _resolve_cjk_fontfile
from pipelines.video_gen_ai_visual import _build_black_clip_vf


def test_vf_includes_fontfile_when_font_available(tmp_path, monkeypatch):
    """有可用字体时 vf 必须带 fontfile=。"""
    fake = tmp_path / "NotoSansCJK-Regular.ttc"
    fake.write_bytes(b"x")
    monkeypatch.setattr("pipelines._ffmpeg_helpers._CJK_FONT_CANDIDATES", (str(fake),))
    monkeypatch.setattr("pipelines._ffmpeg_helpers._CJK_FONT_DIRS", ())

    vf = _build_black_clip_vf("你好世界", width=1152, height=2048)

    assert f"fontfile='{fake}'" in vf, f"缺 fontfile，中文会变方块: {vf}"
    assert vf.startswith("drawtext=")


def test_vf_degrades_without_font(monkeypatch):
    """无字体时不得崩——退回无 fontfile（英文仍可读），不抛异常。"""
    monkeypatch.setattr("pipelines._ffmpeg_helpers._CJK_FONT_CANDIDATES", ())
    monkeypatch.setattr("pipelines._ffmpeg_helpers._CJK_FONT_DIRS", ())

    vf = _build_black_clip_vf("hello", width=640, height=360)

    assert "drawtext" in vf
    assert "fontfile=" not in vf


def test_vf_preserves_original_escaping():
    """回归保护：特殊字符转义与截断行为不变（原 :191 语义）。"""
    vf = _build_black_clip_vf("a'b:c" + "x" * 60, width=640, height=360)
    assert "\\'" in vf and "\\;" in vf
    # 原实现截 40 字
    assert "x" * 41 not in vf


def test_vf_empty_text_uses_default_placeholder():
    vf = _build_black_clip_vf("", width=640, height=360)
    assert "画面生成中" in vf


def test_vf_keeps_layout_params():
    vf = _build_black_clip_vf("hi", width=640, height=360)
    for frag in ("fontcolor=white", "box=1", "boxcolor=black@0.5", "x=(w-text_w)/2"):
        assert frag in vf


def test_resolve_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr("pipelines._ffmpeg_helpers._CJK_FONT_CANDIDATES", ("/nonexistent/x.ttc",))
    monkeypatch.setattr("pipelines._ffmpeg_helpers._CJK_FONT_DIRS", ("/nonexistent",))
    assert _resolve_cjk_fontfile() is None


def test_resolve_scans_dir_fallback(tmp_path, monkeypatch):
    """候选清单全不中时，扫描字体目录兜底（镜像换基底后路径变了也能活）。"""
    sub = tmp_path / "opentype" / "noto"
    sub.mkdir(parents=True)
    found = sub / "NotoSansCJK-Bold.ttc"
    found.write_bytes(b"x")
    monkeypatch.setattr("pipelines._ffmpeg_helpers._CJK_FONT_CANDIDATES", ())
    monkeypatch.setattr("pipelines._ffmpeg_helpers._CJK_FONT_DIRS", (str(tmp_path),))

    assert _resolve_cjk_fontfile() == str(found)
