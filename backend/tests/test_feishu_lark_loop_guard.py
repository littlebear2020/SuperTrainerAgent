"""R5/Q9（2026-09-24 批 1）：lark-oapi 版本钉 + SDK loop 捕获点结构守护.

feishu.py `_run_ws` 的 loop patch 依赖 lark-oapi 1.5.5 的两处内部 loop 捕获点
（ws/client.py 模块级 + core/cache/expiring_cache.py 实例级）。本测试把这两个
前提钉死：

* backend/pyproject.toml 必须**精确钉** lark-oapi（==1.5.5 形态）——放宽到
  >= 会静默引入未审的 SDK 结构变化；
* 安装的 SDK 必须仍是已知形态（模块级 ``loop`` 存在且为事件循环；ExpiringCache
  构造即捕获 loop 并 create_task）。SDK 升级后任一形态变化 → 本文件红 →
  强制人工重审 feishu.py 的 patch 点位（Q9 红线）。
"""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path

import pytest

lark = pytest.importorskip("lark_oapi", reason="lark-oapi 未安装（channels 可选依赖）")

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_pyproject_pins_lark_oapi_exact_version():
    pyproject = (_BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'"lark-oapi([><=~!]=?[^"]*)"?,', pyproject)
    assert m, "pyproject.toml 缺 lark-oapi 依赖声明"
    spec = m.group(1)
    assert re.fullmatch(r"==[\d.]+", spec), f"lark-oapi 必须精确钉版本（现值 {spec!r}）——R5/Q9(2026-09-24)：feishu.py 的 loop patch 依赖 SDK 内部捕获点结构，升级须人工重审 patch 点位并更新本守护"


def test_ws_client_module_level_loop_capture_exists():
    """捕获点 1：ws.client 模块级 ``loop`` 存在且是事件循环（被 feishu.py patch 的对象）。"""
    import lark_oapi.ws.client as ws_client_mod

    captured = getattr(ws_client_mod, "loop", None)
    assert isinstance(captured, asyncio.AbstractEventLoop), (
        "lark-oapi ws/client.py 的模块级 loop 捕获点消失或变形——feishu.py `_run_ws` 的 `_ws_client_mod.loop = loop` patch 不再命中全部使用点，必须重审（对照 1.5.5 的 ws/client.py:25-29 与 :114/:120/:122/:126/:127/:159/:171 使用位）"
    )


def test_expiring_cache_captures_loop_at_init():
    """捕获点 2：ExpiringCache 构造即捕获 loop 并 create_task（ws.Client.__init__ 内）。"""
    from lark_oapi.core.cache import ExpiringCache

    src = inspect.getsource(ExpiringCache.__init__)
    assert "get_event_loop" in src and "create_task" in src, "ExpiringCache.__init__ 的 loop 捕获形态变化——feishu.py 依赖『ws.Client 构造必须发生在专用线程、set_event_loop 之后』这一前提（对照 1.5.5 的 expiring_cache.py:12-17）"


def test_feishu_run_ws_patches_module_loop(monkeypatch):
    """feishu._run_ws 真跑一遍（假 lark.ws.Client）：模块级 loop 被换成本线程 loop。"""
    import asyncio
    import threading

    import lark_oapi.ws.client as ws_client_mod

    from app.channels.feishu import FeishuChannel

    started = threading.Event()
    seen: dict = {}
    original_loop = ws_client_mod.loop

    class _FakeWsClient:
        def __init__(self, **kwargs):
            seen["init_loop"] = asyncio.get_event_loop()

        def start(self):
            seen["start_loop"] = ws_client_mod.loop
            started.set()

    class _FakeHandlerBuilder:
        def register_p2_im_message_receive_v1(self, cb):
            return self

        def build(self):
            return None

    monkeypatch.setattr(lark, "EventDispatcherHandler", type("B", (), {"builder": staticmethod(lambda *a: _FakeHandlerBuilder())}))
    monkeypatch.setattr(lark.ws, "Client", _FakeWsClient)

    channel = FeishuChannel.__new__(FeishuChannel)  # 跳过 __init__ 的 bus/config 依赖
    channel._running = True

    t = threading.Thread(target=channel._run_ws, args=("app", "sec", "https://open.feishu.cn"), daemon=True)
    t.start()
    assert started.wait(timeout=5), "_run_ws 未在时限内到达 start()"
    t.join(timeout=5)

    assert seen["init_loop"] is seen["start_loop"], "ws.Client 构造与 start() 绑定了不同的 loop"
    assert seen["start_loop"] is not original_loop or original_loop is None, "模块级 loop 未被替换（patch 失效）"
    # 收尾：模块 loop 留在线程 loop 上——_run_ws 语义即如此（线程 loop 无人再跑，
    # 恢复原引用以免污染其他测试）
    ws_client_mod.loop = original_loop
