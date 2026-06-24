"""6-step text cleaner + Docling-specific 4-rule fixer.

通用清洗(line endings / control chars / unicode / invisible / page markers / blank lines)
加 4 条 Docling 解析产物修复(PUA / fullwidth / CJK spacing / image placeholder)。
"""
import re
import unicodedata


class DocumentCleaner:
    def clean(
        self,
        text: str,
        fix_punctuation: bool = False,
        collapse_spaces: bool = False,
    ) -> str:
        if not text:
            return ""
        text = self._normalize_line_endings(text)
        text = self._remove_control_chars(text)
        text = self._normalize_unicode(text)
        text = self._remove_invisible_chars(text)
        text = self._remove_pua_chars(text)
        text = self._remove_image_placeholders(text)
        text = self._fullwidth_to_halfwidth(text)
        text = self._collapse_cjk_char_spacing(text)
        if fix_punctuation:
            text = self._normalize_punctuation(text)
        text = self._strip_line_trailing_spaces(text)
        text = self._remove_page_markers(text)
        text = self._collapse_blank_lines(text)
        if collapse_spaces:
            text = self._collapse_extra_spaces(text)
        return text.strip()

    def _normalize_line_endings(self, text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def _remove_control_chars(self, text: str) -> str:
        return re.sub(r"[\x00-\x08\x0b\x0e-\x1f\x7f]", "", text)

    def _normalize_unicode(self, text: str) -> str:
        try:
            import ftfy
            text = ftfy.fix_text(text, normalization="NFC")
        except ImportError:
            text = unicodedata.normalize("NFC", text)
        return text

    def _remove_invisible_chars(self, text: str) -> str:
        text = text.replace("﻿", "")
        text = text.replace("​", "")
        text = text.replace("‌", "")
        text = text.replace("‍", "")
        text = text.replace("­", "")
        text = text.replace("‎", "")
        text = text.replace("‏", "")
        text = text.replace("⁠", "")
        text = text.replace(" ", "\n")
        text = text.replace(" ", "\n")
        text = text.replace(" ", " ")
        text = text.replace(" ", " ")
        text = text.replace("\xa0", " ")
        return text

    def _normalize_punctuation(self, text: str) -> str:
        text = text.replace("“", '"').replace("”", '"')
        text = text.replace("‘", "'").replace("’", "'")
        text = text.replace("–", "-").replace("—", "-")
        text = text.replace("…", "...")
        text = text.replace("⸺", "--").replace("⸻", "--")
        return text

    def _strip_line_trailing_spaces(self, text: str) -> str:
        return "\n".join(line.rstrip() for line in text.split("\n"))

    def _remove_page_markers(self, text: str) -> str:
        text = re.sub(r"(?m)^---\s*Page\s+\d+\s*---\s*$", "", text)
        text = re.sub(r"(?m)^---\s*第\s*\d+\s*页\s*---\s*$", "", text)
        text = re.sub(r"(?m)^<!--\s*pagebreak\s*-->$", "", text)
        text = text.replace("\f", "\n")
        return text

    def _collapse_blank_lines(self, text: str) -> str:
        return re.sub(r"\n{3,}", "\n\n", text)

    def _collapse_extra_spaces(self, text: str) -> str:
        return re.sub(r"[ \t]+", " ", text)

    # ── Docling PDF 解析产物修复(企业级 RAG 必备) ──────────────

    def _remove_pua_chars(self, text: str) -> str:
        """删除 Unicode 私用区字符(BMP + 增补平面)。

        Docling 解析 PDF 偶发产生 \\U001001b0 这类无意义字符,会污染 embedding。
        """
        if not text:
            return text
        return re.sub(
            "[" + chr(0xE000) + "-" + chr(0xF8FF)
                + chr(0xF0000) + "-" + chr(0xFFFFD)
                + chr(0x100000) + "-" + chr(0x10FFFD) + "]",
            "",
            text,
        )

    def _fullwidth_to_halfwidth(self, text: str) -> str:
        """全角 ASCII 范围 → 半角。

        Docling 解析中文 PDF 时常把阿拉伯数字识别成全角(`２０２６` → `2026`),
        会让用户搜"2026"时漏召。
        """
        if not text:
            return text
        result = []
        for ch in text:
            code = ord(ch)
            if 0xFF01 <= code <= 0xFF5E:
                result.append(chr(code - 0xFEE0))
            elif code == 0x3000:
                result.append(" ")
            else:
                result.append(ch)
        return "".join(result)

    def _collapse_cjk_char_spacing(self, text: str) -> str:
        """合并 CJK 字符之间多余的 ASCII 空格(迭代到稳定)。

        Docling 解析中文 PDF 时常把字间距当空格保留(`注 意 事 项` → `注意事项`)。
        单步 re.sub 只能合并 `注 意`→`注意`,但 `注意 事` 还会触发,所以迭代到稳定。
        """
        if not text:
            return text
        # 基本汉字 + 扩展 A
        cjk = "一-鿽㐀-䶿"
        backref = chr(92) + "1" + chr(92) + "2"
        prev = None
        while prev != text:
            prev = text
            text = re.sub("([" + cjk + "]) ([" + cjk + "])", backref, text)
        return text

    def _remove_image_placeholders(self, text: str) -> str:
        """删除 Docling 输出的图片占位符(HTML 注释形式)。

        Docling 解析图片时,如果没匹配上 embedded image 会输出 `<!-- image -->`。
        这类占位符会污染 chunk 文本(出现在正文中)且对检索无意义,直接删除整行。
        """
        if not text:
            return text
        return re.sub(r"^[ \t]*<!--\s*image\s*-->[ \t]*\n?", "", text, flags=re.MULTILINE)


document_cleaner = DocumentCleaner()
