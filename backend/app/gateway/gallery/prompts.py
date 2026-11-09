"""Prompt templates for AI-powered recipe extraction from conversations."""

RECIPE_EXTRACTION_SYSTEM_PROMPT = """你是一位 AI 创作方法论分析师。你的任务是从一段多轮对话中提炼出可复用的「创作配方」，让其他人可以学习同样的方法论来获得优质成果。

输出要求：
1. 只输出 JSON，不要任何其他文字或解释
2. JSON 必须符合指定结构
3. 忽略对话中的隐私信息、文件路径、用户名等敏感内容
4. 用中文填写所有文本字段"""

RECIPE_EXTRACTION_USER_PROMPT = """请分析以下对话记录，提取创作配方。

案例标题：{title}
案例分类：{category}

对话记录：
{conversation}

请输出以下 JSON 结构（只输出 JSON，不要其他文字）：
{{
  "goal": "一句话描述这个案例的目标",
  "approach": "方法论名称（如：渐进式细化法、角色扮演法、结构化提问法）",
  "difficulty": "beginner 或 intermediate 或 advanced",
  "est_steps": 3,
  "context_blocks": [
    "给 AI 的角色设定和上下文指令",
    "..."
  ],
  "steps": [
    {{
      "instruction": "这一步做什么（给用户看的指导）",
      "template": "prompt 模板，用 {{variable}} 标记可变部分",
      "example": "从对话中提取的实际例子",
      "tip": "为什么这样做有效（具体的教学性提示）",
      "vars": {{
        "variable_name": {{
          "label": "对话式提问",
          "type": "string、select 或 file",
          "required": true,
          "placeholder": "输入提示",
          "options": ["选项1", "选项2"]
        }}
      }}
    }}
  ],
  "pitfalls": [
    "从对话中总结的常见错误和避坑建议"
  ]
}}

要求：
1. steps 数量等于实际对话中的关键转折轮次（通常 2-5 步）
2. 每步的 template 必须可复用，用 {{variable}} 标记新用户需填写的部分
3. tip 要具体、有教育意义，不要空话
4. context_blocks 要能作为 system prompt 注入，让 AI 理解角色定位
5. vars 中 type 为 select 时必须提供 options 数组
6. vars 中 type 为 string 时 options 应为 null
7. 若对话方法论依赖用户上传文件（如"上传 PDF 生成 PPT"），在 vars 中补一个 type='file' 的变量：
   label 写明所需文件与用途（如"源文档（PDF）"），accept 给出扩展名（如 ".pdf,.docx"），required=true。
   仍不得包含真实文件名或路径，只描述文件类型与用途"""


RECIPE_RETRY_SUFFIX = """

重要提醒：你上次的输出无法被解析为有效 JSON。请严格按照上述 JSON 结构重新输出，确保：
- 输出的第一个字符是 {
- 最后一个字符是 }
- 没有额外的 markdown 代码块标记
- 所有字符串字段都用双引号"""


def _extract_message_text(content) -> str:
    """Pull text from a model_dump ``content`` field (str or multimodal block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return ""


_ATTACHMENT_TYPE_LABELS = {
    "application/pdf": "PDF",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPT",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Excel",
}


def _attachment_marker(block: dict) -> str | None:
    """非文本 block → 类型标记（不含文件名/路径，隐私保留）。识别不了 → 通用'文件'。"""
    btype = str(block.get("type", ""))
    if btype in ("text",):
        return None
    mime = str(block.get("mime") or block.get("mime_type") or "")
    if btype in ("image", "image_url") or mime.startswith("image/"):
        return "图片"
    label = _ATTACHMENT_TYPE_LABELS.get(mime)
    if label:
        return label
    if btype in ("file", "document", "attachment") or mime:
        return "文件"
    return None


def format_messages_for_extraction(messages: list[dict]) -> str:
    """Format checkpoint messages into a readable conversation transcript.

    ``messages`` are the ``model_dump()`` dicts produced by
    ``serialize_channel_values`` on a checkpoint's ``channel_values["messages"]``
    (Shape B): each carries a ``type`` (``human``/``ai``/``tool``/``system``) and a
    ``content`` that may be a string or a list of multimodal content blocks.

    - ``human`` -> ``👤 用户:`` (capped at 2000 chars)
    - ``ai``    -> ``🤖 AI:`` (capped at 1000 chars); a turn whose only payload is
      ``tool_calls`` (empty text) is skipped so no empty line is emitted
    - ``tool`` / ``system`` / anything else -> skipped
    """
    lines = []
    for msg in messages:
        role = msg.get("type", "")
        # Hidden messages (D-3): dynamic-context reminders carry the user's full
        # memory block; view-image contexts and todo reminders carry internal
        # state. None of it is conversation content — exclude before formatting
        # so it never reaches the extraction LLM as "👤 用户:" input.
        additional_kwargs = msg.get("additional_kwargs") or {}
        if additional_kwargs.get("hide_from_ui") or additional_kwargs.get("dynamic_context_reminder"):
            continue
        text = _extract_message_text(msg.get("content", ""))
        if role == "human":
            # 附件类型标记（2026-07-27 T11）：提取需要"看见"用户上传了文件才能
            # 学会"此处需上传"——只标类型，不带文件名/路径（隐私指令保留）。
            markers: list[str] = []
            content = msg.get("content")
            if isinstance(content, list):
                markers = [m for m in (_attachment_marker(b) for b in content if isinstance(b, dict)) if m]
            marker_text = f" [用户上传了：{'、'.join(dict.fromkeys(markers))}]" if markers else ""
            if not text and not marker_text:
                continue
            lines.append(f"👤 用户: {text[:2000]}{marker_text}")
        elif role == "ai":
            if not text:
                continue
            truncated = text[:1000]
            if len(text) > 1000:
                truncated += "...[已截断]"
            lines.append(f"🤖 AI: {truncated}")

    return "\n\n".join(lines) if lines else "(空对话)"


def strip_markdown_code_fence(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
        # Remove first line (```json or ```) and last line (```)
        body = "\n".join(lines[1:-1]).strip()
        return body
    return stripped
