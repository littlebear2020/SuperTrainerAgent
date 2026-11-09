"""Task 11（remotion 0 字节）: 解码后先校验再落盘。

``remotion_run.py`` 两处 sandbox 取回路径都是

    b64 = await _sandbox_exec(sandbox, f"base64 {render_dir}/out.mp4")
    output_path.write_bytes(base64.b64decode(b64))
    file_size = output_path.stat().st_size     # 记录，不校验

sandbox 里渲染失败/命令出错时 ``base64`` 可能返回空串或错误文本 → 解码出 0 字节
（或垃圾）仍照常 set_result 标成功。用户拿到打不开的文件。

修法：解码后先校验字节数再落盘，0 字节/异常小则响亮失败。
"""

import base64
from pathlib import Path

import pytest

from app.gateway.video_gen.pipelines import remotion_run


def test_decode_helper_exists():
    """须抽出可测的解码+校验 helper。"""
    assert hasattr(remotion_run, "_decode_sandbox_output"), "期望抽出 _decode_sandbox_output(b64, output_path) 承载校验"


def test_empty_b64_rejected(tmp_path):
    """空 base64（sandbox 命令失败）→ 抛错，不得落 0 字节文件。"""
    out = tmp_path / "out.mp4"
    with pytest.raises(RuntimeError, match="0 字节|empty|空"):
        remotion_run._decode_sandbox_output("", out)


def test_whitespace_b64_rejected(tmp_path):
    """只有空白的 base64 → 抛错。"""
    out = tmp_path / "out.mp4"
    with pytest.raises(RuntimeError, match="0 字节|empty|空"):
        remotion_run._decode_sandbox_output("   \n  ", out)


def test_garbage_b64_rejected(tmp_path):
    """非法/无效 base64（sandbox 吐了错误信息）→ 抛错。

    注意 base64 解码对非法字符较宽容，"bash: base64: command not found" 会被
    过滤成少量字节；关键是不能静默落一个空/垃圾文件冒充产物。
    """
    out = tmp_path / "out.mp4"
    with pytest.raises(RuntimeError):
        remotion_run._decode_sandbox_output("!!!", out)


def test_valid_b64_written(tmp_path):
    """正常 base64 → 正确落盘。"""
    payload = b"\x00\x00\x00\x18ftypmp42"  # 12B 合法 mp4 头（与既有 fixture 一致）
    out = tmp_path / "out.mp4"

    remotion_run._decode_sandbox_output(base64.b64encode(payload).decode(), out)

    assert out.exists()
    assert out.read_bytes() == payload


def test_failed_decode_leaves_no_partial_file(tmp_path):
    """校验失败时不得留下半成品文件（下游会误当产物）。"""
    out = tmp_path / "out.mp4"
    with pytest.raises(RuntimeError):
        remotion_run._decode_sandbox_output("", out)
    assert not out.exists(), "失败后仍留下文件 —— 下游会把它当成有效产物"


def test_both_sandbox_sites_use_helper():
    """结构断言：两处 sandbox 取回都必须走 helper（否则漏一处仍会静默产 0 字节）。"""
    src = Path(remotion_run.__file__).read_text(encoding="utf-8")
    assert src.count("_decode_sandbox_output(") >= 3, "两处 sandbox 取回路径 + 定义 = 至少 3 次出现；有路径没走 helper"
    # 只看真实代码行：docstring 里引用旧的坏写法（说明修了什么）不算违规
    import ast

    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.update(doc.splitlines())

    offenders = [ln for ln in src.splitlines() if "write_bytes(base64.b64decode(" in ln and ln.strip() not in {d.strip() for d in docstrings}]
    assert not offenders, f"仍有裸 write_bytes(b64decode(...)) 不做校验：{offenders}"
