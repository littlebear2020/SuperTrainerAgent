"""``PUT /api/skills/{name}`` must not corrupt extensions_config.json (C2).

The handler rewrites the **whole** shared config file to flip one skill's
``enabled`` flag. Two independent defects made that destructive:

1. **Non-atomic, unlocked.** A bare ``open(path, "w") + json.dump``
   truncates the live file before writing, so a crash mid-write wipes every
   MCP server. It also did not take ``_config_write_lock``, while
   ``PUT /api/mcp/config`` writes the *same* file under that lock — the two
   endpoints could interleave read-merge-write and lose each other's data.
   ``mcp.py`` already solved this with ``_atomic_write_json``; skills.py
   simply did not use it (despite already importing ``atomic_write`` for
   SKILL.md at ``:202`` / ``:321``).

2. **``$VAR`` placeholders destroyed.** The merge source was the *in-memory*
   ``get_extensions_config()``, which has already been through
   ``resolve_env_variables`` — and that function replaces **unresolved**
   variables with empty strings. So toggling any skill rewrote every MCP
   server's ``env`` from ``"$GITHUB_TOKEN"`` to either the resolved secret
   (leaking it to disk) or ``""`` (silently breaking that server). This is
   the same class of problem as the secret-writeback risk, and it was
   already happening in production.

The fix mirrors ``mcp.py:277-286``: re-read the raw JSON from disk as the
merge source and write through the shared atomic writer under the shared lock.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from app.gateway.routers import skills as skills_router

_RAW_CONFIG = {
    "mcpServers": {
        "github": {
            "enabled": True,
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            # Deliberately unset in the environment: resolve_env_variables
            # turns this into "" in the in-memory object.
            "env": {"GITHUB_TOKEN": "$DEFINITELY_UNSET_TOKEN_FOR_TEST"},
        }
    },
    "skills": {"demo-skill": {"enabled": False}},
    # A top-level key neither endpoint models — must survive a skill toggle.
    "mcpInterceptors": {"keep": "me"},
}


@pytest.fixture()
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "extensions_config.json"
    path.write_text(json.dumps(_RAW_CONFIG, indent=2), encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(path))
    monkeypatch.delenv("DEFINITELY_UNSET_TOKEN_FOR_TEST", raising=False)
    skills_router.reload_extensions_config()
    return path


class TestSharedWriteDiscipline:
    def test_skills_and_mcp_share_one_write_lock(self) -> None:
        """Two writers to one file must serialize against the same lock.

        Separate locks would be no lock at all: each endpoint would happily
        read-merge-write while the other was mid-sequence.
        """
        assert skills_router._config_write_lock is skills_router._mcp_config_write_lock
        assert isinstance(skills_router._config_write_lock, type(threading.Lock()))

    def test_skills_uses_the_atomic_writer(self) -> None:
        """Not a bare open(w): the config must never be truncated in place."""
        source = Path(skills_router.__file__).read_text(encoding="utf-8")
        # The destructive pattern must be gone from the config write path.
        assert 'open(config_path, "w"' not in source
        assert "_atomic_write_json" in source


class TestEnvPlaceholdersSurvive:
    @pytest.mark.anyio
    async def test_toggling_a_skill_preserves_var_placeholders(self, config_file: Path) -> None:
        """The headline bug: ``$VAR`` must still be ``$VAR`` after a toggle.

        Before the fix this became ``""`` (the unresolved-variable
        substitution), silently breaking the MCP server on next launch.
        """
        await skills_router._persist_skill_state("demo-skill", True)

        on_disk = json.loads(config_file.read_text(encoding="utf-8"))

        assert on_disk["mcpServers"]["github"]["env"]["GITHUB_TOKEN"] == "$DEFINITELY_UNSET_TOKEN_FOR_TEST"

    @pytest.mark.anyio
    async def test_a_resolved_secret_is_not_written_back_to_disk(self, config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the variable *is* set, the secret must not land in the file."""
        monkeypatch.setenv("DEFINITELY_UNSET_TOKEN_FOR_TEST", "ghp_supersecret")
        skills_router.reload_extensions_config()

        await skills_router._persist_skill_state("demo-skill", True)

        text = config_file.read_text(encoding="utf-8")
        assert "ghp_supersecret" not in text
        assert "$DEFINITELY_UNSET_TOKEN_FOR_TEST" in text

    @pytest.mark.anyio
    async def test_the_skill_flag_actually_changes(self, config_file: Path) -> None:
        """Preserving everything else is worthless if the write is a no-op."""
        await skills_router._persist_skill_state("demo-skill", True)

        on_disk = json.loads(config_file.read_text(encoding="utf-8"))

        assert on_disk["skills"]["demo-skill"]["enabled"] is True

    @pytest.mark.anyio
    async def test_unmodelled_top_level_keys_survive(self, config_file: Path) -> None:
        """``mcpInterceptors`` et al. are not in either endpoint's schema."""
        await skills_router._persist_skill_state("demo-skill", True)

        on_disk = json.loads(config_file.read_text(encoding="utf-8"))

        assert on_disk["mcpInterceptors"] == {"keep": "me"}

    @pytest.mark.anyio
    async def test_server_command_and_args_are_untouched(self, config_file: Path) -> None:
        await skills_router._persist_skill_state("demo-skill", True)

        on_disk = json.loads(config_file.read_text(encoding="utf-8"))

        assert on_disk["mcpServers"]["github"]["command"] == "npx"
        assert on_disk["mcpServers"]["github"]["args"] == ["-y", "@modelcontextprotocol/server-github"]


class TestCrashSafety:
    @pytest.mark.anyio
    async def test_a_failed_write_leaves_the_previous_config_intact(self, config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A crash mid-write must not wipe every configured MCP server."""
        original = config_file.read_text(encoding="utf-8")

        def boom(*args, **kwargs):
            raise RuntimeError("process died mid-write")

        monkeypatch.setattr("app.gateway.routers.mcp.json.dump", boom)

        with pytest.raises(RuntimeError):
            await skills_router._persist_skill_state("demo-skill", True)

        assert config_file.read_text(encoding="utf-8") == original
        assert json.loads(config_file.read_text(encoding="utf-8"))["mcpServers"]["github"]["command"] == "npx"
