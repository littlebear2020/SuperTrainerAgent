"""Tests for user_id propagation in memory updater."""

from unittest.mock import MagicMock, patch

from deerflow.agents.memory.updater import _save_memory_to_file, clear_memory_data, get_memory_data

# ── 真实 FileMemoryStorage + tmp_path 层（主）：端到端证明用户隔离 ──────────────
# 下方 mock 断言只钉「user_id 被透传」这一接线事实；这里用真存储证明隔离结果本身。


def _real_storage(tmp_path):
    """Real FileMemoryStorage scoped to a tmp paths root."""
    from deerflow.agents.memory.storage import FileMemoryStorage
    from deerflow.config.paths import Paths

    storage = FileMemoryStorage()
    paths = Paths(tmp_path)
    updater_patch = patch("deerflow.agents.memory.updater.get_memory_storage", return_value=storage)
    paths_patch = patch("deerflow.agents.memory.storage.get_paths", return_value=paths)
    return storage, updater_patch, paths_patch


def test_real_storage_alice_writes_bob_cannot_read(tmp_path):
    """alice 经 updater 包装函数写入 → alice 读回；bob 读到的是空记忆（隔离生效）。"""
    storage, updater_patch, paths_patch = _real_storage(tmp_path)
    with updater_patch, paths_patch:
        memory = {"version": "1.0", "user": {"workContext": {"summary": "alice 的偏好", "updatedAt": ""}}, "facts": [{"id": "f1", "content": "alice secret"}]}
        assert _save_memory_to_file(memory, user_id="alice") is True

        loaded_alice = get_memory_data(user_id="alice")
        assert loaded_alice["facts"] == [{"id": "f1", "content": "alice secret"}]

        loaded_bob = get_memory_data(user_id="bob")
        assert loaded_bob.get("facts") == [], "bob 不得读到 alice 的事实"
        assert loaded_bob["user"]["workContext"]["summary"] == "", "bob 不得读到 alice 的摘要"

    assert (tmp_path / "users" / "alice" / "memory.json").exists()
    assert not (tmp_path / "users" / "bob").exists(), "bob 的读不得落盘 alice 的内容"


def test_real_storage_clear_scoped_to_user(tmp_path):
    """clear_memory_data(user_id=alice) 只清 alice——bob 的数据不受影响。"""
    storage, updater_patch, paths_patch = _real_storage(tmp_path)
    with updater_patch, paths_patch:
        _save_memory_to_file({"version": "1.0", "facts": [{"id": "a1", "content": "alice fact"}]}, user_id="alice")
        _save_memory_to_file({"version": "1.0", "facts": [{"id": "b1", "content": "bob fact"}]}, user_id="bob")

        clear_memory_data(user_id="alice")

        assert get_memory_data(user_id="alice")["facts"] == [], "alice 清空后应为空 facts"
        assert get_memory_data(user_id="bob")["facts"] == [{"id": "b1", "content": "bob fact"}], "清空 alice 不得波及 bob"


# ── mock 层（次）：钉 user_id 透传接线本身 ────────────────────────────────────


def test_get_memory_data_passes_user_id():
    mock_storage = MagicMock()
    mock_storage.load.return_value = {"version": "1.0"}
    with patch("deerflow.agents.memory.updater.get_memory_storage", return_value=mock_storage):
        get_memory_data(user_id="alice")
        mock_storage.load.assert_called_once_with(None, user_id="alice")


def test_save_memory_passes_user_id():
    mock_storage = MagicMock()
    mock_storage.save.return_value = True
    with patch("deerflow.agents.memory.updater.get_memory_storage", return_value=mock_storage):
        _save_memory_to_file({"version": "1.0"}, user_id="bob")
        mock_storage.save.assert_called_once_with({"version": "1.0"}, None, user_id="bob")


def test_clear_memory_data_passes_user_id():
    mock_storage = MagicMock()
    mock_storage.save.return_value = True
    with patch("deerflow.agents.memory.updater.get_memory_storage", return_value=mock_storage):
        clear_memory_data(user_id="charlie")
        # Verify save was called with user_id
        assert mock_storage.save.call_args.kwargs["user_id"] == "charlie"
