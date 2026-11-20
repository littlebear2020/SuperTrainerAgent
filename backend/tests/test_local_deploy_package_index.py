"""local-deploy.sh 的 Python 包索引锚点（2026-11-20）。

背景：脚本原先把 `UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` 写死给远程 `deploy.sh`（后端镜像
`uv sync`）。2026-11-20 在开发机与容器内实测清华源包索引整站 403，tiktoken 等包都拉不到。现在默认显式走
pypi.org（与 docker-compose 的 `UV_INDEX_URL` 默认值一致），受限网络用 `PYPI_INDEX_URL` 覆盖。

边界（第三个用例钉住）：这个索引只用于后端 `uv sync`，不得传给 sandbox 镜像构建——沙箱 Dockerfile 会把 `PIP_INDEX`
烘焙成运行期 pip 源（/etc/pip.conf + ENV PIP_INDEX_URL），换成 pypi.org 会复发 2026-10-23 事故（公司网络对
pypi.org 静默丢包，agent 空等十余分钟）。沙箱的 tiktoken/regex 已 vendoring，构建期不依赖任何索引。

同口味先例：test_sandbox_offline_readiness.py。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_DEPLOY = REPO_ROOT / "scripts" / "local-deploy.sh"


def _local_deploy_code() -> str:
    """脚本的非注释行（注释里解释为什么不再用清华源，不能让它们干扰断言）。"""
    lines = LOCAL_DEPLOY.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def test_local_deploy_defaults_package_index_to_pypi_org_and_stays_overridable():
    code = _local_deploy_code()
    assert 'PYPI_INDEX_URL="${PYPI_INDEX_URL:-https://pypi.org/simple}"' in code
    assert "tuna.tsinghua" not in code, "local-deploy.sh 不得再硬编码清华源（对部分网络整站 403）"


def test_local_deploy_passes_the_index_to_the_remote_deploy_script_in_both_the_run_and_the_manual_hint():
    assert _local_deploy_code().count("UV_INDEX_URL=${PYPI_INDEX_URL}") == 2


def test_local_deploy_does_not_bake_a_build_index_into_the_sandbox_image():
    sandbox_build = next(line for line in _local_deploy_code().splitlines() if "docker/sandbox/Dockerfile" in line)
    assert "docker build" in sandbox_build
    assert "PIP_INDEX" not in sandbox_build
