"""ByteDance Ark seedance-ark video provider (Stage 5.1).

Direct Ark content-generation tasks (submit + poll), distinct from the 360-gateway
``QihuVideoProvider`` (different endpoint + response shape). ``generate_audio=false``
(project自管 TTS — 防双音轨, §5.1 #1). Error classification (requirement #4):

- 429 → :class:`RateLimitError` (infrastructure → fallback).
- 5xx → :class:`ProviderUnavailableError` (infrastructure → fallback to Hailuo).
- 4xx / content-review failure → :class:`RuntimeError` (NOT in ``_FALLBACK_EXC`` → no
  fallback; content-review rejections surface to the user).

Ark API form (Stage 0 实测): ``POST /api/v3/contents/generations/tasks`` → ``{"id":"cgt-..."}``;
``GET .../tasks/{id}`` → ``{status:running→succeeded, content:{video_url}}``. i2v: content
entry ``{type:image_url, image_url:{url}, role:first_frame|last_frame}``. t2v ~155s, i2v ~280s
→ ``max_wait`` default 300s. See backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 5.1.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Any

import httpx

from .base import VideoRef, _append_request_id, _extract_cost
from .seedream import _to_data_uri
from .throttle import ProviderUnavailableError, RateLimitError

logger = logging.getLogger(__name__)


# Task 23 / F7: 轮询期带「稍后再试」语义的 4xx —— 只有这些值得 continue。
# 408 Request Timeout / 409 Conflict（任务状态迁移中）/ 425 Too Early。
# 其余 4xx（401/403/404/400…）是永久错误，continue 只会白等到 max_wait。
_RETRYABLE_POLL_STATUS = frozenset({408, 409, 425})

# Phase 2: return_last_frame 的响应字段已实跑坐实（真实任务
# cgt-20260814112111-4kt6w，2026-08-14 探针，见
# docs/changelog/changelog-2026-08-14-six-feedback-remediation.md）：content.last_frame_url
# （TOS 预签名 PNG，24h 过期），在候选元组第一位。元组保留多候选作前瞻兼容；
# 未命中仍记 warning（静默 fallback 本地抽帧不可观测）。
_LAST_FRAME_CANDIDATES = ("last_frame_url", "last_frame", "tail_frame_url", "last_image_url")


def _parse_last_frame_url(content: dict[str, Any] | None) -> str | None:
    """从 Ark 成功响应的 content 里探测尾帧 URL（C4 P0-A 提为纯函数）。

    未命中时记 warning——修前静默 fallback 本地抽帧，尾帧是否真拿到不可观测。
    """
    if not isinstance(content, dict):
        return None
    for key in _LAST_FRAME_CANDIDATES:
        val = content.get(key)
        if val and isinstance(val, str):
            return val
    logger.warning("seedance-ark: no last-frame field found in content keys=%s", sorted(content.keys()))
    return None


def _normalize_image_url(url: str | None) -> str | None:
    """R1: 把本地 FS 路径转 data-URI（Ark 远程 API 只收 URL/base64/data-URI）。

    复用 seedream._to_data_uri：http/https/data 直通，本地文件→base64，不存在→原样（让 API 报错）。
    修前 seedance_ark 原样塞 url → Ark 4xx → 全黑屏静默"成功"（Finding 1）。
    """
    return _to_data_uri(url)


# E3（2026-08-21 360 主力切换）：Ark 在途闸——Ark 账号级仅 3 并发（个人档，官方
# 文档 §快速入门），Ark 成为 360 的降级兜底后，360 大流量故障转移会把并发原样
# 灌进 Ark。模块级（跨 provider 实例/跨任务）Semaphore 封提交路径，超出排队
# 不报错（由既有 429→重试机制兜底，闸只是平滑突发）。
#
# 按 event loop 缓存：Semaphore 绑定创建它的 loop，跨 loop 复用会炸
# （pytest-asyncio 每用例新 loop）。weakref 让旧 loop 退出后自然回收。
_INFLIGHT_SEMAPHORES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _inflight_gate(limit: int) -> asyncio.Semaphore:
    """取当前 loop 的在途闸（同 limit 复用；limit 变更重建）。"""
    loop = asyncio.get_running_loop()
    per_loop = _INFLIGHT_SEMAPHORES.setdefault(loop, {})
    sem = per_loop.get(limit)
    if sem is None:
        sem = asyncio.Semaphore(limit)
        per_loop[limit] = sem
    return sem


def _assign_roles(n: int, *, entity_refs: bool = False, first_frame_plus_refs: bool = False) -> list[str]:
    """Map N reference images to Ark seedance ``content`` roles (§2.2).

    - 1 image → ``first_frame`` (首帧).
    - 2 images → ``first_frame`` + ``last_frame`` (ViMax 双帧 first/last；回归保护).
    - 3+ images → ``first_frame`` + ``reference_image``×(n-1)（多模态参考图 1-9，
      继承角色/风格/构图）。

    旧实现对 3+ 图错配多个 ``last_frame``（Ark 只允许 1 个 ``last_frame``），
    且从不使用 ``reference_image`` 角色——三视图角色一致性因此无法生效。

    Finding 6: 2 图→first+last 是为 ViMax 双帧（首/尾帧）设计；ai_video 2 视图场景下
    side 被当 ``last_frame`` 属语义错配（side 不是尾帧）。建议 ai_video 上传 3 视图
    （走 reference_image 多模态参考）或仅正面单图。

    entity_refs（二期 M5 角色分配语义对齐）：调用方声明这批图是**实体外观参考**
    （r2v 语义，role="reference"——多实体帧级子集/风格锚图），不是首/尾帧——全部
    映射 ``reference_image``（实体肖像作 first_frame 会让视频从静态肖像开场；
    第二实体作 last_frame 是 Finding 6 同型错配）。默认 False：ViMax 双帧与非实体
    路径映射保持现状零变化。

    first_frame_plus_refs（机制 M，2026-08-31）：ai_video 合并形态——首图是预览图
    （first_frame 首帧），其余是角色/道具参考（reference_image）。n=1 时即单图首帧。
    **不得**走 n=2 默认分支的 first+last——第二张是角色参考不是尾帧（Finding 6
    同型错配在 M 形态下的再现）。调用层触发值 role="first_frame_reference"
    （见 _content_image_entries）。
    """
    if n <= 0:
        return []
    if first_frame_plus_refs:
        ref_count = min(n - 1, 9)
        if n - 1 > 9:
            logger.warning("seedance-ark: %d reference images exceed Ark cap (9); dropping %d", n - 1, n - 1 - 9)
        return ["first_frame"] + ["reference_image"] * ref_count
    if entity_refs:
        # §2.2: reference_image 至多 9 张；超出丢弃并告警（同现状截断纪律）
        ref_count = min(n, 9)
        if n > 9:
            logger.warning("seedance-ark: %d entity reference images exceed Ark cap (9); dropping %d", n, n - 9)
        return ["reference_image"] * ref_count
    if n == 1:
        return ["first_frame"]
    if n == 2:
        return ["first_frame", "last_frame"]
    # §2.2: reference_image 1-9 → first_frame + 至多 9 个 reference_image（n>10 截断）
    ref_count = min(n - 1, 9)
    if n - 1 > 9:
        logger.warning("seedance-ark: %d reference images exceed Ark cap (9); dropping %d", n - 1, n - 1 - 9)
    return ["first_frame"] + ["reference_image"] * ref_count


def _content_image_entries(image_urls: list[str], role: str) -> list[dict[str, Any]]:
    """按调用层 role 语义把 image_urls 组装为 Ark content 图片条目（M 唯一真源）。

    generate_video / submit_video 两处共用（修前各拼一份——机制 M 混合模式须两处
    同步，抽函数防漂移）。role 语义：

    - ``"reference"``（r2v 实体/锚图参考）→ 全 ``reference_image``（M5）；
    - ``"first_frame_reference"``（机制 M 混合：首图预览图首帧 + 其余参考）→
      ``first_frame`` + ``reference_image``×(n-1)；
    - 其余（默认/"first_frame"/preview 单图首帧）→ ``_assign_roles`` 现状映射。
    """
    if role == "reference":
        roles = _assign_roles(len(image_urls), entity_refs=True)
    elif role == "first_frame_reference":
        roles = _assign_roles(len(image_urls), first_frame_plus_refs=True)
    else:
        roles = _assign_roles(len(image_urls))
    return [{"type": "image_url", "image_url": {"url": _normalize_image_url(u)}, "role": r} for u, r in zip(image_urls, roles)]


class SeedanceArkVideoProvider:
    """ByteDance Ark seedance 2.0 async video generation (submit + poll)."""

    supports_image_urls = True  # R3: 显式声明支持多参考图（image_urls 角色一致性）
    max_video_duration = 15.0  # P0-2: Ark 2.0 系列原生支持 [4,15]s（API 参考 §2.3:142）
    min_video_duration = 4.0  # Task 3: 下限显式化（原先只有私有 _DURATION_MIN，调用方看不见）
    integer_seconds_only = True  # Ark 只接受整数秒（API 参考 §2.3:142）
    supports_tail_frame = True  # Phase 2: Ark 原生支持 return_last_frame（API 参考 §2.3:147）

    # Ark duration 合法区间（整数秒；超出强校验报错）
    _DURATION_MIN = 4
    _DURATION_MAX = 15

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        model: str = "doubao-seedance-2-0-260128",
        poll_interval: float = 15.0,
        max_wait: float = 300.0,
        timeout: float = 120.0,
        max_inflight: int = 3,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._poll_interval = poll_interval
        self._max_wait = max_wait
        self._timeout = timeout
        # E3：Ark 在途闸上限（配置项 ark_video_max_inflight；Ark 个人档 3 并发）。
        self._max_inflight = max(1, int(max_inflight))
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def generate_video(
        self,
        prompt: str,
        *,
        duration: float = 5.0,
        image_url: str | None = None,
        image_urls: list[str] | None = None,
        role: str = "first_frame",
        ratio: str | None = None,
        **kwargs: Any,
    ) -> VideoRef:
        """Generate video via Ark seedance. t2v (no image) or i2v/r2v (reference images).

        Reference images: ``image_urls`` (multi — roles via :func:`_assign_roles`:
        1→first_frame, 2→first+last (ViMax §5.2 shot first/last), 3+→first_frame +
        reference_image×(n-1) (§2.2 多模态参考, 三视图角色一致性)) wins; falls back to
        single ``image_url`` (first_frame).

        ``ratio``: Ark 原生画面比例（9:16/16:9/1:1/...，P0-1 修复——修前从不传递，
        9:16 任务拿回默认比例被 Worker cover 裁掉 ~2/3 画面）。``duration`` 按 Ark
        [4,15] 整数秒钳制后真实下发（P0-2 修复——修前只进 VideoRef 回声不进请求体）。
        """
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if image_urls:
            # M5 + 机制 M（2026-08-31）：role 语义映射收敛到 _content_image_entries
            # （reference=全参考 / first_frame_reference=首帧+参考 / 其余=现状映射）。
            content.extend(_content_image_entries(image_urls, role))
        elif image_url:
            content.append({"type": "image_url", "image_url": {"url": _normalize_image_url(image_url)}, "role": role})
        task_id = await self._submit(content, duration=duration, ratio=ratio)
        video_url, last_frame_url, poll_body = await self._poll(task_id)
        cost_cny, raw_usage = _extract_cost(poll_body)
        return VideoRef(url=video_url, duration=duration, source="seedance-ark", cost_cny=cost_cny, raw_usage=raw_usage, last_frame_url=last_frame_url)

    async def submit_video(
        self,
        prompt: str,
        *,
        duration: float = 5.0,
        image_url: str | None = None,
        image_urls: list[str] | None = None,
        role: str = "first_frame",
        ratio: str | None = None,
        **kwargs: Any,
    ) -> str:
        """只提交，返回远端 task_id（E2 / Task 15 幂等重试用）。

        与 :meth:`generate_video` 的区别：**不轮询**。throttle 层拿到 task_id 后
        单独轮询，于是轮询超时重试时**复用同一个远端任务**，不会重新提交 ——
        修前 (submit+poll) 合体重试会让供应商开出新任务、旧任务照跑照计费，
        一次逻辑镜头可扣 2-3 次（叠加 ViMax pipeline 级重试可达 6 次），且无幂等键。
        """
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if image_urls:
            # M5 + 机制 M（同 generate_video，经 _content_image_entries 单点映射）
            content.extend(_content_image_entries(image_urls, role))
        elif image_url:
            content.append({"type": "image_url", "image_url": {"url": _normalize_image_url(image_url)}, "role": role})
        return await self._submit(content, duration=duration, ratio=ratio)

    async def poll_video(self, task_id: str, *, duration: float = 5.0) -> VideoRef:
        """只轮询已提交的任务（E2 / Task 15）。重试安全：不产生新的计费任务。"""
        video_url, last_frame_url, poll_body = await self._poll(task_id)
        cost_cny, raw_usage = _extract_cost(poll_body)
        return VideoRef(url=video_url, duration=duration, source="seedance-ark", cost_cny=cost_cny, raw_usage=raw_usage, last_frame_url=last_frame_url)

    async def _submit(
        self,
        content: list[dict[str, Any]],
        *,
        duration: float = 5.0,
        ratio: str | None = None,
    ) -> str:
        client = self._get_client()
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        body: dict[str, Any] = {
            "model": self._model,
            "content": content,
            "generate_audio": False,  # 防双音轨 — project自管 TTS (§5.1 #1)
            # P0-2: 真实下发时长（修前丢失 → 模型默认 ~5s 出片，Worker 裸循环补长鬼畜）
            "duration": max(self._DURATION_MIN, min(self._DURATION_MAX, int(round(duration)))),
            # Phase 2: return_last_frame 零成本拿到官方尾帧（信任产物豁免 §3.2），
            # 供续写 i2v 使用——省去二次本地抽帧（衍生物可能失豁免）。
            "return_last_frame": True,
        }
        if ratio:
            # P0-1: Ark 原生比例（修前从不传递 → 竖屏任务被 cover 裁切）
            body["ratio"] = ratio
        # E3：提交路径过在途闸（跨任务 ≤ _max_inflight，超出排队不报错）。
        # 挂在 _submit 而非 generate_video/submit_video——生产路径经 Throttle 的
        # submit/poll 解耦（Task 15），闸必须在任务创建点才不漏。
        gate = _inflight_gate(self._max_inflight)
        async with gate:
            resp = await client.post(
                f"{self._base_url}/contents/generations/tasks",
                headers=headers,
                json=body,
            )
        if resp.status_code == 429:
            raise RateLimitError(f"Ark seedance API rate limited: {resp.text}")
        if resp.status_code >= 500:
            raise ProviderUnavailableError(_append_request_id(resp, f"Ark seedance API server error ({resp.status_code}): {resp.text}"))
        if resp.status_code != 200:
            # 4xx (auth/bad-request/content-review at submit) → RuntimeError (no fallback).
            raise RuntimeError(_append_request_id(resp, f"Ark seedance API submit error ({resp.status_code}): {resp.text}"))
        data = resp.json()
        task_id = data.get("id")
        if not task_id:
            raise RuntimeError(f"Ark seedance API: no task id in response: {data}")
        logger.info("seedance-ark task submitted: %s", task_id)
        return task_id

    async def _poll(self, task_id: str) -> tuple[str, str | None, dict[str, Any]]:
        """Poll until success/failed. Returns (video_url, last_frame_url, raw_body).

        Phase 2: last_frame_url 取 content.last_frame_url（已坐实，见模块头注释）；
        `_parse_last_frame_url` 未命中记 warning。
        """
        client = self._get_client()
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"{self._base_url}/contents/generations/tasks/{task_id}"
        elapsed = 0.0
        while elapsed < self._max_wait:
            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval
            resp = await client.get(url, headers=headers)
            if resp.status_code == 429:
                raise RateLimitError(f"Ark seedance poll rate limited: {resp.text}")
            if resp.status_code >= 500:
                raise ProviderUnavailableError(f"Ark seedance poll server error ({resp.status_code}): {resp.text}")
            if resp.status_code != 200:
                # Task 23 / F7: 永久性 4xx 立刻抛，别烧满 max_wait。
                # 修前一律 continue —— 401 鉴权失效 / 404 任务不存在这类**永不自愈**
                # 的错误也要轮满 300s，最后抛的还是误导性的 TimeoutError；用户等满
                # 五分钟，拿到的诊断是「超时」而不是「key 过期了」。
                # 仅对带「稍后再试」语义的状态码保留 continue。
                if resp.status_code not in _RETRYABLE_POLL_STATUS:
                    raise RuntimeError(_append_request_id(resp, f"Ark seedance poll error ({resp.status_code}) for task {task_id}: {resp.text[:300]}"))
                logger.warning("seedance-ark poll error %d, retrying (body=%s)", resp.status_code, resp.text[:300])
                continue
            data = resp.json()
            status = str(data.get("status", "unknown")).lower()
            if status in ("failed", "error"):
                # Content-review rejection (e.g. InputImageSensitiveContentDetected) → RuntimeError
                # (NOT in _FALLBACK_EXC → no fallback; requirement #4: content review surfaces to user).
                msg = self._extract_failure_message(data)
                raise RuntimeError(f"Ark seedance task {task_id} failed: {msg}")
            if status in ("succeeded", "success"):
                content = data.get("content") or {}
                video_url = content.get("video_url") if isinstance(content, dict) else None
                if not video_url:
                    raise RuntimeError(f"seedance-ark task {task_id} succeeded but no video_url: {data}")
                # Phase 2: 尾帧 URL（已坐实 content.last_frame_url，候选元组兼容；未命中记 warning）
                last_frame_url = _parse_last_frame_url(content)
                logger.info("seedance-ark task %s completed in %.0fs", task_id, elapsed)
                return video_url, last_frame_url, data
            logger.debug("seedance-ark task %s: %s (%.0fs elapsed)", task_id, status, elapsed)
        raise TimeoutError(f"Ark seedance task {task_id} timed out after {self._max_wait:.0f}s")

    @staticmethod
    def _extract_failure_message(data: dict[str, Any]) -> str:
        """Pull a human-readable failure reason from a failed poll body (content + error)."""
        parts: list[str] = []
        content = data.get("content")
        if isinstance(content, dict):
            for key in ("fail_code", "failure_reason", "message"):
                v = content.get(key)
                if v:
                    parts.append(str(v))
        err = data.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code")
            if msg:
                parts.append(str(msg))
        if parts:
            return "; ".join(parts)
        return str(data.get("failure_reason") or data.get("error") or "unknown")

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
