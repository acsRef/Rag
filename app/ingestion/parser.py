"""File-type dispatching parser.

Dispatches by suffix to the right handler:
  - .txt/.md/.csv → raw decode with chardet encoding detection
  - .pdf/.docx/.pptx/.html/.json/.xml/.xlsx → Docling → Markdown
  - image files (png/jpg/gif/bmp/webp) → MiniMax vision API
  - embedded images in Docling docs → extracted, described, replaced with text
"""

import codecs
import io
import os
import re
import tempfile

import chardet
from docling.document_converter import DocumentConverter

from app.llm.vision import image_describer

FILE_TYPE_MAP = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "doc",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".pptx": "pptx",
    ".ppt": "ppt",
    ".txt": "text",
    ".md": "text",
    ".csv": "text",
    ".json": "json",
    ".xml": "xml",
    ".html": "html",
    ".htm": "html",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".bmp": "image",
    ".webp": "image",
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
        try:
            return handler(content, filename)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Handler %s failed for %s", file_type, filename)
            raise

    # ── Encoding ──────────────────────────────────────────

    def _detect_encoding(self, content: bytes) -> str:
        """Detect encoding via chardet; normalize GB2312 → gbk."""
        result = chardet.detect(content)
        encoding = result.get("encoding", "utf-8") or "utf-8"
        encoding = encoding.lower().replace("-", "_")
        encoding = encoding.replace("gb2312", "gbk").replace("gb_2312", "gbk")
        try:
            codecs.lookup(encoding)
        except LookupError:
            encoding = "utf-8"
        return encoding

    def _ensure_utf8(self, content: bytes) -> bytes:
        """Decode with detected encoding, re-encode as UTF-8."""
        encoding = self._detect_encoding(content)
        if encoding in ("utf_8", "ascii"):
            return content
        try:
            text = content.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            text = content.decode("utf-8", errors="replace")
        return text.encode("utf-8")

    # ── Temp file helper ──────────────────────────────────

    def _to_tempfile(self, content: bytes, filename: str) -> str:
        """Write bytes to a temp file for Docling (needs filesystem path)."""
        suffix = os.path.splitext(filename)[1] or ".pdf"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            try:
                tmp.write(content)
                return tmp.name
            except Exception:
                os.unlink(tmp.name)
                raise

    # ── Docling based parsing ─────────────────────────────

    def _parse_via_docling(self, content: bytes, filename: str) -> str:
        """Convert to Markdown via Docling; replace embedded images with descriptions."""
        tmp_path = self._to_tempfile(content, filename)
        try:
            result = self.converter.convert(tmp_path)
            md = result.document.export_to_markdown()
            if result.document.pictures:
                md = self._replace_embedded_images(md, result.document.pictures, result.document)
            return md
        finally:
            os.unlink(tmp_path)

    # ── Embedded image replacement ────────────────────────

    _IMG_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

    def _replace_embedded_images(self, md: str, pictures, doc) -> str:
        """Replace each Markdown image in Docling output with a text description from vision API.

        Only describes images that pass size/filter checks; skips or drops the rest.
        `doc` 为 DoclingDocument：PictureItem.get_image 必须携带它才能解出图片
        （旧实现漏传该参数，TypeError 被吞，内嵌图片描述功能整体静默失效）。
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
                pil_img = pic.get_image(doc)
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
            return md

        descriptions = image_describer.describe_batch_sync(images_to_describe)

        result_md = md
        for batch_idx, pic_idx in pic_to_match_idx.items():
            placeholder = matches[pic_idx].group(0)
            desc = (
                f"[图片：{descriptions[batch_idx]}]"
                if batch_idx < len(descriptions)
                else "[图片描述失败]"
            )
            result_md = result_md.replace(placeholder, desc, 1)

        return result_md

    # ── Image file handler (pure image, no Docling) ───────

    def _handle_image(self, content: bytes, filename: str) -> str:
        """Standalone image file → describe via vision API (no Docling involved)."""
        try:
            return image_describer.describe_sync(content, filename)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Vision API failed for %s", filename)
            return "[图片描述失败]"

    # ── Text-like file handlers (with encoding detection) ─

    def _handle_text(self, content: bytes, filename: str) -> str:
        """Plain text with chardet encoding detection; no Docling."""
        encoding = self._detect_encoding(content)
        return content.decode(encoding, errors="replace")

    def _handle_json(self, content: bytes, filename: str) -> str:
        return self._parse_via_docling(content, filename)

    def _handle_xml(self, content: bytes, filename: str) -> str:
        return self._parse_via_docling(content, filename)

    def _handle_html(self, content: bytes, filename: str) -> str:
        return self._parse_via_docling(content, filename)

    # ── Doc handlers ──────────────────────────────────────

    def _handle_pdf(self, content: bytes, filename: str) -> str:
        """PDF via pymupdf4llm — preserves table structure in Markdown (比纯 PyMuPDF 好很多).

        速度约 4 分钟/250页 PDF (CPU),但表格识别质量显著提升。
        后处理：去除页眉页脚噪音（页码、重复的文档标题）。
        """
        import tempfile

        import pymupdf4llm

        try:
            # pymupdf4llm needs a file path, not bytes
            suffix = ".pdf"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                md = pymupdf4llm.to_markdown(tmp_path)
                if not md or not md.strip():
                    return self._parse_pdf_basic(content, filename)
                # 去除页眉页脚噪音
                md = self._clean_pdf_noise(md)
                return md
            finally:
                import os

                os.unlink(tmp_path)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "pymupdf4llm failed for %s, falling back to basic", filename
            )
            return self._parse_pdf_basic(content, filename)

    @staticmethod
    def _clean_pdf_noise(md: str) -> str:
        """去除 PDF 页眉页脚噪音：页码行、重复的文档标题行。

        匹配模式：
        - "**N** / **M**" 或 "N / M" 形式的页码
        - 连续出现 3+ 次的短行（如 "2023 年年度报告"）视为页眉/页脚
        """
        lines = md.split("\n")
        cleaned: list[str] = []

        # 统计短行出现频率，找出页眉/页脚候选
        from collections import Counter

        short_lines = Counter()
        for line in lines:
            stripped = line.strip()
            if stripped and len(stripped) < 40:
                short_lines[stripped] += 1

        # 出现 5+ 次的短行视为页眉/页脚（年报 250 页，页眉至少出现 100+ 次）
        noise_patterns = {text for text, count in short_lines.items() if count >= 5}
        # 页码模式 "**N** / **M**" 或 "N / M"
        page_num_re = re.compile(r"^\*?\*?\d+\*?\*?\s*/\s*\*?\*?\d+\*?\*?$")

        for line in lines:
            stripped = line.strip()
            # 跳过页码行
            if page_num_re.match(stripped):
                continue
            # 跳过高频短行（页眉/页脚）
            if stripped in noise_patterns:
                continue
            cleaned.append(line)

        return "\n".join(cleaned)

    def _parse_pdf_basic(self, content: bytes, filename: str) -> str:
        """Fallback: basic PyMuPDF text extraction (fast but no table structure)."""
        import fitz

        doc = fitz.open(stream=content, filetype="pdf")
        pages_md: list[str] = []
        for i, page in enumerate(doc, 1):
            text = page.get_text("text")
            if text and text.strip():
                pages_md.append(f"<!-- Page {i} -->\n\n{text.strip()}")
        doc.close()
        return "\n\n---\n\n".join(pages_md) if pages_md else ""

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
