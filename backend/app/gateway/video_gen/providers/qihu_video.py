"""360-海螺 async video generation provider.

Wire format from skills/public/video-generation/scripts/generate.py, async rewrite.
Submit: POST {base_url}/v1/videos/async_generations
Poll: GET {base_url}/v1/videos/async_generations/{id}
Supports reference images (image-to-video) via content array.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from typing import Any

import httpx

from .base import VideoRef, _append_request_id, _extract_cost
from .seedream import _to_data_uri
from .throttle import RateLimitError

logger = logging.getLogger(__name__)


# A1: 轮询期带「稍后再试」语义的 4xx —— 只有这些值得 continue。
# 408 Request Timeout / 409 Conflict（任务状态迁移中）/ 425 Too Early / 429 Too Many Requests。
# 其余 4xx（401/403/404/400…）是永久错误，continue 只会白等到 max_wait，最后抛
# 误导性的 TimeoutError 而非真实原因（如「鉴权失效」）。
# 范本：seedance_ark.py:_RETRYABLE_POLL_STATUS（同一修复，早先已落地）。
_RETRYABLE_POLL_STATUS = frozenset({408, 409, 425, 429})


def _extract_task_data_from_message(message: Any) -> dict | None:
    """360 wraps task state inside error.message as a JSON string.

    Poll failures come back as HTTP 400 with body
    ``{"error":{"code":"10940","message":"<JSON string with data.status>"}}``.
    The message field is itself a JSON-encoded string (sometimes a dict).
    Parse it and return the inner ``data`` dict if present, else None.
    """
    if isinstance(message, str):
        try:
            message = json.loads(message)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(message, dict):
        return None
    data = message.get("data")
    return data if isinstance(data, dict) else None


# 2026-08-03：MiniMax-H3 的请求体要求（实测 https://api.360.cn/v1/videos/async_generations）
# 与同 provider 的 Hailuo 2.3 不同——H3 必须带扁平 extra_body.resolution + .ratio，
# 缺任一即 400（`missing required parameter resolution` / `t2va 场景必须显式指定 ratio`）。
# resolution 只接受 768P / 2K（1080P 会 400）；取 768P 作默认（2K 成本更高）。
_H3_RESOLUTION = "768P"
_H3_SUPPORTED_RATIOS = frozenset({"16:9", "4:3", "1:1", "3:4", "9:16", "21:9"})


def _is_h3_model(model: str) -> bool:
    """是否 MiniMax-H3（大小写不敏感，容忍 `minimax/MiniMax-H3` 等前缀写法）。"""
    return "minimax-h3" in (model or "").lower()


# 2026-09-04（H3 r2v 计划 S1）：多图参考开关。默认开；off 值口径同
# visual_routing._OFF_VALUES（本地字面量——providers 层不 import orchestration）。
# 调用时读（对齐 VIDEO_GEN_PREVIEW_INHERIT 惯例：provider 单例跨任务，env 改动
# 下一任务即生效；测试 monkeypatch.setenv 即可）。
_H3_R2V_OFF_VALUES = ("", "0", "false", "no", "off")


def _h3_r2v_enabled() -> bool:
    """VIDEO_GEN_H3_R2V（默认 "1" 开；"0" 一键回退 first-frame-only 旧行为）。"""
    return os.environ.get("VIDEO_GEN_H3_R2V", "1").strip().lower() not in _H3_R2V_OFF_VALUES


def _extract_failure_from_error_body(resp: httpx.Response) -> str | None:
    """For a non-200 poll response, extract a FAILED task's failure_reason.

    Returns the failure_reason string if the embedded task status is
    failed/error, else None (caller should keep polling). Tolerates bodies
    that aren't the expected error envelope.
    """
    try:
        body = resp.json()
    except Exception:
        return None
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return None
    task_data = _extract_task_data_from_message(err.get("message"))
    if task_data is None:
        return None
    status = str(task_data.get("status", "")).lower()
    if status in ("failed", "error"):
        return str(task_data.get("failure_reason") or "unknown")
    return None


def _parse_task_data(body: Any) -> tuple[dict, str | None]:
    """Parse a 200 poll body into (task_data, failure_reason|None).

    failure_reason is non-None when status is failed/error (caller raises).
    """
    task_data = body.get("data", {}) if isinstance(body, dict) else {}
    if not isinstance(task_data, dict):
        task_data = {}
    status = str(task_data.get("status", "unknown")).lower()
    if status in ("failed", "error"):
        return task_data, str(task_data.get("failure_reason") or "unknown")
    return task_data, None


class QihuServerError(RuntimeError):
    """A transient 5xx fault from the 360 video API (e.g. HTTP 500 '服务异常', code 10183).

    Subclasses RuntimeError so existing `except RuntimeError` handlers still catch it,
    but lets the shot-level retry predicate distinguish it from genuine 4xx client
    errors (a 5xx server fault often recovers on retry; a 4xx never will). Raised by
    _submit for status >= 500; 4xx stays a plain RuntimeError.
    """


class QihuClientError(RuntimeError):
    """4xx 客户端错误（重试不会变好，也不计入 provider 熔断）--T3（2026-08-17）。

    事故复盘（MiniMax-H3 duration 400 级联）：确定性 4xx 被 Task 14「非可重试
    也计数」计为 provider failure -> 3 次开闸 -> 时长合法的帧全部被断路器连坐
    占位（12/29）。熔断器度量 provider 健康度，不是单请求合法性。带 status_code
    供 throttle._counts_toward_breaker 豁免分类；429 在此前已抛 RateLimitError
    （可重试+计数语义保持），sc != 429 是防御纵深。
    """

    def __init__(self, msg: str, status_code: int) -> None:
        super().__init__(msg)
        self.status_code = status_code


class QihuVideoProvider:
    """360-海螺 async video generation (submit + poll).

    H3 多图参考 r2v（2026-09-04）：role="reference" 且开关 VIDEO_GEN_H3_R2V 开
    （仅 _is_h3_model）时全量 image_urls 逐张进 content[]、extra_body 注入全局
    role="reference"（与既有 resolution/ratio 合并）——360 网关 r2v 唯一正确接法
    （per-item role 被静默吞掉、裸发多图=首尾帧陷阱，探针 T1/T3，报告见
    temp/h3_reference_probe/RESULTS.md）。首帧语义（role=None/first_frame/
    first_frame_reference）与开关关闭时维持取首图 i2v 旧行为（逐字节不变）。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.360.cn",
        model: str = "minimax/MiniMax-Hailuo-2.3-no-safety_checker",
        poll_interval: float = 15.0,
        max_wait: float = 600.0,
        timeout: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._poll_interval = poll_interval
        self._max_wait = max_wait
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    # ── T1 时长栅格声明（2026-08-17 H3 duration 400 修复）──
    # H3 网关实锤 supported durations: 4s...15s（整数秒，事故 400 报文原文）；
    # 不声明 -> align_to_provider_grid 对 H3 no-op -> 3.84s 旁白直发 3s -> 400 连环。
    # Hailuo 2.3 无栅格（保守 0.0/False/8.0，行为逐值不变）。
    @property
    def min_video_duration(self) -> float:
        return 4.0 if _is_h3_model(self._model) else 0.0

    @property
    def integer_seconds_only(self) -> bool:
        return _is_h3_model(self._model)

    @property
    def max_video_duration(self) -> float:
        return 15.0 if _is_h3_model(self._model) else 8.0

    # ── T3（2026-08-18）：H3 图生视频能力声明 ──
    # 官方实锤（backend/docs/360-api/create-video-generation.md 示例9/10）：
    # H3 图生视频 = content[].image_url（**无 role**），单图=首帧 i2v、双图按序
    # =首尾帧。按 _is_h3_model 门控——Hailuo 2.3（已下架）保守不声明；
    # 子类 seedance/happyhorse 用类属性遮蔽本 property（各自接线不同）。
    # 2026-09-04 r2v：多图参考模式经全局 extra_body.role="reference" 切换（见
    # _r2v_image_urls）——本 property 仍是 i2v/r2v 共用的「支持 image_urls」闸。
    @property
    def supports_image_urls(self) -> bool:
        return _is_h3_model(self._model)

    # ── 审查偏离项 3（2026-08-19）：多图截断打标 ──
    # H3/happyhorse 首帧口径只消费首图，其余参考图（角色三视图 side/back）此前
    # 静默丢弃——对照 registry 降级分支的 degraded_without_refs 打标标准补齐：
    # 实例标记 + warning 日志 + sidecar 字段（ai_video 读，经包裹链透传）。
    multi_image_truncated: bool = False

    def _first_frame_only(self, image_urls: list[str]) -> str:
        """取首图；多图时置截断标记并 warning（registry 降级分支同标准）。

        仅首帧语义路径（role≠"reference"）经此截断；r2v 分派（2026-09-04 起）在
        调用方短路——全量下发、不置标（见 _r2v_image_urls）。
        """
        if len(image_urls) > 1:
            self.multi_image_truncated = True
            logger.warning(
                "%s accepts only a single first-frame image — dropping %d of %d character reference images; character consistency degraded",
                type(self).__name__,
                len(image_urls) - 1,
                len(image_urls),
            )
        return image_urls[0]

    def reset_multi_image_truncated(self) -> None:
        """清零截断标记（provider 单例跨任务；ai_video render 开始经包裹链转发调用）。"""
        self.multi_image_truncated = False

    def _r2v_image_urls(self, image_urls: list[str] | None, role: Any) -> list[str] | None:
        """r2v 分派（2026-09-04）：命中则返回归一后的全量参考图，未命中返回 None。

        命中条件：role="reference" + image_urls 非空 + 本模型是 H3 + 开关开。
        其余（首帧语义 / 非 H3 / 开关关）返回 None，调用方走旧 first-frame-only
        路径（逐字节不变）。单图 role="reference" 同样走 r2v——无预览图帧只有
        1 张合成图 / style_anchor 兜底单图，正是「合成图当首帧」事故（frame 85）
        的核心分支。r2v 无首帧概念：不截断、不置 multi_image_truncated；>6 张
        （超工作点）只记 debug 便于出 400 时定位（张数防护单点在 apply_ref_cap，
        此处不设第二道闸）。
        """
        if role != "reference" or not image_urls:
            return None
        if not _is_h3_model(self._model) or not _h3_r2v_enabled():
            return None
        urls = [_to_data_uri(u) for u in image_urls]
        if len(urls) > 6:
            logger.debug("H3 r2v: %d reference images exceed working point 6 (sent untruncated)", len(urls))
        return urls

    async def generate_video(
        self,
        prompt: str,
        *,
        duration: float = 8.0,
        **kwargs: Any,
    ) -> VideoRef:
        image_url = kwargs.get("image_url")
        # T3（2026-08-18）：调用方（ai_video.py）一律传复数 image_urls——修前只读
        # 单数，复数被 **kwargs 静默吞。只声明能力不改这里 = 从「诚实降级」变
        # 「静默丢图」（更严重）。首帧语义：取首图 → data-URI 归一（官方口径：
        # url 须为可访问 URL 或 Base64，本地路径远端抓不到；registry 降级分支同
        # 规）→ 映到单数走既有 content array 构造（无 role 恰合官方 H3 首帧示例
        # 形态）。r2v（2026-09-04）：role="reference" 且 H3 且开关开 → 全量下发
        # （_r2v_image_urls），不截断不置标。
        image_urls = kwargs.get("image_urls")
        r2v_urls = self._r2v_image_urls(image_urls, kwargs.get("role"))
        if image_urls and not r2v_urls:
            image_url = _to_data_uri(self._first_frame_only(image_urls))
        # 2026-08-03：ratio 此前被 **kwargs 静默吞掉。调用方一直在传
        # （ai_video.py 的 _size_to_ratio、vimax provider_bridge 的 aspect_ratio），
        # 但从未进 body —— H3 因此必然 400（见 _submit 的 extra_body 注释）。
        ratio = kwargs.get("ratio")
        task_id = await self._submit(prompt, duration=duration, image_url=image_url, ratio=ratio, image_urls=r2v_urls)
        video_url, poll_body = await self._poll(task_id)
        cost_cny, raw_usage = _extract_cost(poll_body)
        return VideoRef(url=video_url, duration=duration, source="qihu", cost_cny=cost_cny, raw_usage=raw_usage)

    async def submit_video(
        self,
        prompt: str,
        *,
        duration: float = 8.0,
        image_url: str | None = None,
        image_urls: list[str] | None = None,
        ratio: str | None = None,
        **kwargs: Any,
    ) -> str:
        """A3: 只提交，返回远端 task_id。throttle 层重试时复用此 id 只重连 poll。

        与 :meth:`generate_video` 的区别：**不轮询**。与 seedance_ark.submit_video 同设计。
        seedance/happyhorse 子类继承此方法——它们的 ``_submit`` override 自动生效。
        T3（2026-08-18）：复数 image_urls 与 generate_video 同口径（取首图 data-URI
        归一映到单数）——只修 generate_video 会给 Throttle 分离提交路径留下第二条
        静默丢图通道。r2v（2026-09-04）：role="reference" 分派与 generate_video
        同口径（两条提交通道必须同行为，防第二通道静默丢图）。
        """
        r2v_urls = self._r2v_image_urls(image_urls, kwargs.get("role"))
        if image_urls and not r2v_urls:
            image_url = _to_data_uri(self._first_frame_only(image_urls))
        return await self._submit(prompt, duration=duration, image_url=image_url, ratio=ratio, image_urls=r2v_urls)

    async def poll_video(self, task_id: str, *, duration: float = 8.0, **kwargs: Any) -> VideoRef:
        """A3: 只轮询已提交的任务。重试安全：不产生新的计费任务。"""
        video_url, poll_body = await self._poll(task_id)
        cost_cny, raw_usage = _extract_cost(poll_body)
        return VideoRef(url=video_url, duration=duration, source="qihu", cost_cny=cost_cny, raw_usage=raw_usage)

    async def _submit(
        self,
        prompt: str,
        *,
        duration: float,
        image_url: str | None,
        ratio: str | None = None,
        image_urls: list[str] | None = None,
    ) -> str:
        client = self._get_client()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if image_urls:
            # r2v（2026-09-04）：全量参考图逐张进 content[]，无 per-item role——
            # 360 网关的 r2v 模式由全局 extra_body.role 切换（per-item role 会被
            # 静默吞掉退化首尾帧，探针 T1；MiniMax 官方直连才有 per-item 写法）。
            # 仅 H3 命中 _r2v_image_urls 分派才会传到此（seedance/happyhorse 子类
            # 各自 override _submit，不经此分支）。
            for one in image_urls:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": one},
                    }
                )
        elif image_url:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_url},
                }
            )
        body: dict[str, Any] = {
            "model": self._model,
            "content": content,
            "duration": int(math.ceil(duration)),
        }
        # 2026-08-03（BUG：minimax-h3 100% 400）：H3 强制要求扁平
        # extra_body.resolution（仅 768P/2K，**不支持** 1080P）+ extra_body.ratio
        # （16:9/4:3/1:1/3:4/9:16/21:9），缺任一即 400；Hailuo 2.3 两者都不需要。
        #
        # 只对 H3 加 extra_body（**不是**无条件加）：qihu/Hailuo 既是 config 默认
        # ai_video_provider，又是 seedance-ark 的降级目标，是主力出片路径。
        # 「Hailuo 带 extra_body 也不报错」这一条我没有本地 key 复验，不拿主力路径
        # 去赌一个未复验的结论——按模型门控，H3 得到修复，Hailuo 行为逐字节不变。
        #
        # ratio 兜底 16:9：_size_to_ratio 对非常规尺寸会返回 "adaptive"，而它**不在**
        # H3 的可用值里（会 400）；Hailuo 侧无此字段故无影响。
        if _is_h3_model(self._model):
            extra_body: dict[str, Any] = {"resolution": _H3_RESOLUTION}
            extra_body["ratio"] = ratio if ratio in _H3_SUPPORTED_RATIOS else "16:9"
            if image_urls:
                # r2v：全局 role="reference" 与既有 resolution/ratio **合并**
                # （三键并存，非覆盖——缺 resolution/ratio 仍会 400）。
                extra_body["role"] = "reference"
            body["extra_body"] = extra_body
        resp = await client.post(
            f"{self._base_url}/v1/videos/async_generations",
            headers=headers,
            json=body,
        )
        if resp.status_code == 429:
            raise RateLimitError(f"360 video API rate limited: {resp.text}")
        if resp.status_code != 200:
            # 5xx is a transient server fault (e.g. 500 '服务异常', code 10183) that
            # often recovers on retry → QihuServerError (the shot retry covers it). A 4xx
            # is a client error (bad request / auth) that retrying won't fix → plain
            # RuntimeError so the shot retry skips it.
            msg = f"360 video submit failed ({resp.status_code}): {resp.text}"
            msg = _append_request_id(resp, msg)
            if resp.status_code >= 500:
                raise QihuServerError(msg)
            raise QihuClientError(msg, resp.status_code)
        data = resp.json()
        task_data = data.get("data", {})
        task_id = task_data.get("id")
        if not task_id:
            raise RuntimeError(f"360 video API: no task id in response: {data}")
        logger.info("Video task submitted: %s", task_id)
        return task_id

    async def _poll(self, task_id: str) -> tuple[str, dict[str, Any]]:
        """Poll until success/failed. Returns (video_url, raw_body).

        raw_body is the full poll response JSON (B1: contains ``usage`` for cost extraction).
        """
        client = self._get_client()
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"{self._base_url}/v1/videos/async_generations/{task_id}"
        elapsed = 0.0
        while elapsed < self._max_wait:
            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                # 360 sometimes returns the task state embedded in a 400 error
                # body (code 10940): {"error":{"code":"10940","message":"<JSON
                # with data.status=FAILED/failure_reason>"}}. The old code did a
                # bare `continue` here — never parsing the embedded FAILED — so a
                # content-review rejection polled the full max_wait then raised a
                # misleading TimeoutError ("timed out after 400s") instead of the
                # real failure_reason. Parse the body; if the task is FAILED,
                # raise immediately (second-fastest path after a 200 FAILED).
                failure = _extract_failure_from_error_body(resp)
                if failure is not None:
                    raise RuntimeError(f"Video task {task_id} failed: {failure}")
                # A1: 永久性 4xx 立即抛，不烧满 max_wait（范本 seedance_ark.py:234）。
                # 修前一律 continue——401 key 过期 / 404 任务不存在这类永不自愈的
                # 错误也轮满 600s，最后抛误导性 TimeoutError。仅对带「稍后再试」
                # 语义的状态码保留 continue；5xx 是瞬态服务器故障，也保留。
                if resp.status_code not in _RETRYABLE_POLL_STATUS and resp.status_code < 500:
                    raise RuntimeError(
                        _append_request_id(
                            resp,
                            f"360 video poll permanent error ({resp.status_code}) for task {task_id}: {resp.text[:300]}",
                        )
                    )
                logger.warning("Video poll error %d, retrying (body=%s)", resp.status_code, resp.text[:300])
                continue
            task_data, failure = _parse_task_data(resp.json())
            if failure is not None:
                raise RuntimeError(f"Video task {task_id} failed: {failure}")
            status = task_data.get("status", "unknown").lower()

            if status in ("success", "succeeded"):
                # Hailuo returns "success"; seedance (doubao-seedance-2-0) returns
                # "succeeded". Accept both — discovered via live C0 smoke test (a
                # status=="success"-only check made seedance time out at max_wait
                # despite succeeding at ~150s).
                video_url = task_data.get("url")
                if not video_url:
                    raise RuntimeError("Video task succeeded but no URL returned")
                logger.info("Video task %s completed in %.0fs", task_id, elapsed)
                return video_url, resp.json()
            logger.debug(
                "Video task %s: %s (%.0fs elapsed)",
                task_id,
                status,
                elapsed,
            )

        raise TimeoutError(f"Video task {task_id} timed out after {self._max_wait:.0f}s")

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
