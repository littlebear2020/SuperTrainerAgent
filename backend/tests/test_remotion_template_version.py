"""NUL-delimited, C-collation template fingerprint contracts and behavior."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "docker" / "sandbox" / "Dockerfile"
SKILL_MD = REPO_ROOT / "skills" / "public" / "auto-video-flow" / "SKILL.md"
REMOTION_BASE = REPO_ROOT / "skills" / "public" / "auto-video-flow" / "templates" / "remotion-base"
COURSE_STANDARD = REPO_ROOT / "video-templates" / "course-standard"

FINGERPRINT_COMMAND = 'find . -type f -not -path "./node_modules/*" -not -name ".DS_Store" -not -name "TEMPLATE_VERSION" -print0 | LC_ALL=C sort -z | xargs -0 md5sum | md5sum | awk \'{print $1}\''
COURSE_REFERENCE_COMMAND = (
    'find . -type f -not -path "./node_modules/*" -not -path "./out*" -not -path "./.baseline-m0/*" -not -path "./build/*" '
    '-not -name ".DS_Store" -not -name "TEMPLATE_VERSION" -print0 | LC_ALL=C sort -z | xargs -0 md5sum | md5sum | awk \'{print $1}\''
)
REFERENCE_HASHES = (
    (REMOTION_BASE, FINGERPRINT_COMMAND, "18cc3712bf06ffc6041cfa94f4704722", "0cef2d5c42209807e71b1618735a3468"),
    (COURSE_STANDARD, COURSE_REFERENCE_COMMAND, "93ad6d326092fc0fd57c3bc6b50f0072", "540a05a2154ba8bc873d7587fb0e0219"),
)


def _require_tools() -> None:
    for tool in ("md5sum", "xargs", "sort", "locale"):
        if not shutil.which(tool):
            pytest.skip(f"required fingerprint tool is unavailable: {tool}")
    probe = subprocess.run(["sort", "-z"], input=b"", capture_output=True, check=False)
    if probe.returncode != 0:
        pytest.skip("host sort lacks NUL-delimited -z support; Linux container verification is required")


def _locale_available(name: str) -> bool:
    if name == "C":
        return True
    aliases = subprocess.check_output(["locale", "-a"], text=True).splitlines()

    def normalize(value: str) -> str:
        return value.lower().replace("_", "").replace("-", "").replace(".", "")

    return normalize(name) in {normalize(alias) for alias in aliases}


def _fingerprint(root: Path, locale: str = "C", command: str = FINGERPRINT_COMMAND) -> str:
    _require_tools()
    if not _locale_available(locale):
        pytest.skip(f"locale {locale} is unavailable on this host")
    env = os.environ.copy()
    env["LC_ALL"] = locale
    result = subprocess.run(
        ["bash", "-c", f"set -o pipefail; {command}"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    value = result.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{32}", value), result.stdout
    return value


def _make_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "template"
    root.mkdir()
    (root / "z-last.txt").write_bytes(b"last\n")
    (root / "a b space.txt").write_bytes(b"space\n")
    (root / "line\nbreak.txt").write_bytes(b"newline-name\n")
    (root / "中文.txt").write_bytes(b"unicode\n")
    (root / ".DS_Store").write_bytes(b"ignored-a")
    (root / "TEMPLATE_VERSION").write_text("ignored-a", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "ignored.js").write_text("ignored-a", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-a", encoding="utf-8")
    (root / "outside-link.txt").symlink_to(outside)
    return root


@pytest.mark.parametrize("path", [DOCKERFILE, SKILL_MD], ids=lambda path: path.name)
def test_fingerprint_static_contract(path: Path):
    content = path.read_text(encoding="utf-8")
    assert FINGERPRINT_COMMAND in content
    assert "| sort | xargs md5sum" not in content


def test_skill_compares_image_and_source_hashes_with_diagnostics():
    content = SKILL_MD.read_text(encoding="utf-8")
    assert "image_hash=" in content
    assert "source_hash=" in content
    assert 'test "$image_hash" = "$source_hash"' in content
    assert "expected=" in content
    assert "actual=" in content


@pytest.mark.parametrize("locale", ["C", "C.UTF-8", "en_US.UTF-8"])
def test_fingerprint_matches_c_locale(tmp_path: Path, locale: str):
    root = _make_fixture(tmp_path)
    assert _fingerprint(root, locale) == _fingerprint(root, "C")


def test_fingerprint_ignores_excluded_files_and_external_symlink(tmp_path: Path):
    root = _make_fixture(tmp_path)
    before = _fingerprint(root)

    (root / ".DS_Store").write_bytes(b"ignored-b")
    (root / "TEMPLATE_VERSION").write_text("ignored-b", encoding="utf-8")
    (root / "node_modules" / "ignored.js").write_text("ignored-b", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside-b", encoding="utf-8")

    assert _fingerprint(root) == before


def test_fingerprint_handles_special_names_and_detects_one_byte_change(tmp_path: Path):
    root = _make_fixture(tmp_path)
    before = _fingerprint(root)
    target = root / "line\nbreak.txt"
    target.write_bytes(b"newline-name!")

    assert _fingerprint(root) != before


@pytest.mark.parametrize(
    ("template", "command", "expected_c", "_historical_en_us"),
    REFERENCE_HASHES,
    ids=lambda value: value.name if isinstance(value, Path) else str(value),
)
def test_ubuntu_c_reference_fixture(template: Path, command: str, expected_c: str, _historical_en_us: str):
    assert _fingerprint(template, "C", command) == expected_c


@pytest.mark.parametrize(
    ("template", "command", "expected_c", "historical_en_us"),
    REFERENCE_HASHES,
    ids=lambda value: value.name if isinstance(value, Path) else str(value),
)
def test_en_us_uses_c_collation_not_historical_locale_value(template: Path, command: str, expected_c: str, historical_en_us: str):
    actual = _fingerprint(template, "en_US.UTF-8", command)
    assert actual == expected_c
    assert actual != historical_en_us
