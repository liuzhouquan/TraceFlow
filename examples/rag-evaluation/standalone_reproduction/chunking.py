from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdx"}
MAX_CHUNKS_PER_DOCUMENT = 5000
CHUNK_STRATEGY = "structure_aware_v2"
PLAIN_PARENT_WINDOW_CHILDREN = 4

SectionInstance = tuple[tuple[int, str, int], ...]
ROOT_SECTION: SectionInstance = ((0, "root", 0),)


@dataclass(frozen=True)
class TextChunk:
    position: int
    child_index: int
    content: str
    chunk_id: str
    parent_id: str
    content_hash: str
    chunk_type: str
    section_path: tuple[str, ...]
    start_line: int
    end_line: int

    def metadata(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "parent_id": self.parent_id,
            "child_index": self.child_index,
            "content_hash": self.content_hash,
            "chunk_type": self.chunk_type,
            "section_path": list(self.section_path),
            "section_title": self.section_path[-1] if self.section_path else None,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "chunk_strategy": CHUNK_STRATEGY,
        }


@dataclass
class _Block:
    text: str
    start_line: int
    end_line: int
    kind: str = "paragraph"
    section_path: tuple[str, ...] = ()
    section_instance: SectionInstance = ()
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


def _stable_id(*parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _line_for_offset(text: str, offset: int, first_line: int) -> int:
    return first_line + text.count("\n", 0, max(0, offset))


def _opening_fence_marker(line: str) -> str | None:
    match = re.match(r"^\s*(`{3,}|~{3,})", line)
    return match.group(1) if match else None


def _is_closing_fence(line: str, marker: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped[0] != marker[0]:
        return False
    run_length = len(stripped) - len(stripped.lstrip(marker[0]))
    return run_length >= len(marker) and not stripped[run_length:].strip()


def _split_long_text(block: _Block, max_chars: int) -> list[_Block]:
    if len(block.text) <= max_chars:
        return [block]

    units = [
        unit.strip()
        for unit in re.split(r"(?<=[。！？!?])|(?<=[.!?])\s+|\n+", block.text)
        if unit.strip()
    ]
    if len(units) <= 1:
        units = [
            block.text[index : index + max_chars]
            for index in range(0, len(block.text), max_chars)
        ]

    pieces: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(
                unit[index : index + max_chars]
                for index in range(0, len(unit), max_chars)
            )
            continue
        candidate = f"{current}\n{unit}" if current else unit
        if current and len(candidate) > max_chars:
            pieces.append(current)
            current = unit
        else:
            current = candidate
    if current:
        pieces.append(current)

    output: list[_Block] = []
    cursor = 0
    for piece in pieces:
        offset = block.text.find(piece, cursor)
        if offset < 0:
            offset = cursor
        end_offset = min(len(block.text), offset + len(piece))
        output.append(
            _Block(
                text=piece,
                start_line=_line_for_offset(block.text, offset, block.start_line),
                end_line=_line_for_offset(
                    block.text,
                    max(offset, end_offset - 1),
                    block.start_line,
                ),
                kind=block.kind,
                section_path=block.section_path,
                section_instance=block.section_instance,
                metadata=dict(block.metadata),
            )
        )
        cursor = end_offset
    return output


def _split_long_code_fence(block: _Block, max_chars: int) -> list[_Block]:
    if len(block.text) <= max_chars:
        return [block]

    lines = block.text.splitlines()
    original_opener = lines[0] if lines else "```"
    original_marker = _opening_fence_marker(original_opener) or "```"
    has_closer = len(lines) > 1 and _is_closing_fence(lines[-1], original_marker)
    body = lines[1:-1] if has_closer else lines[1:]

    marker = original_marker
    opener = original_opener
    wrapper_size = len(opener) + len(marker) + 2
    if wrapper_size >= max_chars:
        marker = original_marker[0] * 3
        info = original_opener.lstrip()[len(original_marker) :].strip()
        opener_budget = max_chars - len(marker) - 3
        opener = marker + ((" " + info)[: max(0, opener_budget - len(marker))] if info else "")
        wrapper_size = len(opener) + len(marker) + 2
    body_limit = max_chars - wrapper_size
    if body_limit <= 0:
        raise ValueError("chunk_size 太小，无法容纳 Markdown 代码围栏")
    if not body:
        return [
            _Block(
                text=f"{opener}\n{marker}",
                start_line=block.start_line,
                end_line=block.end_line,
                kind="code_fence",
                section_path=block.section_path,
                section_instance=block.section_instance,
                metadata=dict(block.metadata),
            )
        ]
    first_body_line = block.start_line + 1
    output: list[_Block] = []
    current: list[str] = []
    current_size = 0
    current_start = first_body_line

    def append_body(body_lines: list[str], start_line: int, end_line: int) -> None:
        if not body_lines:
            return
        body_text = "\n".join(body_lines)
        output.append(
            _Block(
                text=f"{opener}\n{body_text}\n{marker}",
                start_line=start_line,
                end_line=end_line,
                kind="code_fence",
                section_path=block.section_path,
                section_instance=block.section_instance,
                metadata={
                    **block.metadata,
                    "fence_start_line": block.start_line,
                    "fence_end_line": block.end_line if has_closer else None,
                },
            )
        )

    for index, line in enumerate(body):
        line_number = first_body_line + index
        if len(line) > body_limit:
            append_body(current, current_start, line_number - 1)
            current = []
            current_size = 0
            for offset in range(0, len(line), body_limit):
                append_body([line[offset : offset + body_limit]], line_number, line_number)
            current_start = line_number + 1
            continue
        added_size = len(line) + (1 if current else 0)
        if current and current_size + added_size > body_limit:
            append_body(current, current_start, line_number - 1)
            current = []
            current_size = 0
            current_start = line_number
        if not current:
            current_start = line_number
        current.append(line)
        current_size += added_size

    last_body_line = block.end_line - (1 if has_closer else 0)
    append_body(current, current_start, max(current_start, last_body_line))
    return output


def _expand_block(block: _Block, max_chars: int) -> list[_Block]:
    if block.kind == "code_fence":
        return _split_long_code_fence(block, max_chars)
    return _split_long_text(block, max_chars)


def _section_prefix(section_path: tuple[str, ...], max_chars: int) -> str:
    value = "\n".join(
        f"{'#' * min(index + 1, 6)} {title}"
        for index, title in enumerate(section_path)
    )
    limit = min(512, max_chars // 3)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _join_blocks(blocks: list[_Block]) -> str:
    return "\n\n".join(block.text for block in blocks if block.text).strip()


def _overlap_blocks(blocks: list[_Block], overlap: int) -> list[_Block]:
    if overlap <= 0:
        return []
    selected: list[_Block] = []
    remaining = overlap
    for block in reversed(blocks):
        if not block.text or block.kind == "code_fence":
            continue
        separator = 2 if selected else 0
        allowance = remaining - separator
        if allowance <= 0:
            break
        if len(block.text) <= allowance:
            selected.append(block)
            remaining -= len(block.text) + separator
            continue
        fragment = block.text[-allowance:]
        boundary = re.search(r"\s+|[。！？.!?；;]", fragment)
        if boundary:
            fragment = fragment[boundary.end() :]
        if not fragment:
            continue
        fragment_offset = len(block.text) - len(fragment)
        selected.append(
            _Block(
                text=fragment,
                start_line=_line_for_offset(block.text, fragment_offset, block.start_line),
                end_line=block.end_line,
                kind=block.kind,
                section_path=block.section_path,
                section_instance=block.section_instance,
                metadata={**block.metadata, "overlap_fragment": True},
            )
        )
        break
    return list(reversed(selected))


def _markdown_blocks(text: str) -> list[_Block]:
    lines = text.split("\n")
    blocks: list[_Block] = []
    headings: list[tuple[int, str, int]] = []
    occurrences: dict[tuple[SectionInstance, int, str], int] = {}
    current: list[str] = []
    current_start = 1
    current_kind = "paragraph"
    fence_marker: str | None = None
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

    def section_path() -> tuple[str, ...]:
        return tuple(title for _, title, _ in headings)

    def section_instance() -> SectionInstance:
        return tuple(headings)

    def flush(end_line: int) -> None:
        nonlocal current
        content = "\n".join(current).strip()
        if content:
            blocks.append(
                _Block(
                    text=content,
                    start_line=current_start,
                    end_line=max(current_start, end_line),
                    kind=current_kind,
                    section_path=section_path(),
                    section_instance=section_instance(),
                )
            )
        current = []

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if fence_marker is not None:
            current.append(line)
            if _is_closing_fence(line, fence_marker):
                flush(line_number)
                fence_marker = None
                current_kind = "paragraph"
            continue

        heading_match = heading_re.match(line)
        if heading_match:
            flush(line_number - 1)
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            while headings and headings[-1][0] >= level:
                headings.pop()
            occurrence_key = (section_instance(), level, title)
            occurrence = occurrences.get(occurrence_key, 0)
            occurrences[occurrence_key] = occurrence + 1
            headings.append((level, title, occurrence))
            blocks.append(
                _Block(
                    text="",
                    start_line=line_number,
                    end_line=line_number,
                    kind="heading",
                    section_path=section_path(),
                    section_instance=section_instance(),
                    metadata={"heading_line": line_number},
                )
            )
            continue

        opening_marker = _opening_fence_marker(line)
        if opening_marker is not None:
            flush(line_number - 1)
            current_start = line_number
            current_kind = "code_fence"
            fence_marker = opening_marker
            current = [line]
            continue

        if not stripped:
            flush(line_number - 1)
            continue
        if not current:
            current_start = line_number
        current.append(line)
    flush(len(lines))
    return blocks


def _plain_parent_window_chars(chunk_size: int, overlap: int) -> int:
    return chunk_size + (PLAIN_PARENT_WINDOW_CHILDREN - 1) * max(
        1,
        chunk_size - overlap,
    )


def _plain_blocks(text: str, *, parent_window_chars: int) -> list[_Block]:
    lines = text.split("\n")
    raw_blocks: list[_Block] = []
    current: list[str] = []
    start_line = 1

    def flush(end_line: int) -> None:
        nonlocal current
        content = "\n".join(current).strip()
        if content:
            raw_blocks.append(
                _Block(
                    content,
                    start_line,
                    max(start_line, end_line),
                )
            )
        current = []

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            flush(line_number - 1)
            continue
        if not current:
            start_line = line_number
        current.append(line)
    flush(len(lines))

    blocks: list[_Block] = []
    window_index = 0
    window_chars = 0
    for raw_block in raw_blocks:
        for block in _split_long_text(raw_block, parent_window_chars):
            separator_chars = 2 if window_chars else 0
            if (
                window_chars
                and window_chars + separator_chars + len(block.text)
                > parent_window_chars
            ):
                window_index += 1
                window_chars = 0
                separator_chars = 0
            section_instance: SectionInstance = (
                (0, "plain-window", window_index),
            )
            blocks.append(
                _Block(
                    text=block.text,
                    start_line=block.start_line,
                    end_line=block.end_line,
                    kind=block.kind,
                    section_path=(),
                    section_instance=section_instance,
                    metadata={
                        **block.metadata,
                        "plain_parent_window": window_index,
                    },
                )
            )
            window_chars += separator_chars + len(block.text)
    return blocks


def _pack_blocks(
    blocks: list[_Block],
    *,
    source_id: str,
    max_chars: int,
    overlap: int,
    default_type: str,
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    current: list[_Block] = []
    current_key: SectionInstance | None = None
    child_counts: dict[SectionInstance, int] = {}

    def flush() -> None:
        nonlocal current
        if not current or current_key is None:
            return
        first = current[0]
        body = _join_blocks(current)
        prefix = _section_prefix(first.section_path, max_chars) if first.section_path else ""
        content = f"{prefix}\n\n{body}" if prefix and body else (prefix or body)
        child_index = child_counts.get(current_key, 0)
        parent_id = _stable_id("parent-v2", source_id, current_key)
        chunk_type = (
            "markdown_code"
            if any(block.kind == "code_fence" for block in current)
            else default_type
        )
        chunks.append(
            TextChunk(
                position=len(chunks) + 1,
                child_index=child_index,
                content=content,
                chunk_id=_stable_id("chunk-v2", parent_id, child_index),
                parent_id=parent_id,
                content_hash=_content_hash(content),
                chunk_type=chunk_type,
                section_path=first.section_path,
                start_line=min(block.start_line for block in current),
                end_line=max(block.end_line for block in current),
            )
        )
        child_counts[current_key] = child_index + 1

    for raw_block in blocks:
        if raw_block.kind == "code_fence" and current:
            if any(block.text for block in current):
                flush()
            current = []
        key = raw_block.section_instance or ROOT_SECTION
        prefix = _section_prefix(raw_block.section_path, max_chars) if raw_block.section_path else ""
        body_limit = max(1, max_chars - len(prefix) - (2 if prefix else 0))
        expand_limit = body_limit
        if raw_block.kind != "code_fence" and overlap > 0 and len(raw_block.text) > body_limit:
            overlap_reserve = min(overlap, max(1, body_limit // 3))
            expand_limit = max(1, body_limit - overlap_reserve - 2)
        for block in _expand_block(raw_block, expand_limit):
            key = block.section_instance or ROOT_SECTION
            if current and key != current_key:
                flush()
                current = []
            current_key = key
            if block.kind == "code_fence":
                current = [block]
                flush()
                current = []
                continue
            candidate = _join_blocks([*current, block])
            candidate_size = len(candidate) + len(prefix) + (2 if prefix else 0)
            if current and candidate_size > max_chars:
                previous = list(current)
                flush()
                overlap_budget = min(
                    overlap,
                    max(0, body_limit - len(block.text) - 2),
                )
                current = _overlap_blocks(previous, overlap_budget)
                while current:
                    candidate = _join_blocks([*current, block])
                    if len(candidate) + len(prefix) + (2 if prefix else 0) <= max_chars:
                        break
                    current.pop(0)
            current.append(block)
    flush()
    if len(chunks) > MAX_CHUNKS_PER_DOCUMENT:
        raise ValueError(f"文档切片不能超过 {MAX_CHUNKS_PER_DOCUMENT} 个")
    if any(len(chunk.content) > max_chars for chunk in chunks):
        raise AssertionError("切片内容超过 chunk_size")
    return chunks


def split_document(
    text: str,
    *,
    source_id: str,
    filename: str,
    chunk_size: int = 1000,
    overlap: int = 80,
) -> list[TextChunk]:
    if chunk_size < 80:
        raise ValueError("chunk_size 不能小于 80")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须大于等于 0 且小于 chunk_size")
    source_key = source_id.strip()
    if not source_key:
        raise ValueError("source_id 不能为空")

    clean = normalize_text(text)
    if not clean.strip():
        return []
    if Path(filename).suffix.lower() in MARKDOWN_SUFFIXES:
        return _pack_blocks(
            _markdown_blocks(clean),
            source_id=source_key,
            max_chars=chunk_size,
            overlap=overlap,
            default_type="markdown_section",
        )
    return _pack_blocks(
        _plain_blocks(
            clean,
            parent_window_chars=_plain_parent_window_chars(chunk_size, overlap),
        ),
        source_id=source_key,
        max_chars=chunk_size,
        overlap=overlap,
        default_type="text",
    )


def split_text(text: str, chunk_size: int = 1000, overlap: int = 80) -> list[TextChunk]:
    return split_document(
        text,
        source_id="legacy-text",
        filename="document.txt",
        chunk_size=chunk_size,
        overlap=overlap,
    )
