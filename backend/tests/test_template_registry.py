"""Tests for template registry and schema."""

import json

from app.gateway.video_gen.templates.schema import TemplateMeta, TemplateParam, get_template, list_templates


def test_template_meta_basic():
    meta = TemplateMeta(id="test", name="Test", desc="A test template")
    assert meta.id == "test"
    assert meta.category == "general"
    assert meta.params == []


def test_template_param_types():
    p = TemplateParam(name="color", type="color", label="主题色", default="#4F46E5")
    assert p.type == "color"
    assert p.default == "#4F46E5"


def test_template_param_enum():
    p = TemplateParam(name="anim", type="enum", options=["fade", "slide", "zoom"], default="fade")
    assert p.options == ["fade", "slide", "zoom"]


def test_template_param_number_bounds():
    p = TemplateParam(name="size", type="number", min=10, max=72, default=24)
    assert p.min == 10
    assert p.max == 72


def test_list_templates_returns_base(tmp_path, monkeypatch):
    """list_templates should find remotion-base template.json."""
    templates_dir = tmp_path / "templates"
    base_dir = templates_dir / "remotion-base"
    base_dir.mkdir(parents=True)
    (base_dir / "template.json").write_text(
        json.dumps(
            {
                "id": "remotion-base",
                "name": "基础字幕",
                "desc": "通用口播",
            }
        )
    )

    monkeypatch.setattr(
        "app.gateway.video_gen.templates.schema._get_templates_root",
        lambda: templates_dir,
    )

    result = list_templates()
    assert len(result) >= 1
    assert result[0].id == "remotion-base"
    assert result[0].name == "基础字幕"


def test_list_templates_skips_invalid_json(tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    bad_dir = templates_dir / "bad-template"
    bad_dir.mkdir(parents=True)
    (bad_dir / "template.json").write_text("{invalid json")

    monkeypatch.setattr(
        "app.gateway.video_gen.templates.schema._get_templates_root",
        lambda: templates_dir,
    )

    result = list_templates()
    assert result == []


def test_get_template_found(tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    base_dir = templates_dir / "remotion-base"
    base_dir.mkdir(parents=True)
    (base_dir / "template.json").write_text(
        json.dumps(
            {
                "id": "remotion-base",
                "name": "基础字幕",
            }
        )
    )

    monkeypatch.setattr(
        "app.gateway.video_gen.templates.schema._get_templates_root",
        lambda: templates_dir,
    )

    meta = get_template("remotion-base")
    assert meta is not None
    assert meta.id == "remotion-base"


def test_get_template_not_found(tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir(parents=True)

    monkeypatch.setattr(
        "app.gateway.video_gen.templates.schema._get_templates_root",
        lambda: templates_dir,
    )

    assert get_template("nonexistent") is None


def test_list_templates_multiple(tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    for tid, name in [("remotion-base", "基础"), ("remotion-news", "新闻"), ("remotion-quote", "金句")]:
        d = templates_dir / tid
        d.mkdir(parents=True, exist_ok=True)
        (d / "template.json").write_text(json.dumps({"id": tid, "name": name}))

    monkeypatch.setattr(
        "app.gateway.video_gen.templates.schema._get_templates_root",
        lambda: templates_dir,
    )

    result = list_templates()
    assert len(result) == 3
    ids = {t.id for t in result}
    assert ids == {"remotion-base", "remotion-news", "remotion-quote"}
