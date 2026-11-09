"""沙箱离线就绪锚点（2026-10-23 公司服务器 course-video 事故热修，release/v2）。

事故链：agent 在工作区旧 venv 里 `import cv2` 失败（venv 隔离系统包，镜像其实预装
OpenCV）→ 转而运行期 `pip install`→ 任务用户的 pip 读不到只配给 root 的清华源、回退
pypi.org → 公司网络国外静默丢包，下载永不完成 → agent sleep 空等十余分钟。

两层防线各钉一组锚点：
- SKILL.md「沙箱离线纪律」节（技能层主修——禁运行期安装、用系统 python3、venv 陷阱）；
- docker/sandbox/Dockerfile 的全局 pip 源（/etc/pip.conf + PIP_INDEX_URL）与 python3.10-venv
  （镜像层防御纵深——任务用户 pip 不再挂死 pypi.org；venv 不再半残）。

同口味先例：test_auto_video_flow_skill.py（SKILL.md 锚点）。改锚点措辞须同步改这里。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_MD = REPO_ROOT / "skills" / "public" / "auto-video-flow" / "SKILL.md"
DOCKERFILE = REPO_ROOT / "docker" / "sandbox" / "Dockerfile"

OFFLINE_DISCIPLINE_ANCHORS = (
    "沙箱离线纪律",
    "禁止任何运行期安装",
    "静默挂起",
    "不要创建或激活 venv",
    "cv2",
    "不要先 `rm",
)


@pytest.mark.parametrize("anchor", OFFLINE_DISCIPLINE_ANCHORS, ids=lambda a: a)
def test_skill_md_has_offline_discipline_anchor(anchor: str):
    content = SKILL_MD.read_text(encoding="utf-8")
    assert anchor in content, f"SKILL.md 缺离线纪律锚点 {anchor!r}——沙箱离线纪律节被误删/漂移？"


def test_offline_discipline_sits_in_prerequisites():
    """纪律必须在「前置要求」节内（agent 读环境红线的第一落点），而非埋在文末。"""
    content = SKILL_MD.read_text(encoding="utf-8")
    pre = content.index("## 前置要求")
    nxt = content.index("## 创建 Remotion 项目")
    assert pre < content.index("沙箱离线纪律") < nxt, "「沙箱离线纪律」应位于「前置要求」节内"


def _dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_pins_sandbox_base_1110():
    """K20：基座钉 1.11.0（浮动 latest 缺 /v1/bash/exec 路由，10-13 事故）。"""
    assert re.search(r"^ARG BASE_IMAGE=.*all-in-one-sandbox:1\.11\.0\s*$", _dockerfile(), re.M)


def test_dockerfile_writes_system_wide_pip_index():
    """清华源必须对所有用户生效：/etc/pip.conf 落盘 + PIP_INDEX_URL 环境变量双保险。

    修前 `pip3 config set global.index-url` 只写 root 的 ~/.config/pip/pip.conf，任务用户
    （uid 1000）与 venv 内 pip 全部回退 pypi.org。
    """
    df = _dockerfile()
    assert "/etc/pip.conf" in df, "Dockerfile 未写系统级 /etc/pip.conf"
    assert re.search(r"^ENV\s+PIP_INDEX_URL=", df, re.M), "Dockerfile 未设 ENV PIP_INDEX_URL（venv/任务用户 pip 兜底）"


def test_dockerfile_installs_python_venv_package():
    """`python3 -m venv` 需 python3.10-venv（修前报 ensurepip 不可用，agent 手动 ensurepip 凑半残 venv）。"""
    assert re.search(r"python3\.10-venv", _dockerfile()), "Dockerfile 未安装 python3.10-venv"
