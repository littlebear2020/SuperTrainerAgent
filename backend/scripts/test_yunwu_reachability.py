#!/usr/bin/env python
"""yunwu.ai / OpenRouter / MiniMax 可达性探针（2026-07-22 ViMax 上游差距修复 Task 2）。

背景：vendoring 裁掉了上游 8 个具体 provider，其中 Veo 3.1（veo3.1-fast）走
yunwu.ai 国内中转——公司服务器（仅国内可达）可能可用。恢复 Veo = 原生音频
（对白+音效+唇同步随视频生成）+ provider 备份，一箭三雕。本脚本只测可达性，
不发起计费调用：
  - DNS 解析 → TCP 连接 + TLS 握手 → HTTP GET /v1/models（无 key 用 dummy
    Bearer，期望 401/200 而非连接超时）
  - 设 YUNWU_API_KEY 后 yunwu 行追加鉴权检查（200 + 可用模型数）

用法：cd backend && uv run python scripts/test_yunwu_reachability.py
退出码：0 = yunwu 可达；1 = 不可达（服务器若被封则无需再评估 Veo 路线）。
"""

import os
import socket
import ssl
import sys
import time

import httpx

TARGETS = [
    # (名称, base_url, 说明)
    ("yunwu", "https://yunwu.ai", "上游 Veo/NanoBanana/Seedream 国内中转（探测目标）"),
    ("openrouter", "https://openrouter.ai", "上游默认视频通道（国外，对照组——服务器预期不可达）"),
    ("minimax", "https://api.minimax.io", "上游 MiniMax LLM 预设（国内厂商，备用参考）"),
]
TIMEOUT = 10.0


def probe(name: str, base: str) -> dict:
    """Probe one target: DNS → TCP/TLS → HTTP GET /v1/models. Returns a result dict."""
    result: dict = {"name": name, "base": base, "ok": False}
    host = base.split("://", 1)[1]
    t0 = time.monotonic()
    try:
        result["dns"] = socket.gethostbyname(host)
    except OSError as e:
        result.update(stage="dns", error=str(e))
        return result
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                pass
        result["tls_ms"] = int((time.monotonic() - t0) * 1000)
    except OSError as e:
        result.update(stage="tcp/tls", error=str(e))
        return result
    try:
        key = os.environ.get("YUNWU_API_KEY", "").strip() or "reachability-probe"
        r = httpx.get(f"{base}/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
        result["http"] = r.status_code
        if name == "yunwu" and r.status_code == 200:
            try:
                result["models"] = len(r.json().get("data", []))
            except Exception:
                result["models"] = "?"
        # 任何 HTTP 状态码（含 401/404）都证明链路可达；连接异常才是不可达
        result["ok"] = True
    except httpx.HTTPError as e:
        result.update(stage="http", error=f"{type(e).__name__}: {e}")
    return result


def main() -> int:
    print(f"{'target':<12} {'ok':<4} {'stage/dns':<18} {'http':<6} {'detail'}")
    print("-" * 78)
    yunwu_ok = False
    for name, base, note in TARGETS:
        r = probe(name, base)
        if r["ok"]:
            detail = f"tls={r.get('tls_ms')}ms models={r.get('models', '-')}"
            print(f"{name:<12} {'YES':<4} {r.get('dns', ''):<18} {r.get('http', ''):<6} {detail}  ({note})")
        else:
            print(f"{name:<12} {'NO':<4} {r.get('stage', ''):<18} {'-':<6} {r.get('error', '')}  ({note})")
        if name == "yunwu":
            yunwu_ok = r["ok"]
    print("-" * 78)
    if yunwu_ok:
        print("结论: yunwu.ai 可达 → Veo 恢复路线可进入评估（见计划 Task 13 go/no-go）")
        return 0
    print("结论: yunwu.ai 不可达 → Veo 路线关闭，维持 seedream/seedance 单 provider 现状")
    return 1


if __name__ == "__main__":
    sys.exit(main())
