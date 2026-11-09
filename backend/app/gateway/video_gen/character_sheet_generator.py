"""Task 10/11: 角色三视图生成 + 上传。

方式 A（generate_character_sheet）：文字描述 → front/side/back 三视图，
  side/back 用 front 作 reference 保持一致性。复用 ViMax character_portraits 思路。
方式 B（save_uploaded_character_sheet）：用户上传 front/side/back 图，落盘。

Task 12/13 注入各引擎作 reference，实现跨视频角色一致。

合成图方案（2026-08-27 计划 X2~X4）：三视图生成/上传后自动拼 1 张横向合成图
（turnaround sheet）——注入通道每角色恒带 1 张合成图（三人同框 3×1 卡工作点 6，
根治 T2⑤ 第 3 人被预算截断纯文本画崩）；分离三视图保留资产页展示。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from app.gateway._shared.safe_download import safe_download

from .course_models import CharacterSheet

logger = logging.getLogger(__name__)

# X2 布局常量：每格视图 512×512（等比缩放 + 白底填充）+ 底部 40px 中文标注。
_COMPOSITE_VIEW_SIZE = 512
_COMPOSITE_LABEL_H = 40
_COMPOSITE_TAG_LABELS = {"front": "正面", "side": "侧面", "back": "背面"}
# A（2026-08-31 拍板）：合成图方形化——横排三联 1536×552（比例 2.783）超出
# i2v provider 比例硬校验 [0.4, 2.5]（MiniMax-H3 实锤，任务 vg_a01c4ceb1a3c
# 8 帧全败根因之一）。三联内容等比缩放居中到 1152×1152 方形白底画布（比例 1.0）。
_COMPOSITE_SQUARE_SIZE = 1152

# 跨平台 CJK 字体候选（macOS PingFang / Linux Noto·文泉驿）。均不可用时回落
# Pillow 内置位图字体（中文标注退化为方块——合成图主体可用性不受影响，日志告知）。
_CJK_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",  # macOS（容器外本地跑）
    "/System/Library/Fonts/STHeiti Light.ttc",  # macOS 旧版
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Debian/Ubuntu noto-cjk
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",  # 火山云镜像常见路径
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",  # 文泉驿微米黑
)


def _load_label_font(size: int):
    """标注字体：候选路径首个可用者；无 CJK 字体时回落 load_default（方块降级）。"""
    from PIL import ImageFont

    for p in _CJK_FONT_CANDIDATES:
        try:
            if Path(p).exists():
                return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001 - ttc 损坏等 → 试下一个候选
            continue
    logger.warning("composite label: no CJK font found in candidates — labels will render as boxes")
    return ImageFont.load_default()


def build_composite_image(views: list[tuple[str, str]], dest: Path) -> str:
    """X2：三视图横向拼接为 1 张合成图（turnaround sheet），返回合成图绝对路径。

    ``views`` 为 ``[(tag, path), ...]`` 按序横排（tag 映射 正面/侧面/背面 标注）；
    每格统一 512×512（保持宽高比、白底填充）+ 底部 40px 标注。1 张视图时直接复制
    为合成图（不重复缩放损失）。视图文件缺失/损坏 → 跳过该视图并在标注区留空位
    说明（「无正面」）；全部不可用 → ``ValueError``（调用方按「无合成图」降级）。

    A（2026-08-31）：横排条带**等比缩放居中到 1152×1152 方形白底画布**——修前
    1536×552（比例 2.783）超出 i2v provider 比例硬校验 [0.4, 2.5]（H3 实锤），
    且作为首帧下发的通道会整批 400。比例 ≈1.0 居中，白底填充；标注随内容缩放。
    """
    from PIL import Image, ImageDraw, ImageOps

    if not views:
        raise ValueError("build_composite_image: no views provided")

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cells: list[tuple[Image.Image, str]] = []
    for tag, path in views:
        label = _COMPOSITE_TAG_LABELS.get(tag, tag or "")
        img = None
        try:
            with Image.open(path) as opened:
                opened.load()  # 触发解码——损坏文件在此抛异常而非留到 save
                img = ImageOps.contain(opened.convert("RGB"), (_COMPOSITE_VIEW_SIZE, _COMPOSITE_VIEW_SIZE))
        except Exception as e:  # noqa: BLE001 - 缺失/损坏降级为留白格
            logger.warning("composite view %s unusable (%s): %s — leaving blank cell", tag, path, e)
        cells.append((img, label))

    if all(img is None for img, _ in cells):
        raise ValueError(f"build_composite_image: all views unusable: {[p for _, p in views]}")

    if len(cells) == 1 and cells[0][0] is not None:
        # 单视图直出：复制原文件（不重复缩放损失可接受，X2 计划口径；A 不动此分支
        # ——单图本身就是常规比例，生图产物不会超 [0.4,2.5]）
        shutil.copyfile(views[0][1], dest)
        return str(dest.resolve())

    cell_h = _COMPOSITE_VIEW_SIZE + _COMPOSITE_LABEL_H
    strip = Image.new("RGB", (_COMPOSITE_VIEW_SIZE * len(cells), cell_h), "white")
    draw = ImageDraw.Draw(strip)
    font = _load_label_font(28)
    for i, (img, label) in enumerate(cells):
        x0 = i * _COMPOSITE_VIEW_SIZE
        if img is not None:
            # 等比缩放后居中贴到白底格（ImageOps.contain 已保比例；含白边填充）
            strip.paste(img, (x0 + (_COMPOSITE_VIEW_SIZE - img.width) // 2, (_COMPOSITE_VIEW_SIZE - img.height) // 2))
            text = label
        else:
            text = f"无{label}"
        # 标注居中（缺字体的 load_default 下 textbbox 仍可用，仅字形退化）
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            (x0 + (_COMPOSITE_VIEW_SIZE - tw) // 2 - bbox[0], _COMPOSITE_VIEW_SIZE + (_COMPOSITE_LABEL_H - th) // 2 - bbox[1]),
            text,
            fill=(80, 80, 80),
            font=font,
        )
    # A（2026-08-31）：条带等比缩放到方形画布内接并居中（白底填充上下留白）。
    side = _COMPOSITE_SQUARE_SIZE
    scale = min(side / strip.width, side / strip.height)
    scaled = strip.resize((max(1, round(strip.width * scale)), max(1, round(strip.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGB", (side, side), "white")
    canvas.paste(scaled, ((side - scaled.width) // 2, (side - scaled.height) // 2))
    canvas.save(dest, format="PNG")
    return str(dest.resolve())


async def _download_image_bytes(url: str, dest: Path) -> None:
    """#7: provider ImageRef（远程 URL **或 data: URI**）bytes 落盘，character_reference 不随 URL 过期。

    B3 镜像补全（2026-08-31）：风格锚图与三视图走同一 provider 链，而该链已实测会
    返回 data: URI（锚图侧正是因此修过落盘）——裸 httpx.get 遇 data: 抛
    UnsupportedProtocol，三视图生成直接 500。data URI 就地 base64 解码。
    命名契约维持 .png（serve glob `{tag}*.png` 与三视图路径假定 .png）；PIL 拼合与
    浏览器按魔数识别实际格式，不受扩展名影响（与 URL 分支既有行为一致）。
    """
    import base64

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("data:"):
        b64 = url.split(",", 1)[1] if "," in url else ""
        dest.write_bytes(base64.b64decode(b64))
        return
    # C2（2026-10-05 审计 §2-1）：出站下载统一走 SSRF 守卫。
    dest.write_bytes(await safe_download(url, timeout=60.0))


def _local_rel(project_id: str, name: str) -> str:
    """character_sheets/<project_id>/<name>_<uuid8>.png 本地相对路径（唯一，防多次生成覆盖）。"""
    return f"character_sheets/{project_id}/{name}_{uuid.uuid4().hex[:8]}.png"


def _with_style(style_prefix: str, body: str) -> str:
    """B1: 把项目 style_prefix 前置到角色图 prompt（空则原样返回，无前导逗号）。

    镜像 ``storyboard_planner.py``「Every prompt MUST start with style_prefix」约定，
    让「项目视觉风格预设」对角色三视图生效（修前角色图完全无视风格）。
    T5 三轨对齐（2026-08-18）：委托 planner.with_style_prefix 共用拼装措辞——与
    重生端点硬前置、bulk system 硬约束三轨同一语义（改一处须同步其余两轨）。
    """
    from .orchestration.storyboard_planner import with_style_prefix

    return with_style_prefix(style_prefix, body)


async def generate_character_sheet(
    *,
    description: str,
    name: str,
    features: str,
    image_gen,
    storage_dir: str,
    project_id: str,
    style_prefix: str = "",
) -> CharacterSheet:
    """方式 A：文字描述 → 三视图。front 先出（串行，side/back 依赖它作 reference）；
    side/back **并发**生成（2026-08-31 方案 A，asyncio.gather 默认失败语义，见下方注释）。
    #7：下载 provider URL bytes 落盘，返回**绝对**本地路径（与方式 B 一致；相对路径会让
    _to_data_uri/_to_image_url 的 os.path.exists 按 CWD 解析失败→Ark image 字段塞相对串→4xx 不降级）。
    """
    front = await image_gen(_with_style(style_prefix, f"{description}, 正面视图 portrait, {features}, front view"))
    front_abs = str(Path(storage_dir) / _local_rel(project_id, "front"))
    await _download_image_bytes(front.url, Path(front_abs))

    # 方案 A（2026-08-31）：side/back 并发生成——两者互不依赖、都以 front 为
    # reference，串行 3×33s≈100s 是角色参考图慢的主因（实测 seedream-360 单张
    # 33s，Throttle qps=1.0 与 fallback 均无异常）。gather 默认失败语义（一个
    # 失败即抛）与串行现状一致（整体失败）；已知可接受差异：side 失败时 back
    # 请求已发出（多一次生图计费约 ¥0.5；back 若成功，其落盘文件无 sheet 记录
    # 引用、注入链路不读取，仅占磁盘）。**不引入取消逻辑**——33s 的 HTTP 请求
    # 发出后取消不了，徒增复杂度。qps=1.0 下两个并发调用被限流器自然排队
    # （≥1s 间隔），无安全风险；并发后总耗时 ≈ front(33s)+max(side,back)(33s)
    # ≈ 66s（原 ≈100s）。
    async def _gen_view(view_prompt: str, tag: str) -> str:
        ref = await image_gen(
            _with_style(style_prefix, view_prompt),
            reference_image=front_abs,  # 绝对路径，provider 桥 _to_data_uri 可解析
        )
        abs_path = str(Path(storage_dir) / _local_rel(project_id, tag))
        await _download_image_bytes(ref.url, Path(abs_path))
        return abs_path

    side_abs, back_abs = await asyncio.gather(
        _gen_view(f"{description}, 侧面视图 profile, {features}, side view", "side"),
        _gen_view(f"{description}, 背面视图, {features}, back view", "back"),
    )

    # X3：三视图落盘后拼合成图（增强件——失败不阻断三视图返回，composite_url=""）
    composite_abs = ""
    try:
        composite_abs = build_composite_image(
            [("front", front_abs), ("side", side_abs), ("back", back_abs)],
            Path(storage_dir) / "character_sheets" / project_id / f"composite_{uuid.uuid4().hex[:8]}.png",
        )
    except Exception as e:  # noqa: BLE001 - 拼图失败按「无合成图」降级（注入侧回退视图逻辑）
        logger.warning("composite build failed for %s: %s — falling back to per-view refs", name, e)

    return CharacterSheet(
        name=name,
        front_url=front_abs,  # 绝对本地路径，非 TOS URL、非相对路径
        side_url=side_abs,
        back_url=back_abs,
        composite_url=composite_abs,
        features=features,
    )


async def save_uploaded_character_sheet(
    *,
    name: str,
    features: str,
    front_bytes: bytes,
    side_bytes: bytes | None = None,  # U1: 可选
    back_bytes: bytes | None = None,  # U1: 可选
    storage_dir: str,
    project_id: str,
) -> CharacterSheet:
    """方式 B：用户上传三视图图片，落盘返回 CharacterSheet。U1: side/back 可选。"""
    base = Path(storage_dir) / "character_sheets" / project_id
    base.mkdir(parents=True, exist_ok=True)

    def _save(b: bytes | None, tag: str) -> str:
        if not b:
            return ""  # U1: 未提供该视图 → 空串
        # SEC-M7（批次 2 项 2.6）：落盘后缀按魔数嗅探（路由层已校验+拒绝
        # 非图片 bytes；此处只做后缀校正，JPEG 不再躺成 .png——B3 口径）。
        from app.gateway.video_gen.visual_sources.ai_image import _sniff_image_ext

        ext = _sniff_image_ext(b) or ".png"
        p = base / f"{tag}_{uuid.uuid4().hex[:8]}{ext}"
        p.write_bytes(b)
        return str(p)

    saved = {"front": _save(front_bytes, "front"), "side": _save(side_bytes, "side"), "back": _save(back_bytes, "back")}

    # X4：按可用视图（1~3 张）拼合成图（增强件——失败不阻断上传，composite_url=""；
    # 单张上传 → 合成图即该图本身副本，build_composite_image 单图直出语义）
    composite_abs = ""
    views = [(tag, p) for tag, p in saved.items() if p]
    if views:
        try:
            composite_abs = build_composite_image(views, base / f"composite_{uuid.uuid4().hex[:8]}.png")
        except Exception as e:  # noqa: BLE001 - 拼图失败按「无合成图」降级
            logger.warning("composite build failed on upload (%s): %s", name, e)

    return CharacterSheet(
        name=name,
        features=features,
        front_url=saved["front"],
        side_url=saved["side"],
        back_url=saved["back"],
        composite_url=composite_abs,
    )
