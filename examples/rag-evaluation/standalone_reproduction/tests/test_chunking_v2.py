from __future__ import annotations

from collections import defaultdict
import re

import pytest

from chunking import CHUNK_STRATEGY, TextChunk, split_document
from document_loader import load_document


def _split(text: str, *, source_id: str = "doc-1", filename: str = "guide.md", size: int = 220, overlap: int = 24) -> list[TextChunk]:
    return split_document(
        text,
        source_id=source_id,
        filename=filename,
        chunk_size=size,
        overlap=overlap,
    )


def _body(chunk: TextChunk) -> str:
    if chunk.section_path and "\n\n" in chunk.content:
        return chunk.content.split("\n\n", 1)[1]
    return chunk.content


def test_loader_preserves_source_line_layout() -> None:
    loaded = load_document(
        "runbook.md",
        b"\r\n\r\n# Runbook\r\n\r\n\r\nRollback now.  \r\n",
    )

    assert loaded.title == "Runbook"
    assert loaded.text == "\n\n# Runbook\n\n\nRollback now.  \n"


def test_markdown_heading_hierarchy_and_line_ranges_are_preserved() -> None:
    chunks = _split(
        "# API\n\nintro\n\n## Auth\n\nrefresh\n\n### Failure\n\nreturn 401",
        size=200,
    )

    assert [chunk.section_path for chunk in chunks] == [
        ("API",),
        ("API", "Auth"),
        ("API", "Auth", "Failure"),
    ]
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (1, 3),
        (5, 7),
        (9, 11),
    ]
    assert "# API\n## Auth\n### Failure" in chunks[-1].content
    assert all(chunk.chunk_type == "markdown_section" for chunk in chunks)


def test_repeated_headings_have_distinct_stable_parents() -> None:
    text = "# API\n\n## Auth\n\nfirst\n\n## Auth\n\nsecond"

    first = _split(text)
    second = _split(text)
    auth_chunks = [chunk for chunk in first if chunk.section_path == ("API", "Auth")]

    assert len(auth_chunks) == 2
    assert auth_chunks[0].parent_id != auth_chunks[1].parent_id
    assert [chunk.parent_id for chunk in first] == [chunk.parent_id for chunk in second]
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert [chunk.content_hash for chunk in first] == [chunk.content_hash for chunk in second]


def test_structural_ids_content_hash_and_source_isolation_have_separate_roles() -> None:
    before = _split("# Runbook\n\nRetry once.", source_id="doc-a")[0]
    changed = _split("# Runbook\n\nRetry twice.", source_id="doc-a")[0]
    other_source = _split("# Runbook\n\nRetry once.", source_id="doc-b")[0]

    assert changed.parent_id == before.parent_id
    assert changed.chunk_id == before.chunk_id
    assert changed.content_hash != before.content_hash
    assert other_source.parent_id != before.parent_id
    assert other_source.chunk_id != before.chunk_id
    assert other_source.content_hash == before.content_hash


def test_code_fence_is_a_hard_boundary_with_exact_source_lines() -> None:
    fence = "```"
    text = "\n".join(
        [
            "# Guide",
            "",
            "before code",
            "",
            f"{fence}python",
            "print('ok')",
            fence,
            "",
            "after code",
        ]
    )

    chunks = _split(text, size=200)

    assert [chunk.chunk_type for chunk in chunks] == [
        "markdown_section",
        "markdown_code",
        "markdown_section",
    ]
    code = chunks[1]
    assert (code.start_line, code.end_line) == (5, 7)
    assert code.content.count(fence) == 2
    assert "before code" not in code.content
    assert "after code" not in code.content
    assert "print('ok')" not in chunks[0].content
    assert "print('ok')" not in chunks[2].content


def test_long_code_fence_stays_balanced_bounded_and_lossless() -> None:
    fence = "````"
    code_lines = [f"line_{index:02d} = {'x' * 28}" for index in range(1, 9)]
    text = "\n".join(["# Guide", "", f"{fence}python", *code_lines, fence])

    chunks = _split(text, size=80, overlap=8)
    code_chunks = [chunk for chunk in chunks if chunk.chunk_type == "markdown_code"]

    assert len(code_chunks) > 1
    assert all(len(chunk.content) <= 80 for chunk in code_chunks)
    assert all(chunk.content.count(fence) == 2 for chunk in code_chunks)
    assert all(4 <= chunk.start_line <= chunk.end_line <= 11 for chunk in code_chunks)
    combined = "\n".join(chunk.content for chunk in code_chunks)
    assert all(combined.count(line) == 1 for line in code_lines)


def test_overlap_is_kept_inside_each_markdown_parent() -> None:
    alpha = " ".join(f"alpha{index:02d} keeps evidence." for index in range(14))
    beta = " ".join(f"beta{index:02d} keeps evidence." for index in range(8))
    chunks = _split(
        f"# Alpha\n\n{alpha}\n\n# Beta\n\n{beta}",
        size=100,
        overlap=40,
    )
    alpha_chunks = [chunk for chunk in chunks if chunk.section_path == ("Alpha",)]
    beta_chunks = [chunk for chunk in chunks if chunk.section_path == ("Beta",)]

    assert len(alpha_chunks) > 2
    for previous, current in zip(alpha_chunks, alpha_chunks[1:]):
        previous_tokens = set(re.findall(r"alpha\d{2}", _body(previous)))
        current_tokens = set(re.findall(r"alpha\d{2}", _body(current)))
        assert previous_tokens & current_tokens
    assert all("alpha" not in _body(chunk) for chunk in beta_chunks)
    assert {chunk.parent_id for chunk in alpha_chunks}.isdisjoint(
        {chunk.parent_id for chunk in beta_chunks}
    )


def test_plain_text_keeps_leading_blank_line_offsets() -> None:
    chunks = _split(
        "\n\nfirst\nsecond\n\nthird\n",
        filename="notes.txt",
        size=200,
    )

    assert len(chunks) == 1
    assert chunks[0].section_path == ()
    assert chunks[0].chunk_type == "text"
    assert (chunks[0].start_line, chunks[0].end_line) == (3, 6)
    assert chunks[0].content == "first\nsecond\n\nthird"


def test_large_plain_text_uses_stable_bounded_parent_windows() -> None:
    text = "\n\n".join(
        f"paragraph-{index:03d} " + (f"value{index:03d} " * 12)
        for index in range(100)
    )

    first = _split(text, filename="events.log", size=100, overlap=20)
    second = _split(text, filename="events.log", size=100, overlap=20)
    parents: dict[str, list[TextChunk]] = defaultdict(list)
    for chunk in first:
        parents[chunk.parent_id].append(chunk)

    assert len(parents) > 20
    assert [chunk.parent_id for chunk in first] == [chunk.parent_id for chunk in second]
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert all(chunk.section_path == () for chunk in first)
    assert all(
        [chunk.child_index for chunk in siblings] == list(range(len(siblings)))
        for siblings in parents.values()
    )
    assert max(
        sum(len(chunk.content) for chunk in siblings)
        for siblings in parents.values()
    ) < 600


def test_chunk_contract_is_unique_ordered_and_parent_local() -> None:
    text = "# A\n\n" + ("A sentence. " * 40) + "\n\n# B\n\n" + ("B sentence. " * 20)
    chunks = _split(text, size=100, overlap=16)
    children: dict[str, list[int]] = defaultdict(list)
    for chunk in chunks:
        children[chunk.parent_id].append(chunk.child_index)
        metadata = chunk.metadata()
        assert metadata["chunk_strategy"] == CHUNK_STRATEGY
        assert metadata["chunk_id"] == chunk.chunk_id
        assert metadata["content_hash"] == chunk.content_hash
        assert chunk.start_line <= chunk.end_line
        assert len(chunk.content) <= 100

    assert [chunk.position for chunk in chunks] == list(range(1, len(chunks) + 1))
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    assert all(indices == list(range(len(indices))) for indices in children.values())


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"source_id": " ", "filename": "a.md", "chunk_size": 100, "overlap": 10}, "source_id"),
        ({"source_id": "doc", "filename": "a.md", "chunk_size": 79, "overlap": 10}, "chunk_size"),
        ({"source_id": "doc", "filename": "a.md", "chunk_size": 100, "overlap": 100}, "overlap"),
    ],
)
def test_split_document_rejects_invalid_identity_and_limits(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        split_document("content", **kwargs)  # type: ignore[arg-type]
