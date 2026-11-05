"""Extract readable assignment text for the test generator (not arbitrary code execution)."""
from __future__ import annotations
import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree


def assignment_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {'.txt', '.md'}:
        return content.decode('utf-8', 'replace')[:18000]
    if suffix == '.docx':
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            doc = ElementTree.fromstring(z.read('word/document.xml'))
        ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        return '\n'.join(''.join(node.text or '' for node in paragraph.iter(ns+'t'))
                         for paragraph in doc.iter(ns+'p'))[:18000]
    if suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return '\n'.join(page.extract_text() or '' for page in reader.pages[:30])[:18000]
    return ''
