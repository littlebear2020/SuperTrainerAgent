"""草稿音频合并漂移修复（2026-10-06 计划 v2，§5 测试计划 T4-1~T4-9）。

机理：draft 后台 TTS（~50s 窗口）期间用户原地改旁白/重排帧序 → 旧代音频按
frame.index 合并写回错配帧（无校验）。修复口径（v2 评审裁决，三条红线）：
1. 内容等价比对——帧级旁白字符串比对（remotion_course 先 strip_image_markers）
   + voice/speed 在磁盘 data["config"] 层一次比对；禁 CAS 键重算（P2-14 降级帧
   文件名挂实际音色键，按请求音色重算必假阳性）。
2. verify_drift 默认 False，仅 draft 调用点传 True（其余 4 调用点行为零变化）。
3. 补配轮 = 持合成锁重读磁盘后整帧集重放 synth+merge（≤2 轮），禁部分帧调用。

写法：受控边界 = 实例级 gate 包住 _merge_audio_into_storyboard（停在「合成完、
未合并」）；PUT/reorder 走真实 router handler 且作并行 task——其
_invalidate_audio_artifacts 排队等合成锁，整段 await 会挂到 draft 任务放锁（评审
P3 警示）。TTS 边界 mock（CAS/降级归位语义由真实 _synth_frame_cas 承担），
付费调用计数即 mock 计数（CAS 命中不进 mock）。
"""

import asyncio
import contextlib
import json
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway._shared.task_framework.schemas import DRAFT
from app.gateway.routers.video_gen import UpdateFrameRequest, reorder_frames, update_storyboard_frame
from app.gateway.video_gen.schemas import ReorderRequest
from app.gateway.video_gen.service import _audio_cache_key, _synth_lock_for
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

VOICE = "zh-CN-YunjianNeural"
DEGRADED = "zh-CN-DegradedVoice"
# 与 StoryboardConfig 缺省一致（test_video_gen_service.py 同口径）
SPEED = 1.2


def _task_dir(tmp_path: Path, task_id: str) -> Path:
    """router 端点按 get_effective_user_id() 计算 task_dir（conftest autouse 注入
    test-user-autouse，非 "default"）——测试与被调端点必须同目录。"""
    from deerflow.runtime.user_context import get_effective_user_id

    d = tmp_path / get_effective_user_id() / "tasks" / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_svc(tmp_path: Path):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", script_model="test-script-model")
    svc = VideoGenService(
        repo=MagicMock(),
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )
    task = MagicMock()
    task.payload = "{}"
    task.status = DRAFT
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock()
    svc.repo.clear_dirty_frames = AsyncMock()
    return svc


class _FakeRequest:
    """router 端点直调所需的最小 request：_get_service 只读 request.app.state.video_gen_service。"""

    def __init__(self, svc):
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(video_gen_service=svc))


def _mk_sb(narrations, *, video_type="ai_image", voice_id=VOICE, tts_speed=SPEED) -> Storyboard:
    return Storyboard(
        title="t",
        config=StoryboardConfig(video_type=video_type, width=1080, height=1920, voice_id=voice_id, tts_speed=tts_speed),
        frames=[StoryboardFrame(index=i, narration=n) for i, n in enumerate(narrations)],
    )


def _write_sb(task_dir: Path, narrations, *, video_type="ai_image", voice_id=VOICE, tts_speed=SPEED) -> None:
    sb = _mk_sb(narrations, video_type=video_type, voice_id=voice_id, tts_speed=tts_speed)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")


def _read_sb(task_dir: Path) -> dict:
    return json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))


async def _wait_sb(task_dir: Path, pred, timeout: float = 5.0) -> None:
    """轮询磁盘 storyboard 直到谓词成立（PUT/reorder 的 sb 写落盘信号）。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            if pred(_read_sb(task_dir)):
                return
        except (json.JSONDecodeError, OSError):
            pass
        await asyncio.sleep(0.01)
    raise AssertionError(f"storyboard 谓词等待超时：{[_read_sb(task_dir).get('frames')]} 不满足条件")


def _payload_updates(svc) -> list[dict]:
    """repo.update 收到的全部 payload（按调用序）——draft 侧与 B5 失效侧的历史都在。"""
    out = []
    for c in svc.repo.update.call_args_list:
        pl = c.kwargs.get("payload")
        if pl:
            out.append(json.loads(pl))
    return out


@contextlib.contextmanager
def _tts_env(degrade_texts=frozenset()):
    """TTS 边界 mock：CAS 文件按真实 _synth_frame_cas 语义落盘（降级归位由生产代码执行）。
    counter 计「付费 TTS 调用」次数（CAS 命中不进 mock）——未变帧零重配的断言依据。"""
    counter = {"n": 0, "texts": []}
    from app.gateway.video_gen.orchestration.tts import TTSResult

    def _fake(text, **kwargs):
        counter["n"] += 1
        counter["texts"].append(text)
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake-mp3")
        return TTSResult(audio_path=str(out), srt="", duration=2.0, actual_voice_id=(DEGRADED if text in degrade_texts else None))

    with (
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_fake),
        patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
        patch("app.gateway.video_gen.service._write_storyboard_srt"),
        patch("app.gateway.video_gen.service._get_audio_duration", new=lambda p: 2.0),
    ):
        yield counter


def _install_gate(svc):
    """每轮 merge 前停一次（=「合成完、未合并」受控边界）。
    gate_reached 每轮 set；release set 放行一轮（协程内自 clear，自动复位）。"""
    gate_reached, release = asyncio.Event(), asyncio.Event()
    calls = {"n": 0}
    orig = svc._merge_audio_into_storyboard

    async def _gated(storyboard, task_dir, **kwargs):
        calls["n"] += 1
        gate_reached.set()
        await release.wait()
        release.clear()
        return await orig(storyboard, task_dir, **kwargs)

    svc._merge_audio_into_storyboard = _gated
    return gate_reached, release, calls


async def _release_and_drain(draft, gate_reached, release, *, extra_gates: int = 2, gate_timeout: float = 10.0):
    """放行当前 merge，并逐一放行补配轮的后续 merge 直至 draft 收尾
    （gate 挡每轮 merge——补配轮的 merge 也须放行，否则 draft 挂死在 gate 上）。"""
    gate_reached.clear()
    release.set()
    for _ in range(extra_gates):
        if draft.done():
            return
        try:
            await asyncio.wait_for(gate_reached.wait(), timeout=gate_timeout)
        except TimeoutError:
            return
        gate_reached.clear()
        release.set()


# ---------------------------------------------------------------------------
# T4-1 原地编辑窗口
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_1_inplace_edit_window_no_stale_audio(tmp_path):
    """T4-1：窗口内原地改帧 3 旁白 → 旧代音频不写回该帧；补配轮自愈
    （新旁白 CAS 音频就位、audio_ready 置位）。"""
    task_id = "vg_drift_t41"
    task_dir = _task_dir(tmp_path, task_id)
    _write_sb(task_dir, ["alpha", "beta", "gamma", "delta"])
    svc = _make_svc(tmp_path)
    gate_reached, release, merge_calls = _install_gate(svc)
    req = _FakeRequest(svc)

    with _tts_env():
        draft = asyncio.ensure_future(svc._generate_draft_audio(task_id, task_dir))
        await asyncio.wait_for(gate_reached.wait(), timeout=30)
        gate_reached.clear()
        # PUT 走真实 handler + B5 路径；并行 task（其失效操作排队等合成锁，不得整段 await）
        put = asyncio.ensure_future(update_storyboard_frame(req, task_id, 3, UpdateFrameRequest(narration="delta-new")))
        await _wait_sb(task_dir, lambda d: d["frames"][3].get("narration") == "delta-new")
        await _release_and_drain(draft, gate_reached, release)
        await asyncio.wait_for(draft, timeout=30)
        await asyncio.wait_for(put, timeout=30)

    d = _read_sb(task_dir)
    final_texts = ["alpha", "beta", "gamma", "delta-new"]
    for i, text in enumerate(final_texts):
        f = d["frames"][i]
        assert f.get("audio_path"), f"帧 {i} 缺 audio_path"
        assert Path(f["audio_path"]).exists(), f"帧 {i} audio_path 指向不存在的文件"
        assert _audio_cache_key(text, VOICE, SPEED) in f["audio_path"], f"帧 {i} 音频与旁白不吻合：{f.get('audio_path')}"
    # 修前缺陷直击：旧代 delta 音频不得写回帧 3
    assert _audio_cache_key("delta", VOICE, SPEED) not in d["frames"][3]["audio_path"]

    # draft 侧 payload 曾置 audio_ready（终态可能被排队的 B5 失效操作过清——R3 可接受，查调用历史）
    assert any(p.get("audio_ready") is True for p in _payload_updates(svc))
    # 首轮合并（检出漂移）+ 一轮补配，共 2 次
    assert merge_calls["n"] == 2
    # T4-9：draft 结束后合成锁必须已释放（补配轮同协程重入不死锁、退出干净）
    assert not _synth_lock_for(str(task_dir)).locked()


# ---------------------------------------------------------------------------
# T4-2 重排窗口
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_2_reorder_window_no_cross_assignment(tmp_path):
    """T4-2：窗口内重排（真实 reorder updater 重写全帧 index）→ 全部帧音频与
    各自新旁白吻合、无张冠李戴；未变帧零重配（TTS spy 证明 CAS 全命中）。"""
    task_id = "vg_drift_t42"
    task_dir = _task_dir(tmp_path, task_id)
    _write_sb(task_dir, ["alpha", "beta", "charlie", "delta"])
    svc = _make_svc(tmp_path)
    gate_reached, release, _ = _install_gate(svc)
    req = _FakeRequest(svc)

    with _tts_env() as tts:
        draft = asyncio.ensure_future(svc._generate_draft_audio(task_id, task_dir))
        await asyncio.wait_for(gate_reached.wait(), timeout=30)
        gate_reached.clear()
        # 真实 reorder：帧 0 移到位置 3 → [alpha,beta,charlie,delta] 变 [beta,charlie,alpha,delta]
        ro = asyncio.ensure_future(reorder_frames(req, task_id, ReorderRequest(from_index=0, to_index=3)))
        expected = ["beta", "charlie", "alpha", "delta"]
        await _wait_sb(task_dir, lambda d: [f["narration"] for f in d["frames"]] == expected)
        await _release_and_drain(draft, gate_reached, release)
        await asyncio.wait_for(draft, timeout=30)
        await asyncio.wait_for(ro, timeout=30)
        # 补配轮必须全 CAS 命中：付费 TTS 调用数 = 首轮 4 次，一次不多
        assert tts["n"] == 4, f"重排补配须零重配（CAS 全命中），实测 TTS 调用 {tts['texts']}"

    d = _read_sb(task_dir)
    for i, text in enumerate(expected):
        f = d["frames"][i]
        assert f.get("audio_path"), f"帧 {i} 缺 audio_path"
        assert _audio_cache_key(text, VOICE, SPEED) in f["audio_path"], f"帧 {i}（{text}）音频张冠李戴：{f.get('audio_path')}"


# ---------------------------------------------------------------------------
# T4-3 降级 ∩ 漂移交集（v1 键重算口径的死刑用例）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_3_degraded_voice_not_false_drifted(tmp_path):
    """T4-3：TTS 降级帧（P2-14 归位到实际音色键）在窗口内另一帧被编辑时，
    不得误判漂移（内容等价比对下旁白未变即不漂移）；被编辑帧正确补配。"""
    task_id = "vg_drift_t43"
    task_dir = _task_dir(tmp_path, task_id)
    _write_sb(task_dir, ["alpha", "beta", "gamma", "delta"])
    svc = _make_svc(tmp_path)
    gate_reached, release, _ = _install_gate(svc)
    req = _FakeRequest(svc)

    with _tts_env(degrade_texts={"alpha"}) as tts:
        draft = asyncio.ensure_future(svc._generate_draft_audio(task_id, task_dir))
        await asyncio.wait_for(gate_reached.wait(), timeout=30)
        gate_reached.clear()
        put = asyncio.ensure_future(update_storyboard_frame(req, task_id, 2, UpdateFrameRequest(narration="gamma-new")))
        await _wait_sb(task_dir, lambda d: d["frames"][2].get("narration") == "gamma-new")
        await _release_and_drain(draft, gate_reached, release)
        await asyncio.wait_for(draft, timeout=30)
        await asyncio.wait_for(put, timeout=30)

    d = _read_sb(task_dir)
    # 降级帧：音频在位且挂实际音色键（v1 键重算口径下此帧会反复假阳性、终态无音频）
    f0 = d["frames"][0]
    assert f0.get("audio_path"), "降级帧被误判漂移——内容等价比对下不得发生"
    assert _audio_cache_key("alpha", DEGRADED, SPEED) in f0["audio_path"]
    # 被编辑帧：正确补配
    f2 = d["frames"][2]
    assert _audio_cache_key("gamma-new", VOICE, SPEED) in f2.get("audio_path", "")
    # R5 登记口径：整帧集重放对降级帧重付一次 TTS（有界 ≤2 次/帧——首轮 1 + 补配 1）
    assert tts["texts"].count("alpha") == 2, f"alpha 付费调用应恰为 2（R5），实测 {tts['texts']}"
    assert tts["texts"].count("gamma-new") == 1
    assert tts["n"] == 6, f"总调用应 6（首轮4 + alpha重付1 + gamma-new补配1），实测 {tts['texts']}"
    assert any(p.get("audio_ready") is True for p in _payload_updates(svc))


# ---------------------------------------------------------------------------
# T4-4 持续漂移者（有界放弃 + 复活分支报数）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_4_persistent_drift_bounded_giveup(tmp_path):
    """T4-4：每轮合并前都改 3/4 帧 → 2 轮补配耗尽后放弃，audio_error 报数准确。"""
    task_id = "vg_drift_t44"
    task_dir = _task_dir(tmp_path, task_id)
    _write_sb(task_dir, ["alpha", "beta", "gamma", "delta"])
    svc = _make_svc(tmp_path)
    gate_reached, release, _ = _install_gate(svc)
    req = _FakeRequest(svc)
    bases = ("alpha", "beta", "gamma")

    puts: list = []
    with _tts_env():
        draft = asyncio.ensure_future(svc._generate_draft_audio(task_id, task_dir))
        await asyncio.wait_for(gate_reached.wait(), timeout=30)
        # 3 个 gate = 首轮合并 + 2 轮补配；每 gate 前窗口内改 3 帧
        for rnd in (1, 2, 3):
            for i, base in enumerate(bases):
                puts.append(asyncio.ensure_future(update_storyboard_frame(req, task_id, i, UpdateFrameRequest(narration=f"{base}-v{rnd}"))))
            await _wait_sb(task_dir, lambda d, r=rnd: all(d["frames"][j].get("narration") == f"{b}-v{r}" for j, b in enumerate(bases)))
            release.set()
            if rnd < 3:
                gate_reached.clear()
                await asyncio.wait_for(gate_reached.wait(), timeout=30)
        await asyncio.wait_for(draft, timeout=60)
        await asyncio.wait_for(asyncio.gather(*puts), timeout=30)

    d = _read_sb(task_dir)
    for i in (0, 1, 2):
        assert not d["frames"][i].get("audio_path"), f"持续漂移帧 {i} 不应残留任何代次 audio_path"
    f3 = d["frames"][3]
    assert _audio_cache_key("delta", VOICE, SPEED) in f3.get("audio_path", "")
    # 复活分支：报数准确（3/4 帧需重新生成；draft 侧历史——终态会被 B5 失效侧 pop 掉）
    errs = [p for p in _payload_updates(svc) if p.get("audio_error")]
    assert errs, "持续漂移须触发 audio_error（E11 后名存实亡分支的复活）"
    assert errs[0]["audio_error"] == "旁白已修改，3/4 帧需重新生成音频"
    assert "audio_ready" not in errs[0]


# ---------------------------------------------------------------------------
# T4-5 remotion_course strip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_5_remotion_course_strip_before_compare(tmp_path):
    """T4-5（单元）：磁盘旁白带【图片N】标记 → 比对前先过同一 strip_image_markers，
    不误判漂移（map 侧是合成时已剥离的文本）。"""
    task_dir = _task_dir(tmp_path, "vg_drift_t45")
    # 内存合成代：旁白已被 _synthesize_audio 的 strip 防御网改写（无标记）
    sb = _mk_sb(["第一段开场白", "第二段内容"], video_type="remotion_course")
    for f in sb.frames:
        f.audio_path = str(task_dir / "audio" / "cas.mp3")
        f.duration = 2.0
        f.srt = ""
        f.speech_duration = 2.0
    # 磁盘代：旁白带标记（该防御网存在的原因——磁盘可能残留）
    _write_sb(task_dir, ["第一段【图片1】开场白", "第二段【图片2】内容"], video_type="remotion_course")
    svc = _make_svc(tmp_path)

    merged, drifted = await svc._merge_audio_into_storyboard(sb, task_dir, verify_drift=True)
    assert merged == 2, f"strip 后内容等价，应全量合并（drifted={drifted}）"
    assert drifted == []
    d = _read_sb(task_dir)
    assert all(f.get("audio_path") for f in d["frames"])
    # 磁盘旁白保持原样（带标记）——比对不回写旁白
    assert d["frames"][0]["narration"] == "第一段【图片1】开场白"


@pytest.mark.asyncio
async def test_t4_5b_remotion_course_draft_end_to_end(tmp_path):
    """T4-5（端到端，回归锁）：remotion_course 草稿音频全流程带标记旁白不触发误判。
    修前代码无校验天然绿；无 strip 的实现 / v1 键重算口径下必红（漂移→放弃→无音频）。"""
    task_id = "vg_drift_t45b"
    task_dir = _task_dir(tmp_path, task_id)
    _write_sb(task_dir, ["第一段【图片1】开场白", "第二段内容"], video_type="remotion_course")
    svc = _make_svc(tmp_path)

    with _tts_env():
        await asyncio.wait_for(svc._generate_draft_audio(task_id, task_dir), timeout=30)

    d = _read_sb(task_dir)
    for f in d["frames"]:
        assert f.get("audio_path"), "带标记旁白被误判漂移（未 strip 磁盘侧）"
    assert any(p.get("audio_ready") is True for p in _payload_updates(svc))


# ---------------------------------------------------------------------------
# T4-6 作用域：verify_drift 默认关 = 修前行为；开 = 校验生效
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_6_default_off_preserves_legacy_merge(tmp_path):
    """T4-6：不传 verify_drift（其余 4 调用点路径）——不校验、照常合并（含陈旧写入），
    与修前行为逐字节一致；旁白字段不进合并载荷（不覆写磁盘新旁白）。"""
    task_dir = _task_dir(tmp_path, "vg_drift_t46")
    sb = _mk_sb(["old-a", "old-b"])
    for f in sb.frames:
        f.audio_path = "/legacy/cas-old.mp3"
        f.duration = 2.0
        f.srt = ""
        f.speech_duration = 2.0
    _write_sb(task_dir, ["new-a", "new-b"])  # 磁盘已漂移（等价于窗口内被编辑）
    svc = _make_svc(tmp_path)

    merged, drifted = await svc._merge_audio_into_storyboard(sb, task_dir)
    assert merged == 2
    assert drifted == []
    d = _read_sb(task_dir)
    assert d["frames"][0]["audio_path"] == "/legacy/cas-old.mp3"  # 修前语义照写（其余调用点依赖）
    assert d["frames"][0]["narration"] == "new-a"  # 旁白不是合并载荷，磁盘新旁白不被覆写


@pytest.mark.asyncio
async def test_t4_6_verify_on_blocks_stale_and_flags_new_frame(tmp_path):
    """verify_drift=True：旁白漂移帧不合并；窗口内新增帧（不在合成代 map）也计 drifted。"""
    task_dir = _task_dir(tmp_path, "vg_drift_t46b")
    sb = _mk_sb(["old-a", "old-b"])
    for f in sb.frames:
        f.audio_path = "/legacy/cas-old.mp3"
        f.duration = 2.0
        f.srt = ""
        f.speech_duration = 2.0
    _write_sb(task_dir, ["new-a", "old-b", "added-c"])
    svc = _make_svc(tmp_path)

    merged, drifted = await svc._merge_audio_into_storyboard(sb, task_dir, verify_drift=True)
    assert merged == 1  # 帧 1（old-b）内容等价，正常合并
    assert drifted == [0, 2]  # 帧 0 旁白漂移；帧 2 不在合成代 map（需补配）
    d = _read_sb(task_dir)
    assert not d["frames"][0].get("audio_path"), "陈旧音频不得写入漂移帧"
    assert d["frames"][1].get("audio_path") == "/legacy/cas-old.mp3"
    assert not d["frames"][2].get("audio_path")


@pytest.mark.asyncio
async def test_t4_6_config_level_voice_drift_marks_all(tmp_path):
    """config 级比对：磁盘 voice_id 与合成代不一致（窗口内改音色）→ 整批漂移、零合并。"""
    task_dir = _task_dir(tmp_path, "vg_drift_t46c")
    sb = _mk_sb(["a", "b"], voice_id=VOICE)
    for f in sb.frames:
        f.audio_path = "/legacy/cas-old.mp3"
        f.duration = 2.0
        f.srt = ""
        f.speech_duration = 2.0
    _write_sb(task_dir, ["a", "b"], voice_id="zh-OtherVoice")  # 同旁白、不同音色
    svc = _make_svc(tmp_path)

    merged, drifted = await svc._merge_audio_into_storyboard(sb, task_dir, verify_drift=True)
    assert merged == 0
    assert drifted == [0, 1]
    d = _read_sb(task_dir)
    assert not d["frames"][0].get("audio_path"), "音色漂移下旧音色音频不得写入"


# ---------------------------------------------------------------------------
# 2026-10-07 遗留清偿批（A2/A3）：payload 互斥 + storyboard 消失防御
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tb1_giveup_payload_has_no_stale_audio_ready(tmp_path):
    """T-B1（A2）：give-up 分支写 audio_error 时必须 pop 残留的 audio_ready——
    payload 预置上一代成功标记（P0-3 重启恢复直发场景，无 already_ready 短路）+
    三轮持续漂移 → draft 侧 error payload 不得同时含 audio_ready。"""
    task_id = "vg_tb1"
    task_dir = _task_dir(tmp_path, task_id)
    _write_sb(task_dir, ["alpha", "beta", "gamma", "delta"])
    svc = _make_svc(tmp_path)
    svc.repo.get.return_value.payload = json.dumps({"audio_ready": True})  # 上代残留
    gate_reached, release, _ = _install_gate(svc)
    req = _FakeRequest(svc)
    bases = ("alpha", "beta", "gamma")

    puts: list = []
    with _tts_env():
        draft = asyncio.ensure_future(svc._generate_draft_audio(task_id, task_dir))
        await asyncio.wait_for(gate_reached.wait(), timeout=30)
        for rnd in (1, 2, 3):
            for i, base in enumerate(bases):
                puts.append(asyncio.ensure_future(update_storyboard_frame(req, task_id, i, UpdateFrameRequest(narration=f"{base}-v{rnd}"))))
            await _wait_sb(task_dir, lambda d, r=rnd: all(d["frames"][j].get("narration") == f"{b}-v{r}" for j, b in enumerate(bases)))
            release.set()
            if rnd < 3:
                gate_reached.clear()
                await asyncio.wait_for(gate_reached.wait(), timeout=30)
        await asyncio.wait_for(draft, timeout=60)
        await asyncio.wait_for(asyncio.gather(*puts), timeout=30)

    errs = [p for p in _payload_updates(svc) if p.get("audio_error") == "旁白已修改，3/4 帧需重新生成音频"]
    assert errs, "give-up 分支须写入 audio_error"
    assert "audio_ready" not in errs[0], f"error 与 ready 必须互斥（A2 修复目标），实测 {errs[0]}"
    # 全历史互斥不变量（B3 复审次要项 2）：任何时点不得存在双键并存的 payload
    for p in _payload_updates(svc):
        assert not (p.get("audio_ready") and p.get("audio_error")), f"互斥不变量被打破：{p}"


@pytest.mark.asyncio
async def test_tb2_exception_payload_has_no_stale_audio_ready(tmp_path):
    """T-B2（A2）：except 分支（fresh read 最新库内 payload）写「音频生成失败」时
    同样 pop 残留 audio_ready——TTS 全链失败 + payload 预置 ready → 终态仅 error。"""
    task_id = "vg_tb2"
    task_dir = _task_dir(tmp_path, task_id)
    _write_sb(task_dir, ["alpha", "beta"])
    svc = _make_svc(tmp_path)
    svc.repo.get.return_value.payload = json.dumps({"audio_ready": True})

    def _boom(text, **kwargs):
        raise RuntimeError("tts down")

    with (
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_boom),
        patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
        patch("app.gateway.video_gen.service._write_storyboard_srt"),
    ):
        await asyncio.wait_for(svc._generate_draft_audio(task_id, task_dir), timeout=30)

    ups = _payload_updates(svc)
    assert ups, "except 分支须写 audio_error"
    # Plan A Task 3（2026-10-18）：文案含同轮次阶段（TTS 全链失败 → 配音合成）+
    # audio_error_stage 字段；A2 互斥不变式不变。
    assert ups[-1].get("audio_error") == "音频生成失败（配音合成），请重试"
    assert ups[-1].get("audio_error_stage") == "tts"
    assert "audio_ready" not in ups[-1], f"error 与 ready 必须互斥（A2 修复目标），实测 {ups[-1]}"


@pytest.mark.asyncio
async def test_tb3_vanished_storyboard_writes_nothing(tmp_path):
    """T-B3（A3）：窗口内 task_dir 被整体删除（任务删除竞态）→ merge 对缺失文件
    静默 no-op（merged=0, drifted=[]）→ 修前 give-up 会报「旁白已修改 4/4」张冠
    李戴——A3 防御：warning + return。无 PUT 骨架：draft 全程唯一写点即尾部，
    故断言 repo.update **零调用**（最严口径）。"""
    import shutil as _shutil

    task_id = "vg_tb3"
    task_dir = _task_dir(tmp_path, task_id)
    _write_sb(task_dir, ["alpha", "beta", "gamma", "delta"])
    svc = _make_svc(tmp_path)
    gate_reached, release, _ = _install_gate(svc)

    with _tts_env():
        draft = asyncio.ensure_future(svc._generate_draft_audio(task_id, task_dir))
        await asyncio.wait_for(gate_reached.wait(), timeout=30)
        gate_reached.clear()
        _shutil.rmtree(task_dir)  # 合成完、未合并的窗口内删除
        release.set()
        await asyncio.wait_for(draft, timeout=30)

    assert svc.repo.update.call_count == 0, f"A3 须拦截写库（实测调用 {svc.repo.update.call_args_list}）"
