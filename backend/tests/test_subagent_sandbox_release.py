"""Subagent chains must not release the parent run's inherited sandbox (P3-1, 2026-09-29).

Before this fix, SandboxMiddleware.after_agent released unconditionally, so a
subagent finishing its run released the sandbox it had *inherited* from the
parent via task_tool's sandbox_state passthrough — tearing down the shared
container (and its warm-pool bookkeeping) underneath a still-running parent.

Covers:
- release_on_exit=False keeps the inherited sandbox acquired (state and
  context sources)
- Default release_on_exit=True keeps releasing (lead-chain regression)
- build_subagent_runtime_middlewares assembles its SandboxMiddleware with
  release_on_exit=False, lead builder leaves it True
"""

from types import SimpleNamespace

import pytest

import deerflow.sandbox.middleware as sandbox_middleware_module
from deerflow.sandbox.middleware import SandboxMiddleware


class _RecordingProvider:
    def __init__(self) -> None:
        self.released: list[str] = []

    def acquire(self, thread_id: str) -> str:
        return f"sandbox-{thread_id}"

    def release(self, sandbox_id: str) -> None:
        self.released.append(sandbox_id)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> _RecordingProvider:
    recording = _RecordingProvider()
    monkeypatch.setattr(sandbox_middleware_module, "get_sandbox_provider", lambda: recording)
    return recording


def _runtime(context: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(context=context or {})


class TestReleaseOnExitFlag:
    def test_inherited_state_sandbox_is_kept(self, provider):
        middleware = SandboxMiddleware(release_on_exit=False)
        state = {"sandbox": {"sandbox_id": "parent-sbx"}}

        result = middleware.after_agent(state, _runtime())

        assert result is None
        assert provider.released == []

    def test_inherited_context_sandbox_is_kept(self, provider):
        middleware = SandboxMiddleware(release_on_exit=False)
        state: dict = {}

        result = middleware.after_agent(state, _runtime({"sandbox_id": "parent-sbx"}))

        assert result is None
        assert provider.released == []

    def test_no_sandbox_noop_with_release_disabled(self, provider):
        middleware = SandboxMiddleware(release_on_exit=False)

        result = middleware.after_agent({}, _runtime())

        assert result is None
        assert provider.released == []

    def test_default_still_releases_state_sandbox(self, provider):
        """Lead-chain regression: the default keeps the old release behavior."""
        middleware = SandboxMiddleware()

        result = middleware.after_agent({"sandbox": {"sandbox_id": "lead-sbx"}}, _runtime())

        assert result is None
        assert provider.released == ["lead-sbx"]

    def test_default_still_releases_context_sandbox(self, provider):
        middleware = SandboxMiddleware()

        result = middleware.after_agent({}, _runtime({"sandbox_id": "lead-sbx"}))

        assert result is None
        assert provider.released == ["lead-sbx"]

    def test_explicit_release_on_exit_true_releases(self, provider):
        middleware = SandboxMiddleware(release_on_exit=True)

        middleware.after_agent({"sandbox": {"sandbox_id": "lead-sbx"}}, _runtime())

        assert provider.released == ["lead-sbx"]


class TestChainAssembly:
    def _app_config(self):
        from deerflow.config.app_config import AppConfig
        from deerflow.config.guardrails_config import GuardrailsConfig
        from deerflow.config.sandbox_config import SandboxConfig

        return AppConfig(sandbox=SandboxConfig(use="test"), guardrails=GuardrailsConfig(enabled=False))

    def test_subagent_chain_does_not_release_the_inherited_sandbox(self, provider):
        """End to end at the middleware seam: the assembled subagent chain's
        SandboxMiddleware sees the parent's sandbox_state after the subagent
        finishes and must leave it acquired."""
        from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares

        chain = build_subagent_runtime_middlewares(app_config=self._app_config(), agent_name="general-purpose", lazy_init=True)
        sandbox_middlewares = [m for m in chain if isinstance(m, SandboxMiddleware)]
        assert len(sandbox_middlewares) == 1

        sandbox_middlewares[0].after_agent({"sandbox": {"sandbox_id": "parent-sbx"}}, _runtime())

        assert provider.released == []

    def test_subagent_builder_flags_release_off_lead_builder_keeps_default(self):
        from deerflow.agents.middlewares.tool_error_handling_middleware import (
            build_lead_runtime_middlewares,
            build_subagent_runtime_middlewares,
        )

        subagent_chain = build_subagent_runtime_middlewares(app_config=self._app_config(), agent_name="general-purpose", lazy_init=True)
        lead_chain = build_lead_runtime_middlewares(app_config=self._app_config(), lazy_init=True)

        subagent_flags = [m._release_on_exit for m in subagent_chain if isinstance(m, SandboxMiddleware)]
        lead_flags = [m._release_on_exit for m in lead_chain if isinstance(m, SandboxMiddleware)]

        assert subagent_flags == [False]
        assert lead_flags == [True]
