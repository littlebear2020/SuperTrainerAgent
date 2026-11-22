from __future__ import annotations

import json
import subprocess
import sys
import tarfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release_archive.py"
REPO_ROOT = SCRIPT.parents[1]


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit(repo: Path, message: str = "change") -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "artifact.txt").write_text("version A\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs/tracked.md").write_text("tracked\n", encoding="utf-8")
    commit(repo, "base")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    return repo


def run_cli(repo: Path, output: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--ref", "origin/main", "--output", str(output), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def read_text_member(archive: Path, name: str) -> str:
    with tarfile.open(archive, "r:gz") as bundle:
        member = bundle.extractfile(name)
        assert member is not None
        return member.read().decode()


def manifest(archive: Path) -> dict:
    return json.loads(read_text_member(archive, "release-manifest.json"))


def test_archive_contains_only_committed_content_and_manifest(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    source_sha = git(repo, "rev-parse", "HEAD")
    (repo / "artifact.txt").write_text("version B\n", encoding="utf-8")
    (repo / "secret-placeholder.txt").write_text("DO_NOT_SHIP\n", encoding="utf-8")
    output = tmp_path / "release.tar.gz"

    result = run_cli(repo, output)

    assert result.returncode == 0, result.stderr
    assert read_text_member(output, "artifact.txt") == "version A\n"
    with tarfile.open(output, "r:gz") as bundle:
        names = bundle.getnames()
    assert "secret-placeholder.txt" not in names
    assert names.count("release-manifest.json") == 1
    metadata = manifest(output)
    assert metadata["source_sha"] == source_sha
    assert metadata["requested_ref"] == "origin/main"
    assert metadata["base_ref"] == "origin/main"
    assert metadata["base_sha"] == source_sha
    assert metadata["includes_local_config"] is False
    assert metadata["config_files"] == []


def test_unmerged_ref_requires_opt_in_and_failure_preserves_output(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-b", "feature")
    (repo / "artifact.txt").write_text("feature\n", encoding="utf-8")
    feature_sha = commit(repo, "feature")
    output = tmp_path / "release.tar.gz"
    output.write_bytes(b"previous-release")

    rejected = subprocess.run(
        [sys.executable, str(SCRIPT), "--ref", "feature", "--output", str(output)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode == 1
    assert output.read_bytes() == b"previous-release"

    allowed = subprocess.run(
        [sys.executable, str(SCRIPT), "--ref", "feature", "--allow-unmerged", "--output", str(output)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert allowed.returncode == 0, allowed.stderr
    assert manifest(output)["source_sha"] == feature_sha
    assert read_text_member(output, "artifact.txt") == "feature\n"


def test_exclude_and_explicit_local_config(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    cache = repo / "docker/sandbox/cache"
    (cache / "chrome-linux").mkdir(parents=True)
    (cache / "chrome-linux/chrome").write_text("chrome", encoding="utf-8")
    for relative in ("python-wheels/pkg.whl", "tiktoken/base.bpe", "fonts/font.ttf"):
        path = cache / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    commit(repo, "cache")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    (repo / "config.actual.yaml").write_text("version: test\n", encoding="utf-8")
    (repo / "config.yaml").symlink_to("config.actual.yaml")
    (repo / ".env").write_text("TEST_PLACEHOLDER=not-a-secret\n", encoding="utf-8")
    (repo / "frontend").mkdir()
    (repo / "frontend/.env").write_text("FRONTEND_PLACEHOLDER=not-a-secret\n", encoding="utf-8")
    output = tmp_path / "release.tar.gz"

    result = run_cli(
        repo,
        output,
        "--include-local-config",
        "--exclude",
        "docker/sandbox/cache/chrome-linux",
    )

    assert result.returncode == 0, result.stderr
    with tarfile.open(output, "r:gz") as bundle:
        names = bundle.getnames()
    assert "docker/sandbox/cache/chrome-linux/chrome" not in names
    assert "docker/sandbox/cache/python-wheels/pkg.whl" in names
    assert "docker/sandbox/cache/tiktoken/base.bpe" in names
    assert "docker/sandbox/cache/fonts/font.ttf" in names
    assert read_text_member(output, "config.yaml") == "version: test\n"
    assert read_text_member(output, ".env") == "TEST_PLACEHOLDER=not-a-secret\n"
    assert read_text_member(output, "frontend/.env") == "FRONTEND_PLACEHOLDER=not-a-secret\n"
    assert manifest(output)["config_files"] == ["config.yaml", ".env", "frontend/.env"]


def test_invalid_config_and_exclude_never_replace_existing_output(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    output = tmp_path / "release.tar.gz"

    (repo / "config.yaml").write_text("version: test\n", encoding="utf-8")
    missing_env = run_cli(repo, output, "--include-local-config")
    assert missing_env.returncode == 1
    assert not output.exists()

    (repo / ".env").write_text("TEST_PLACEHOLDER=not-a-secret\n", encoding="utf-8")
    (repo / "config.yaml").unlink()
    outside = tmp_path / "outside.yaml"
    outside.write_text("outside\n", encoding="utf-8")
    (repo / "config.yaml").symlink_to(outside)
    output.write_bytes(b"previous-release")
    outside_link = run_cli(repo, output, "--include-local-config")
    assert outside_link.returncode == 1
    assert output.read_bytes() == b"previous-release"

    invalid_exclude = run_cli(repo, output, "--exclude", "../outside")
    assert invalid_exclude.returncode == 1
    assert output.read_bytes() == b"previous-release"


def test_wrapper_scripts_delegate_to_immutable_packaging() -> None:
    production = (REPO_ROOT / "deploy_on_server.sh").read_text(encoding="utf-8")
    test = (REPO_ROOT / "deploy_to_test_server.sh").read_text(encoding="utf-8")

    assert "scripts/release_archive.py" in production
    assert "scripts/pre_deploy_check.py" in production
    assert "DEPLOY_REF:-origin/main" in production
    assert "DEPLOY_INCLUDE_LOCAL_CONFIG" in production
    assert "scripts/release_archive.py" in test
    assert "TEST_DEPLOY_REF:-origin/main" in test
    assert "TEST_ALLOW_UNMERGED" in test
    assert "TEST_INCLUDE_LOCAL_CONFIG" in test
    for script in (production, test):
        assert "docker/sandbox/cache/chrome-linux" in script
        assert "--exclude=docker/sandbox/cache'" not in script
        assert "release-manifest.json" in script
