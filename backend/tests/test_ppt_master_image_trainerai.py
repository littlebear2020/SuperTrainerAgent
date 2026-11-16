"""PPT Master 生图 qihu360 backend + trainerai 适配器测试（2026-11-16）。

覆盖：注册链（IMAGE_BACKEND=qihu360）、360 请求合同（extra_body.size /
watermark / 参考图 image；无顶层 size、无 sequential_image_generation）、
三档尺寸映射、显式 PNG、BMP/TIFF MIME、无显式 backend 的 QIHU 默认路由、
响应下载/b64、trainerai 适配器只选 QIHU。
"""

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "skills/public/ppt-master/scripts"
BACKEND_MOD_PATH = SCRIPTS_DIR / "image_backends" / "backend_qihu360.py"
ADAPTER_MOD_PATH = SCRIPTS_DIR / "image_backends" / "backend_trainerai.py"
IMAGE_GEN_MOD_PATH = SCRIPTS_DIR / "image_gen.py"

DEFAULT_MODEL = "bytedance/doubao-seedream-5.0-pro"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-payload"


@pytest.fixture(autouse=True)
def clear_qihu_environment(monkeypatch):
    for name in ("QIHU_API_KEY", "QIHU_MODEL", "QIHU_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _valid_png_bytes(tmp_path):
    from PIL import Image

    path = tmp_path / "valid-source.png"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(path, format="PNG")
    return path.read_bytes()


@pytest.fixture()
def mod(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("backend_qihu360_image", BACKEND_MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for name in [n for n in sys.modules if n == "config" or n.startswith("image_backends")]:
        sys.modules.pop(name)
    return m


@pytest.fixture()
def adapter(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("backend_trainerai_image", ADAPTER_MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_default_model_and_reference_support(mod):
    assert mod.DEFAULT_MODEL == DEFAULT_MODEL
    assert mod.SUPPORTS_REFERENCE_IMAGE is True


def test_size_mapping_only_three_seedream_tiers(mod):
    assert mod._resolve_size("16:9") == "2048x1152"
    assert mod._resolve_size("1:1") == "2048x2048"
    assert mod._resolve_size("9:16") == "1152x2048"
    assert mod._resolve_size("3:4") == "1152x2048"
    assert mod._resolve_size("4:5") == "2048x2048"
    assert mod._resolve_size("21:9") == "2048x1152"


def test_unsupported_model_rejected(mod):
    with pytest.raises(ValueError, match="Unsupported 360 image model"):
        mod._validate_model("google/gemini-3.1-flash-image-preview")


def test_generate_generations_body_contract(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("QIHU_API_KEY", "test-qihu-key")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        return FakeResponse({"data": [{"url": "https://cdn.example/image.png"}]})

    def fake_download(url, path, headers=None, timeout=0):
        captured["download"] = (url, path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(PNG_BYTES)
        return path

    monkeypatch.setattr(mod.requests, "post", fake_post)
    monkeypatch.setattr(mod, "download_image", fake_download)

    result = mod.generate(
        "一只在讲台前的猫",
        aspect_ratio="16:9",
        image_size="2K",
        output_dir=str(tmp_path),
    )

    body = captured["body"]
    assert captured["url"] == "https://api.360.cn/v1/images/generations"
    assert captured["headers"]["Authorization"] == "Bearer test-qihu-key"
    assert body["model"] == DEFAULT_MODEL
    assert body["prompt"] == "一只在讲台前的猫"
    assert body["extra_body"] == {
        "size": "2048x1152",
        "watermark": False,
        "output_format": "png",
    }
    assert body["extra_body"]["output_format"] == "png"
    assert "output_format" not in body
    assert "size" not in body
    assert "sequential_image_generation" not in body
    assert captured["download"][0] == "https://cdn.example/image.png"
    assert Path(result).is_file()
    assert Path(result).read_bytes() == PNG_BYTES


def test_generate_prefers_url_when_response_also_has_invalid_b64(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("QIHU_API_KEY", "test-qihu-key")
    valid_png = _valid_png_bytes(tmp_path)
    invalid_b64 = base64.b64encode(b"x" * 33).decode("ascii")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResponse(
            {
                "data": [
                    {
                        "b64_json": invalid_b64,
                        "url": "https://cdn.example/valid.png",
                    }
                ]
            }
        )

    class FakeDownload:
        status_code = 200
        content = valid_png
        headers = {"Content-Type": "image/png"}

        def raise_for_status(self):
            return None

    def fake_get(url, headers=None, timeout=None):
        captured["download_url"] = url
        return FakeDownload()

    monkeypatch.setattr(mod.requests, "post", fake_post)
    monkeypatch.setattr(mod.requests, "get", fake_get)

    result = mod.generate(
        "valid URL with invalid b64",
        output_dir=str(tmp_path),
        filename="dual-response",
        max_retries=0,
    )

    assert captured["download_url"] == "https://cdn.example/valid.png"
    assert Path(result).read_bytes() == valid_png


def test_generate_accepts_valid_b64_only_response(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("QIHU_API_KEY", "test-qihu-key")
    valid_png = _valid_png_bytes(tmp_path)
    encoded = base64.b64encode(valid_png).decode("ascii")
    monkeypatch.setattr(
        mod.requests,
        "post",
        lambda *args, **kwargs: FakeResponse({"data": [{"b64_json": encoded}]}),
    )

    result = mod.generate(
        "b64 only",
        output_dir=str(tmp_path),
        filename="b64-only",
        max_retries=0,
    )

    assert Path(result).read_bytes() == valid_png


@pytest.mark.parametrize("failure_kind", ["http", "network"])
def test_generate_falls_back_to_valid_b64_when_url_download_fails(mod, monkeypatch, tmp_path, failure_kind):
    monkeypatch.setenv("QIHU_API_KEY", "test-qihu-key")
    valid_png = _valid_png_bytes(tmp_path)
    encoded = base64.b64encode(valid_png).decode("ascii")
    post_calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        post_calls.append(url)
        return FakeResponse(
            {
                "data": [
                    {
                        "b64_json": encoded,
                        "url": "https://cdn.example/unavailable.png",
                    }
                ]
            }
        )

    class FakeHttpFailure:
        status_code = 404
        content = b""
        headers = {}

        def raise_for_status(self):
            raise requests.HTTPError("404 Not Found")

    def fake_get(url, headers=None, timeout=None):
        if failure_kind == "network":
            raise requests.ConnectionError("cdn unreachable")
        return FakeHttpFailure()

    monkeypatch.setattr(mod.requests, "post", fake_post)
    monkeypatch.setattr(mod.requests, "get", fake_get)

    result = mod.generate(
        "fallback without another paid request",
        output_dir=str(tmp_path),
        filename="url-failure-fallback",
        max_retries=0,
    )

    assert len(post_calls) == 1
    assert Path(result).read_bytes() == valid_png


def test_generate_preserves_url_download_error_without_b64(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("QIHU_API_KEY", "test-qihu-key")
    monkeypatch.setattr(
        mod.requests,
        "post",
        lambda *args, **kwargs: FakeResponse({"data": [{"url": "https://cdn.example/unavailable.png"}]}),
    )

    def failed_get(url, headers=None, timeout=None):
        raise requests.ConnectionError("cdn unreachable")

    monkeypatch.setattr(mod.requests, "get", failed_get)

    with pytest.raises(RuntimeError) as error:
        mod.generate(
            "URL only failure",
            output_dir=str(tmp_path),
            filename="url-only-failure",
            max_retries=0,
        )

    assert "Image download failed" in str(error.value)
    assert "cdn unreachable" in str(error.value)
    assert "b64" not in str(error.value)


def test_generate_reports_both_errors_when_url_and_b64_fail(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("QIHU_API_KEY", "test-qihu-key")
    invalid_b64 = base64.b64encode(b"x" * 33).decode("ascii")
    monkeypatch.setattr(
        mod.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(
            {
                "data": [
                    {
                        "b64_json": invalid_b64,
                        "url": "https://cdn.example/unavailable.png",
                    }
                ]
            }
        ),
    )

    def failed_get(url, headers=None, timeout=None):
        raise requests.ConnectionError("cdn unreachable")

    monkeypatch.setattr(mod.requests, "get", failed_get)

    with pytest.raises(RuntimeError) as error:
        mod.generate(
            "both candidates fail",
            output_dir=str(tmp_path),
            filename="dual-failure",
            max_retries=0,
        )

    message = str(error.value)
    assert "Image download failed" in message
    assert "cannot identify image file" in message
    assert not (tmp_path / "dual-failure.png").exists()


def test_generate_reference_image_uses_edits_and_data_uri(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("QIHU_API_KEY", "test-qihu-key")
    reference = tmp_path / "ref.png"
    reference.write_bytes(PNG_BYTES)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return FakeResponse({"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]})

    monkeypatch.setattr(mod.requests, "post", fake_post)
    monkeypatch.setattr(mod, "save_image_bytes", lambda data, path, content_type=None: path)

    mod.generate(
        "把背景换成教室",
        aspect_ratio="1:1",
        image_size="2K",
        output_dir=str(tmp_path),
        reference_image=str(reference),
    )

    body = captured["body"]
    assert captured["url"] == "https://api.360.cn/v1/images/edits"
    assert body["extra_body"]["size"] == "2048x2048"
    assert body["extra_body"]["watermark"] is False
    assert body["extra_body"]["output_format"] == "png"
    assert "output_format" not in body
    image_value = body["extra_body"]["image"]
    assert isinstance(image_value, str)
    assert image_value.startswith("data:image/png;base64,")
    assert base64.b64decode(image_value.split(",", 1)[1]) == PNG_BYTES


@pytest.mark.parametrize(
    ("suffix", "expected_mime"),
    [(".bmp", "image/bmp"), (".tif", "image/tiff"), (".tiff", "image/tiff")],
)
def test_reference_image_preserves_bmp_and_tiff_mime(mod, tmp_path, suffix, expected_mime):
    reference = tmp_path / f"reference{suffix}"
    reference.write_bytes(b"local-reference-image")

    data_uri = mod._reference_to_data_uri(str(reference))

    assert data_uri.startswith(f"data:{expected_mime};base64,")
    assert base64.b64decode(data_uri.split(",", 1)[1]) == b"local-reference-image"


def test_generate_requires_qihu_key(mod, monkeypatch, tmp_path):
    monkeypatch.delenv("QIHU_API_KEY", raising=False)
    with pytest.raises(ValueError, match="QIHU_API_KEY"):
        mod.generate("hi", output_dir=str(tmp_path))


def test_generate_surfaces_http_error(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("QIHU_API_KEY", "test-key")
    monkeypatch.setattr(
        mod.requests,
        "post",
        lambda *a, **k: FakeResponse({"error": "content policy"}, status_code=400),
    )
    with pytest.raises(RuntimeError, match=r"400.*content policy"):
        mod.generate("hi", output_dir=str(tmp_path))


def test_trainerai_adapter_only_uses_qihu(adapter, monkeypatch):
    for key in ("ARK_API_KEY", "DASHSCOPE_API_KEY", "ZHIPU_API_KEY", "VOLCENGINE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("QIHU_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="QIHU_API_KEY"):
        adapter.resolve_backend()

    monkeypatch.setenv("ARK_API_KEY", "ark")
    monkeypatch.setenv("QIHU_API_KEY", "qihu")
    backend, module, _label = adapter.resolve_backend()
    assert backend == "qihu360"
    assert module == "backend_qihu360"


def _load_image_gen(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("image_gen_mod", IMAGE_GEN_MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "image_gen_mod", m)
    spec.loader.exec_module(m)
    return m


def test_image_gen_cli_defaults_to_qihu360_with_mock_http(tmp_path, monkeypatch):
    """脚本级：无 IMAGE_BACKEND/--backend 时，QIHU 凭据自动选择 360 并落盘 PNG。"""
    image_gen = _load_image_gen(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IMAGE_BACKEND", raising=False)
    monkeypatch.setenv("QIHU_API_KEY", "test-qihu-key")
    monkeypatch.delenv("QIHU_BASE_URL", raising=False)
    monkeypatch.setattr(image_gen, "_load_image_env_file", lambda: None)

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return FakeResponse({"data": [{"url": "https://cdn.example/generated.png"}]})

    class FakeDownload:
        status_code = 200
        content = PNG_BYTES
        headers = {"Content-Type": "image/png"}

        def raise_for_status(self):
            return None

    def fake_get(url, headers=None, timeout=None):
        captured["download_url"] = url
        return FakeDownload()

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    output_dir = tmp_path / "images"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "image_gen.py",
            "一张课程封面",
            "--aspect_ratio",
            "16:9",
            "-o",
            str(output_dir),
        ],
    )

    image_gen.main()

    outputs = list(output_dir.glob("*.png"))
    assert len(outputs) == 1
    assert outputs[0].read_bytes() == PNG_BYTES
    assert captured["url"] == "https://api.360.cn/v1/images/generations"
    assert captured["body"]["model"] == DEFAULT_MODEL
    assert captured["body"]["extra_body"]["size"] == "2048x1152"
    assert captured["body"]["extra_body"]["output_format"] == "png"
    assert "output_format" not in captured["body"]
    assert "size" not in captured["body"]
    assert "sequential_image_generation" not in captured["body"]
    assert captured["download_url"] == "https://cdn.example/generated.png"
