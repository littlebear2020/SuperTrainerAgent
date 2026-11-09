"""360 seedance (volcengine/doubao-seedance-2-0) async video generation.

Same submit+poll wire format as Hailuo (``QihuVideoProvider``): POST
``/v1/videos/async_generations`` + GET ``/v1/videos/async_generations/{id}`` poll.
Endpoint + content shape confirmed in the 2026-06-22 multi-engine plan spec
(§技术发现表). Differs from Hailuo in:

- **model**: ``volcengine/doubao-seedance-2-0`` (vs ``minimax/MiniMax-Hailuo-...``).
- **r2v reference**: seedance accepts MULTIPLE reference images in the content
  array, marked via ``extra_body.role="reference"`` (vs Hailuo's single
  image_url first-frame i2v). This aligns with ViMax ``ReferenceImageSelector``
  output (≤8 reference images).

Subclasses ``QihuVideoProvider`` to reuse the verified poll loop + httpx client +
error classification (``QihuServerError`` for 5xx, ``RateLimitError`` for 429);
only ``generate_video`` / ``_submit`` are overridden for the r2v content shape.

A live smoke test against real 360 (with ``qihu_api_key``) is the final
validation — flagged as the C0 follow-up in the optimization plan; the mocked
tests here assert the documented request shape.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from .base import VideoRef, _append_request_id, _extract_cost
from .qihu_video import QihuClientError, QihuServerError, QihuVideoProvider
from .seedream import _to_data_uri
from .throttle import ProviderUnavailableError, RateLimitError

logger = logging.getLogger(__name__)

# 2026-08-31 事故修正（任务 vg_ffe94de7b688/vg_c27ad4a467f4「每镜闪现角色三视图」）
# 的官方依据：360 官方文档《创建视频生成（seedance）》定义 6 种生成类型——
# 类型 2 图生视频-首帧 = [text, image×1]；类型 3 首尾帧 = [text, image×2]（无
# role，第一张=首帧、第二张=尾帧）；类型 6 多模态参考 = [text, image×n] +
# role=reference。**官方不存在「首帧 + 多图参考」形态**——机制 M（389099ea2）
# 的「多图无 role 全量下发」落入未定义区（2 图被解释为首尾帧 → 三视图当尾帧
# → 视频尾部闪回；3 图 fast 版取末图作首帧）。归一逻辑见
# :func:`_enforce_official_gen_types`。
_OFFICIAL_GEN_TYPES_DOC = "https://ai.360.com/docs/503146854e0.md"

# 显式首尾帧形态的 role 触发值（官方类型 3）：仅显式调用方（vimax 双帧等）可
# 用，恰 2 图无 role 放行；ai_video 链路不产生该形态（2026-08-31 全仓核查：
# 除 vimax 桥——360 通道走 role=reference 默认值、双帧走 Ark 通道 per-image
# role——外无人依赖「≥2 图无 role」下发）。
ROLE_FIRST_LAST_FRAMES = "first_last_frames"


def _record_dropped_refs_sidecar(task_dir: str | Path | None, *, frame_index: int | None, kept: str, dropped: list[str]) -> None:
    """``dropped_360_multi_ref`` 留痕（读改合并，非整条替换）——仅在 360 全链成功后由调用方落盘。

    ai_video 在调用 provider 前已为该帧写过 ref_injection 记录（injected_refs
    是前端「参考资产」标签数据源）——整条替换会抹掉它，故此处把被弃参考图
    **追加**进既有记录的 skipped 并改 reason；无既有记录（未来新调用方）才
    新建。重试路径可能重复触发，按 (path, reason) 去重。task_dir 缺失或
    frame_index 未知时跳过（sidecar 按 frame_index 合并，写错会顶掉别帧记录
    ——宁缺毋错，仅日志留痕）。

    复核修复（2026-08-31 第二轮）：留痕失败**不得阻塞渲染**（本仓纪律）——
    合法 JSON 但顶层非数组（外部损坏，如 ``42``/``null``）按无既有记录处理
    （本轮重写自愈），外层兜底也从 ``except OSError`` 放宽为 ``except Exception``。
    """
    if not task_dir or frame_index is None:
        return
    try:
        path = Path(task_dir) / "sidecar" / "ref_injection.json"
        records: list = []
        if path.exists():
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                parsed = []
            if isinstance(parsed, list):
                records = parsed
        for rec in records:
            if isinstance(rec, dict) and rec.get("frame_index") == frame_index:
                skipped = list(rec.get("skipped") or [])
                seen = {s.get("path") for s in skipped if isinstance(s, dict) and s.get("reason") == "dropped_360_multi_ref"}
                skipped.extend({"entity": "ref", "path": p, "reason": "dropped_360_multi_ref"} for p in dropped if p not in seen)
                rec["skipped"] = skipped
                rec["reason"] = "dropped_360_multi_ref"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
                return
        from ..service import record_ref_injection_sidecar  # 惰性导入避免环（service → visual_factory → providers）

        record_ref_injection_sidecar(
            task_dir,
            frame_index=frame_index,
            injected_refs=[{"entity": "preview", "path": kept, "view": "preview"}],
            skipped=[{"entity": "ref", "path": p, "reason": "dropped_360_multi_ref"} for p in dropped],
            reason="dropped_360_multi_ref",
            first_frame=True,
        )
    except Exception:  # 复核修复：留痕失败不阻塞渲染（同 ai_video record_degradation 口径）
        logger.warning("dropped_360_multi_ref sidecar write failed", exc_info=True)


def _parse_extra_data(task_data: dict[str, Any]) -> dict[str, Any]:
    """E9（2026-08-21）：解析 360 轮询响应 ``data.extra_data``（**JSON 字符串**，
    T8 实测）——``content.last_frame_url``（官方尾帧）与 ``usage``（token 计数）
    均在其中。

    容错：缺失/非 JSON/非 dict → 返回空 dict（调用方回落本地抽帧，不炸主流程）。
    """
    raw = task_data.get("extra_data") if isinstance(task_data, dict) else None
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("seedance: extra_data is not valid JSON (len=%d); tail frame/usage unavailable", len(raw) if isinstance(raw, str) else -1)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _last_frame_from_extra(extra: dict[str, Any]) -> str | None:
    """从解析后的 extra_data 提取官方尾帧 URL（``content.last_frame_url``）。"""
    content = extra.get("content")
    if isinstance(content, dict):
        url = content.get("last_frame_url")
        if isinstance(url, str) and url:
            return url
    return None


class SeedanceVideoProvider(QihuVideoProvider):
    """360 seedance r2v/i2v video generation (submit + poll), same endpoint as Hailuo."""

    # ── T1（D2 拍板，2026-08-17）：360 通道与 Ark 同模型（doubao-seedance-2.0），
    # 按官方 [4,15] 整数秒声明（BYTEDANCE_ARK_API_REFERENCE §2.3:142；H3 的 400
    # 实锤 360 网关透传模型层时长栅格）。类属性遮蔽基类 property（Hailuo 分支）。
    # 实探针复验记于修复计划 E2E（duration=3/15 各一发）。
    min_video_duration = 4.0
    integer_seconds_only = True
    max_video_duration = 15.0
    # ── T3（2026-08-18）：图生视频能力声明——实际消费 image_urls（r2v 多参考 +
    # i2v 首帧）但修前类上无声明，ai_video 的 supports_image_urls 闸门误判
    # 「不支持」。类属性遮蔽基类 H3 门控 property。
    supports_image_urls = True
    # ── E9（2026-08-21 360 主力切换）：官方尾帧支持——T8 实测
    # ``extra_body.return_last_frame=true`` 透传被接受，响应 ``data.extra_data``
    # （JSON 字符串）内 ``content.last_frame_url`` 即官方尾帧图。供帧内续写
    # i2v（ai_video 续写闸），省去本地抽帧（衍生物可能被审核拦）。
    supports_tail_frame = True

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.360.cn",
        model: str = "bytedance/seedance-2-0",
        poll_interval: float = 15.0,
        max_wait: float = 600.0,
        timeout: float = 120.0,
        resolution: str | None = None,
    ) -> None:
        # E1（2026-08-21 360 主力切换）：extra_body.resolution 接配置（未配置不发，
        # 交 360 侧默认）。取值口径见官方文档（480p~4k，fast/mini 仅 480p/720p）。
        self._resolution = (resolution or "").strip() or None
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            poll_interval=poll_interval,
            max_wait=max_wait,
            timeout=timeout,
        )

    def _enforce_official_gen_types(self, refs: list[str], role: str | None) -> tuple[list[str], list[str]]:
        """2026-08-31 事故修正：按官方 6 类型（见 ``_OFFICIAL_GEN_TYPES_DOC``）归一下发图集。

        **纯计算无副作用**：返回 ``(下发图集, 被弃参考图)``——sidecar 留痕由调用方在
        提交/轮询**成功后**落盘（复核修复 2026-08-31 第二轮：修前在 ``_submit`` 前写，
        360 提交失败降级 Ark 时——Ark 以全量参考图兜底（seedance_ark.
        ``_content_image_entries``）——台账虚报「参考图被弃」，污染「参考资产」标签
        与降级排查口径）。

        调用前 ``role`` 已完成首帧语义归一（first_frame/first_frame_reference →
        None）。规则：

        1. 归一后 role=None 且 ≥2 图（ai_video preview_first 形态）→ **截单图**
           （image_urls[0]=预览图，类型 2），其余参考图丢弃（调用方成功后写
           ``dropped_360_multi_ref`` 留痕；360 通道不支持首帧+参考混合，角色一致
           性降级文字契约——X13 规划期契约 + 预览图首帧演化）。
           修前「多图无 role 全量下发」：2 图落官方类型 3（三视图当**尾帧** →
           每镜尾部闪回）、3 图未定义（fast 版取末图作首帧）。
        2. role="reference"（r2v）→ 全量数组 + role=reference（类型 6，现状不变）。
        3. 单图 role=None（纯 preview 首帧）→ 单图下发（类型 2，现状不变）。
        4. :data:`ROLE_FIRST_LAST_FRAMES`（显式首尾帧，类型 3）→ 不进本函数
           （generate_video/submit_video 显式校验恰 2 图后直接放行）。
        """
        if role is not None or len(refs) < 2:
            return refs, []
        dropped = refs[1:]
        logger.warning(
            "seedance: 360 官方无「首帧+多图参考」形态（%s）——截单图下发 %s，弃参考图 %s（角色一致性降级文字契约）",
            _OFFICIAL_GEN_TYPES_DOC,
            refs[0],
            dropped,
        )
        return refs[:1], dropped

    async def generate_video(
        self,
        prompt: str,
        *,
        duration: float = 8.0,
        image_urls: list[str] | None = None,
        image_url: str | None = None,
        role: str | None = "reference",
        ratio: str | None = None,
        return_last_frame: bool = False,
        task_dir: str | Path | None = None,
        frame_index: int | None = None,
        **kwargs: Any,
    ) -> VideoRef:
        """Generate video via seedance.

        Reference images: explicit ``image_urls`` (multi — preferred) wins; falls
        back to the legacy single ``image_url`` kwarg (i2v first-frame, which
        ``SeedanceVideoGeneratorForViMax`` forwards from ``reference_image_paths[0]``).

        T3（2026-08-18）role 语义（官方 create-video-generation-seedance.md）：
        ``role`` 是 ``extra_body`` **全局生成类型标识**（reference=参考生视频，
        「会将所有的图像、视频和音频对象设置为参考类型」），无 per-image role。
        ``role="reference"`` → r2v（默认，vimax 桥多参考路径维持现状）；
        ``role=None`` → 不设 role = 首帧 i2v（官方示例2；preview 首帧继承用）；
        双图按序 = 首尾帧（官方示例3）。

        E1（2026-08-21）：``ratio`` 显式 kwarg——修前落进 ``**kwargs`` 被静默丢弃，
        9:16 任务拿默认比例（T1 实测 ``extra_body.ratio`` 真实生效 720x1280）。
        比例一律跟随任务参数，禁止硬编码。

        E9（2026-08-21）：``role="first_frame"``（调用层首帧语义，ai_video 续写
        第 2 段 / Ark 风格调用）归一为 None——360 的 role 是全局生成类型标识，
        无 "first_frame" 值；不设 role 即首帧 i2v（官方示例2）。
        ``return_last_frame=True``（续写场景）→ ``extra_body.return_last_frame``，
        响应官方尾帧进 ``VideoRef.last_frame_url``（T8 实测）。

        2026-08-31 事故修正（取代机制 M 的「探针结果 a」结论——该探针模型错配
        （标准版而非生产 fast）且只对比首帧灰度，尾部闪回漏检）：``role="first_
        frame_reference"`` 归一为 None 后**多图截单图**（官方类型 2），其余参考
        图丢弃 + ``dropped_360_multi_ref`` sidecar 留痕——360 官方无「首帧+多图
        参考」形态，全量下发的 2 图会被解释为首尾帧（三视图当尾帧 → 每镜尾部
        闪现）。**留痕在全链成功后写**（复核修复 2026-08-31 第二轮）：提交/轮询
        失败降级 Ark 时 Ark 用全量参考图，先写台账会虚报弃图。``task_dir``/
        ``frame_index``（ai_video 主渲染路径透传）供留痕，其他调用方缺省仅日志。
        """
        refs: list[str] = list(image_urls) if image_urls else ([image_url] if image_url else [])
        if role in ("first_frame", "first_frame_reference"):
            role = None  # E9 首帧语义 + M 混合语义 → 360 不设 role（首帧 i2v）
        dropped_refs: list[str] = []
        if role == ROLE_FIRST_LAST_FRAMES:
            if len(refs) != 2:
                raise ValueError(f"role={ROLE_FIRST_LAST_FRAMES!r} 仅允许恰 2 图（官方类型 3 首尾帧），实际 {len(refs)} 图")
            role = None  # 官方类型 3 = 双图无 role（第一张首帧、第二张尾帧）
        else:
            refs, dropped_refs = self._enforce_official_gen_types(refs, role)
        task_id = await self._submit(prompt, duration=duration, image_urls=refs, role=role, ratio=ratio, return_last_frame=return_last_frame)
        video_url, poll_body = await self._poll(task_id)
        if dropped_refs:
            # 留痕后置：360 全链（提交+轮询）成功才写——任一环失败降级 Ark 时，
            # Ark 以全量参考图兜底，此处不写才与实际下发一致（台账真实性）。
            _record_dropped_refs_sidecar(task_dir, frame_index=frame_index, kept=refs[0], dropped=dropped_refs)
        return self._video_ref(video_url, poll_body, duration=duration)

    async def submit_video(
        self,
        prompt: str,
        *,
        duration: float = 8.0,
        image_urls: list[str] | None = None,
        image_url: str | None = None,
        role: str | None = "reference",
        ratio: str | None = None,
        return_last_frame: bool = False,
        task_dir: str | Path | None = None,
        frame_index: int | None = None,
        **kwargs: Any,
    ) -> str:
        """A3: 只提交——seedance 的 r2v content shape 与基类不同（image_urls + role）。

        生成类型归一与 :meth:`generate_video` 同规（官方 6 类型，2026-08-31）。留痕
        后置到**提交成功后**（复核修复 2026-08-31 第二轮）：提交失败（429/网络错
        耗尽重试）降级 Ark 时 Ark 用全量参考图，先写台账会虚报弃图。残余边界：
        提交成功但轮询耗尽重试后仍降级 Ark 的罕见场景，此处的留痕会虚报——分拆
        路径 poll 侧无留痕钩子，接受并见 changelog 已知边界。"""
        refs: list[str] = list(image_urls) if image_urls else ([image_url] if image_url else [])
        if role in ("first_frame", "first_frame_reference"):
            role = None  # E9 + 机制 M 归一：同 generate_video
        dropped_refs: list[str] = []
        if role == ROLE_FIRST_LAST_FRAMES:
            if len(refs) != 2:
                raise ValueError(f"role={ROLE_FIRST_LAST_FRAMES!r} 仅允许恰 2 图（官方类型 3 首尾帧），实际 {len(refs)} 图")
            role = None
        else:
            refs, dropped_refs = self._enforce_official_gen_types(refs, role)
        task_id = await self._submit(prompt, duration=duration, image_urls=refs, role=role, ratio=ratio, return_last_frame=return_last_frame)
        if dropped_refs:
            # 留痕后置：360 未开出任务即失败 → Fallback 降级 Ark（全量参考图）——
            # 此处不写，台账才与实际下发一致。
            _record_dropped_refs_sidecar(task_dir, frame_index=frame_index, kept=refs[0], dropped=dropped_refs)
        return task_id

    async def poll_video(self, task_id: str, *, duration: float = 8.0, **kwargs: Any) -> VideoRef:
        """A3: 只轮询已提交的任务。重试安全：不产生新的计费任务。"""
        video_url, poll_body = await self._poll(task_id)
        return self._video_ref(video_url, poll_body, duration=duration)

    def _video_ref(self, video_url: str, poll_body: dict[str, Any], *, duration: float) -> VideoRef:
        """E9：统一构造 VideoRef——成本取顶层 usage（amount），回落 extra_data 内
        usage（token 计数，360 seedance 的真实落点，T8 实测）；尾帧取
        extra_data.content.last_frame_url。"""
        cost_cny, raw_usage = _extract_cost(poll_body)
        task_data = poll_body.get("data", {}) if isinstance(poll_body, dict) else {}
        extra = _parse_extra_data(task_data if isinstance(task_data, dict) else {})
        if raw_usage is None and isinstance(extra.get("usage"), dict):
            raw_usage = extra["usage"]
        last_frame_url = _last_frame_from_extra(extra)
        if last_frame_url is None and extra:
            logger.info("seedance: no last_frame_url in extra_data keys=%s", sorted(extra.keys()))
        return VideoRef(url=video_url, duration=duration, source="seedance", cost_cny=cost_cny, raw_usage=raw_usage, last_frame_url=last_frame_url)

    async def _submit(
        self,
        prompt: str,
        *,
        duration: float,
        image_urls: list[str],
        role: str | None,
        ratio: str | None = None,
        return_last_frame: bool = False,
    ) -> str:
        client = self._get_client()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        # T3（2026-08-18）：refs 逐张 data-URI 归一——修前原样塞本地路径，远端 360
        # 抓不到会被拒/静默丢图（官方口径：url 须为可访问 URL 或 Base64）。
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": _to_data_uri(url)}})
        body: dict[str, Any] = {
            "model": self._model,
            "content": content,
            "duration": int(math.ceil(duration)),
        }
        # E1（2026-08-21 360 主力切换）：extra_body **无条件构造**——
        # - watermark: false（T4 实测生效；默认 360 会打水印）；
        # - generate_audio: false（T8 实测 360 默认 **true**，不显式关会给视频带上
        #   不需要的 AI 音轨、与自管 TTS 链路冲突——双音轨事故源）；
        # - role：仅带参考图且显式时设（T3 语义不变）；
        # - ratio：跟随任务参数（T1 实测真实生效；不传不发，行为同现状）；
        # - resolution：接配置（ctor），未配置不发。
        extra_body: dict[str, Any] = {
            "watermark": False,
            "generate_audio": False,
        }
        # 时长透传修复（2026-09-01）：360 网关对 seedance 的时长参数只认
        # extra_body.duration（官方《创建视频生成（seedance)》专文形态 required=[model,
        # extra_body]）。探针 A/B/C 对照（3 任务实网，cgt-20260901140416-z9whr 系列）：
        # A 顶层 duration=10 → 产出 5.04s（被忽略，默认 5s）；B extra_body=10 → 10.04s；
        # C extra_body=12 → 12.04s（上限非 5s）。顶层保留双写——通用接口 required
        # 字段（删了有 400 风险），同值双写幂等无害。超发（duration_safety_factor）与
        # 续写（ext_dur=gap）经同一 _submit 自动修复；H3 走基类 qihu_video（顶层对 H3
        # 生效）不受影响。
        extra_body["duration"] = int(math.ceil(duration))
        if image_urls and role:
            # r2v：全局生成类型标识（2026-06-22 spec §技术发现表 + 官方 seedance
            # 专文「会将所有的图像、视频和音频对象设置为参考类型」）。role=None
            # → 不设 role = 首帧 i2v（官方示例2）——T3 role 语义修复：修前带图恒设，
            # preview 首帧继承会被错标为 r2v 参考图。
            extra_body["role"] = role
        if ratio:
            extra_body["ratio"] = ratio
        if self._resolution:
            extra_body["resolution"] = self._resolution
        if return_last_frame:
            # E9（2026-08-21）：续写场景透传官方尾帧（T8 实测 extra_data JSON 字符串
            # 含 content.last_frame_url）。未启用不发——默认路径行为不变。
            extra_body["return_last_frame"] = True
        body["extra_body"] = extra_body
        resp = await client.post(
            f"{self._base_url}/v1/videos/async_generations",
            headers=headers,
            json=body,
        )
        if resp.status_code == 429:
            raise RateLimitError(f"360 seedance API rate limited: {resp.text}")
        # AC4 演练补齐（2026-08-24 实测）：401/403 鉴权失效（360 返回
        # {"code":"1002","message":"鉴权不通过"}）是**通道整体不可用**而非单请求
        # 内容问题——映射 ProviderUnavailableError 进重试+降级链（坏 key 演练
        # 「100% 落 Ark」的语义前提）；其余 4xx（400 内容拒绝等）维持
        # QihuClientError 不降级（requirement #4 不变）。
        if resp.status_code in (401, 403):
            raise ProviderUnavailableError(_append_request_id(resp, f"360 seedance auth rejected ({resp.status_code}): {resp.text[:200]}"))
        if resp.status_code != 200:
            # 5xx = transient server fault (recovers on retry) → QihuServerError (the
            # vimax bridge's shot-retry covers it); 4xx = client error → RuntimeError.
            msg = f"360 seedance submit failed ({resp.status_code}): {resp.text}"
            msg = _append_request_id(resp, msg)
            if resp.status_code >= 500:
                raise QihuServerError(msg)
            raise QihuClientError(msg, resp.status_code)
        data = resp.json()
        task_data = data.get("data", {})
        task_id = task_data.get("id")
        if not task_id:
            raise RuntimeError(f"360 seedance API: no task id in response: {data}")
        logger.info("Seedance task submitted: %s", task_id)
        return task_id
