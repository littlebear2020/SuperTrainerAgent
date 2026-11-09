"""SEC-H12 (批次 2 项 2.8): feishu/wecom 补 allowed_users 白名单.

两渠道此前取出发送者 id 后无任何过滤——任何能给机器人发消息的平台用户
都可驱动 agent。本批把白名单补齐到与 telegram/slack/wechat/dingtalk 四家
同水平。**兼容铁律：默认空 = 放行**（存量渠道 config 无此字段，fail-closed
会把现有机器人全部锁死）。

- feishu `_on_message`：sender open_id 表外 → 静默 return（不建线程不回话）；
- wecom `_publish_ws_inbound`：userid 表外 → 静默 return（不回复 working、
  不 publish inbound）。

默认身份映射与发送者→用户身份映射是缓做项（0.3），不在本批。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.channels.feishu import FeishuChannel
from app.channels.message_bus import MessageBus
from app.channels.wecom import WeComChannel


def _feishu_event(sender_open_id: str = "ou_attacker"):
    sender = SimpleNamespace(sender_id=SimpleNamespace(open_id=sender_open_id))
    message = SimpleNamespace(chat_id="oc_1", message_id="om_1", root_id=None, parent_id=None, thread_id=None, content="{}")
    return SimpleNamespace(event=SimpleNamespace(sender=sender, message=message))


def _make_feishu(allowed_users) -> FeishuChannel:
    ch = FeishuChannel(bus=MagicMock(), config={"app_id": "a", "app_secret": "s", "allowed_users": allowed_users})
    return ch


class TestFeishu:
    def test_empty_whitelist_allows_everyone(self):
        ch = _make_feishu([])  # 兼容铁律：空 = 放行
        assert ch._allowed_users == set()

    def test_single_string_normalized(self):
        ch = _make_feishu("ou_alice")
        assert ch._allowed_users == {"ou_alice"}

    def test_allowed_user_passes(self, caplog):
        ch = _make_feishu(["ou_alice"])
        with caplog.at_level(logging.INFO):
            ch._on_message(_feishu_event("ou_alice"))
        # 表内用户不得走白名单拦截分支（拦截是静默 return，无断言会空转）
        assert not any("non-allowed user" in r.message for r in caplog.records)

    def test_disallowed_sender_silently_ignored(self, caplog):
        ch = _make_feishu(["ou_alice"])
        ev = _feishu_event("ou_attacker")
        with caplog.at_level(logging.INFO):
            ch._on_message(ev)  # 静默 return —— 不抛错不处理
        assert any("non-allowed user ou_attacker" in r.message for r in caplog.records)

    def test_null_config_allows_everyone(self):
        ch = _make_feishu(None)
        assert ch._allowed_users == set()


class TestWeCom:
    def _make_wecom(self, allowed_users) -> WeComChannel:
        return WeComChannel(bus=MessageBus(), config={"bot_id": "b", "bot_secret": "s", "allowed_users": allowed_users})

    @pytest.mark.asyncio
    async def test_empty_whitelist_publishes(self):
        ch = self._make_wecom([])
        ch._ws_client = MagicMock()
        frame = {"body": {"msgid": "m1", "from": {"userid": "u_anyone"}, "aibotid": "a", "chattype": "single"}}
        with pytest.MonkeyPatch.context() as mp:
            published = []

            async def fake_publish(inbound):
                published.append(inbound)

            mp.setattr(ch.bus, "publish_inbound", fake_publish)
            await ch._publish_ws_inbound(frame, "hello")
        assert len(published) == 1

    @pytest.mark.asyncio
    async def test_disallowed_user_not_published(self, caplog):
        ch = self._make_wecom(["u_alice"])
        ch._ws_client = MagicMock()
        frame = {"body": {"msgid": "m2", "from": {"userid": "u_attacker"}, "aibotid": "a", "chattype": "single"}}
        with pytest.MonkeyPatch.context() as mp:
            published = []

            async def fake_publish(inbound):
                published.append(inbound)

            mp.setattr(ch.bus, "publish_inbound", fake_publish)
            with caplog.at_level(logging.INFO):
                await ch._publish_ws_inbound(frame, "hello")
        assert published == []
        assert any("non-allowed user u_attacker" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_allowed_user_published(self):
        ch = self._make_wecom(["u_alice"])
        ch._ws_client = MagicMock()
        frame = {"body": {"msgid": "m3", "from": {"userid": "u_alice"}, "aibotid": "a", "chattype": "single"}}
        with pytest.MonkeyPatch.context() as mp:
            published = []

            async def fake_publish(inbound):
                published.append(inbound)

            mp.setattr(ch.bus, "publish_inbound", fake_publish)
            await ch._publish_ws_inbound(frame, "hello")
        assert len(published) == 1
