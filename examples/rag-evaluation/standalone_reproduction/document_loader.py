from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader

from chunking import normalize_text


SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".log", ".csv", ".json", ".pdf", ".docx"}
MAX_PDF_PAGES = 200
MAX_DOCX_ENTRIES = 2000
MAX_DOCX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 1_000_000


@dataclass(frozen=True)
class LoadedDocument:
    title: str
    text: str


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("文本文件不是 UTF-8 或 GB18030 编码")


def _load_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"PDF 文件无法解析：{exc}") from exc
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"PDF 不能超过 {MAX_PDF_PAGES} 页")
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)


def _validate_docx_archive(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("DOCX 文件结构无效") from exc
    if len(entries) > MAX_DOCX_ENTRIES:
        raise ValueError(f"DOCX 压缩包条目不能超过 {MAX_DOCX_ENTRIES} 个")
    expanded_size = sum(entry.file_size for entry in entries)
    if expanded_size > MAX_DOCX_UNCOMPRESSED_BYTES:
        limit_mb = MAX_DOCX_UNCOMPRESSED_BYTES // (1024 * 1024)
        raise ValueError(f"DOCX 解压后不能超过 {limit_mb} MB")


def _load_docx(data: bytes) -> str:
    _validate_docx_archive(data)
    try:
        document = DocxDocument(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"DOCX 文件无法解析：{exc}") from exc
    blocks = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            blocks.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n\n".join(blocks)


def _load_csv(data: bytes) -> str:
    text = _decode(data)
    rows = csv.reader(io.StringIO(text))
    return "\n".join(" | ".join(cell.strip() for cell in row) for row in rows)


def _load_json(data: bytes) -> str:
    value = json.loads(_decode(data))
    return json.dumps(value, ensure_ascii=False, indent=2)


def _title(filename: str, text: str) -> str:
    for line in text.splitlines():
        value = line.strip()
        if value.startswith("# "):
            return value[2:].strip()[:200]
    return Path(filename).stem[:200]


def load_document(filename: str, data: bytes) -> LoadedDocument:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = "、".join(sorted(SUPPORTED_SUFFIXES))
        raise ValueError(f"暂不支持 {suffix or '无扩展名'} 文件，可上传：{supported}")

    if suffix == ".pdf":
        text = _load_pdf(data)
    elif suffix == ".docx":
        text = _load_docx(data)
    elif suffix == ".csv":
        text = _load_csv(data)
    elif suffix == ".json":
        text = _load_json(data)
    else:
        text = _decode(data)

    clean = normalize_text(text)
    if not clean.strip():
        raise ValueError("没有从文件中提取到可索引的文字")
    if len(clean) > MAX_EXTRACTED_CHARACTERS:
        raise ValueError(f"提取文字不能超过 {MAX_EXTRACTED_CHARACTERS:,} 个字符")
    return LoadedDocument(title=_title(filename, clean), text=clean)
