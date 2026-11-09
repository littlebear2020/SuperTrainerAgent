"""Strict Blockbuster runtime context scoped to DeerFlow business code.

Creates a `BlockBuster` instance with `scanned_modules=("app", "deerflow")`
so that test infrastructure (pytest, langchain, importlib, third-party libs)
is out of scope and does not produce false positives. Only loop-blocking
sync IO whose caller stack passes through `app.*` or `deerflow.*` raises
`BlockingError`.

Used by `backend/tests/blocking_io/conftest.py` to gate the regression suite.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType

from blockbuster import BlockBuster, BlockBusterFunction, BlockingError

# Local package layout: `app/` (Gateway) and `packages/harness/deerflow/`
# (importable as `deerflow.*` via the workspace install) — both names are
# valid top-level import roots here.
_SCANNED_MODULES: tuple[str, ...] = ("app", "deerflow")

# Add DeerFlow-local rules here only when Blockbuster's default rule set misses
# a generic blocking primitive used by production code. If a path is invisible
# because no test exercises it, add a production-path runtime anchor instead.
_PROJECT_BLOCKING_RULES: tuple[tuple[str, BlockBusterFunction], ...] = ()


def _install_project_rules(bb: BlockBuster) -> None:
    for name, rule in _PROJECT_BLOCKING_RULES:
        bb.functions[name] = rule


_MISSING = object()


@contextmanager
def _mask_pathological_module_file_attrs() -> Iterator[None]:
    """Mask ``sys.modules`` entries whose ``__file__`` is not a real path.

    Some test modules register hand-built stub modules in ``sys.modules`` with
    a module-level ``__getattr__`` that fabricates a function for any missing
    attribute — including ``__file__`` (e.g. the ``pipelines._ffmpeg_helpers``
    stubs in worker-pipeline test modules). Blockbuster's frame walk calls
    ``inspect.getframeinfo`` → ``inspect.findsource`` → ``inspect.getmodule``,
    which iterates ``sys.modules`` and reads ``module.__file__``; a non-str
    value crashes stdlib ``inspect.getsourcefile`` with
    ``AttributeError: 'function' object has no attribute 'endswith'``.

    The mask value must be a truthy *str*, not None: ``inspect.getmodule``
    caches ``_filesbymodname[modname] = module.__file__`` per walk, and a None
    mask mismatches a truthy cached entry (left by an earlier registration of
    the real module under the same name), sending the module into
    ``inspect.getabsfile`` → ``inspect.getfile``, which raises
    ``TypeError: <module ... from None> is a built-in module`` for falsy
    ``__file__``. ``os.devnull`` is used because it exists on disk, so
    ``inspect.getsourcefile`` short-circuits at its ``os.path.exists`` check
    without touching the stub's fabricated ``__loader__``.
    """
    masked: list[tuple[ModuleType, object]] = []
    for module in list(sys.modules.values()):
        if not isinstance(module, ModuleType):
            continue
        try:
            file_attr = getattr(module, "__file__", None)
        except Exception:
            file_attr = _MISSING  # unreadable -> equally poisonous to inspect
        if file_attr is not None and not isinstance(file_attr, str):
            masked.append((module, module.__dict__.get("__file__", _MISSING)))
            module.__dict__["__file__"] = os.devnull
    try:
        yield
    finally:
        for module, original in masked:
            if original is _MISSING:
                module.__dict__.pop("__file__", None)
            else:
                module.__dict__["__file__"] = original


@contextmanager
def detect_blocking_io_strict() -> Iterator[BlockBuster]:
    """Activate Blockbuster scoped to app.* and deerflow.* callers only."""
    bb = BlockBuster(scanned_modules=list(_SCANNED_MODULES))
    _install_project_rules(bb)
    with _mask_pathological_module_file_attrs():
        try:
            bb.activate()
            yield bb
        finally:
            bb.deactivate()


__all__ = ["BlockingError", "detect_blocking_io_strict"]
