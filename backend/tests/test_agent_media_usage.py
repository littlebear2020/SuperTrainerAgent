"""线程媒体计量账本——事件契约、受限读取与服务端映射（媒体计量计划 PR 2 Step 1）。

账本是沙箱内技能脚本按行追加的 ``<workspace>/.usage/media-usage.jsonl``；
本文件钉死 :mod:`deerflow.runtime.media_usage` 的契约：

- 合法 tts / image 事件及服务端映射（image 的非默认 Seedream ``model`` 原样记录）；
- 必填缺失 / 越界 / 未知 kind / 不含 ``seedream`` 的 model 计 error；
- 未知字段（含 provider/method/amount，以及 tts 事件里的 model）被忽略且不进 metadata；
- 尾部半行不处理；文件大于窗口 ``truncated=True``；symlink / FIFO / 超长行不阻塞且计 error；
- ``call_id`` 同 thread+event 跨 run 相同、跨 thread 不同，长度 46（不能只靠 SQLite 证明）。
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from deerflow.config.paths import Paths
from deerflow.runtime import media_usage as mu


def _line(**event) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def _tts(event_id: str = "e-tts-1", *, char_count: int = 42, with_timestamps: bool = False, **extra) -> dict:
    return {"event_id": event_id, "kind": "tts", "with_timestamps": with_timestamps, "char_count": char_count, **extra}


def _image(event_id: str = "e-img-1", *, model: str = "doubao-seedream-5-0-pro-260628", width: int = 2048, height: int = 1152, **extra) -> dict:
    return {"event_id": event_id, "kind": "image", "model": model, "width": width, "height": height, **extra}


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def ledger(tmp_path, monkeypatch) -> Path:
    """账本落进 tmp_path 下的线程 workspace（宿主路径 = sandbox_work_dir/.usage/media-usage.jsonl）。"""
    monkeypatch.setattr(mu, "get_paths", lambda: Paths(base_dir=tmp_path))
    return mu.ledger_path("thread-1", "user-1")


class TestLedgerPath:
    def test_ledger_path_lives_in_thread_workspace_dot_usage(self, ledger, tmp_path):
        expected = tmp_path / "users" / "user-1" / "threads" / "thread-1" / "user-data" / "workspace" / ".usage" / "media-usage.jsonl"
        assert ledger == expected
        assert mu.LEDGER_DIR_NAME == ".usage"
        assert mu.LEDGER_FILE_NAME == "media-usage.jsonl"


class TestCallId:
    def test_call_id_is_stable_per_thread_and_event_and_46_chars(self):
        a = mu.media_call_id("thread-1", "evt-1")
        b = mu.media_call_id("thread-1", "evt-1")
        c = mu.media_call_id("thread-2", "evt-1")
        d = mu.media_call_id("thread-1", "evt-2")
        assert a == b, "同 thread+event 跨 run 必须相同（幂等去重靠它）"
        assert a != c, "跨 thread 必须不同"
        assert a != d, "同 thread 不同 event 必须不同"
        assert len(a) == 46 and a.startswith("media_"), f"len={len(a)}（call_id 列 String(64)，SQLite 不校验长度）"

    def test_call_id_fits_the_db_column_even_for_long_inputs(self):
        long_id = mu.media_call_id("t" * 200, "e" * 500)
        assert len(long_id) == 46


class TestValidEventsAndMapping:
    def test_valid_tts_and_image_lines_are_parsed_in_file_order(self, ledger):
        _write(ledger, _line(**_tts("e1", char_count=120)) + _line(**_image("e2")) + _line(**_tts("e3", char_count=7, with_timestamps=True)))
        read = mu.read_ledger_events(ledger)
        assert read.errors == 0
        assert read.truncated is False
        assert [e.event_id for e in read.events] == ["e1", "e2", "e3"]
        tts1, img, tts3 = read.events
        assert (tts1.kind, tts1.char_count, tts1.with_timestamps) == ("tts", 120, False)
        assert tts1.model is None, "tts 事件不携带 model（服务端固定）"
        assert (img.kind, img.model, img.width, img.height) == ("image", "doubao-seedream-5-0-pro-260628", 2048, 1152)
        assert (tts3.kind, tts3.char_count, tts3.with_timestamps) == ("tts", 7, True)

    def test_server_side_mapping_for_tts(self, ledger):
        _write(ledger, _line(**_tts("plain", char_count=1500)) + _line(**_tts("sub", char_count=20, with_timestamps=True, elapsed_ms=900)))
        plain, sub = mu.read_ledger_events(ledger).events
        m_plain = mu.map_event_to_call(plain)
        assert (m_plain.provider, m_plain.method, m_plain.status) == ("doubao_tts", "synthesize_sse", "success")
        assert m_plain.model == "seed-tts-2.0"
        assert m_plain.metadata == {"model": "seed-tts-2.0", "char_count": 1500, "metering_source": "sandbox_reported"}
        assert m_plain.elapsed_ms is None
        m_sub = mu.map_event_to_call(sub)
        assert m_sub.method == "synthesize_sse_with_timestamps"
        assert m_sub.elapsed_ms == 900

    def test_server_side_mapping_for_image_keeps_the_real_model(self, ledger):
        _write(ledger, _line(**_image("default")) + _line(**_image("nondefault", model="bytedance/doubao-seedream-5.0-pro")) + _line(**_image("bare", width=None, height=None)))
        default, nondefault, bare = mu.read_ledger_events(ledger).events
        m_default = mu.map_event_to_call(default)
        assert (m_default.provider, m_default.method, m_default.status) == ("seedream-360", "generate_image", "success")
        assert m_default.model == "doubao-seedream-5-0-pro-260628"
        assert m_default.metadata == {"model": "doubao-seedream-5-0-pro-260628", "width": 2048, "height": 1152, "metering_source": "sandbox_reported"}
        m_non = mu.map_event_to_call(nondefault)
        assert m_non.model == "bytedance/doubao-seedream-5.0-pro", "非默认 Seedream 必须原样记录，不得改写为默认模型（模型级价目按它命中）"
        assert m_non.metadata["model"] == "bytedance/doubao-seedream-5.0-pro"
        m_bare = mu.map_event_to_call(bare)
        assert m_bare.metadata == {"model": "doubao-seedream-5-0-pro-260628", "metering_source": "sandbox_reported"}, "可选 width/height 缺失时不写稀疏键"

    def test_long_course_sentence_char_count_is_valid(self, ledger):
        """课程视频逐句调用、无单句上限——5000 字合法，不能用播客 300 / PPT 1000 推上界。"""
        _write(ledger, _line(**_tts("long", char_count=5000)))
        read = mu.read_ledger_events(ledger)
        assert read.errors == 0
        assert read.events[0].char_count == 5000

    def test_unknown_fields_are_ignored_and_never_reach_metadata(self, ledger):
        _write(
            ledger,
            _line(**_tts("t1", char_count=10, model="evil-model", provider="x", method="y", amount=999)) + _line(**_image("i1", provider="qihu", method="edit_image", amount=0.01, foo="bar")),
        )
        read = mu.read_ledger_events(ledger)
        assert read.errors == 0
        t1, i1 = read.events
        assert t1.model is None
        m_t1 = mu.map_event_to_call(t1)
        assert m_t1.provider == "doubao_tts" and m_t1.model == "seed-tts-2.0"
        assert "amount" not in m_t1.metadata and "provider" not in m_t1.metadata and "method" not in m_t1.metadata
        m_i1 = mu.map_event_to_call(i1)
        assert m_i1.provider == "seedream-360" and "amount" not in m_i1.metadata and "foo" not in m_i1.metadata

    def test_duplicate_event_id_within_one_read_counts_once(self, ledger):
        _write(ledger, _line(**_tts("same", char_count=10)) + _line(**_tts("same", char_count=10)))
        read = mu.read_ledger_events(ledger)
        assert [e.event_id for e in read.events] == ["same"]
        assert read.errors == 0

    def test_missing_or_empty_file_is_an_empty_read(self, tmp_path):
        read = mu.read_ledger_events(tmp_path / "nope" / "media-usage.jsonl")
        assert (read.events, read.errors, read.truncated) == ([], 0, False)
        empty = _write(tmp_path / "empty.jsonl", "")
        read = mu.read_ledger_events(empty)
        assert (read.events, read.errors, read.truncated) == ([], 0, False)


class TestInvalidLinesCountAsErrors:
    @pytest.mark.parametrize(
        ("bad", "why"),
        [
            (_line(kind="tts", char_count=10), "missing event_id"),
            (_line(event_id="x", kind="tts", with_timestamps=False), "tts missing char_count"),
            (_line(event_id="x", kind="tts", char_count=0, with_timestamps=False), "char_count below 1"),
            (_line(event_id="x", kind="tts", char_count=100001, with_timestamps=False), "char_count above 100000"),
            (_line(event_id="x", kind="tts", char_count="120", with_timestamps=False), "char_count not an int"),
            (_line(event_id="x", kind="video", duration=5), "unknown kind"),
            (_line(event_id="x", kind="image"), "image missing model"),
            (_line(event_id="x", kind="image", model="gpt-image-2"), "model without seedream"),
            (_line(event_id="x", kind="image", model="seedream-" + "m" * 200), "model longer than 128"),
            (_line(event_id="", kind="image", model="seedream-x"), "empty event_id"),
            ("not json at all\n", "not json"),
            (_line(**_image("w", width="wide")), "width not an int"),
        ],
        ids=["no-event-id", "tts-no-char", "char-0", "char-100001", "char-str", "unknown-kind", "image-no-model", "model-no-seedream", "model-too-long", "empty-event-id", "not-json", "width-str"],
    )
    def test_each_bad_line_counts_one_error_and_is_excluded(self, ledger, bad, why):
        _write(ledger, _line(**_tts("good", char_count=3)) + bad + _line(**_image("also-good")))
        read = mu.read_ledger_events(ledger)
        assert read.errors == 1, why
        assert [e.event_id for e in read.events] == ["good", "also-good"], why


class TestBoundedRead:
    def test_trailing_half_line_is_left_for_the_next_read(self, ledger):
        _write(ledger, _line(**_tts("done", char_count=3)) + _line(**_tts("partial", char_count=4)).rstrip("\n"))
        read = mu.read_ledger_events(ledger)
        assert [e.event_id for e in read.events] == ["done"]
        assert read.errors == 0, "写入中的半行不是坏数据"
        # 下一次读取（写入方已补上换行）能看到它
        _write(ledger, _line(**_tts("done", char_count=3)) + _line(**_tts("partial", char_count=4)))
        assert [e.event_id for e in mu.read_ledger_events(ledger).events] == ["done", "partial"]

    def test_file_larger_than_window_is_truncated_and_first_half_line_dropped(self, ledger, monkeypatch):
        lines = [_line(**_tts(f"e{i:02d}", char_count=10)) for i in range(10)]
        _write(ledger, "".join(lines))
        full = mu.read_ledger_events(ledger)
        assert full.truncated is False and len(full.events) == 10
        per_line = len(lines[0])
        # 窗口起点落在 e06 行内（距 e07 行首 5 字节）→ 丢首个半行，不计 error
        monkeypatch.setattr(mu, "TAIL_WINDOW_BYTES", per_line * 3 + 5)
        windowed = mu.read_ledger_events(ledger)
        assert windowed.truncated is True
        assert windowed.errors == 0
        assert [e.event_id for e in windowed.events] == ["e07", "e08", "e09"], "窗口内完整行从旧到新；首个半行丢弃"
        # 窗口起点恰好落在行边界 → 首行完整，不丢
        monkeypatch.setattr(mu, "TAIL_WINDOW_BYTES", per_line * 3)
        exact = mu.read_ledger_events(ledger)
        assert exact.truncated is True
        assert [e.event_id for e in exact.events] == ["e07", "e08", "e09"]

    def test_overlong_line_counts_error_without_blocking(self, ledger, monkeypatch):
        monkeypatch.setattr(mu, "MAX_LINE_BYTES", 200)
        _write(ledger, _line(**_tts("ok", char_count=1)) + _line(**_tts("fat", char_count=1, padding="x" * 500)) + _line(**_tts("ok2", char_count=1)))
        read = mu.read_ledger_events(ledger)
        assert read.errors == 1
        assert [e.event_id for e in read.events] == ["ok", "ok2"]

    def test_symlink_is_rejected_without_following(self, ledger, tmp_path):
        target = _write(tmp_path / "elsewhere.jsonl", _line(**_tts("secret", char_count=1)))
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.symlink_to(target)
        read = mu.read_ledger_events(ledger)
        assert (read.events, read.errors) == ([], 1)

    def test_fifo_is_rejected_without_blocking(self, ledger):
        ledger.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(ledger)
        assert stat.S_ISFIFO(ledger.stat().st_mode)
        read = mu.read_ledger_events(ledger)  # O_NONBLOCK: must return, not hang
        assert (read.events, read.errors) == ([], 1)

    def test_directory_at_ledger_path_is_rejected(self, ledger):
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.mkdir()
        read = mu.read_ledger_events(ledger)
        assert (read.events, read.errors) == ([], 1)


class TestReaderBounds:
    """终审 M-4：读取器拒绝 DB 一定会拒绝的值（超范围整数、负尺寸、不可打印 model），不让它每次 drain 重复报错。"""

    @pytest.mark.parametrize(
        ("event", "why"),
        [
            (_image("e", elapsed_ms=2**31), "elapsed_ms above 32-bit INTEGER"),
            (_image("e", elapsed_ms=-1), "negative elapsed_ms"),
            (_image("e", width=-1), "negative width"),
            (_image("e", height=10**6 + 1), "height above 1e6"),
            (_image("e", model="seedream-\ud800"), "lone surrogate in model"),
            (_image("e", model="seedream-\x00"), "control char in model"),
            (_image("e", model="seedream-x\n"), "newline in model"),
        ],
        ids=["elapsed-overflow", "elapsed-negative", "width-negative", "height-too-big", "model-surrogate", "model-control", "model-newline"],
    )
    def test_out_of_range_values_count_as_errors(self, ledger, event, why):
        # 默认 ensure_ascii：孤立代理对只能以 \udXXXX 转义形式出现在文件里（发射器无法写出别的形态）
        _write(ledger, json.dumps(event) + "\n")
        read = mu.read_ledger_events(ledger)
        assert (read.events, read.errors) == ([], 1), why

    def test_boundary_values_are_accepted(self, ledger):
        _write(ledger, _line(**_image("e", elapsed_ms=2**31 - 1, width=10**6, height=0)))
        read = mu.read_ledger_events(ledger)
        assert read.errors == 0
        assert read.events[0].elapsed_ms == 2**31 - 1 and read.events[0].width == 10**6 and read.events[0].height == 0

    @pytest.mark.parametrize("failing", ["fstat", "lseek", "read"], ids=["fstat", "lseek", "read"])
    def test_oserror_during_read_phase_is_an_error_not_an_exception(self, ledger, monkeypatch, failing):
        """终审 M-5：EIO/ESTALE 发生在 open 之后也必须计 error，不得抛出。"""
        _write(ledger, _line(**_tts("ok")))

        def boom(*args, **kwargs):
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(mu.os, failing, boom)
        read = mu.read_ledger_events(ledger)
        assert (read.events, read.errors) == ([], 1)
