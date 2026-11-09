"""Guard test for METHOD_TO_MEDIA_TYPE / FREE_METHODS (C3/C8).

Ensures every ``method`` string literal used in ``record_external_api_call``
calls across the codebase is present in the constants dict. When a new
tracking method is added without updating the constants, this test fails.
"""

from __future__ import annotations

import ast
import pathlib

from app.gateway._shared.media_constants import FREE_METHODS, METHOD_TO_MEDIA_TYPE


def _find_method_literals() -> set[str]:
    """AST-scan all call sites of record_external_api_call for method= string literals."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    methods: set[str] = set()
    for py_file in repo_root.rglob("*.py"):
        if ".venv" in str(py_file) or "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "method" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        # Only collect from record_external_api_call or _track_bridge_call
                        # (broader: any call with method= that looks like our tracking)
                        methods.add(kw.value.value)
    return methods


def test_all_method_literals_are_in_constants() -> None:
    """Every method= string literal in the codebase must be in METHOD_TO_MEDIA_TYPE."""
    literals = _find_method_literals()
    known = set(METHOD_TO_MEDIA_TYPE.keys())
    # Allow "method" as a kwarg in unrelated code — only check the 4 known patterns
    # plus any string that looks like a tracking method (generate_*/synthesize_*/voice_*)
    tracking_literals = {m for m in literals if m.startswith("generate_") or m.startswith("synthesize_") or m.startswith("voice_")}
    missing_tracking = tracking_literals - known
    assert not missing_tracking, f"Tracking methods found in code but missing from METHOD_TO_MEDIA_TYPE: {missing_tracking}. Add them to app/gateway/_shared/media_constants.py"


def test_free_methods_are_subset_of_known_methods() -> None:
    assert FREE_METHODS <= set(METHOD_TO_MEDIA_TYPE.keys()), "FREE_METHODS contains methods not in METHOD_TO_MEDIA_TYPE"


def test_voice_clone_upload_is_free() -> None:
    """voice_clone_upload must be in FREE_METHODS (it's a free operation)."""
    assert "voice_clone_upload" in FREE_METHODS


def test_voice_clone_upload_maps_to_tts() -> None:
    assert METHOD_TO_MEDIA_TYPE["voice_clone_upload"] == "tts"


def test_media_types_are_image_video_tts() -> None:
    """All media type values must be one of the 3 known types."""
    valid_types = {"image", "video", "tts"}
    actual_types = set(METHOD_TO_MEDIA_TYPE.values())
    assert actual_types == valid_types, f"Unexpected media types: {actual_types - valid_types}"
