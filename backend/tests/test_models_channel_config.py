"""Tests for ChannelConfig and ChannelThreadMapping models."""

from app.gateway.models.channel_config import ChannelConfig, ChannelThreadMapping


def test_channel_config_table_name():
    assert ChannelConfig.__tablename__ == "channel_configs"


def test_channel_thread_mapping_table_name():
    assert ChannelThreadMapping.__tablename__ == "channel_thread_mappings"


def test_channel_config_has_expected_columns():
    expected = {"id", "user_id", "channel_type", "encrypted_credentials", "config_json", "enabled", "created_at", "updated_at"}
    actual = {c.name for c in ChannelConfig.__table__.columns}
    assert expected == actual


def test_channel_thread_mapping_has_expected_columns():
    expected = {"id", "user_id", "channel_type", "chat_id", "topic_id", "thread_id", "created_at", "updated_at"}
    actual = {c.name for c in ChannelThreadMapping.__table__.columns}
    assert expected == actual
