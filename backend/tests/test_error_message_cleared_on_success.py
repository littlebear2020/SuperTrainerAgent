"""行为锁：任务成功时必须清空 error_message。

error_message 有 9 处写入（retry/restart/stall 标记）、0 处清空（复核确认）。
任务最终成功后残留仍在行，video-gen-detail.tsx:575 无条件渲染 error_message，
导致能正常播放的视频顶着红色「错误信息」框。
线上未观测到是因为测试任务未经历重试/重启——条件触发，代码缺陷确凿。

断言方式：用 AST 定位「那一次具体调用」，而非全文件 substring/DOTALL 正则。
后者会误绿——文件里任何位置出现 error_message=None 都能让全文搜索通过，
而真正要锁的是 success_kwargs 与 status="ready" 这两个调用点自身带上该字段。
"""

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _kwargs_of_call(node: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in node.keywords if kw.arg}


def _module_dict_assigns(tree: ast.Module) -> dict[str, dict[str, ast.expr]]:
    """收集模块/函数内 ``name = {...}`` 的 dict 字面量赋值，供 **解包调用解析。

    2026-08-08 P0-4：ready 分支改为 ``await progress(**completion_kwargs)`` 后，
    纯字面量关键字的 AST 扫描拿不到任何键（``kw.arg is None`` 被过滤），
    行为锁空转恒红。此处把 dict 字面量还原成 {键: 值节点} 与字面量关键字同构处理。
    """
    assigns: dict[str, dict[str, ast.expr]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                assigns[target.id] = {k.value: v for k, v in zip(node.value.keys, node.value.values) if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return assigns


def _ready_call_kwargs(tree: ast.Module) -> list[dict[str, ast.expr]]:
    """所有 status="ready" 调用的键值——含字面量关键字与 **dict 解包两种形态。"""
    dict_assigns = _module_dict_assigns(tree)
    ready_calls: list[dict[str, ast.expr]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kwargs = _kwargs_of_call(node)
        # **name 解包：并入同名 dict 字面量赋值的键值
        for kw in node.keywords:
            if kw.arg is None and isinstance(kw.value, ast.Name):
                kwargs = {**dict_assigns.get(kw.value.id, {}), **kwargs}
        status = kwargs.get("status")
        if isinstance(status, ast.Constant) and status.value == "ready":
            ready_calls.append(kwargs)
    return ready_calls


def _is_none(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def test_worker_success_kwargs_clears_error_message():
    """worker 侧 success_kwargs = dict(...) 必须含 error_message=None。"""
    src = (_REPO / "docker/production-worker/worker.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "success_kwargs" for t in node.targets):
            continue
        assert isinstance(node.value, ast.Call), "success_kwargs 预期是 dict(...) 调用"
        found.append(_kwargs_of_call(node.value))

    assert found, "worker.py 未找到 success_kwargs = dict(...) 赋值"
    for kwargs in found:
        assert "error_message" in kwargs and _is_none(kwargs["error_message"]), "worker.py 的 success_kwargs 必须包含 error_message=None，否则成功任务仍残留旧错误信息（Retry N/M / Gateway restart recovery）"
        # 同时锁住这确实是成功路径，防以后被挪到别的语义上
        assert isinstance(kwargs.get("status"), ast.Constant) and kwargs["status"].value == "ready"


def test_gateway_ready_progress_clears_error_message():
    """gateway 侧 await progress(status="ready", ...) 必须携带 error_message=None。"""
    src = (_REPO / "backend/app/gateway/_shared/task_framework/service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    ready_calls = _ready_call_kwargs(tree)

    assert ready_calls, 'task_framework/service.py 未找到 status="ready" 的调用'
    for kwargs in ready_calls:
        assert "error_message" in kwargs and _is_none(kwargs["error_message"]), 'task_framework/service.py 的 progress(status="ready") 必须携带 error_message=None，否则重试/重启标记会残留到成功任务上'
