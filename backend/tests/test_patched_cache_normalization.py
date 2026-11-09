"""B2: cache 字段名归一——360/Ark 的 prompt_cache_hit_tokens → 标准 cached_tokens。

下游 journal._extract_cache_read 读 input_token_details.cache_read；
LangChain _create_usage_metadata 从 prompt_tokens_details.cached_tokens 填入。
修前 360 非标字段被丢弃 → cache_read 恒 0。
"""


def test_normalize_cache_field_rewrites_prompt_cache_hit():
    from deerflow.models.patched_openai import _normalize_cache_field

    usage = {"prompt_tokens": 200, "completion_tokens": 50, "prompt_cache_hit_tokens": 100}
    result = _normalize_cache_field(dict(usage))
    assert result["prompt_tokens_details"]["cached_tokens"] == 100


def test_normalize_cache_field_noop_when_absent():
    from deerflow.models.patched_openai import _normalize_cache_field

    usage = {"prompt_tokens": 200, "completion_tokens": 50}
    result = _normalize_cache_field(dict(usage))
    assert "prompt_tokens_details" not in result or "cached_tokens" not in result.get("prompt_tokens_details", {})


def test_normalize_cache_field_preserves_existing_details():
    from deerflow.models.patched_openai import _normalize_cache_field

    usage = {
        "prompt_tokens": 200,
        "completion_tokens": 50,
        "prompt_cache_hit_tokens": 80,
        "prompt_tokens_details": {"audio_tokens": 10},
    }
    result = _normalize_cache_field(dict(usage))
    assert result["prompt_tokens_details"]["cached_tokens"] == 80
    assert result["prompt_tokens_details"]["audio_tokens"] == 10


def test_normalize_cache_field_string_value():
    """360 sometimes returns amount as string; cache_hit may also be string."""
    from deerflow.models.patched_openai import _normalize_cache_field

    usage = {"prompt_tokens": 200, "completion_tokens": 50, "prompt_cache_hit_tokens": "120"}
    result = _normalize_cache_field(dict(usage))
    assert result["prompt_tokens_details"]["cached_tokens"] == 120


def test_streaming_chunk_cache_read_is_populated():
    """B2 核心场景：流式 chunk 带 prompt_cache_hit_tokens → usage_metadata.cache_read 有值。"""
    from deerflow.models.patched_openai import PatchedChatOpenAI

    llm = PatchedChatOpenAI(
        api_key="test-key",
        model="doubao-seed-1.6-pro-32k-250615",
        base_url="https://api.360.cn/v1",
    )
    chunk = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "model": "doubao-seed-1.6-pro-32k-250615",
        "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
        "usage": {"prompt_tokens": 200, "completion_tokens": 50, "prompt_cache_hit_tokens": 150},
    }
    from langchain_core.messages.ai import AIMessageChunk

    gen_chunk = llm._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert gen_chunk is not None
    um = gen_chunk.message.usage_metadata
    assert um is not None
    assert um["input_token_details"]["cache_read"] == 150
