"""单帧重生死锁回归（2026-08-02 用户实测：改旁白点「重生配音」一直转圈）。

**根因：`asyncio.Lock` 不可重入，同一把锁被嵌套获取两次。**

调用链：
  routers/video_gen.py:_regenerate_frame_bg
    └─ async with _synth_lock_for(task_dir)      ← ① 外层取得锁
       └─ svc._regenerate_frame(...)
          └─ service.py 音频段
             └─ async with _synth_lock_for(task_dir)   ← ② 同一把锁，永久等待

两处的 key 都是 `str(task_dir)`，`_synth_lock_for` 对同一 key 返回**同一个**
`asyncio.Lock` 实例（非 RLock，asyncio 标准库无重入锁）→ ② 永远等不到 ①
释放，而 ① 正等着 ② 返回 → 死锁。

用户可见症状（全部由此解释）：
- 点「重生配音」→ 接口 202 立即返回，**前端无任何报错**（所以控制台只有普通日志）；
- 后台任务卡在锁上，`regen_state` 永远停在 `"running"`；
- 前端释放条件 `isRegenResolved`（done/error）永不满足 → **一直转圈**；
- 直到 `REGEN_TIMEOUT_MS`（300s）才弹「重新生成超时」。

为什么「渲染完成后」才遇到：重生入口只在 ready/failed 态开放
（router:1551 非 ready/failed 直接 409），未渲染完根本点不到。

⚠️ 与两个锁本身的用途无关——两处加锁的理由都成立（防 audio.mp3 撕裂），
错的是**同一条路径上加了两次**。

修法：`_synth_lock_guard` —— 用 **ContextVar** 按协程登记持有者，
同协程重入则复用（不死锁），未持有则真加锁（撕裂保护不丢）。

⚠️ 两条弯路都记在这里，避免有人「简化」回去：
1. 「断言调用方必须持锁」：把调用契约变严，打红 5 个直接单测
   `_regenerate_frame` 的既有文件 → 那是新引入的回归，弃用。
2. 「`Lock.locked()` 则跳过加锁」：**错的**。`locked()` 只答「有人持锁吗」，
   不答「我持锁吗」。同一把锁另有 4 个 `_synthesize_audio` 持有点
   （渲染/草稿/合成/分段）与本路径并行 → 「别人持锁」被误判成「我已持锁」
   → 跳过加锁 → 并发写 audio.mp3。已用双协程探针实测复现，非推断。
   守护用例：`test_guard_does_not_skip_lock_when_another_coroutine_holds_it`。
两条加锁路径分别由 tests/test_synthesize_audio_lock.py 的
`test_regenerate_frame_holds_synth_lock_during_concat`（外层持锁）与
`test_regenerate_frame_self_locks_when_called_unlocked`（无锁直调）守护。
"""

from __future__ import annotations

import asyncio

import pytest

from app.gateway.video_gen.service import _synth_lock_for, _synth_lock_guard


@pytest.mark.asyncio
async def test_guard_does_not_skip_lock_when_another_coroutine_holds_it():
    """**最关键的一条**：`locked()` 式「已持锁就跳过」是错的，本用例守住这点。

    2026-08-02 首版修复用 `Lock.locked()` 判断是否已持有 —— 但它只能回答
    「**有人**持锁吗」，不能回答「**我**持锁吗」。同一把锁另有 4 个
    `_synthesize_audio` 持有点（渲染/草稿/合成/分段）与重生路径并行，
    于是「别的协程正持锁」会被误判成「我已持锁」→ 跳过加锁 →
    两个协程同时写 audio.mp3（撕裂，vg_93e83a7ee259 实证过该类事故）。

    实测坐实（非推断）：两协程交错的探针脚本在首版实现下确实走进了
    nullcontext 分支。故改用 ContextVar 按协程登记。

    本用例：别的协程持锁时，未登记的协程**必须真的等待**（超时即证明在等）。
    若有人把 guard 改回 `locked()` 判断，这里会立刻转红。
    """
    d = "/tmp/_probe_guard_crosstalk"
    started, release = asyncio.Event(), asyncio.Event()

    async def other_holder():
        async with _synth_lock_guard(d):
            started.set()
            await release.wait()

    holder = asyncio.create_task(other_holder())
    await started.wait()
    assert _synth_lock_for(d).locked(), "前提：别的协程已持锁"

    async def me():
        async with _synth_lock_guard(d):
            return "entered"

    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(me(), timeout=0.4)
    finally:
        release.set()
        await holder


@pytest.mark.asyncio
async def test_guard_is_reentrant_within_same_coroutine():
    """同协程嵌套必须复用而非自死锁 —— 这正是用户「一直转圈」的直接修复。"""

    async def nested():
        async with _synth_lock_guard("/tmp/_probe_guard_reentrant"):
            async with _synth_lock_guard("/tmp/_probe_guard_reentrant"):
                return "ok"

    assert await asyncio.wait_for(nested(), timeout=2) == "ok"


@pytest.mark.asyncio
async def test_guard_releases_lock_and_clears_registration():
    """退出后锁必须释放且登记清空，否则后续请求会被永久挡住。"""
    d = "/tmp/_probe_guard_release"
    async with _synth_lock_guard(d):
        assert _synth_lock_for(d).locked()
    assert not _synth_lock_for(d).locked(), "退出后锁未释放（会挡死后续所有合成）"

    # 登记已清 → 再次进入应真的加锁（而不是被残留登记骗成 no-op）
    async with _synth_lock_guard(d):
        assert _synth_lock_for(d).locked(), "ContextVar 登记未清，第二次进入没真加锁"


@pytest.mark.asyncio
async def test_synth_lock_is_not_reentrant():
    """前提坐实：`_synth_lock_for` 同 key 返回同一把不可重入锁。

    这条是下面死锁判定的基础——若哪天换成可重入锁，本用例会提醒重新评估。
    """
    d = "/tmp/_probe_reentrancy"
    assert _synth_lock_for(d) is _synth_lock_for(d), "同一 task_dir 必须是同一把锁"

    async with _synth_lock_for(d):
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_synth_lock_for(d).acquire(), timeout=0.3)


@pytest.mark.asyncio
async def test_regenerate_frame_does_not_take_raw_lock():
    """**核心回归**：`_regenerate_frame` 不得裸取 `_synth_lock_for`。

    真实调用链上，外层 `_regenerate_frame_bg` 已持这把锁；此处再裸取同一把
    不可重入锁 → 内层等外层释放、外层等内层返回 → 永久挂起（用户「一直转圈」）。
    正确写法是 `_synth_lock_guard`（同协程重入则复用）。

    用 AST 而非字符串包含来判定：注释里提到 `_synth_lock_for`（解释为什么不能
    裸取）是**必要的文档**，不该让测试转红；要禁的是真正的
    `async with _synth_lock_for(...)` 语句。字符串检查区分不了两者。
    """
    import ast
    import inspect
    import textwrap

    from app.gateway.video_gen.service import VideoGenService

    tree = ast.parse(textwrap.dedent(inspect.getsource(VideoGenService._regenerate_frame)))

    offenders = []
    guarded = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                call = item.context_expr
                if not isinstance(call, ast.Call):
                    continue
                name = getattr(call.func, "id", None)
                if name == "_synth_lock_for":
                    offenders.append(node.lineno)
                elif name == "_synth_lock_guard":
                    guarded.append(node.lineno)

    assert not offenders, f"_regenerate_frame 内不得裸取 _synth_lock_for（发现于函数内第 {offenders} 行）——调用者 _regenerate_frame_bg 已持同一把锁，嵌套获取不可重入锁会死锁；应改用 _synth_lock_guard"
    # 互补方向：保护不能被整个删掉（音频段仍须在锁内跑）
    assert guarded, "_regenerate_frame 音频段必须用 _synth_lock_guard 保护——两边都删锁会退回 audio.mp3 撕裂（2026-07-22 审计）"


@pytest.mark.asyncio
async def test_regenerate_frame_bg_still_holds_the_lock():
    """互补护栏：锁不能被两边都删掉。

    内层复用的前提是**外层确实持锁**。若将来有人「顺手」把外层锁也删了，
    就会退回 2026-07-22 审计修过的 audio.mp3 撕裂问题（两帧并发重生
    各持旧快照并发写同一文件）。

    ⚠️ 外层也必须用 `_synth_lock_guard` 而非裸锁 —— guard 才会在 ContextVar 里
    登记持有者，内层据此判断「是我持的」；换回裸锁则内层登记为空 → 重新加锁 → 死锁复发。
    """
    import inspect

    from app.gateway.routers import video_gen as router_mod

    src = inspect.getsource(router_mod._regenerate_frame_bg)
    assert "async with _synth_lock_guard" in src, "_regenerate_frame_bg 必须用 async with _synth_lock_guard 持锁（防 audio.mp3 撕裂，且让内层可重入复用）"
