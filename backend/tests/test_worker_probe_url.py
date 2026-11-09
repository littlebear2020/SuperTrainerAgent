"""Task 10 (NEW-3): worker 探测恒失败 → v2/v3 管线在生产完全不可达。

``worker_probe.py`` 的探测地址是

    url = worker_url or os.environ.get("WORKER_HEALTH_URL", "http://localhost:8040")

而 ``WORKER_HEALTH_URL`` **全仓、全 compose 文件零处设置**（已 grep 确认）→ 恒用
``http://localhost:8040``。

但生产拓扑是（docker-compose-deploy.yaml）：
- ``gateway`` 容器监听 **8001**
- ``production-worker`` 是**独立容器**、**replicas: 3**、监听 **8040**

gateway 自己的 localhost:8040 没有任何东西在听 → 探测必然失败 → except 分支
``logger.warning("Worker probe failed... assuming v1 only")`` → ``caps = ["v1"]``。

后果：``should_use_mixed_pipeline`` / ``should_use_segmented_pipeline`` 都要求 caps
含 v2_mixed / v3_segmented，**永远不满足** → **全部任务恒走 v1**。而 worker 明明在
worker.py:385 宣告 ``["v1","v2_mixed","v3_segmented"]``。

即 v2_mixed / v3_segmented 两条管线在生产**完全不可达**（死代码）。审计里所有关于
segmented 的发现当前都打不到用户——除非先修这个探测。
"""

import re
from pathlib import Path

import pytest

from app.gateway.video_gen import worker_probe

REPO = Path(__file__).resolve().parents[2]
COMPOSE_FILES = [
    REPO / "docker" / "docker-compose-deploy.yaml",
    REPO / "docker" / "docker-compose-dev.yaml",
]


def _gateway_env_lines(text: str) -> list[str]:
    """粗提 gateway 服务块里的 environment 行（够用即可，不引 yaml 依赖）。"""
    return [ln.strip() for ln in text.splitlines() if "WORKER_HEALTH_URL" in ln]


@pytest.mark.parametrize("compose", COMPOSE_FILES, ids=lambda p: p.name)
def test_compose_sets_worker_health_url(compose):
    """compose 必须显式给 gateway 提供 WORKER_HEALTH_URL，指向 worker 服务名。

    缺它 → 探测恒打 gateway 自己的 localhost:8040（无人监听）→ caps=["v1"] →
    v2_mixed/v3_segmented 永不启用。
    """
    assert compose.exists(), f"{compose} 不存在"
    text = compose.read_text(encoding="utf-8")
    lines = _gateway_env_lines(text)
    assert lines, f"{compose.name} 未设置 WORKER_HEALTH_URL —— 探测会回落 localhost:8040，而 worker 是独立容器 → v2/v3 管线在生产不可达"
    # 必须指向服务名而非 localhost（localhost 在 gateway 容器里指向自己）
    for ln in lines:
        assert "localhost" not in ln, f"{compose.name}: WORKER_HEALTH_URL 指向 localhost（gateway 容器自身）：{ln}"
        assert re.search(r"production-worker:\d+", ln), f"{compose.name}: WORKER_HEALTH_URL 应指向 production-worker 服务名：{ln}"


def test_probe_default_does_not_point_at_gateway_itself(monkeypatch):
    """未设环境变量时，默认值不得是「会指向 gateway 自身」的地址。

    localhost:8040 在 gateway 容器内没有监听者 —— 与其静默回落 v1，不如让默认值
    指向 Docker DNS 里的 worker 服务名（Compose 网络下可解析）。
    """
    monkeypatch.delenv("WORKER_HEALTH_URL", raising=False)
    default_url = worker_probe._default_worker_url()
    assert "localhost" not in default_url and "127.0.0.1" not in default_url, f"默认探测地址仍指向本机：{default_url} —— 在 gateway 容器里无人监听 8040"


def test_env_var_still_wins(monkeypatch):
    """显式设置的 WORKER_HEALTH_URL 优先（不得被默认值覆盖）。"""
    monkeypatch.setenv("WORKER_HEALTH_URL", "http://custom-worker:9999")
    assert worker_probe._default_worker_url() == "http://custom-worker:9999"


@pytest.mark.asyncio
async def test_probe_failure_logs_actionable_message(monkeypatch, caplog):
    """探测失败的日志必须带上实际地址，便于定位（回归保护）。"""
    monkeypatch.setattr(worker_probe, "_probe_cache", None, raising=False)
    monkeypatch.setenv("WORKER_HEALTH_URL", "http://unreachable-host-xyz:8040")

    import logging

    with caplog.at_level(logging.WARNING):
        caps = await worker_probe.probe_worker_capabilities()

    assert caps == ["v1"], "探测失败仍应保守回落 v1"
    assert any("unreachable-host-xyz" in r.getMessage() for r in caplog.records), "失败日志未包含探测地址，运维无从定位"


def test_worker_declares_all_three_capabilities():
    """worker 侧确实宣告三种能力——证明缺口在探测而非 worker。"""
    worker_py = REPO / "docker" / "production-worker" / "worker.py"
    text = worker_py.read_text(encoding="utf-8")
    assert '"v1", "v2_mixed", "v3_segmented"' in text, "worker 未宣告三种能力，Task 10 的前提需重新核实"


# ── 审查发现 5：WORKER_HEALTH_URL 加错了 compose 服务 ───────────────────────
#
# 变量被写进 **frontend** 服务的 environment，而探测跑在 **gateway**。
# compose 下靠代码默认值 http://production-worker:8040 碰巧能工作，但：
#   ① 部署清单「.env 覆盖时确认未置空」是误导——覆盖根本到不了 gateway；
#   ② 真要改 worker 地址时探测会静默失败 → caps=["v1"] → v2/v3 不可达。


def _service_env_block(compose_text: str, service: str) -> list[str]:
    """粗提某个服务块的 environment 行（够用即可，不引 yaml 依赖）。"""
    lines = compose_text.splitlines()
    out: list[str] = []
    in_service = False
    in_env = False
    for ln in lines:
        if ln.startswith(f"  {service}:"):
            in_service = True
            continue
        if in_service and ln.startswith("  ") and not ln.startswith("    ") and ln.strip().endswith(":"):
            break  # 下一个服务块
        if in_service and ln.strip() == "environment:":
            in_env = True
            continue
        if in_env:
            if ln.strip().startswith("- "):
                out.append(ln.strip()[2:])
            elif ln.strip() and not ln.strip().startswith("#"):
                in_env = False
    return out


@pytest.mark.parametrize("compose", COMPOSE_FILES, ids=lambda p: p.name)
def test_worker_health_url_is_on_gateway_not_frontend(compose):
    """探测跑在 gateway，变量就必须在 gateway 的 environment 里。"""
    text = compose.read_text(encoding="utf-8")

    gateway_env = _service_env_block(text, "gateway")
    frontend_env = _service_env_block(text, "frontend")

    assert any("WORKER_HEALTH_URL" in e for e in gateway_env), f"{compose.name}: gateway 的 environment 缺 WORKER_HEALTH_URL —— 写在别的服务块里对探测无效"
    assert not any("WORKER_HEALTH_URL" in e for e in frontend_env), f"{compose.name}: WORKER_HEALTH_URL 不该在 frontend 块（探测不在那里跑）"
