"""Provider-neutral, text-faithful SRT timing helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass


DEFAULT_SUBTITLE_MAX_CHARS = 20
DEFAULT_BOUNDARY_OVERLAP_TOLERANCE_MS = 100
_TICKS_PER_MILLISECOND = 10_000
_SENTENCE_END = frozenset("。！？!?")
_CLAUSE_END = frozenset("，,；;：:")
_CLOSING_PUNCTUATION = frozenset('”’」』）》)"\'')


@dataclass(frozen=True)
class _MappedWord:
    start: int
    end: int
    source_start: int
    source_end: int


@dataclass(frozen=True)
class _SubtitleCue:
    start: int
    end: int
    text: str


def _text_key(text: str) -> str:
    return "".join(character.casefold() for character in text if character.isalnum())


def _source_key_positions(text: str) -> tuple[str, list[int]]:
    key: list[str] = []
    positions: list[int] = []
    for index, character in enumerate(text):
        if not character.isalnum():
            continue
        normalized = character.casefold()
        key.extend(normalized)
        positions.extend([index] * len(normalized))
    return "".join(key), positions


def _map_word_boundaries(
    text: str,
    boundaries: list[dict],
    provider_label: str,
) -> list[_MappedWord]:
    source_key, source_positions = _source_key_positions(text)
    boundary_keys = [_text_key(boundary["text"]) for boundary in boundaries]
    boundary_key = "".join(boundary_keys)
    if not source_key or source_key != boundary_key:
        raise RuntimeError(
            f"{provider_label} word boundaries could not be aligned with the narration text; "
            "subtitle timing was not generated"
        )

    mapped: list[_MappedWord] = []
    key_offset = 0
    for boundary, word_key in zip(boundaries, boundary_keys):
        if not word_key:
            continue
        key_end = key_offset + len(word_key)
        mapped.append(
            _MappedWord(
                start=boundary["offset"],
                end=boundary["offset"] + boundary["duration"],
                source_start=source_positions[key_offset],
                source_end=source_positions[key_end - 1] + 1,
            )
        )
        key_offset = key_end
    return mapped


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _display_length(text: str, start: int, end: int) -> int:
    return sum(not character.isspace() for character in text[start:end])


def _is_sentence_end(text: str, index: int) -> bool:
    character = text[index]
    if character in _SENTENCE_END:
        return True
    if character != ".":
        return False
    previous = text[index - 1] if index else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    return not (previous.isdigit() and following.isdigit())


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(text):
        if not _is_sentence_end(text, index):
            index += 1
            continue
        end = index + 1
        while end < len(text) and text[end] in _CLOSING_PUNCTUATION:
            end += 1
        span = _trim_span(text, start, end)
        if span[0] < span[1]:
            spans.append(span)
        start = end
        index = end
    span = _trim_span(text, start, len(text))
    if span[0] < span[1]:
        spans.append(span)
    return spans


def _hard_split_span(
    text: str,
    span: tuple[int, int],
    words: list[_MappedWord],
    max_chars: int,
) -> list[tuple[int, int]]:
    start, end = span
    parts: list[tuple[int, int]] = []
    while _display_length(text, start, end) > max_chars:
        remaining_length = _display_length(text, start, end)
        remaining_parts = (remaining_length + max_chars - 1) // max_chars
        target_length = (remaining_length + remaining_parts - 1) // remaining_parts
        candidates = [
            (word.source_end, _display_length(text, start, word.source_end))
            for word in words
            if start < word.source_end < end
            and _display_length(text, start, word.source_end) <= max_chars
        ]
        if candidates:
            split_at, _ = min(
                candidates,
                key=lambda candidate: (
                    abs(candidate[1] - target_length),
                    -candidate[1],
                ),
            )
        else:
            split_at = next(
                (
                    word.source_end
                    for word in words
                    if start < word.source_end < end
                ),
                end,
            )
        if split_at >= end:
            break
        part = _trim_span(text, start, split_at)
        if part[0] < part[1]:
            parts.append(part)
        start = split_at
    part = _trim_span(text, start, end)
    if part[0] < part[1]:
        parts.append(part)
    return parts


def _split_sentence_span(
    text: str,
    sentence: tuple[int, int],
    words: list[_MappedWord],
    max_chars: int,
) -> list[tuple[int, int]]:
    if _display_length(text, *sentence) <= max_chars:
        return [sentence]

    start, end = sentence
    clauses: list[tuple[int, int]] = []
    clause_start = start
    for index in range(start, end):
        if text[index] not in _CLAUSE_END:
            continue
        clause = _trim_span(text, clause_start, index + 1)
        if clause[0] < clause[1]:
            clauses.append(clause)
        clause_start = index + 1
    clause = _trim_span(text, clause_start, end)
    if clause[0] < clause[1]:
        clauses.append(clause)

    atoms = [
        part
        for clause in clauses
        for part in _hard_split_span(text, clause, words, max_chars)
    ]
    merged: list[tuple[int, int]] = []
    for atom in atoms:
        if not merged:
            merged.append(atom)
            continue
        candidate = (merged[-1][0], atom[1])
        if _display_length(text, *candidate) <= max_chars:
            merged[-1] = candidate
        else:
            merged.append(atom)
    return merged


def _clamp_small_overlaps(
    cues: list[_SubtitleCue],
    *,
    tolerance_ms: int = DEFAULT_BOUNDARY_OVERLAP_TOLERANCE_MS,
    provider_label: str,
) -> list[_SubtitleCue]:
    tolerance = tolerance_ms * _TICKS_PER_MILLISECOND
    normalized: list[_SubtitleCue] = []
    for cue in cues:
        if normalized and cue.start < normalized[-1].end:
            overlap = normalized[-1].end - cue.start
            if overlap > tolerance:
                overlap_ms = overlap / _TICKS_PER_MILLISECOND
                raise RuntimeError(
                    f"{provider_label} returned {overlap_ms:g} ms of overlapping "
                    "word-boundary timing; audio and subtitles were not published"
                )
            cue = _SubtitleCue(
                start=normalized[-1].end,
                end=cue.end,
                text=cue.text,
            )
        if cue.end <= cue.start:
            raise RuntimeError(
                f"{provider_label} returned an invalid subtitle timing interval; "
                "audio and subtitles were not published"
            )
        normalized.append(cue)
    return normalized


def _subtitle_cues(
    text: str,
    boundaries: list[dict],
    max_chars: int,
    provider_label: str,
) -> list[_SubtitleCue]:
    if max_chars < 1:
        raise ValueError("subtitle_max_chars must be at least 1")
    words = _map_word_boundaries(text, boundaries, provider_label)
    spans = [
        span
        for sentence in _sentence_spans(text)
        for span in _split_sentence_span(text, sentence, words, max_chars)
    ]

    pending: list[tuple[int, int, str]] = []
    assigned_word_indexes: list[int] = []
    for start, end in spans:
        matching = [
            (index, word)
            for index, word in enumerate(words)
            if word.source_start >= start and word.source_end <= end
        ]
        if not matching:
            continue
        cue_text = re.sub(r"\s+", " ", text[start:end]).strip()
        assigned_word_indexes.extend(index for index, _ in matching)
        pending.append((matching[0][1].start, matching[-1][1].end, cue_text))

    if assigned_word_indexes != list(range(len(words))):
        raise RuntimeError(
            f"{provider_label} word boundaries crossed subtitle split points; "
            "subtitle timing was not generated"
        )
    if not pending:
        raise RuntimeError(f"{provider_label} produced no timed subtitle cues")

    cues: list[_SubtitleCue] = []
    for index, (start, word_end, cue_text) in enumerate(pending):
        next_start = pending[index + 1][0] if index + 1 < len(pending) else None
        end = next_start if next_start is not None and next_start > word_end else word_end
        cues.append(_SubtitleCue(start=start, end=end, text=cue_text))
    cues = _clamp_small_overlaps(cues, provider_label=provider_label)

    source_text = re.sub(r"\s+", "", text)
    subtitle_text = re.sub(r"\s+", "", "".join(cue.text for cue in cues))
    if subtitle_text != source_text:
        raise RuntimeError(
            f"{provider_label} subtitle text does not match the narration text; "
            "audio and subtitles were not published"
        )
    if any(_display_length(cue.text, 0, len(cue.text)) > max_chars for cue in cues):
        raise RuntimeError(
            f"A single {provider_label} word boundary exceeds the subtitle character limit; "
            "audio and subtitles were not published"
        )
    return cues


def _srt_timestamp(ticks: int) -> str:
    total_milliseconds = round(ticks / 10_000)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _format_srt(cues: list[_SubtitleCue]) -> str:
    blocks = [
        (
            f"{index}\n"
            f"{_srt_timestamp(cue.start)} --> {_srt_timestamp(cue.end)}\n"
            f"{cue.text}"
        )
        for index, cue in enumerate(cues, 1)
    ]
    return "\n\n".join(blocks) + "\n"


def format_word_timed_srt(
    text: str,
    boundaries: list[dict],
    max_chars: int = DEFAULT_SUBTITLE_MAX_CHARS,
    *,
    provider_label: str = "TTS provider",
) -> str:
    """Regroup provider word timings into compact, text-faithful SRT cues."""
    return _format_srt(
        _subtitle_cues(
            text,
            boundaries,
            max_chars,
            provider_label,
        )
    )

