"""File-type dispatching parser.

Dispatches by suffix to the right handler:
  - .txt/.md/.csv → raw decode with chardet encoding detection
  - .pdf/.docx/.pptx/.html/.json/.xml/.xlsx → Docling → Markdown
  - image files (png/jpg/gif/bmp/webp) → MiniMax vision API
  - embedded images in Docling docs → extracted, described, replaced with text
"""

import io
import os
import re
import tempfile

import chardet
from docling.document_converter import DocumentConverter

from app.llm.vision import image_describer


FILE_TYPE_MAP = {
    ".pdf": "pdf",
    ".docx": "docx", ".doc": "doc",
    ".xlsx": "xlsx", ".xls": "xls",
    ".pptx": "pptx", ".ppt": "ppt",
    ".txt": "text", ".md": "text", ".csv": "text",
    ".json": "json", ".xml": "xml",
    ".html": "html", ".htm": "html",
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".gif": "image", ".bmp": "image", ".webp": "image",
}


class DocumentParser:
    """Parses uploaded document bytes into Markdown text.

    Uses FILE_TYPE_MAP to dispatch to suffix-specific handlers.
    Text files go through chardet encoding detection; binary/doc formats
    go through Docling; image-only files go through the vision API.
    Embedded images in Docling output are extracted and described via vision API.
    """

    def __init__(self):
        self.converter = DocumentConverter()

    def parse_bytes(self, content: bytes, filename: str) -> str:
        """Main entry point: detect type from suffix, dispatch to handler."""
        suffix = os.path.splitext(filename)[1].lower()
        file_type = FILE_TYPE_MAP.get(suffix, "unknown")
        handler = getattr(self, f"_handle_{file_type}", None)
        if handler is None:
            raise ValueError(f"Unsupported file type: {suffix}")
        return handler(content, filename)

    # ── Encoding ──────────────────────────────────────────

    def _detect_encoding(self, content: bytes) -> str:
        """Detect encoding via chardet; normalize GB2312 → gbk."""
        result = chardet.detect(content)
        encoding = result.get("encoding", "utf-8") or "utf-8"
        encoding = encoding.lower().replace("-", "_")
        encoding = encoding.replace("gb2312", "gbk").replace("gb_2312", "gbk")
        return encoding

    def _ensure_utf8(self, content: bytes) -> bytes:
        """Decode with detected encoding, re-encode as UTF-8."""
        encoding = self._detect_encoding(content)
        if encoding in ("utf_8", "ascii"):
            return content
        text = content.decode(encoding, errors="replace")
        return text.encode("utf-8")

    # ── Temp file helper ──────────────────────────────────

    def _to_tempfile(self, content: bytes, filename: str) -> str:
        """Write bytes to a temp file for Docling (needs filesystem path)."""
        suffix = os.path.splitext(filename)[1] or ".tmp"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(content)
            return f.name

    # ── Docling based parsing ─────────────────────────────

    def _parse_via_docling(self, content: bytes, filename: str) -> str:
        """Convert to Markdown via Docling; replace embedded images with descriptions."""
        tmp_path = self._to_tempfile(content, filename)
        try:
            result = self.converter.convert(tmp_path)
            md = result.document.export_to_markdown()
            if result.document.pictures:
                md = self._replace_embedded_images(md, result.document.pictures)
            return md
        finally:
            os.unlink(tmp_path)

    # ── Embedded image replacement ────────────────────────

    _IMG_PATTERN = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')

    def _replace_embedded_images(self, md: str, pictures) -> str:
        """Replace each Markdown image in Docling output with a text description from vision API.

        Only describes images that pass size/filter checks; skips or drops the rest.
        """
        matches = list(self._IMG_PATTERN.finditer(md))
        if not matches or not pictures:
            return md

        images_to_describe: list[tuple[bytes, str]] = []
        pic_to_match_idx: dict[int, int] = {}

        for i, pic in enumerate(pictures):
            if i >= len(matches):
                break
            try:
                pil_img = pic.get_image()
                if pil_img is None:
                    continue
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                img_bytes = buf.getvalue()
                if not image_describer._should_skip(img_bytes, pil_img.size):
                    images_to_describe.append((img_bytes, f"image_{i}.png"))
                    pic_to_match_idx[len(images_to_describe) - 1] = i
            except Exception:
                continue

        if not images_to_describe:
            return self._IMG_PATTERN.sub("", md)

        descriptions = image_describer.describe_batch(images_to_describe)

        result_md = md
        for batch_idx, pic_idx in pic_to_match_idx.items():
            placeholder = matches[pic_idx].group(0)
            desc = f"[图片：{descriptions[batch_idx]}]"
            result_md = result_md.replace(placeholder, desc, 1)

        result_md = self._IMG_PATTERN.sub("", result_md)
        return result_md

    # ── Image file handler (pure image, no Docling) ───────

    def _handle_image(self, content: bytes, filename: str) -> str:
        """Standalone image file → describe via vision API (no Docling involved)."""
        return image_describer.describe(content, filename)

    # ── Text-like file handlers (with encoding detection) ─

    def _handle_text(self, content: bytes, filename: str) -> str:
        """Plain text with chardet encoding detection; no Docling."""
        encoding = self._detect_encoding(content)
        return content.decode(encoding, errors="replace")

    def _handle_json(self, content: bytes, filename: str) -> str:
        content = self._ensure_utf8(content)
        return self._parse_via_docling(content, filename)

    def _handle_xml(self, content: bytes, filename: str) -> str:
        content = self._ensure_utf8(content)
        return self._parse_via_docling(content, filename)

    def _handle_html(self, content: bytes, filename: str) -> str:
        content = self._ensure_utf8(content)
        return self._parse_via_docling(content, filename)

    # ── Doc handlers ──────────────────────────────────────

    def _handle_pdf(self, content: bytes, filename: str) -> str:
        return self._parse_via_docling(content, filename)

    def _handle_docx(self, content: bytes, filename: str) -> str:
        return self._parse_via_docling(content, filename)

    def _handle_doc(self, content: bytes, filename: str) -> str:
        return self._parse_via_docling(content, filename)

    def _handle_xlsx(self, content: bytes, filename: str) -> str:
        return self._parse_via_docling(content, filename)

    def _handle_xls(self, content: bytes, filename: str) -> str:
        return self._parse_via_docling(content, filename)

    def _handle_pptx(self, content: bytes, filename: str) -> str:
        return self._parse_via_docling(content, filename)

    def _handle_ppt(self, content: bytes, filename: str) -> str:
        return self._parse_via_docling(content, filename)

    def _handle_unknown(self, content: bytes, filename: str) -> str:
        raise ValueError(f"Unsupported file type: {os.path.splitext(filename)[1]}")


document_parser = DocumentParser()
