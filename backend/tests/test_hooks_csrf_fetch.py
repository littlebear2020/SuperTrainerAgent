"""E3: hooks.ts 3 处 raw fetch 须用 CSRF-aware fetcher（非 globalThis.fetch→403）。"""

import re
from pathlib import Path

_HOOKS_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src" / "core" / "video-gen" / "hooks.ts"

# 直接调 fetch 的导出 mutation hooks（E3 修的是其中 3 个；M6 道具批新增 2 个 +
# 角色排序 1 个同形态——凡直连后端的 hook 都必须走 shadow 的 CSRF fetch）。
_DIRECT_FETCH_HOOKS = [
    "useGenerateCharacterSheet",
    "useUploadCharacterSheet",
    "useDeleteCharacterSheet",
    "useReorderCharacterSheets",
    "useRedoVimaxShot",
    "useUploadPropAsset",
    "useDeletePropAsset",
]


def _hook_body(src: str, name: str) -> str:
    """Slice an exported hook's source span (from `export function <name>` to the next export)."""
    start = src.index(f"export function {name}")
    nxt = src.find("\nexport ", start + 1)
    return src[start:] if nxt == -1 else src[start:nxt]


def test_hooks_uses_csrf_aware_fetch():
    """E3: useGenerateCharacterSheet/useUploadCharacterSheet/useRedoVimaxShot 旧用 raw
    globalThis.fetch（无 X-CSRF-Token）→ csrf_middleware 403。须 import { fetch } from
    fetcher（shadow global fetch，自动 mint X-CSRF-Token）。"""
    src = _HOOKS_SRC.read_text(encoding="utf-8")

    # 1) 不得存在绕开 helper 的裸全局 fetch 调用点（shadow import 之外的后门）。
    assert 'import { fetch } from "@/core/api/fetcher"' in src, "hooks.ts 须 import { fetch } from fetcher（CSRF-aware）"
    assert "globalThis.fetch(" not in src, "hooks.ts 不得直接调 globalThis.fetch（无 X-CSRF-Token → 403）"
    assert "window.fetch(" not in src, "hooks.ts 不得直接调 window.fetch（无 X-CSRF-Token → 403）"

    # 2) 每个直连后端的导出 hook 都必须经 shadow 的 fetch（= CSRF helper）发出请求。
    for name in _DIRECT_FETCH_HOOKS:
        body = _hook_body(src, name)
        assert re.search(r"\bfetch\(", body), f"{name} 未调用 shadow 的 fetch（CSRF helper）——直连后端却绕过 fetcher 会 403"
