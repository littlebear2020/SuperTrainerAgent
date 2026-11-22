from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "agent_context.py"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit_file(repo: Path, relative: str, content: str, message: str) -> str:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(repo, "add", relative)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    commit_file(repo, "tracked.txt", "base\n", "base")
    commit_file(repo, "docs/plans/demo.md", "plan v1\n", "plan")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    return repo


def load_module():
    spec = importlib.util.spec_from_file_location("agent_context", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_context_reports_git_state_dirty_paths_and_spec_hashes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-b", "feature")
    feature_sha = commit_file(repo, "tracked.txt", "feature\n", "feature")
    git(repo, "checkout", "main")
    commit_file(repo, "main.txt", "main advanced\n", "main advanced")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    git(repo, "checkout", "feature")
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    context = load_module().build_context(repo, "origin/main", "docs/plans/demo.md")

    assert context["head_sha"] == feature_sha
    assert context["branch"] == "feature"
    assert context["base_ref"] == "origin/main"
    assert context["base_sha"] == git(repo, "rev-parse", "main")
    assert context["ahead"] == 1
    assert context["behind"] == 1
    assert context["dirty"] is True
    assert context["changed_paths"] == ["tracked.txt", "untracked.txt"]
    assert context["patch_equivalent_to_base"] is False
    assert context["worktrees"] == [str(repo.resolve())]
    assert context["spec_path"] == "docs/plans/demo.md"
    assert context["spec_blob_sha"] == git(repo, "rev-parse", "HEAD:docs/plans/demo.md")
    assert context["spec_sha256"] == hashlib.sha256(b"plan v1\n").hexdigest()


def test_context_distinguishes_patch_equivalent_and_divergent_branches(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-b", "feature")
    commit_file(repo, "tracked.txt", "feature\n", "feature")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", "feature")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    git(repo, "checkout", "feature")

    assert load_module().build_context(repo, "origin/main", None)["patch_equivalent_to_base"] is True

    git(repo, "checkout", "-b", "divergent")
    commit_file(repo, "other.txt", "different\n", "different change")
    assert load_module().build_context(repo, "origin/main", None)["patch_equivalent_to_base"] is False


def test_cli_json_human_output_and_required_guards(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    expected_hash = hashlib.sha256(b"plan v1\n").hexdigest()
    base_args = (
        "--base",
        "origin/main",
        "--spec",
        "docs/plans/demo.md",
        "--require-clean",
        "--require-head",
    )

    result = run_cli(repo, *base_args, "--expect-spec-sha256", expected_hash, "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["spec_sha256"] == expected_hash
    assert payload["dirty"] is False

    result = run_cli(repo, *base_args)
    assert result.returncode == 0, result.stderr
    assert "HEAD" in result.stdout
    assert "origin/main" in result.stdout
    assert "docs/plans/demo.md" in result.stdout

    (repo / "docs/plans/demo.md").write_text("plan v2\n", encoding="utf-8")
    assert run_cli(repo, *base_args).returncode == 1
    assert run_cli(repo, "--spec", "docs/plans/demo.md", "--expect-spec-sha256", expected_hash).returncode == 1

    git(repo, "checkout", "--", "docs/plans/demo.md")
    commit_file(repo, "feature.txt", "feature\n", "feature")
    assert run_cli(repo, *base_args).returncode == 1
    assert run_cli(repo, "--spec", "missing.md").returncode == 1


def test_cli_rejects_git_failures(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    result = run_cli(repo, "--base", "origin/does-not-exist")
    assert result.returncode == 1
    assert result.stderr


@pytest.mark.parametrize("dirty_content", ["tracked change", "untracked change"])
def test_default_mode_allows_dirty_worktree(tmp_path: Path, dirty_content: str) -> None:
    repo = make_repo(tmp_path)
    if dirty_content == "tracked change":
        (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    else:
        (repo / "new-file.txt").write_text("new\n", encoding="utf-8")
    result = run_cli(repo, "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["dirty"] is True


def test_untracked_spec_has_content_hash_without_blob(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    spec_path = repo / "docs/plans/untracked.md"
    spec_path.write_text("new plan\n", encoding="utf-8")

    context = load_module().build_context(repo, "origin/main", "docs/plans/untracked.md")

    assert context["spec_blob_sha"] is None
    assert context["spec_sha256"] == hashlib.sha256(b"new plan\n").hexdigest()


def test_expect_spec_hash_requires_spec_path(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    result = run_cli(repo, "--expect-spec-sha256", "a" * 64)
    assert result.returncode == 1
    assert "--spec" in result.stderr
