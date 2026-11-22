"""Doubao voice ID helpers retained for the existing call sites."""

from __future__ import annotations


def is_doubao_voice_id(voice_id: str) -> bool:
    return voice_id.startswith("doubao:") and len(voice_id) > len("doubao:")


def parse_doubao_voice_id(voice_id: str) -> str:
    speaker = voice_id.split(":", 1)[1]
    if "/" in speaker or "\\" in speaker or ".." in speaker:
        raise ValueError(f"invalid doubao speaker id: {speaker!r}")
    return speaker
