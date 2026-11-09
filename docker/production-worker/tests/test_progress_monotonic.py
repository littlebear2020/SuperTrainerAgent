"""行为锁：进度不得倒退。

线上实测（2026-07-29 确认复现）：Gateway 交棒写 progress=95，
Worker 首个检查点写 progress_cb(5)（video_gen_ai_visual.py:372）。
progress_cb 是裸 db_update(task_id, progress=pct)（worker.py:238），
无高水位判断——Gateway 侧的 ProgressUpdater 钳制只在 Gateway 进程内。

注意：进度条在列表页只渲染 status=running 的任务，
交棒时已是 composing，列表页无感知；只有详情页 composing 条的数字会看到 95→5。
"""

from worker import clamp_progress


def test_forward_allowed():
    assert clamp_progress(current=50, incoming=70) == 70


def test_backward_clamped_to_current():
    """95 → 5 的倒退被钳到 95。"""
    assert clamp_progress(current=95, incoming=5) == 95


def test_equal_is_noop():
    assert clamp_progress(current=70, incoming=70) == 70


def test_none_current_accepts_incoming():
    """行里还没有 progress 值时直接用传入值。"""
    assert clamp_progress(current=None, incoming=5) == 5


def test_completion_always_wins():
    """100 永远可写——终态不受钳制影响。"""
    assert clamp_progress(current=95, incoming=100) == 100
    assert clamp_progress(current=100, incoming=0) == 100


def test_zero_to_five_allowed():
    """0 → 5 是正常推进，不得误拦。"""
    assert clamp_progress(current=0, incoming=5) == 5


def test_progress_cb_always_writes_even_when_clamped():
    """源码锁：钳住时也必须写 DB——否则 updated_at 冻结，reaper 谓词 A 误杀健康任务。

    reaper 有两条谓词（task_framework/service.py reap_stalled_tasks）：
      A. updated_at 超过 stall_timeout 没动 → 判 stalled
      B. progress 值长期不变 → 判 fake-busy
    交棒后 gateway 已写 progress=95，worker 从 5% 起算，几乎每次上报都被钳住。
    若实现成「钳住就 skip db_update」，96 帧任务整个渲染主体只有 1 次 DB 写入，
    updated_at 冻结在交棒时刻 → 谓词 A 把正在正常渲染的任务杀成 failed。
    Gateway 侧 ProgressUpdater 是「钳值但仍写行」，此处必须对齐。
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "worker.py").read_text(encoding="utf-8")
    body = src.split("def progress_cb(")[1].split("\n    try:")[0]

    assert "db_update(task_id, progress=clamp_progress(" in body, "progress_cb 必须无条件 db_update（钳值后再写），不得跳过写入"
    assert "if effective != current" not in body, "不得「钳住就跳过 db_update」——updated_at 会冻结，健康任务被 reaper 误杀"


def test_incremental_branch_reports_progress_per_frame():
    """源码锁：增量分支循环内必须逐帧上报进度。

    上一条保证「每次上报都真写 DB」，但前提是**有人上报**。增量分支修前
    从 progress_cb(5) 到 progress_cb(70) 之间零上报，而它每帧要下载 + 转码，
    帧多时整段 updated_at 冻结 → reaper 谓词 A 照样误杀。
    全渲分支本就逐帧上报，增量漏了是不对称——两条一起才闭合。
    """
    from pathlib import Path as _P

    src = (_P(__file__).resolve().parents[1] / "pipelines/video_gen_ai_visual.py").read_text(encoding="utf-8")
    lines = src.split("\n")

    # 精确切出 `for ... in enumerate(valid_changed):` 这个循环体：按缩进判定，
    # 不能用「下一个 segments = sorted(...)」当结束标记——那会匹配到全渲分支里的
    # 同名语句，把 progress_cb(5)/progress_cb(70) 一并纳入，变异测试就抓不到了。
    head = next(i for i, ln in enumerate(lines) if "in enumerate(valid_changed)" in ln)
    indent = len(lines[head]) - len(lines[head].lstrip())
    body_lines = []
    for ln in lines[head + 1 :]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
            break
        # 必须剔掉注释行：本处注释里就写着「progress_cb(5) 到 progress_cb(70)」，
        # 不剔的话删掉真实调用后断言仍绿（初版就是这样漏过变异测试的）。
        if ln.lstrip().startswith("#"):
            continue
        body_lines.append(ln)
    body = "\n".join(body_lines)

    assert "progress_cb(" in body, "增量分支循环内必须有 progress_cb（真实调用，非注释）——否则长增量 recompose 全程冻结 updated_at，被 reaper 判 stalled"
