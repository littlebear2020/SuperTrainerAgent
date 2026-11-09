"""SEC-H13 (批次 2 项 2.5): user-channels config 字段白名单.

``POST /api/user-channels`` 的 ``config`` 是自由 dict，与 credentials 原样
合并进渠道实例——用户可设 feishu ``domain``（携带 app 凭据的 SSRF）、
wechat ``base_url``/``cdn_base_url``、任意 ``state_dir``（任意路径写）。
``UserChannelRegistry.create_or_update`` 入口做剥脱过滤（warning 不拒存，
存量 DB 行启动重放不炸）+ feishu domain scheme/后缀校验。HTTP 路径与启动
init 重放都过这一个汇聚点。
"""

from __future__ import annotations

from app.channels.user_registry import _sanitize_channel_inputs, _validate_feishu_domain


def test_unknown_keys_stripped_with_config_kept():
    config, creds = _sanitize_channel_inputs(
        "feishu",
        {"domain": "https://open.feishu.cn", "malicious_key": "x"},
        {"app_id": "id", "app_secret": "s", "extra_cred": "y"},
    )
    assert config == {"domain": "https://open.feishu.cn"}
    assert creds == {"app_id": "id", "app_secret": "s"}


def test_wechat_consumed_keys_all_pass():
    raw = {
        "bot_token": "t",
        "base_url": "https://api.example.com",
        "cdn_base_url": "https://cdn.example.com",
        "state_dir": "/tmp/state",
        "polling_timeout": 30,
        "qrcode_login_enabled": True,
        "allowed_users": ["u1"],
    }
    config, creds = _sanitize_channel_inputs("wechat", dict(raw), {})
    assert config == raw


def test_wechat_state_dir_and_base_url_cannot_be_injected_via_unknown_channel():
    """未知渠道类型不过滤——由 _get_channel_class 抛 ValueError。"""
    config, creds = _sanitize_channel_inputs("carrier-pigeon", {"anything": 1}, {"x": 2})
    assert config == {"anything": 1}
    assert creds == {"x": 2}


def test_feishu_domain_valid_official_hosts_pass():
    for domain in ("https://open.feishu.cn", "https://open.larksuite.com", "https://internal.feishu.cn"):
        assert _validate_feishu_domain(domain) == domain


def test_feishu_domain_internal_or_malformed_rejected():
    # 内网 IP / 非官方后缀 → None（剥脱后回落官方默认）
    for domain in ("http://169.254.169.254", "https://evil.internal.host", "https://evil.feishu.cn.evil.com", "ftp://open.feishu.cn", "not a url", "", None, "https://user:pass@open.feishu.cn", "https://open.feishu.cn/path"):
        assert _validate_feishu_domain(domain) is None


def test_feishu_domain_reverted_when_validation_fails():
    config, _ = _sanitize_channel_inputs("feishu", {"domain": "http://192.168.1.1"}, {})
    assert "domain" not in config


def test_empty_dicts_pass_through():
    assert _sanitize_channel_inputs("feishu", {}, {}) == ({}, {})
