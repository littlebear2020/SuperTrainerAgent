"""SEC-C4① (批次 2 项 2.13): 沙箱未消费密钥剔除 + bash 输出密钥脱敏.

① config.yaml sandbox.environment 删除 GITHUB_TOKEN 与 QIHU_TTS_MALE/
FEMALE_VOICE_ID（前者仅 github-deep-research 软依赖——匿名 API 仍可用，
限速降低；后者 podcast/daily-news 有默认值 332/158）；
② bash 输出追加密钥值过滤 mask_secret_values_in_output——注入集合的
具体值（$VAR 按 host env 解析 + 字面值）在输出中替换为 ***，与既有
mask_local_paths_in_output（路径脱敏）叠加，本地/远端两分支都套。
"""

from __future__ import annotations

from unittest.mock import patch

from deerflow.sandbox.tools import mask_secret_values_in_output


def test_secret_values_masked_from_injection_set():
    fake_cfg = type("C", (), {})()
    fake_sandbox = type("S", (), {})()
    fake_sandbox.environment = {"QIHU_API_KEY": "$QIHU_API_KEY", "OPENAI_BASE_URL": "https://api.360.cn/v1"}
    fake_cfg.sandbox = fake_sandbox

    with (
        patch("deerflow.sandbox.tools.get_app_config", return_value=fake_cfg),
        patch.dict("os.environ", {"QIHU_API_KEY": "sk-secret-qihu-123456"}),
    ):
        out = mask_secret_values_in_output("key is sk-secret-qihu-123456 and base https://api.360.cn/v1 fine")
    assert "sk-secret-qihu-123456" not in out
    assert "***" in out


def test_unresolvable_env_ref_skipped():
    fake_cfg = type("C", (), {})()
    fake_sandbox = type("S", (), {})()
    fake_sandbox.environment = {"GONE_KEY": "$__NO_SUCH_VAR__"}
    fake_cfg.sandbox = fake_sandbox

    with patch("deerflow.sandbox.tools.get_app_config", return_value=fake_cfg):
        out = mask_secret_values_in_output("nothing to mask here")
    assert out == "nothing to mask here"


def test_short_values_not_masked():
    """<6 字符的值不掩（避免把短常量/URL 片段搅碎输出）。"""
    fake_cfg = type("C", (), {})()
    fake_sandbox = type("S", (), {})()
    fake_sandbox.environment = {"SHORT": "abc"}
    fake_cfg.sandbox = fake_sandbox

    with patch("deerflow.sandbox.tools.get_app_config", return_value=fake_cfg):
        out = mask_secret_values_in_output("keep abc intact")
    assert out == "keep abc intact"


def test_config_error_degrades_to_passthrough():
    def boom():
        raise RuntimeError("no config")

    with patch("deerflow.sandbox.tools.get_app_config", side_effect=boom):
        out = mask_secret_values_in_output("raw output")
    assert out == "raw output"


def test_removed_keys_absent_from_repo_config():
    """本地 config.yaml（gitignore，部署随包拷贝）不再注入任何生产密钥。

    P1-S2（2026-09-30 上游移植，修 B2）：SEC-C4① 只移除了 GITHUB_TOKEN /
    QIHU_TTS_*VOICE_ID 并保留在用三键；本批把 sandbox.environment 整块清空
    （第一道防线 = 不注入）。钉死：块为空 + 回退注释在位 + SEC-C4① 移除键
    自然仍缺席。
    """
    from pathlib import Path

    import pytest
    import yaml

    config = Path(__file__).resolve().parents[2] / "config.yaml"
    if not config.exists():  # 干净检出无本地配置（服务器/CI 形态）
        pytest.skip("no local config.yaml (gitignored)")
    text = config.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    env_keys = set((data.get("sandbox", {}).get("environment") or {}).keys())
    assert env_keys == set(), f"sandbox.environment must stay empty (P1-S2), found: {env_keys}"
    assert "GITHUB_TOKEN" not in env_keys
    assert "QIHU_TTS_MALE_VOICE_ID" not in env_keys
    assert "QIHU_TTS_FEMALE_VOICE_ID" not in env_keys
    # 回退杆说明必须在位（部署事故期分钟级止血的操作指引）
    assert "回退杆" in text and "P1-S2" in text


def test_example_config_documents_removal():
    """config.example.yaml（跟踪文件）在 sandbox.environment 注释位登记移除事实。

    P1-S2（2026-09-30）：示例模板改为"保持为空 + required-secrets 声明面"，
    保留 SEC-C4① 历史标记。
    """
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / "config.example.yaml"
    text = example.read_text(encoding="utf-8")
    assert "SEC-C4①" in text
    assert "P1-S2" in text
    assert "required-secrets" in text
    assert "#   environment: {}" in text
