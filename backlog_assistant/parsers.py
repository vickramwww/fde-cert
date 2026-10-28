"""Input parsing and preprocessing: text, PDF and JSON backlog files."""
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("backlog.parsers")


class InputError(Exception):
    """User-facing problem with an input file."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise InputError(f"Cannot read {path}: {e}") from e


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise InputError("PDF support needs pypdf: pip install pypdf") from e
    try:
        return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    except Exception as e:  # pypdf raises many types for corrupt files
        raise InputError(f"Cannot parse PDF {path}: {e}") from e


def clean_text(text: str) -> str:
    """Normalise whitespace and drop empty noise while keeping structure."""
    text = text.replace("\r\n", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_document(path_str: str) -> str:
    """Load a .txt/.md/.pdf document and return cleaned text."""
    path = Path(path_str)
    if not path.is_file():
        raise InputError(f"File not found: {path}")
    text = _read_pdf(path) if path.suffix.lower() == ".pdf" else _read_text(path)
    text = clean_text(text)
    if not text:
        raise InputError(f"{path} is empty (or has no extractable text).")
    log.info("Loaded %s (%d chars)", path, len(text))
    return text


def load_backlog(path_str: str) -> list[dict]:
    """Load backlog JSON: a list of items, or {"items"|"backlog"|"tasks": [...]}."""
    path = Path(path_str)
    if not path.is_file():
        raise InputError(f"File not found: {path}")
    try:
        data = json.loads(_read_text(path))
    except json.JSONDecodeError as e:
        raise InputError(f"{path} is not valid JSON: {e}") from e
    if isinstance(data, dict):
        data = next((data[k] for k in ("items", "backlog", "tasks") if k in data), None)
    if not isinstance(data, list) or not data:
        raise InputError(f"{path} must contain a non-empty list of backlog items.")
    items = []
    for i, item in enumerate(data):
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict) or not (item.get("title") or item.get("summary")):
            log.warning("Skipping backlog entry %d: no title (%r)", i, item)
            continue
        items.append(item)
    if not items:
        raise InputError(f"{path} has no usable backlog items (each needs a 'title').")
    log.info("Loaded %d backlog items from %s", len(items), path)
    return items
