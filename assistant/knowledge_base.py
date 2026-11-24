"""Local document library and explicit long-term memory backed by SQLite."""
from __future__ import annotations

import hashlib
import heapq
import io
import json
import logging
import math
import re
import sqlite3
import struct
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable

from .settings_store import app_data_dir

logger = logging.getLogger("live_assistant.knowledge")

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".pdf", ".docx"}
MAX_DOCUMENT_BYTES = 40 * 1024 * 1024
MAX_EXTRACTED_CHARS = 20 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 160 * 1024 * 1024
MAX_DOCX_ENTRIES = 1200
MAX_PDF_PAGES = 5000
MAX_CHUNKS_PER_DOCUMENT = 50000
MAX_EMBEDDING_DIMENSIONS = 65536
MAX_SEMANTIC_SCAN_CHUNKS = 20000
MAX_SEMANTIC_SCAN_BYTES = 64 * 1024 * 1024
CHUNK_CHARS = 1800
CHUNK_OVERLAP = 220
KNOWLEDGE_SCHEMA_VERSION = 2


class KnowledgeError(RuntimeError):
    """A user-facing local knowledge-base operation error."""


class KnowledgeCancelled(KnowledgeError):
    """Raised when a document operation is cancelled before its DB commit."""


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise KnowledgeCancelled("Операция с базой знаний отменена пользователем.")


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _clean_text(text: str) -> str:
    text = str(text).replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\t\x0b\x0c]+", " ", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text(text: str, max_chars: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text near paragraph/sentence/word boundaries with bounded overlap."""
    normalized = _clean_text(text)
    if not normalized:
        return []
    max_chars = max(300, int(max_chars))
    overlap = max(0, min(int(overlap), max_chars // 3))
    chunks: list[str] = []
    start = 0
    length = len(normalized)
    while start < length:
        end = min(length, start + max_chars)
        if end < length:
            floor = start + max(1, max_chars // 2)
            candidates = (
                normalized.rfind("\n\n", floor, end),
                normalized.rfind("\n", floor, end),
                normalized.rfind(". ", floor, end),
                normalized.rfind("? ", floor, end),
                normalized.rfind("! ", floor, end),
                normalized.rfind(" ", floor, end),
            )
            cut = max(candidates)
            if cut > start:
                marker = normalized[cut:cut + 2]
                end = cut + (2 if marker in {"\n\n", ". ", "? ", "! "} else 1)
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        next_start = max(start + 1, end - overlap)
        while next_start < end and not normalized[next_start].isspace():
            next_start += 1
        start = min(length, next_start + 1)
    return chunks


def _check_archive_limits(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_DOCX_ENTRIES:
                raise KnowledgeError("DOCX содержит слишком много элементов архива.")
            total_uncompressed = sum(max(0, info.file_size) for info in infos)
            if total_uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise KnowledgeError("DOCX превышает допустимый распакованный размер.")
            for info in infos:
                if info.file_size > 0 and info.compress_size == 0:
                    raise KnowledgeError("DOCX содержит подозрительную запись архива.")
                if info.compress_size > 0 and info.file_size / info.compress_size > 1000:
                    raise KnowledgeError("DOCX превышает допустимый коэффициент сжатия.")
    except KnowledgeError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise KnowledgeError(f"Файл DOCX не является корректным ZIP-документом: {exc}") from exc


def extract_document(
    path: str | Path,
    *,
    cancel_event: threading.Event | None = None,
) -> tuple[list[tuple[str, str]], bytes]:
    """Extract source-located text sections and return original bytes for hashing.

    Cancellation is checked between bounded extraction units. No database state
    is changed by this function, so a cancelled extraction is always safe.
    """
    _check_cancel(cancel_event)
    file_path = Path(path).expanduser()
    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise KnowledgeError(f"Формат {suffix or '(без расширения)'} не поддерживается.")
    try:
        file_size = file_path.stat().st_size
    except OSError as exc:
        raise KnowledgeError(f"Не удалось получить сведения о файле {file_path.name}: {exc}") from exc
    if file_size <= 0:
        raise KnowledgeError(f"Файл {file_path.name} пустой.")
    if file_size > MAX_DOCUMENT_BYTES:
        raise KnowledgeError(
            f"Файл {file_path.name} превышает ограничение {MAX_DOCUMENT_BYTES // (1024 * 1024)} МБ."
        )

    # Reject oversized files before allocating their contents. Keep the
    # post-read check as a guard against a file that grows after stat().
    try:
        data = file_path.read_bytes()
    except OSError as exc:
        raise KnowledgeError(f"Не удалось прочитать файл {file_path.name}: {exc}") from exc
    if not data:
        raise KnowledgeError(f"Файл {file_path.name} пустой.")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise KnowledgeError(
            f"Файл {file_path.name} превышает ограничение {MAX_DOCUMENT_BYTES // (1024 * 1024)} МБ."
        )

    sections: list[tuple[str, str]] = []
    extracted_chars = 0

    def add_section(locator: str, text: str) -> None:
        nonlocal extracted_chars
        value = str(text)
        extracted_chars += len(value)
        if extracted_chars > MAX_EXTRACTED_CHARS:
            raise KnowledgeError("Извлечённый текст превышает допустимый размер 20 МБ.")
        if value.strip():
            sections.append((locator, value))

    if suffix in {".txt", ".md", ".markdown"}:
        text = _decode_text(data)
        lines = text.splitlines()
        if suffix in {".md", ".markdown"}:
            # Keep Markdown headings as source locators so answers can point
            # the user to a meaningful section instead of a generic text blob.
            current_heading = "начало документа"
            current_start = 1
            current_lines: list[str] = []
            for line_number, line in enumerate(lines, start=1):
                if line_number % 128 == 0:
                    _check_cancel(cancel_event)
                heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
                if heading and current_lines:
                    add_section(
                        f"раздел «{current_heading}», строки {current_start}-{line_number - 1}",
                        "\n".join(current_lines),
                    )
                    current_heading = heading.group(1).strip()[:160]
                    current_start = line_number
                    current_lines = [line]
                else:
                    if heading:
                        current_heading = heading.group(1).strip()[:160]
                        current_start = line_number
                    current_lines.append(line)
            if current_lines:
                add_section(
                    f"раздел «{current_heading}», строки {current_start}-{len(lines)}",
                    "\n".join(current_lines),
                )
        else:
            add_section(f"строки 1-{max(1, len(lines))}", text)
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data), strict=False)
            if len(reader.pages) > MAX_PDF_PAGES:
                raise KnowledgeError(f"PDF содержит более {MAX_PDF_PAGES} страниц.")
            if reader.is_encrypted:
                decrypted = reader.decrypt("")
                if not decrypted:
                    raise KnowledgeError("PDF защищён паролем и не может быть прочитан.")
            for index, page in enumerate(reader.pages, start=1):
                _check_cancel(cancel_event)
                try:
                    page_text = page.extract_text(extraction_mode="layout")
                except TypeError:
                    page_text = page.extract_text()
                if page_text and page_text.strip():
                    add_section(f"страница {index}", page_text)
        except KnowledgeError:
            raise
        except Exception as exc:
            raise KnowledgeError(f"Не удалось извлечь текст из PDF: {exc}") from exc
    elif suffix == ".docx":
        try:
            _check_archive_limits(data)
            from docx import Document

            document = Document(io.BytesIO(data))
            for index, paragraph in enumerate(document.paragraphs, start=1):
                if index % 128 == 0:
                    _check_cancel(cancel_event)
                if paragraph.text.strip():
                    add_section(f"абзац {index}", paragraph.text)
            for table_index, table in enumerate(document.tables, start=1):
                for row_index, row in enumerate(table.rows, start=1):
                    if row_index % 64 == 0:
                        _check_cancel(cancel_event)
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        add_section(f"таблица {table_index}, строка {row_index}", " | ".join(cells))
            for section_index, section in enumerate(document.sections, start=1):
                for kind, container in (("колонтитул", section.header), ("нижний колонтитул", section.footer)):
                    for paragraph_index, paragraph in enumerate(container.paragraphs, start=1):
                        if paragraph_index % 128 == 0:
                            _check_cancel(cancel_event)
                        if paragraph.text.strip():
                            add_section(f"{kind} {section_index}, абзац {paragraph_index}", paragraph.text)
        except KnowledgeError:
            raise
        except Exception as exc:
            raise KnowledgeError(f"Не удалось прочитать DOCX: {exc}") from exc

    if not any(text.strip() for _, text in sections):
        raise KnowledgeError(
            f"В {file_path.name} не найден извлекаемый текст. Возможно, PDF состоит из сканов; OCR пока не поддерживается."
        )
    _check_cancel(cancel_event)
    return sections, data


def _stable_norm(values: Iterable[float]) -> float:
    """Compute an Euclidean norm without expanding a huge positional argument list."""
    norm = 0.0
    for value in values:
        norm = math.hypot(norm, float(value))
    return norm


def _semantic_scan_limit(dimensions: int) -> int:
    """Bound the temporary vector scan by both rows and approximate raw bytes."""
    dimensions = max(1, int(dimensions))
    bytes_per_vector = dimensions * 4
    by_memory = max(1, MAX_SEMANTIC_SCAN_BYTES // bytes_per_vector)
    return min(MAX_SEMANTIC_SCAN_CHUNKS, by_memory)


def build_reference_context(header: str, materials: Iterable[str], max_chars: int) -> str:
    """Build a bounded, source-labelled retrieval block without truncating labels.

    The returned block is suitable for appending to the current user message.
    Each source header is kept intact; only a final body may be shortened.
    """
    try:
        limit = max(0, int(max_chars))
    except (TypeError, ValueError, OverflowError):
        return ""
    prefix = str(header).strip()
    if not prefix or limit < len(prefix) + 80:
        return ""

    output = [prefix]
    used = len(prefix) + 2  # reserve the leading separator appended to the user turn
    separator = "\n\n"
    truncation_marker = "[Фрагмент сокращён по бюджету контекста.]"

    for raw_material in materials:
        material = str(raw_material).strip()
        if not material:
            continue
        available = limit - used - len(separator)
        if available <= 0:
            break
        if len(material) <= available:
            output.append(material)
            used += len(separator) + len(material)
            continue

        label, newline, body = material.partition("\n")
        if not newline:
            label, body = material[: min(len(material), 120)], material[min(len(material), 120):]
        required = len(label) + 1 + len(truncation_marker)
        body_budget = available - required - 1
        if body_budget < 80:
            break
        excerpt = body[:body_budget].rstrip()
        # Prefer not to cut a word or sentence in half when the remaining
        # budget permits it; otherwise retain the hard bounded prefix.
        boundary = max(excerpt.rfind("\n"), excerpt.rfind(". "), excerpt.rfind(" "))
        if boundary >= max(40, len(excerpt) // 2):
            excerpt = excerpt[:boundary].rstrip()
        if not excerpt:
            break
        piece = f"{label}\n{excerpt}\n{truncation_marker}"
        output.append(piece)
        used += len(separator) + len(piece)
        break

    if len(output) == 1:
        return ""
    block = "\n\n" + "\n\n".join(output)
    return block if len(block) <= limit else ""


def _pack_vector(vector: Iterable[float]) -> tuple[bytes, int]:
    try:
        values = [float(value) for value in vector]
    except (TypeError, ValueError, OverflowError) as exc:
        raise KnowledgeError("Некорректный embedding-вектор.") from exc
    if not values or len(values) > MAX_EMBEDDING_DIMENSIONS or not all(math.isfinite(value) for value in values):
        raise KnowledgeError("Некорректный embedding-вектор.")
    norm = _stable_norm(values)
    if not math.isfinite(norm) or norm <= 1e-12:
        raise KnowledgeError("Embedding-вектор имеет нулевую или некорректную длину.")
    normalized = [value / norm for value in values]
    try:
        packed = struct.pack(f"<{len(normalized)}f", *normalized)
    except (struct.error, OverflowError) as exc:
        raise KnowledgeError("Embedding-вектор не помещается в формат хранения.") from exc
    if not all(math.isfinite(value) for value in struct.unpack(f"<{len(normalized)}f", packed)):
        raise KnowledgeError("Embedding-вектор содержит недопустимые значения после упаковки.")
    return packed, len(normalized)


def _unpack_vector(blob: bytes, dimensions: int) -> tuple[float, ...]:
    dimensions = int(dimensions)
    if dimensions <= 0 or dimensions > MAX_EMBEDDING_DIMENSIONS or len(blob) != dimensions * 4:
        return ()
    try:
        values = struct.unpack(f"<{dimensions}f", blob)
    except struct.error:
        return ()
    if not all(math.isfinite(value) for value in values):
        return ()
    return values


def _cosine_dot(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or len(left) != len(right):
        return -1.0
    return sum(a * b for a, b in zip(left, right))


def _safe_limit(value: int, *, maximum: int = 100) -> int:
    try:
        return max(0, min(int(value), maximum))
    except (TypeError, ValueError, OverflowError):
        return 0


def _normalise_tags(tags: Iterable[str] | None) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = tags.split(",")
    result: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        value = _clean_text(str(tag))[:40]
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
        if len(result) >= 20:
            break
    return result


class KnowledgeBaseStore:
    """Persistent document and explicit-memory store in its own SQLite DB."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else app_data_dir() / "knowledge.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fts_available = False
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=20000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        try:
            return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.DatabaseError:
            return set()

    def _initialize(self) -> None:
        with self._connection() as conn:
            previous_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if previous_version > KNOWLEDGE_SCHEMA_VERSION:
                raise KnowledgeError(
                    "База знаний создана более новой версией LocalAssistant. "
                    "Не открывайте её старой версией приложения, чтобы не повредить данные."
                )
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    imported_at REAL NOT NULL,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    embedding_model TEXT
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    locator TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding BLOB,
                    embedding_dimensions INTEGER,
                    embedding_model TEXT,
                    UNIQUE(document_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id, ordinal);
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS embedding_staging (
                    job_id TEXT NOT NULL,
                    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
                    embedding BLOB NOT NULL,
                    dimensions INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(job_id, chunk_id)
                );
                CREATE INDEX IF NOT EXISTS idx_embedding_staging_created ON embedding_staging(created_at);
            """)
            try:
                document_columns = self._table_columns(conn, "document_fts")
                document_rebuild = not document_columns or document_columns != {"title", "content"}
                if document_columns and document_rebuild:
                    conn.execute("DROP TABLE document_fts")
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS document_fts "
                    "USING fts5(title, content, tokenize='unicode61 remove_diacritics 2')"
                )

                memory_columns = self._table_columns(conn, "memory_fts")
                memory_rebuild = not memory_columns or memory_columns != {"title", "content", "category", "tags"}
                if memory_columns and memory_rebuild:
                    conn.execute("DROP TABLE memory_fts")
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
                    "USING fts5(title, content, category, tags, tokenize='unicode61 remove_diacritics 2')"
                )
                if previous_version < KNOWLEDGE_SCHEMA_VERSION or document_rebuild:
                    conn.execute("DELETE FROM document_fts")
                    conn.execute("""
                        INSERT INTO document_fts(rowid,title,content)
                        SELECT c.id,d.name,c.content FROM chunks c
                        JOIN documents d ON d.id=c.document_id
                    """)
                if previous_version < KNOWLEDGE_SCHEMA_VERSION or memory_rebuild:
                    conn.execute("DELETE FROM memory_fts")
                    conn.execute("""
                        INSERT INTO memory_fts(rowid,title,content,category,tags)
                        SELECT id,title,content,category,tags FROM memories
                    """)
                self.fts_available = True
            except sqlite3.OperationalError:
                self.fts_available = False
                logger.warning("SQLite FTS5 unavailable; knowledge search will use LIKE fallback.")
            conn.execute(f"PRAGMA user_version={KNOWLEDGE_SCHEMA_VERSION}")
            # Remove abandoned staging rows left by a process crash. Active jobs
            # are short-lived; entries older than two days cannot be active.
            conn.execute("DELETE FROM embedding_staging WHERE created_at < ?", (time.time() - 172800,))

    @staticmethod
    def _tokens(query: str) -> list[str]:
        tokens = re.findall(r"[\w]{2,}", str(query).lower(), flags=re.UNICODE)
        unique: list[str] = []
        for token in tokens:
            if token not in unique:
                unique.append(token)
            if len(unique) >= 12:
                break
        return unique

    @classmethod
    def _fts_query(cls, query: str) -> str:
        return " OR ".join('"' + token.replace('"', '""') + '"' for token in cls._tokens(query))

    @staticmethod
    def _split_sections(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for locator, text in sections:
            chunks = split_text(text)
            for index, chunk in enumerate(chunks, start=1):
                source_locator = (
                    locator if len(chunks) == 1
                    else f"{locator}, фрагмент {index}/{len(chunks)}"
                )
                result.append((source_locator, chunk))
        return result

    def list_documents(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id,path,name,extension,sha256,size_bytes,imported_at,chunk_count,embedding_model "
                "FROM documents ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [dict(row) for row in rows]

    def import_document(
        self,
        path: str | Path,
        *,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        file_path = Path(path).expanduser().resolve()
        _check_cancel(cancel_event)
        sections, data = extract_document(file_path, cancel_event=cancel_event)
        _check_cancel(cancel_event)
        digest = hashlib.sha256(data).hexdigest()
        pieces = self._split_sections(sections)
        if not pieces:
            raise KnowledgeError("Документ не содержит текста для индексации.")
        if len(pieces) > MAX_CHUNKS_PER_DOCUMENT:
            raise KnowledgeError(f"Документ разбивается более чем на {MAX_CHUNKS_PER_DOCUMENT} фрагментов.")

        with self._connection() as conn:
            _check_cancel(cancel_event)
            existing = conn.execute(
                "SELECT id,sha256,chunk_count FROM documents WHERE path=?", (str(file_path),)
            ).fetchone()
            if existing and existing["sha256"] == digest:
                return {
                    "status": "unchanged",
                    "document_id": int(existing["id"]),
                    "chunks": int(existing["chunk_count"]),
                    "name": file_path.name,
                }
            if existing:
                old_ids = [row[0] for row in conn.execute(
                    "SELECT id FROM chunks WHERE document_id=?", (existing["id"],)
                ).fetchall()]
                if self.fts_available and old_ids:
                    conn.executemany("DELETE FROM document_fts WHERE rowid=?", [(int(value),) for value in old_ids])
                conn.execute("DELETE FROM documents WHERE id=?", (existing["id"],))

            cur = conn.execute(
                "INSERT INTO documents(path,name,extension,sha256,size_bytes,imported_at,chunk_count) "
                "VALUES(?,?,?,?,?,?,?)",
                (str(file_path), file_path.name, file_path.suffix.lower(), digest, len(data), time.time(), len(pieces)),
            )
            document_id = int(cur.lastrowid)
            for ordinal, (locator, content) in enumerate(pieces, start=1):
                if ordinal % 128 == 0:
                    _check_cancel(cancel_event)
                chunk_cur = conn.execute(
                    "INSERT INTO chunks(document_id,ordinal,locator,content) VALUES(?,?,?,?)",
                    (document_id, ordinal, locator, content),
                )
                if self.fts_available:
                    conn.execute(
                        "INSERT INTO document_fts(rowid,title,content) VALUES(?,?,?)",
                        (int(chunk_cur.lastrowid), file_path.name, content),
                    )
            _check_cancel(cancel_event)
        return {"status": "imported", "document_id": document_id, "chunks": len(pieces), "name": file_path.name}

    def remove_document(self, document_id: int) -> bool:
        with self._connection() as conn:
            ids = [row[0] for row in conn.execute(
                "SELECT id FROM chunks WHERE document_id=?", (int(document_id),)
            ).fetchall()]
            if self.fts_available and ids:
                conn.executemany("DELETE FROM document_fts WHERE rowid=?", [(int(value),) for value in ids])
            cur = conn.execute("DELETE FROM documents WHERE id=?", (int(document_id),))
            return cur.rowcount > 0

    def list_memories(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id,title,content,category,tags,created_at,updated_at FROM memories ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def add_memory(self, title: str, content: str, category: str = "", tags: Iterable[str] = ()) -> int:
        title = _clean_text(title)[:140]
        content = _clean_text(content)
        category = _clean_text(category)[:80]
        normalized_tags = _normalise_tags(tags)
        if not title:
            raise KnowledgeError("Укажите название записи памяти.")
        if not content:
            raise KnowledgeError("Содержание записи памяти пустое.")
        if len(content) > 12000:
            raise KnowledgeError("Одна запись памяти не может превышать 12 000 символов.")
        now = time.time()
        with self._connection() as conn:
            cur = conn.execute(
                "INSERT INTO memories(title,content,category,tags,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (title, content, category, json.dumps(normalized_tags, ensure_ascii=False), now, now),
            )
            memory_id = int(cur.lastrowid)
            if self.fts_available:
                conn.execute(
                    "INSERT INTO memory_fts(rowid,title,content,category,tags) VALUES(?,?,?,?,?)",
                    (memory_id, title, content, category, " ".join(normalized_tags)),
                )
            return memory_id

    def update_memory(self, memory_id: int, title: str, content: str, category: str = "", tags: Iterable[str] = ()) -> None:
        title = _clean_text(title)[:140]
        content = _clean_text(content)
        category = _clean_text(category)[:80]
        normalized_tags = _normalise_tags(tags)
        if not title or not content:
            raise KnowledgeError("Название и содержание памяти обязательны.")
        if len(content) > 12000:
            raise KnowledgeError("Одна запись памяти не может превышать 12 000 символов.")
        with self._connection() as conn:
            exists = conn.execute("SELECT id FROM memories WHERE id=?", (int(memory_id),)).fetchone()
            if not exists:
                raise KnowledgeError("Запись памяти уже удалена.")
            conn.execute(
                "UPDATE memories SET title=?,content=?,category=?,tags=?,updated_at=? WHERE id=?",
                (title, content, category, json.dumps(normalized_tags, ensure_ascii=False), time.time(), int(memory_id)),
            )
            if self.fts_available:
                conn.execute("DELETE FROM memory_fts WHERE rowid=?", (int(memory_id),))
                conn.execute(
                    "INSERT INTO memory_fts(rowid,title,content,category,tags) VALUES(?,?,?,?,?)",
                    (int(memory_id), title, content, category, " ".join(normalized_tags)),
                )

    def delete_memory(self, memory_id: int) -> bool:
        with self._connection() as conn:
            if self.fts_available:
                conn.execute("DELETE FROM memory_fts WHERE rowid=?", (int(memory_id),))
            cur = conn.execute("DELETE FROM memories WHERE id=?", (int(memory_id),))
            return cur.rowcount > 0

    def _keyword_document_results(self, conn: sqlite3.Connection, query: str, limit: int) -> list[dict[str, Any]]:
        limit = _safe_limit(limit)
        if limit == 0:
            return []
        expression = self._fts_query(query)
        if self.fts_available and expression:
            rows = conn.execute("""
                SELECT c.id AS chunk_id,c.document_id,c.ordinal,c.locator,c.content,
                       d.name,d.path,d.embedding_model,bm25(document_fts) AS rank
                FROM document_fts
                JOIN chunks c ON c.id=document_fts.rowid
                JOIN documents d ON d.id=c.document_id
                WHERE document_fts MATCH ? ORDER BY rank ASC LIMIT ?
            """, (expression, limit)).fetchall()
        else:
            tokens = self._tokens(query)
            if not tokens:
                return []
            conditions = " OR ".join(["c.content LIKE ?" for _ in tokens] + ["d.name LIKE ?"])
            params = [f"%{token}%" for token in tokens] + [f"%{query.strip()}%", limit]
            rows = conn.execute(f"""
                SELECT c.id AS chunk_id,c.document_id,c.ordinal,c.locator,c.content,
                       d.name,d.path,d.embedding_model,0 AS rank
                FROM chunks c JOIN documents d ON d.id=c.document_id
                WHERE {conditions} ORDER BY d.imported_at DESC,c.ordinal LIMIT ?
            """, params).fetchall()
        return [dict(row) for row in rows]

    def _keyword_memory_results(self, conn: sqlite3.Connection, query: str, limit: int) -> list[dict[str, Any]]:
        limit = _safe_limit(limit)
        if limit == 0:
            return []
        expression = self._fts_query(query)
        if self.fts_available and expression:
            rows = conn.execute("""
                SELECT m.id,m.title,m.content,m.category,m.tags,m.updated_at,
                       bm25(memory_fts) AS rank
                FROM memory_fts JOIN memories m ON m.id=memory_fts.rowid
                WHERE memory_fts MATCH ? ORDER BY rank ASC LIMIT ?
            """, (expression, limit)).fetchall()
        else:
            tokens = self._tokens(query)
            if not tokens:
                return []
            searchable = ("m.title", "m.content", "m.category", "m.tags")
            conditions = " OR ".join(
                f"{column} LIKE ?" for _token in tokens for column in searchable
            )
            params = [f"%{token}%" for token in tokens for _column in searchable] + [limit]
            rows = conn.execute(
                f"SELECT m.id,m.title,m.content,m.category,m.tags,m.updated_at,0 AS rank "
                f"FROM memories m WHERE {conditions} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def _semantic_document_results(
        self, conn: sqlite3.Connection, vector: Iterable[float], model: str, limit: int
    ) -> list[dict[str, Any]]:
        """Rank vectors with bounded memory, then fetch text for the top hits only."""
        limit = _safe_limit(limit)
        if limit == 0:
            return []
        query_blob, dimensions = _pack_vector(vector)
        query_vector = _unpack_vector(query_blob, dimensions)
        scan_limit = _semantic_scan_limit(dimensions)

        # Do not fetch chunk text for every semantic candidate. A 20k-row
        # scan of 1,800-character chunks would otherwise materialize tens of
        # megabytes of Python strings in addition to the vector payload.
        cursor = conn.execute("""
            SELECT c.id AS chunk_id,c.embedding,c.embedding_dimensions
            FROM chunks c JOIN documents d ON d.id=c.document_id
            WHERE d.embedding_model=? AND c.embedding_model=? AND c.embedding IS NOT NULL
              AND c.embedding_dimensions=?
            ORDER BY d.imported_at DESC,c.id DESC LIMIT ?
        """, (model, model, dimensions, scan_limit))

        # Keep only top-k (score, chunk_id) pairs; memory use is O(limit), not
        # O(number of candidate chunks). Candidate ids break equal-score ties
        # deterministically in favour of newer chunks.
        top: list[tuple[float, int]] = []
        for row in cursor:
            candidate = _unpack_vector(row["embedding"], int(row["embedding_dimensions"] or 0))
            if len(candidate) != dimensions:
                continue
            score = _cosine_dot(query_vector, candidate)
            item = (score, int(row["chunk_id"]))
            if len(top) < limit:
                heapq.heappush(top, item)
            elif item > top[0]:
                heapq.heapreplace(top, item)

        ranked_ids = [chunk_id for _score, chunk_id in sorted(top, reverse=True)]
        if not ranked_ids:
            return []
        placeholders = ",".join("?" for _ in ranked_ids)
        rows = conn.execute(f"""
            SELECT c.id AS chunk_id,c.document_id,c.ordinal,c.locator,c.content,
                   d.name,d.path,d.embedding_model
            FROM chunks c JOIN documents d ON d.id=c.document_id
            WHERE c.id IN ({placeholders})
        """, ranked_ids).fetchall()
        by_id = {int(row["chunk_id"]): dict(row) for row in rows}
        return [by_id[chunk_id] for chunk_id in ranked_ids if chunk_id in by_id]

    def search(
        self,
        query: str,
        *,
        document_limit: int = 5,
        memory_limit: int = 3,
        embedding_model: str = "",
        query_embedding: Iterable[float] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        query = str(query).strip()
        document_limit = _safe_limit(document_limit, maximum=50)
        memory_limit = _safe_limit(memory_limit, maximum=50)
        if not query or (document_limit == 0 and memory_limit == 0):
            return {"documents": [], "memories": []}
        with self._connection() as conn:
            keyword_docs = self._keyword_document_results(conn, query, document_limit * 4) if document_limit else []
            keyword_memories = self._keyword_memory_results(conn, query, memory_limit * 3) if memory_limit else []
            semantic_docs = []
            if document_limit and embedding_model and query_embedding is not None:
                try:
                    semantic_docs = self._semantic_document_results(
                        conn, query_embedding, embedding_model, document_limit * 4
                    )
                except (KnowledgeError, ValueError, struct.error, sqlite3.DatabaseError):
                    logger.exception("Семантический поиск отключён для этого запроса; используется FTS.")

        # Reciprocal-rank fusion combines lexical and semantic ranks without
        # assuming that their raw score scales are comparable.
        combined: dict[int, tuple[float, dict[str, Any]]] = {}
        for source in (keyword_docs, semantic_docs):
            for rank, row in enumerate(source, start=1):
                key = int(row["chunk_id"])
                score, existing = combined.get(key, (0.0, row))
                combined[key] = (score + 1.0 / (60 + rank), existing)
        docs = [value[1] for value in sorted(combined.values(), key=lambda item: item[0], reverse=True)[:document_limit]]
        return {"documents": docs, "memories": keyword_memories[:memory_limit]}

    def index_document_embeddings(
        self,
        document_id: int,
        *,
        model: str,
        embed: Callable[[list[str]], list[list[float]]],
        batch_size: int = 12,
        cancel_event: threading.Event | None = None,
    ) -> int:
        """Build embeddings in staging and atomically replace the active index.

        Existing vectors stay available until every batch succeeds. If a request
        fails or the source document changes mid-indexing, the old index remains
        intact and temporary rows are removed.
        """
        model = str(model).strip()
        if not model:
            raise KnowledgeError("Не выбрана модель эмбеддингов.")
        batch_size = max(1, min(int(batch_size), 32))
        document_id = int(document_id)
        job_id = uuid.uuid4().hex
        _check_cancel(cancel_event)
        with self._connection() as conn:
            _check_cancel(cancel_event)
            document = conn.execute(
                "SELECT sha256,chunk_count FROM documents WHERE id=?", (document_id,)
            ).fetchone()
            if not document:
                raise KnowledgeError("Документ уже удалён из базы знаний.")
            source_sha = str(document["sha256"])
            rows = conn.execute(
                "SELECT id,content FROM chunks WHERE document_id=? ORDER BY ordinal", (document_id,)
            ).fetchall()
            if not rows:
                raise KnowledgeError("В документе нет фрагментов для индексации.")

        indexed = 0
        expected_dimensions: int | None = None
        try:
            for offset in range(0, len(rows), batch_size):
                _check_cancel(cancel_event)
                batch = rows[offset:offset + batch_size]
                vectors = embed([str(row["content"]) for row in batch])
                _check_cancel(cancel_event)
                if len(vectors) != len(batch):
                    raise KnowledgeError("Модель вернула неверное количество векторов.")
                packed: list[tuple[str, int, bytes, int, str, float]] = []
                for row, vector in zip(batch, vectors):
                    blob, dimensions = _pack_vector(vector)
                    if expected_dimensions is None:
                        expected_dimensions = dimensions
                    elif dimensions != expected_dimensions:
                        raise KnowledgeError("Размерность embedding изменилась между пакетами.")
                    packed.append((job_id, int(row["id"]), blob, dimensions, model, time.time()))
                with self._connection() as conn:
                    _check_cancel(cancel_event)
                    current = conn.execute(
                        "SELECT sha256 FROM documents WHERE id=?", (document_id,)
                    ).fetchone()
                    if not current or str(current["sha256"]) != source_sha:
                        raise KnowledgeError("Документ изменился или был удалён во время индексации.")
                    conn.executemany(
                        "INSERT INTO embedding_staging(job_id,chunk_id,embedding,dimensions,model,created_at) "
                        "VALUES(?,?,?,?,?,?)",
                        packed,
                    )
                indexed += len(batch)

            _check_cancel(cancel_event)
            with self._connection() as conn:
                _check_cancel(cancel_event)
                current = conn.execute(
                    "SELECT sha256,chunk_count FROM documents WHERE id=?", (document_id,)
                ).fetchone()
                staged = int(conn.execute(
                    "SELECT COUNT(*) FROM embedding_staging WHERE job_id=?", (job_id,)
                ).fetchone()[0])
                current_chunks = int(conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE document_id=?", (document_id,)
                ).fetchone()[0])
                _check_cancel(cancel_event)
                if (
                    not current
                    or str(current["sha256"]) != source_sha
                    or staged != len(rows)
                    or current_chunks != len(rows)
                ):
                    raise KnowledgeError("Документ изменился: индекс embeddings не был опубликован.")
                conn.execute("""
                    UPDATE chunks
                    SET embedding=(SELECT s.embedding FROM embedding_staging s
                                   WHERE s.job_id=? AND s.chunk_id=chunks.id),
                        embedding_dimensions=(SELECT s.dimensions FROM embedding_staging s
                                              WHERE s.job_id=? AND s.chunk_id=chunks.id),
                        embedding_model=(SELECT s.model FROM embedding_staging s
                                         WHERE s.job_id=? AND s.chunk_id=chunks.id)
                    WHERE document_id=?
                """, (job_id, job_id, job_id, document_id))
                conn.execute("UPDATE documents SET embedding_model=? WHERE id=?", (model, document_id))
                conn.execute("DELETE FROM embedding_staging WHERE job_id=?", (job_id,))
            return indexed
        except Exception:
            with self._connection() as conn:
                conn.execute("DELETE FROM embedding_staging WHERE job_id=?", (job_id,))
            raise

    def has_embeddings(self, model: str) -> bool:
        """Return whether at least one searchable document vector exists for model."""
        model = str(model).strip()
        if not model:
            return False
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM chunks c JOIN documents d ON d.id=c.document_id "
                "WHERE d.embedding_model=? AND c.embedding_model=? "
                "AND c.embedding IS NOT NULL LIMIT 1",
                (model, model),
            ).fetchone()
        return row is not None

    def embedding_for_query(
        self,
        query: str,
        *,
        model: str,
        ollama_url: str,
        embedder: Callable[[list[str]], list[list[float]]] | None = None,
    ) -> list[float]:
        query = str(query).strip()
        if not query:
            raise KnowledgeError("Поисковый запрос для embedding пустой.")
        if embedder is None:
            from .embeddings import embed_texts
            vectors = embed_texts([query], model=model, ollama_url=ollama_url, timeout=90)
        else:
            vectors = embedder([query])
        if len(vectors) != 1:
            raise KnowledgeError("Модель вернула неверное количество embedding-векторов.")
        _pack_vector(vectors[0])  # Validate before passing the vector to the search layer.
        return [float(value) for value in vectors[0]]
