"""T5（2026-09-13 死链修复）：reconcile_video_tasks 对账脚本单测。

契约（docs/plans/2026-09-13-video-gen-stale-artifact-404-fix-plan.md T5）：
- dry-run 三档圈选正确（双失 / 仅缩略图失 / 完好），默认仅 status='ready'；
- clear-thumbnail-refs 只清「仅缩略图失」档的 DB 列，不动 result JSON、不删行；
- delete-rows 必须传 --confirm-delete 且计数匹配才执行；external_api_calls 台账一行不删；
- --path-map 显式映射 / storage-root 自动映射在容器路径形态下正确重定向。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reconcile_video_tasks.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("reconcile_video_tasks_t", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_db(tmp_path: Path):
    db = tmp_path / "deerflow.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE unified_tasks (
            id TEXT PRIMARY KEY, user_id TEXT, task_type TEXT, status TEXT,
            created_at TEXT, thumbnail_url TEXT, result TEXT)"""
    )
    conn.execute("CREATE TABLE external_api_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT)")
    return conn, db


def _add_task(conn, tid, *, status="ready", turl=None, thumb=None, out=None, result_raw=None):
    if result_raw is not None:
        result = result_raw
    else:
        r: dict = {}
        if thumb:
            r["thumbnail_path"] = str(thumb)
        if out:
            r["output_path"] = str(out)
        result = json.dumps(r)
    conn.execute(
        "INSERT INTO unified_tasks (id, user_id, task_type, status, created_at, thumbnail_url, result) VALUES (?,?,?,?,?,?,?)",
        (tid, "u1", "ai_image", status, "2026-09-13 00:00:00", turl, result),
    )
    conn.commit()


def _seed(tmp_path: Path):
    """构造五档任务：完好 / 双失 / 仅缩略图失 / 仅成片失 / result 损坏 + 1 个 draft 双失。"""
    conn, db = _make_db(tmp_path)
    store = tmp_path / "store"
    store.mkdir()
    ok_thumb = store / "thumb_ok.jpg"
    ok_out = store / "out_ok.mp4"
    ok_thumb.write_bytes(b"x")
    ok_out.write_bytes(b"x")
    gone_thumb = tmp_path / "gone" / "thumb.jpg"
    gone_out = tmp_path / "gone" / "out.mp4"

    _add_task(conn, "vg_intact", turl="/api/video/tasks/vg_intact/thumbnail", thumb=ok_thumb, out=ok_out)
    _add_task(conn, "vg_both", turl="/api/video/tasks/vg_both/thumbnail", thumb=gone_thumb, out=gone_out)
    _add_task(conn, "vg_thumb_only", turl="/api/video/tasks/vg_thumb_only/thumbnail", thumb=gone_thumb, out=ok_out)
    _add_task(conn, "vg_out_only", turl="/api/video/tasks/vg_out_only/thumbnail", thumb=ok_thumb, out=gone_out)
    _add_task(conn, "vg_draft_gone", status="draft", turl="/api/video/tasks/vg_draft_gone/thumbnail", thumb=gone_thumb, out=gone_out)
    _add_task(conn, "vg_badjson", turl=None, result_raw='{"thumbnail_path": "/tmp/broken')
    conn.execute("INSERT INTO external_api_calls (task_id) VALUES ('vg_both'), ('vg_both'), ('vg_thumb_only')")
    conn.commit()
    return conn, db, ok_out


def test_dry_run_buckets_and_status_filter(tmp_path):
    mod = _load_module()
    conn, db, _ = _seed(tmp_path)
    try:
        buckets = mod.scan_tasks(conn, mod.make_mapper([]))
        assert [t["id"] for t in buckets["both_missing"]] == ["vg_both"]
        assert [t["id"] for t in buckets["thumb_missing"]] == ["vg_thumb_only"]
        assert [t["id"] for t in buckets["output_missing_only"]] == ["vg_out_only"]
        assert sorted(t["id"] for t in buckets["intact"]) == ["vg_intact"]
        # 损坏 JSON 归 unparseable 档（只报告）
        assert [t["id"] for t in buckets["unparseable"]] == ["vg_badjson"]

        # 默认仅 ready：draft 双失不进圈选；--all-statuses 放宽后进入
        assert all(t["id"] != "vg_draft_gone" for lst in buckets.values() for t in lst)
        buckets_all = mod.scan_tasks(conn, mod.make_mapper([]), all_statuses=True)
        assert [t["id"] for t in buckets_all["both_missing"]] == ["vg_both", "vg_draft_gone"]
    finally:
        conn.close()


def test_path_map_explicit_and_auto(tmp_path):
    mod = _load_module()
    host_root = tmp_path / "opt" / "trainer-ai" / ".deer-flow"
    vg_dir = host_root / "video-gen" / "u1" / "tasks" / "vg_map"
    vg_dir.mkdir(parents=True)
    out = vg_dir / "output.mp4"
    out.write_bytes(b"x")
    container_path = f"{mod.CONTAINER_PREFIX}/video-gen/u1/tasks/vg_map/output.mp4"

    # 显式 --path-map：无条件前缀替换（原容器路径本机不存在）
    mapper = mod.make_mapper([(mod.CONTAINER_PREFIX, str(host_root))])
    assert mapper(container_path) == str(out)
    # 自动映射：storage_root 指定 + 原路径不存在 → 重定向到宿主根
    mapper_auto = mod.make_mapper([], str(host_root))
    assert mapper_auto(container_path) == str(out)
    # 非容器前缀路径原样返回
    plain = str(tmp_path / "plain.mp4")
    assert mapper_auto(plain) == plain
    # 容器前缀但映射目标也不存在 → 维持原路径（按缺失计，不臆造）
    ghost = f"{mod.CONTAINER_PREFIX}/video-gen/u1/tasks/vg_ghost/output.mp4"
    assert mapper_auto(ghost) == ghost
    # 复审加固（2026-09-13）：'/app/backend/.deer-flow-x' 同前缀兄弟目录不属容器根，
    # 即使宿主侧恰好存在同名后缀文件也不得自动重映射（否则失联产物被误判完好）
    sibling_dir = tmp_path / "opt" / "trainer-ai" / ".deer-flow-x" / "video-gen" / "u1" / "tasks" / "vg_sibling"
    sibling_dir.mkdir(parents=True)
    (sibling_dir / "output.mp4").write_bytes(b"x")
    sibling = f"{mod.CONTAINER_PREFIX}-x/video-gen/u1/tasks/vg_sibling/output.mp4"
    assert mapper_auto(sibling) == sibling


def test_clear_thumbnail_refs_only_touches_thumb_missing(tmp_path, capsys):
    mod = _load_module()
    conn, db, _ = _seed(tmp_path)
    conn.close()
    rc = mod.main(["--db-path", str(db), "--apply", "clear-thumbnail-refs"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "已清 1 行" in out
    conn = sqlite3.connect(db)
    try:
        turls = dict(conn.execute("SELECT id, thumbnail_url FROM unified_tasks").fetchall())
        assert turls["vg_thumb_only"] is None  # 仅缩略图失档：列已清
        assert turls["vg_both"] == "/api/video/tasks/vg_both/thumbnail"  # 双失档不动（属 delete-rows）
        assert turls["vg_intact"] is not None  # 完好档不动
        assert conn.execute("SELECT COUNT(*) FROM unified_tasks").fetchone()[0] == 6  # 一行未删
        result = conn.execute("SELECT result FROM unified_tasks WHERE id='vg_thumb_only'").fetchone()[0]
        assert "thumbnail_path" in result  # result JSON 未被改写
    finally:
        conn.close()


def test_delete_rows_confirm_gate_and_ledger_kept(tmp_path, capsys):
    mod = _load_module()
    conn, db, _ = _seed(tmp_path)
    conn.close()

    # 计数不匹配（真实双失=1，谎报 26）→ 拒绝执行，rc=1，零行被删
    rc = mod.main(["--db-path", str(db), "--apply", "delete-rows", "--confirm-delete", "26"])
    assert rc == 1
    capsys.readouterr()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM unified_tasks").fetchone()[0] == 6
    conn.close()

    # 计数匹配 → 只删双失行；台账一行不删；其余档位保留
    rc = mod.main(["--db-path", str(db), "--apply", "delete-rows", "--confirm-delete", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "台账 2 行" in out  # vg_both 的 2 行 soft-ref 被报告且保留
    conn = sqlite3.connect(db)
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM unified_tasks").fetchall()]
        assert "vg_both" not in ids
        assert set(ids) == {"vg_intact", "vg_thumb_only", "vg_out_only", "vg_draft_gone", "vg_badjson"}
        calls = [r[0] for r in conn.execute("SELECT task_id FROM external_api_calls").fetchall()]
        assert calls == ["vg_both", "vg_both", "vg_thumb_only"]  # 867 行台账口径：一行不删
    finally:
        conn.close()


def test_dry_run_default_is_readonly(tmp_path, capsys):
    """不带 --apply：只读连接 + 明示 dry-run 提示，零写库。"""
    mod = _load_module()
    conn, db, _ = _seed(tmp_path)
    conn.close()
    rc = mod.main(["--db-path", str(db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert "双失（假就绪，delete-rows 候选）] 1 个" in out
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM unified_tasks").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM unified_tasks WHERE thumbnail_url IS NOT NULL").fetchone()[0] == 5
    finally:
        conn.close()
