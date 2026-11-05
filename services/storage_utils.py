"""Keep uploaded files private, under project-local storage directories."""
import hashlib
import io
import uuid
import zipfile
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
STORAGE_ROOT = ROOT / 'storage'
MAX_ASSIGNMENT_BYTES = 12 * 1024 * 1024
MAX_RULES_BYTES = 128 * 1024


def validate_document(filename: str, content: bytes):
    if not filename or Path(filename).suffix.lower() not in {'.pdf', '.docx', '.txt', '.md'}:
        raise ValueError('Upload an assignment as PDF, DOCX, TXT, or MD.')
    if not content or len(content) > MAX_ASSIGNMENT_BYTES:
        raise ValueError('Assignment file must be nonempty and no larger than 12 MB.')
    suffix = Path(filename).suffix.lower()
    if suffix == '.pdf' and not content.startswith(b'%PDF-'):
        raise ValueError('This file is not a valid PDF.')
    if suffix == '.docx':
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if 'word/document.xml' not in archive.namelist():
                    raise ValueError('This file is not a valid DOCX document.')
        except zipfile.BadZipFile as exc:
            raise ValueError('This file is not a valid DOCX document.') from exc
    if suffix in {'.txt', '.md'}:
        try:
            content.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('Text assignment must be UTF-8 encoded.') from exc


def write_private(kind: str, filename: str, content: bytes):
    if kind not in {'assignments', 'rules', 'submissions', 'references'}:
        raise ValueError('Invalid storage category.')
    suffix = Path(filename).suffix.lower()
    folder = STORAGE_ROOT / kind
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{uuid.uuid4().hex}{suffix}'
    with path.open('xb') as out:
        out.write(content)
    return path.relative_to(ROOT).as_posix(), hashlib.sha256(content).hexdigest()


def resolve_private(path: str, kind: str):
    if kind not in {'assignments', 'rules', 'submissions', 'references'}:
        raise ValueError('Invalid storage category.')
    # Rows uploaded on Windows contain relative paths with backslashes, e.g.
    # storage\submissions\<uuid>.c.  On WSL, Path does not recognize those
    # separators, which incorrectly makes legitimate stored files inaccessible.
    # Normalize relative paths only; do not treat a Windows absolute path as
    # relative to the project (or permit UNC/network paths).
    raw = str(path)
    if (not raw or "\x00" in raw or re.match(r"^[A-Za-z]:", raw)
            or raw.startswith("\\\\") or raw.startswith("//")):
        raise PermissionError('File path is outside the permitted storage directory.')
    target = Path(raw.replace("\\", "/"))
    if not target.is_absolute():
        target = ROOT / target
    target = target.resolve()
    allowed = (STORAGE_ROOT / kind).resolve()
    if not target.is_relative_to(allowed):
        raise PermissionError('File path is outside the permitted storage directory.')
    return target


def read_private(path: str, kind: str):
    return resolve_private(path, kind).read_bytes()


def discard_private(path: str | None, kind: str):
    if path:
        resolve_private(path, kind).unlink(missing_ok=True)
