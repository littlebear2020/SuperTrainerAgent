"""Test SSRF protection in video-dl download.py."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1].parent / "skills" / "public" / "video-dl" / "scripts" / "download.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("download", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def dl():
    return _load_module()


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://gateway:8001/",
        "http://localhost:8080/",
    ],
)
def test_ssrf_urls_rejected(dl, url):
    with pytest.raises(ValueError, match="(?i)scheme|private|internal|loopback|reserved|localhost"):
        dl.validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.bilibili.com/video/BV1xx411c7mD",
        "https://b23.tv/BV1xx411c7mD",
    ],
)
def test_valid_urls_accepted(dl, url):
    dl.validate_url(url)  # should not raise
