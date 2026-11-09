"""T-A7（2026-09-09 feedback-round1 M11）：per-render staging public 目录。

修前病灶：``_reset_public_assets`` 无锁重建**共享** public/assets 符号链接，
渲染与 still 双槽并发时互相切走对方素材链接 → 占位帧 + QC 循环。
修后：每次渲染/still 独立 staging 目录（assets→task_dir）经 --public-dir 传入。
"""

from __future__ import annotations

from pathlib import Path

import pipelines.compose_remotion as cr


def _mk_task_dir(tmp_path: Path, name: str, marker: str) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True)
    (d / marker).write_text(marker, encoding="utf-8")
    return d


class TestStagedPublicDir:
    def test_two_concurrent_stages_isolated(self, tmp_path, monkeypatch):
        """双槽并发核心用例：渲染与 still 各自 staging 互不干扰——修前共享
        链接被后到者切走，先到者素材寻址全部落空。"""
        monkeypatch.setattr(cr, "TEMPLATE_DIR", tmp_path / "template")
        cr.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        render_dir = _mk_task_dir(tmp_path, "task_render", "render_marker.txt")
        still_dir = _mk_task_dir(tmp_path, "task_still", "still_marker.txt")

        with cr._staged_public_dir(render_dir) as render_stage:
            # 渲染进行中触发 still：still 的 staging 建立不影响渲染侧寻址
            with cr._staged_public_dir(still_dir) as still_stage:
                assert (render_stage / "assets" / "render_marker.txt").read_text(encoding="utf-8") == "render_marker.txt"
                assert (still_stage / "assets" / "still_marker.txt").read_text(encoding="utf-8") == "still_marker.txt"
                assert render_stage != still_stage
            # still 退出清理后，渲染侧 staging 仍完好
            assert (render_stage / "assets" / "render_marker.txt").exists()

    def test_stage_cleaned_up_after_exit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cr, "TEMPLATE_DIR", tmp_path / "template")
        cr.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        task = _mk_task_dir(tmp_path, "task_a", "m.txt")
        with cr._staged_public_dir(task) as stage:
            pass
        assert not Path(stage).exists(), "staging 目录退出即清理（不留垃圾）"
        assert task.exists(), "task_dir 不受清理影响"

    def test_no_shared_symlink_left_behind(self, tmp_path, monkeypatch):
        """修前遗留病灶：共享 public/assets 链接残留会被下一次渲染撞上。staging
        方案不触碰 public/，用后即清。"""
        monkeypatch.setattr(cr, "TEMPLATE_DIR", tmp_path / "template")
        cr.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        (cr.TEMPLATE_DIR / "public").mkdir(exist_ok=True)
        task = _mk_task_dir(tmp_path, "task_b", "m.txt")
        with cr._staged_public_dir(task):
            pass
        public = cr.TEMPLATE_DIR / "public" / "assets"
        assert not public.exists(), "不得在 public/ 下留共享链接"

    def test_shared_reset_function_removed(self):
        """守卫：_reset_public_assets 已删除（staging 取代；防回归重引入共享链接）。"""
        assert not hasattr(cr, "_reset_public_assets")


class TestRenderCommandCarriesPublicDir:
    def test_render_and_still_cmds_use_staging(self, tmp_path, monkeypatch):
        """渲染/静帧命令必须带 --public-dir（staging 生效的接线断言——漏传时
        remotion 回落默认 public/，staging 形同虚设）。"""
        import inspect

        src = inspect.getsource(cr.compose_remotion)
        assert "--public-dir={public_dir}" in src, "render 命令必须携带 staging public-dir"
        src_still = inspect.getsource(cr._render_still)
        assert "--public-dir={public_dir}" in src_still, "still 命令必须携带 staging public-dir"
        assert "_reset_public_assets" not in src and "_reset_public_assets" not in src_still
